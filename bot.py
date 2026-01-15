import asyncio
import logging
import sys
from datetime import datetime, timedelta
from collections import defaultdict
import time

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from config import BOT_TOKEN, CHIEF_ADMIN_IDS, TECH_SPECIALIST_ID
from keyboards import get_main_menu_keyboard
from handlers.common import router as common_router
from handlers.organizer import router as organizer_router
from handlers.admin import router as admin_router
from handlers.tech_support import router as tech_support_router
from handlers.ban import router as ban_router

from database import (
    init_db, enable_wal, get_bot_status, get_or_create_user,
    AsyncSessionLocal, Conference, Application
)

# ────────────────────────────────────────────────
# Настройка логирования (терминал + файл)
# ────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG,  # DEBUG для отладки, потом можно INFO
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ],
    force=True
)

logging.getLogger("aiogram").setLevel(logging.DEBUG)
logging.getLogger("aiohttp").setLevel(logging.DEBUG)

# ────────────────────────────────────────────────
# Инициализация бота и диспетчера
# ────────────────────────────────────────────────

default_properties = DefaultBotProperties(parse_mode="HTML")
bot = Bot(token=BOT_TOKEN, default=default_properties)
dp = Dispatcher()


# ────────────────────────────────────────────────
# Антиспам (rate-limit 2 запроса в секунду)
# ────────────────────────────────────────────────

class SimpleRateLimitMiddleware:
    def __init__(self, rate_limit: float = 0.5):
        self.rate_limit = rate_limit
        self.last_call = defaultdict(float)

    async def __call__(self, handler, event, data):
        user_id = None
        if hasattr(event, 'from_user') and event.from_user:
            user_id = event.from_user.id
        elif hasattr(event, 'message') and event.message and event.message.from_user:
            user_id = event.message.from_user.id
        elif hasattr(event, 'callback_query') and event.callback_query and event.callback_query.from_user:
            user_id = event.callback_query.from_user.id

        if not user_id:
            return await handler(event, data)

        now = time.time()
        last = self.last_call[user_id]
        if now - last < self.rate_limit:
            if hasattr(event, 'message') and event.message:
                await event.message.answer("⏳ Не спамьте, подождите секунду...")
            elif hasattr(event, 'callback_query') and event.callback_query:
                await event.callback_query.answer("⏳ Не спамьте!", show_alert=True)
            return

        self.last_call[user_id] = now
        return await handler(event, data)


dp.update.middleware(SimpleRateLimitMiddleware(rate_limit=0.5))


# ────────────────────────────────────────────────
# Глобальный middleware: бан + пауза бота
# ────────────────────────────────────────────────



# Подключаем роутеры
dp.include_router(common_router)
dp.include_router(organizer_router)
dp.include_router(admin_router)
dp.include_router(tech_support_router)
dp.include_router(ban_router)


# ────────────────────────────────────────────────
# Главное меню
# ────────────────────────────────────────────────

from middlewares.ban_middleware import BanMiddleware

dp.message.middleware(BanMiddleware())
dp.callback_query.middleware(BanMiddleware())

# Универсальная функция главного меню с приветствием
async def show_main_menu(message: types.Message | types.CallbackQuery):
    if isinstance(message, types.CallbackQuery):
        user = message.from_user
        msg = message.message
    else:
        user = message.from_user
        msg = message

    db_user = await get_or_create_user(user.id, user.full_name)

    if db_user.is_banned:
        await msg.answer(
            "🚫 Вы заблокированы в боте.\n"
            "Обратитесь к техподдержке для разблокировки."
        )
        return

    welcome_text = (
        f"Привет, <b>{user.full_name or 'друг'}</b>!\n\n"
        "Добро пожаловать в <b>MUN-Бот</b> — платформу для участия и организации конференций Модели ООН.\n\n"
        f"Ваша роль: <b>{db_user.role}</b>\n\n"
        "Выберите действие:"
    )

    if user.id in CHIEF_ADMIN_IDS:
        welcome_text += "\n\n🔧 <b>Вы — Главный Админ</b>. Полный доступ."

    if user.id == TECH_SPECIALIST_ID:
        welcome_text += "\n\n🛠 <b>Вы — Главный Тех Специалист</b>."

    status = await get_bot_status()
    if status.is_paused and not (user.id in CHIEF_ADMIN_IDS or user.id == TECH_SPECIALIST_ID):
        welcome_text += f"\n\n🛑 <b>Бот приостановлен</b>\nПричина: {status.pause_reason or 'Технические работы'}"

    await msg.answer(welcome_text, reply_markup=get_main_menu_keyboard(db_user.role))


# ────────────────────────────────────────────────
# Хендлеры команд и кнопок главного меню
# ────────────────────────────────────────────────

@dp.message(Command("start", "main_menu"))
async def cmd_start_or_main_menu(message: types.Message):
    await show_main_menu(message)


@dp.message(F.text == "🔄 Обновить")
async def refresh_menu(message: types.Message):
    await show_main_menu(message)


# Участник
@dp.message(F.text == "🔍 Просмотр конференций")
async def text_conferences(message: types.Message):
    from handlers.common import cmd_conferences
    await cmd_conferences(message)


@dp.message(F.text == "📝 Подать заявку на участие")
async def text_register(message: types.Message):
    from handlers.common import cmd_register
    await cmd_register(message)


@dp.message(F.text == "➕ Создать конференцию")
async def text_create_conference(message: types.Message, state: FSMContext):
    from handlers.common import cmd_create_conference
    await cmd_create_conference(message, state)


@dp.message(F.text == "📞 Обращение к тех. специалисту")
async def text_support_appeal(message: types.Message, state: FSMContext):
    from handlers.common import start_support_appeal
    await start_support_appeal(message, state)


# Организатор
@dp.message(F.text == "📋 Мои конференции")
async def text_my_conferences(message: types.Message):
    from handlers.organizer import my_conferences
    await my_conferences(message)


@dp.message(F.text == "📩 Заявки участников")
async def text_applications(message: types.Message):
    from handlers.organizer import current_applications
    await current_applications(message)


@dp.message(F.text == "🗃 Архив заявок")
async def text_archive(message: types.Message):
    from handlers.organizer import archive_applications
    await archive_applications(message)


# Глав Тех Специалист
@dp.message(F.text == "📞 Очередь обращений участников")
async def text_support_requests(message: types.Message):
    from handlers.tech_support import list_support_requests
    await list_support_requests(message)


@dp.message(F.text == "🚫 Список забаненных пользователей")
async def text_banned_list(message: types.Message):
    from handlers.ban import banned_list
    await banned_list(message)


@dp.message(F.text == "⚠ Бан/разбан пользователей")
async def text_ban_menu(message: types.Message):
    await message.answer(
        "Команды для бана/разбана:\n"
        "/ban @username или /ban ID — забанить\n"
        "/unban @username или /unban ID — разбанить"
    )


@dp.message(F.text == "🔑 Назначить роль другим пользователям")
async def text_set_role_tech(message: types.Message):
    await message.answer("Используйте команду /set_role @username роль")


@dp.message(F.text == "📤 Экспорт данных бота")
async def text_export_bot_data_tech(message: types.Message):
    from handlers.admin import export_bot_data
    await export_bot_data(message)


@dp.message(F.text == "📊 Статистика")
async def text_stats_tech(message: types.Message):
    from handlers.admin import stats
    await stats(message)


@dp.message(F.text == "🗂 Все конференции")
async def text_all_confs_tech(message: types.Message):
    from handlers.admin import view_all_conferences
    await view_all_conferences(message)


@dp.message(F.text == "🗑 Удалить конференцию")
async def text_delete_conf_tech(message: types.Message):
    await message.answer("Используйте команду /delete_conf ID_конференции причина")


# Админ
@dp.message(F.text == "📩 Просмотр заявок на конференции")
async def text_admin_requests(message: types.Message):
    from handlers.admin import admin_conference_requests
    await admin_conference_requests(message)


@dp.message(F.text == "🗂 Все конференции")
async def text_all_confs_admin(message: types.Message):
    from handlers.admin import view_all_conferences
    await view_all_conferences(message)


@dp.message(F.text == "🗑 Удалить конференцию")
async def text_delete_conf_admin(message: types.Message):
    await message.answer("Используйте команду /delete_conf ID_конференции причина")


# Главный Админ
@dp.message(F.text == "📥 Посмотреть апелляции")
async def text_view_appeals(message: types.Message):
    from handlers.admin import view_appeals
    await view_appeals(message)


# Общие
@dp.message(F.text == "❓ Помощь")
async def text_help_button(message: types.Message):
    await cmd_help(message)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.full_name)
    help_text = "📖 <b>Помощь по MUN-Боту</b>\n\n"
    help_text += "Общие команды:\n"
    help_text += "/start — начать работу\n"
    help_text += "/main_menu — возврат в главное меню\n\n"

    if user.role == "Участник":
        help_text += "😊 Для участников:\n"
        help_text += "🔍 Найти конференции — Cписок доступных конференций\n"
        help_text += "📝 Подать заявку — Регистрация на конференцию\n"
        help_text += "➕ Создать конференцию — Заявление на создание конференции\n"
        help_text += "📩 Обращение к тех. специалисту — Поддержка бота\n\n"

    elif user.role == "Организатор":
        help_text += "🧑‍💼 Для организаторов:\n"
        help_text += "📋 Мои конференции — Ваша конференция\n"
        help_text += "📩 Заявки участников — Новые заявление на участие от участников\n"
        help_text += "🗃 Архив заявок — Старые заявление на участие\n"
        help_text += "📩 Обращение к тех. специалисту — Поддержка бота\n\n"

    elif user.role == "Админ":
        help_text += "🔧 Для админов:\n"
        help_text += "📩 Просмотр заявок на конференции — Модерация\n"
        help_text += "🗂 Все конференции — Список конференций\n"
        help_text += "🗑 Удалить конференцию — /delete_conf ID причина\n"
        help_text += "❗️ Бан/разбан — /ban,/unban id\n"
        help_text += "📊 Статистика — Общая статистика\n"
        help_text += "📩 Обращение к тех. специалисту — Поддержка бота\n\n"

    elif user.role == "Глав Админ":
        help_text += "👑 Для Глав Админа:\n"
        help_text += "📩 Просмотр заявок на конференции — Модерация\n"
        help_text += "📥 Посмотреть апелляции — Апелляции\n"
        help_text += "🗂 Все конференции — Список конференций\n"
        help_text += "🗑 Удалить конференцию — /delete_conf ID причина\n"
        help_text += "❗️ Бан/разбан — /ban,/unban id\n"
        help_text += "📊 Статистика — Общая статистика\n"
        help_text += "📩 Обращение к тех. специалисту — Поддержка бота\n"
        help_text += "/stats — Статистика\n\n"

    elif user.role == "Глав Тех Специалист":
        help_text += "🛠 Для Главного Тех Специалиста:\n"
        help_text += "📞 Очередь обращений — Список обращений\n"
        help_text += "❗️ Бан/разбан — /ban,/unban id\n"
        help_text += "🔑 Назначить роль — /set_role @username роль\n"
        help_text += "📤 Экспорт данных — Экспорт информации\n"
        help_text += "📊 Статистика — Общая статистика\n"
        help_text += "🗂 Все конференции — Список конференций\n"
        help_text += "🗑 Удалить конференцию — /delete_conf ID причина\n"
        help_text += "📢 Рассылка всем — /broadcast\n"
        help_text += "/stats — Статистика\n\n"

    await message.answer(help_text, parse_mode="HTML")


@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>", parse_mode="HTML")


# ────────────────────────────────────────────────
# Callback-обработчики отмены и возврата
# ────────────────────────────────────────────────

@dp.callback_query(F.data == "cancel_form")
async def cancel_form(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_main_menu(callback)
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await show_main_menu(callback)
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()


# ────────────────────────────────────────────────
# Напоминания
# ────────────────────────────────────────────────

async def send_daily_reminders():
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Conference)
            .where(sa.func.date(Conference.date) == tomorrow.strftime("%Y-%m-%d"))
            .options(joinedload(Conference.applications).joinedload(Application.user))
        )
        conferences = result.scalars().unique().all()

        for conf in conferences:
            if not conf.is_active:
                continue

            confirmed_apps = [app for app in conf.applications if app.status in ["confirmed", "link_sent"]]

            # Участникам (с проверкой на бан)
            for app in confirmed_apps:
                user = app.user
                if user.is_banned:
                    logging.warning(f"Пропуск напоминания для забаненного пользователя: {user.telegram_id}")
                    continue
                try:
                    text = (
                        f"Напоминание! 🎉\n\n"
                        f"Завтра ({conf.date}) состоится конференция:\n"
                        f"<b>{conf.name}</b>\n"
                        f"Город: {conf.city or 'Онлайн'}\n"
                        f"Оргвзнос: {conf.fee} руб.\n\n"
                        f"Не забудьте подготовиться!\n"
                        f"По вопросам — пишите в техподдержку."
                    )
                    await bot.send_message(user.telegram_id, text)
                except Exception:
                    pass

            # Организатору
            try:
                await bot.send_message(
                    conf.organizer.telegram_id,
                    f"Напоминание организатору!\n\n"
                    f"Завтра ваша конференция <b>{conf.name}</b>\n"
                    f"Участников подтверждено: {len(confirmed_apps)}"
                )
            except Exception:
                pass


async def reminder_scheduler():
    while True:
        logging.info("Проверка напоминаний о конференциях...")
        await send_daily_reminders()
        await asyncio.sleep(3600)


# ────────────────────────────────────────────────
# Точка входа
# ────────────────────────────────────────────────

async def main():
    logging.info("Инициализация базы данных...")
    await init_db()
    await enable_wal()
    logging.info("База готова. Запуск бота...")

    asyncio.create_task(reminder_scheduler())

    try:
        logging.info("Начинаем polling... Ожидаем сообщения от Telegram")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await bot.session.close()
        logging.info("Бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())