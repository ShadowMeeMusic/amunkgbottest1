from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardButton, BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func, delete
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta
import os
import pandas as pd
import logging

from database import AsyncSessionLocal, Conference, Application, User, Role, ConferenceEditRequest
from keyboards import get_main_menu_keyboard, get_cancel_keyboard
from states import RejectReason, EditConference, Broadcast
from config import CHIEF_ADMIN_IDS, TECH_SPECIALIST_ID

router = Router()

PAYMENTS_DIR = "payments"
os.makedirs(PAYMENTS_DIR, exist_ok=True)
os.makedirs("qr_codes", exist_ok=True)
os.makedirs("posters", exist_ok=True)

pagination = {}
last_my_conferences_msg = {}

logger = logging.getLogger(__name__)


# Проверка: Организатор и НЕ забанен + исключение для Главного Тех Специалиста
async def is_active_organizer(user_id: int) -> bool:
    if user_id == TECH_SPECIALIST_ID:
        return True

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return False
        return user.role == Role.ORGANIZER.value and not user.is_banned


# Получение заявок
async def get_applications(user_id: int, mode: str):
    if not await is_active_organizer(user_id):
        return []

    async with AsyncSessionLocal() as session:
        organizer_result = await session.execute(select(User).where(User.telegram_id == user_id))
        organizer = organizer_result.scalar_one_or_none()
        if not organizer:
            return []

        conf_result = await session.execute(select(Conference).where(Conference.organizer_id == organizer.id))
        conf_ids = [c.id for c in conf_result.scalars().all()]
        if not conf_ids:
            return []

        query = select(Application).options(
            joinedload(Application.user),
            joinedload(Application.conference)
        ).where(Application.conference_id.in_(conf_ids))

        if mode == "current":
            query = query.where(Application.status.in_(["pending", "payment_pending", "payment_sent", "confirmed"]))
        else:  # archive
            query = query.where(Application.status.in_(["approved", "rejected", "link_sent"]))

        result = await session.execute(query.order_by(Application.id))
        return result.unique().scalars().all()


# Клавиатура для заявки — УНИКАЛЬНЫЙ префикс nav_org_
def build_keyboard(app_id: int, index: int, total: int, mode: str):
    builder = InlineKeyboardBuilder()

    if mode == "current":
        builder.row(
            InlineKeyboardButton(text="✅ Принять", callback_data=f"approve_{app_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{app_id}")
        )

    nav = []
    if index > 0:
        nav.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"nav_org_{mode}_{index - 1}"))
    if index < total - 1:
        nav.append(InlineKeyboardButton(text="▶ Вперёд", callback_data=f"nav_org_{mode}_{index + 1}"))
    if nav:
        builder.row(*nav)

    export_text = "📊 Экспорт текущих" if mode == "current" else "📊 Экспорт архива"
    builder.row(InlineKeyboardButton(text=export_text, callback_data=f"export_{mode}"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu_org"))
    return builder.as_markup()


# Отображение заявки
async def show_application(target, apps: list, index: int, mode: str):
    if not apps:
        text = "Нет текущих заявок." if mode == "current" else "Архив пуст."
        if isinstance(target, types.Message):
            await target.answer(text, reply_markup=get_main_menu_keyboard("Организатор"))
        else:
            await target.message.edit_text(text, reply_markup=get_main_menu_keyboard("Организатор"))
        return

    app = apps[index]
    conf = app.conference
    participant = app.user

    text = f"<b>Заявка {index + 1} из {len(apps)}</b>\n\n"
    text += f"<b>🎯 Конференция:</b> {conf.name}\n"
    text += f"<b>ID заявки:</b> <code>{app.id}</code>\n\n"
    text += f"<b>👤 Анкета участника:</b>\n"
    text += f"• ФИО: {participant.full_name or 'Не указано'}\n"
    text += f"• Возраст: {participant.age or '—'}\n"
    text += f"• Email: {participant.email or '—'}\n"
    text += f"• Учебное заведение: {participant.institution or '—'}\n"
    text += f"• Опыт в MUN: {participant.experience or 'Нет'}\n"
    text += f"• Комитет: {app.committee or '—'}\n\n"
    text += f"<b>📊 Статус:</b> {app.status}"
    if app.reject_reason:
        text += f"\n<b>❌ Причина отклонения:</b> {app.reject_reason}"

    keyboard = build_keyboard(app.id, index, len(apps), mode)

    if isinstance(target, types.Message):
        await target.answer(text, reply_markup=keyboard)
    else:
        await target.message.edit_text(text, reply_markup=keyboard)


# 📋 Мои конференции
@router.message(F.text == "📋 Мои конференции")
async def my_conferences(message: types.Message):
    user_id = message.from_user.id

    if not await is_active_organizer(user_id):
        await message.answer("🚫 Доступ запрещён: вы заблокированы или не являетесь Организатором.")
        return

    async with AsyncSessionLocal() as session:
        organizer = (await session.execute(select(User).where(User.telegram_id == user_id))).scalar_one_or_none()
        if not organizer:
            await message.answer("Ошибка доступа.")
            return

        conferences = (
            await session.execute(select(Conference).where(Conference.organizer_id == organizer.id))).scalars().all()

        if not conferences:
            await message.answer("У вас пока нет конференций.", reply_markup=get_main_menu_keyboard("Организатор"))
            return

        builder = InlineKeyboardBuilder()
        text = "<b>📋 Ваши конференции:</b>\n\n"
        for conf in conferences:
            text += f"<b>🏆 {conf.name}</b>\n"
            text += f"📍 Город: {conf.city or 'Онлайн'}\n"
            text += f"📅 Дата: {conf.date}\n"
            text += f"💰 Оргвзнос: {conf.fee} сом.\n\n"

            builder.row(InlineKeyboardButton(text="🗑 Удалить конференцию", callback_data=f"delete_conf_{conf.id}"))
            builder.row(InlineKeyboardButton(text="📢 Рассылка участникам", callback_data=f"broadcast_{conf.id}"))
            builder.row(InlineKeyboardButton(text="📊 Экспорт участников", callback_data=f"export_conf_{conf.id}"))

        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu_org"))

        # Удаляем старое сообщение
        if user_id in last_my_conferences_msg:
            try:
                await message.bot.delete_message(message.chat.id, last_my_conferences_msg[user_id])
            except:
                pass

        sent = await message.answer(text, reply_markup=builder.as_markup())
        last_my_conferences_msg[user_id] = sent.message_id


# 🔄 Навигация по заявкам — ТОЛЬКО наши кнопки nav_org_
@router.callback_query(F.data.startswith("nav_org_"))
async def navigate(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    parts = callback.data.split("_")
    mode = parts[2]  # current или archive
    index = int(parts[3])

    user_id = callback.from_user.id
    pagination[user_id] = {"mode": mode, "index": index}

    apps = await get_applications(user_id, mode)
    await show_application(callback, apps, index, mode)
    await callback.answer()


# 📩 Текущие заявки
@router.message(F.text == "📩 Заявки участников")
async def current_applications(message: types.Message):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("🚫 Доступ запрещён: вы заблокированы или не являетесь Организатором.")
        return

    apps = await get_applications(message.from_user.id, "current")
    pagination[message.from_user.id] = {"mode": "current", "index": 0}
    await show_application(message, apps, 0, "current")


# 🗃 Архив заявок
@router.message(F.text == "🗃 Архив заявок")
async def archive_applications(message: types.Message):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("🚫 Доступ запрещён: вы заблокированы или не являетесь Организатором.")
        return

    apps = await get_applications(message.from_user.id, "archive")
    pagination[message.from_user.id] = {"mode": "archive", "index": 0}
    await show_application(message, apps, 0, "archive")


# ✅ Одобрение заявки
@router.callback_query(F.data.startswith("approve_"))
async def approve_application(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    app_id = int(callback.data.split("_")[1])
    async with AsyncSessionLocal() as session:
        app = await session.get(Application, app_id)
        if not app:
            await callback.answer("Заявка не найдена.")
            return

        app.status = "approved"
        await session.commit()

        conf = await session.get(Conference, app.conference_id)
        participant = await session.get(User, app.user_id)

        await callback.bot.send_message(
            participant.telegram_id,
            f"🎉 <b>Ваша заявка на {conf.name} одобрена!</b>\n\n"
            "Нажмите кнопку ниже для подтверждения участия.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить участие", callback_data=f"confirm_part_{app.id}")]
            ])
        )

        await callback.answer("✅ Заявка одобрена!")

        # Обновляем список
        user_id = callback.from_user.id
        state = pagination.get(user_id, {"mode": "current", "index": 0})
        apps = await get_applications(user_id, state["mode"])
        if apps and state["index"] < len(apps):
            await show_application(callback, apps, state["index"], state["mode"])


# ❌ Отклонение заявки
@router.callback_query(F.data.startswith("reject_"))
async def start_reject(callback: types.CallbackQuery, state: FSMContext):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    app_id = int(callback.data.split("_")[1])
    await state.update_data(app_id=app_id)
    await state.set_state(RejectReason.waiting)
    await callback.message.answer("📝 Введите причину отклонения:", reply_markup=get_cancel_keyboard())
    await callback.answer()


@router.message(RejectReason.waiting)
async def save_reject_reason(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("🚫 Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    data = await state.get_data()
    app_id = data["app_id"]

    async with AsyncSessionLocal() as session:
        app = await session.get(Application, app_id)
        if app:
            app.status = "rejected"
            app.reject_reason = message.text.strip()
            await session.commit()

            conf = await session.get(Conference, app.conference_id)
            participant = await session.get(User, app.user_id)

            await message.bot.send_message(
                participant.telegram_id,
                f"❌ К сожалению, ваша заявка на <b>{conf.name}</b> отклонена.\n\n"
                f"<b>Причина:</b> {message.text.strip()}"
            )

    await message.answer("✅ Заявка отклонена, причина сохранена.", reply_markup=get_main_menu_keyboard("Организатор"))
    await state.clear()


# 👤 Подтверждение участия
@router.callback_query(F.data.startswith("confirm_part_"))
async def confirm_participation(callback: types.CallbackQuery):
    app_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        app = await session.get(Application, app_id)
        if not app:
            await callback.answer("Заявка не найдена.")
            return

        conf = await session.get(Conference, app.conference_id)
        participant = await session.get(User, app.user_id)
        organizer = await session.get(User, conf.organizer_id)

        participant_name = participant.full_name or f"ID {participant.telegram_id}"

        if conf.fee > 0:
            app.status = "payment_pending"
            await session.commit()

            text = (
                "💳 <b>Конференция платная!</b>\n\n"
                "🎉 Поздравляем, вы прошли отбор! "
                "Подтвердите своё участие, оплатив оргвзнос по QR-коду ниже и отправив скриншот чека боту."
            )

            if conf.qr_code_path and os.path.exists(conf.qr_code_path):
                photo = FSInputFile(conf.qr_code_path)
                await callback.bot.send_photo(participant.telegram_id, photo, caption=text)
            else:
                await callback.bot.send_message(participant.telegram_id, text + "\n\n<i>(QR-код не загружен)</i>")

            await callback.bot.send_message(participant.telegram_id, "📸 Отправьте скриншот оплаты:")
        else:
            app.status = "confirmed"
            await session.commit()

            await callback.bot.send_message(
                participant.telegram_id,
                "✅ <b>Участие подтверждено!</b>\n\n"
                "Ожидайте ссылку на чат комитета от организатора.",
                reply_markup=get_main_menu_keyboard("Участник")
            )

            organizer_text = (
                f"✅ <b>Участник подтвердил участие</b> (бесплатная конференция)\n\n"
                f"👤 {participant_name}\n"
                f"📋 ID заявки: <code>{app.id}</code>\n\n"
                f"📎 Отправьте ссылку на чат: <code>/verify {app.id} [ссылка]</code>"
            )
            await callback.bot.send_message(organizer.telegram_id, organizer_text)

    await callback.answer("✅ Участие подтверждено!")


# 💳 Приём скриншота оплаты
@router.message(F.photo)
async def receive_payment_screenshot(message: types.Message):
    async with AsyncSessionLocal() as session:
        user_apps = await session.execute(
            select(Application)
            .join(User)
            .where(User.telegram_id == message.from_user.id)
            .where(Application.status == "payment_pending")
        )
        apps = user_apps.scalars().all()

        if not apps:
            return  # Игнорируем, если не ждём оплаты

        app = apps[0]  # Берём первую
        conf = await session.get(Conference, app.conference_id)
        organizer = await session.get(User, conf.organizer_id)
        participant = await session.get(User, app.user_id)

        participant_name = participant.full_name or f"ID {participant.telegram_id}"

        # Сохраняем скриншот
        file_info = await message.bot.get_file(message.photo[-1].file_id)
        file_path = f"{PAYMENTS_DIR}/payment_{app.id}_{message.message_id}.jpg"
        await message.bot.download_file(file_info.file_path, file_path)

        app.payment_screenshot = file_path
        app.status = "payment_sent"
        await session.commit()

        caption = (
            f"💳 <b>Новый скриншот оплаты!</b>\n\n"
            f"👤 Участник: {participant_name}\n"
            f"📋 ID заявки: <code>{app.id}</code>\n"
            f"🎯 Конференция: {conf.name}\n\n"
            f"✅ Проверьте оплату и подтвердите:\n"
            f"<code>/verify {app.id} [ссылка_на_чат]</code>"
        )
        await message.bot.send_photo(organizer.telegram_id, message.photo[-1].file_id, caption=caption)

    await message.answer(
        "✅ Скриншот отправлен организатору!\n"
        "Ожидайте подтверждения оплаты и ссылку на чат."
    )


# 🔗 Команда /verify
@router.message(Command("verify"))
async def verify_payment(message: types.Message):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("🚫 Доступ запрещён: вы заблокированы или не Организатор.")
        return

    try:
        _, app_id_str, *link_parts = message.text.split(maxsplit=2)
        app_id = int(app_id_str)
        link = " ".join(link_parts).strip()
        if not link:
            raise ValueError("Не указана ссылка")
    except:
        await message.answer(
            "📋 <b>Формат:</b> <code>/verify ID_заявки ссылка_на_чат</code>\n\n"
            "Пример: <code>/verify 123 https://t.me/chat123</code>"
        )
        return

    async with AsyncSessionLocal() as session:
        app = await session.get(Application, app_id)
        if not app:
            await message.answer("❌ Заявка не найдена.")
            return

        participant = await session.get(User, app.user_id)

        app.status = "link_sent"
        await session.commit()

        await message.bot.send_message(
            participant.telegram_id,
            f"✅ <b>Участие полностью подтверждено!</b>\n\n"
            f"🔗 <b>Ссылка на чат комитета:</b>\n<code>{link}</code>\n\n"
            "Удачи на конференции! 🚀"
        )

    await message.answer(f"✅ Ссылка отправлена участнику заявки <code>{app_id}</code>")


# 📤 Экспорт участников конференции
@router.callback_query(F.data.startswith("export_conf_"))
async def export_conference_participants(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    conf_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id)
        if not conf:
            await callback.answer("Конференция не найдена.")
            return

        result = await session.execute(
            select(Application).options(joinedload(Application.user)).where(Application.conference_id == conf_id)
        )
        apps = result.scalars().all()

        if not apps:
            await callback.answer("Нет участников для экспорта", show_alert=True)
            return

        data = []
        for app in apps:
            participant = app.user
            data.append({
                "ФИО": participant.full_name or "—",
                "Возраст": participant.age or "—",
                "Email": participant.email or "—",
                "Учебное заведение": participant.institution or "—",
                "Опыт MUN": participant.experience or "—",
                "Комитет": app.committee or "—",
                "Статус": app.status,
                "Причина отклонения": app.reject_reason or "—",
                "Скриншот оплаты": app.payment_screenshot or "—"
            })

        df = pd.DataFrame(data)
        filename = f"participants_{conf.name.replace(' ', '_')[:30]}_{conf.id}.xlsx"
        df.to_excel(filename, index=False)

        with open(filename, "rb") as f:
            file = BufferedInputFile(f.read(), filename=filename)

        await callback.message.answer_document(
            file,
            caption=f"📊 <b>Экспорт участников:</b> {conf.name}\nВсего: {len(apps)} заявок"
        )
        await callback.answer("✅ Файл отправлен!")
        os.remove(filename)


# 📊 Экспорт текущих/архива заявок
@router.callback_query(F.data.in_(["export_current", "export_archive"]))
async def export_applications(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    mode = "current" if callback.data == "export_current" else "archive"
    user_id = callback.from_user.id

    apps = await get_applications(user_id, mode)
    if not apps:
        await callback.answer(f"Нет заявок для экспорта ({mode})", show_alert=True)
        return

    data = []
    for app in apps:
        participant = app.user
        data.append({
            "ID": app.id,
            "ФИО": participant.full_name or "—",
            "Возраст": participant.age or "—",
            "Email": participant.email or "—",
            "УЗ": participant.institution or "—",
            "Опыт": participant.experience or "—",
            "Комитет": app.committee or "—",
            "Статус": app.status,
            "Причина": app.reject_reason or "—"
        })

    df = pd.DataFrame(data)
    filename = f"applications_{mode}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    df.to_excel(filename, index=False)

    with open(filename, "rb") as f:
        file = BufferedInputFile(f.read(), filename=filename)

    await callback.message.answer_document(
        file,
        caption=f"📊 Экспорт {mode}: {len(apps)} заявок"
    )
    await callback.answer("✅ Готово!")
    os.remove(filename)


# 🗑 Удаление конференции
@router.callback_query(F.data.startswith("delete_conf_"))
async def confirm_delete(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    conf_id = int(callback.data.split("_")[-1])
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔴 ДА, УДАЛИТЬ", callback_data=f"confirm_delete_{conf_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_menu_org")
    )
    await callback.message.edit_text(
        "⚠️ <b>ВЫ УВЕРЕНЫ?</b>\n\n"
        "Будет удалена конференция + ВСЕ заявки навсегда!\n"
        "Действие <b>необратимо</b>.",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete_"))
async def do_delete(callback: types.CallbackQuery):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    conf_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id)
        if not conf:
            await callback.answer("Конференция не найдена.")
            return

        organizer = await session.get(User, conf.organizer_id)

        # Уведомляем админов
        notify_text = f"🗑 <b>Организатор удалил конференцию:</b>\n{conf.name}\n👤 @{organizer.telegram_id}"
        for admin_id in CHIEF_ADMIN_IDS:
            try:
                await callback.bot.send_message(admin_id, notify_text)
            except:
                pass

        # Удаляем всё связанное
        await session.execute(delete(Application).where(Application.conference_id == conf_id))
        await session.execute(delete(ConferenceEditRequest).where(ConferenceEditRequest.conference_id == conf_id))
        await session.delete(conf)
        await session.commit()

        # Проверяем, остались ли конференции
        remaining_confs = await session.scalar(
            select(func.count(Conference.id)).where(Conference.organizer_id == organizer.id)
        )
        if remaining_confs == 0:
            organizer.role = Role.PARTICIPANT.value
            await session.commit()
            await callback.bot.send_message(
                organizer.telegram_id,
                "📢 <b>У вас больше нет конференций!</b>\n\n"
                "🔄 Роль изменена на <b>Участник</b>.\n"
                "/main_menu — для обновления меню."
            )

    # Удаляем старое сообщение о конференциях
    if user_id in last_my_conferences_msg:
        try:
            await callback.bot.delete_message(callback.message.chat.id, last_my_conferences_msg[user_id])
            del last_my_conferences_msg[user_id]
        except:
            pass

    await callback.message.edit_text(
        f"✅ <b>Конференция удалена:</b> {conf.name}\n"
        f"🗑 Все заявки ({await session.scalar(select(func.count()).where(Application.conference_id == conf_id))}) тоже."
    )
    await callback.answer("🗑 Удалено!")

    # Показываем оставшиеся конференции
    if remaining_confs > 0:
        await my_conferences(callback.message)


# 📢 Рассылка участникам конференции
@router.callback_query(F.data.startswith("broadcast_"))
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if not await is_active_organizer(callback.from_user.id):
        await callback.answer("🚫 Доступ запрещён: вы заблокированы.", show_alert=True)
        return

    conf_id = int(callback.data.split("_")[-1])
    await state.update_data(conference_id=conf_id)
    await state.set_state(Broadcast.message_text)

    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id)
        if not conf:
            await callback.answer("Конференция не найдена.")
            return

        await callback.message.edit_text(
            f"📢 <b>Рассылка по конференции:</b> {conf.name}\n\n"
            "💬 Введите текст сообщения:",
            reply_markup=get_cancel_keyboard()
        )
    await callback.answer()


@router.message(Broadcast.message_text)
async def send_broadcast(message: types.Message, state: FSMContext):
    if not await is_active_organizer(message.from_user.id):
        await message.answer("🚫 Доступ запрещён: вы заблокированы.")
        await state.clear()
        return

    data = await state.get_data()
    conf_id = data["conference_id"]
    text = message.text.strip()

    if not text:
        await message.answer("❌ Текст не может быть пустым!")
        return

    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id)
        if not conf:
            await message.answer("Конференция не найдена.")
            await state.clear()
            return

        result = await session.execute(
            select(Application).options(joinedload(Application.user)).where(
                Application.conference_id == conf_id,
                Application.status.in_(["approved", "payment_pending", "payment_sent", "confirmed", "link_sent"])
            )
        )
        applications = result.scalars().all()

        sent_count = 0
        failed_count = 0
        for app in applications:
            try:
                await message.bot.send_message(
                    app.user.telegram_id,
                    f"📢 <b>Сообщение от организатора {conf.name}</b>\n\n{text}"
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Ошибка рассылки {app.user.telegram_id}: {e}")
                failed_count += 1

    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📨 Отправлено: <b>{sent_count}</b>\n"
        f"❌ Ошибок: <b>{failed_count}</b>",
        reply_markup=get_main_menu_keyboard("Организатор")
    )
    await state.clear()


# 🔙 Главное меню (уникальный callback для организатора)
@router.callback_query(F.data == "back_to_menu_org")
async def back_to_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    # Очищаем старое сообщение конференций
    if user_id in last_my_conferences_msg:
        try:
            await callback.bot.delete_message(callback.message.chat.id, last_my_conferences_msg[user_id])
            del last_my_conferences_msg[user_id]
        except:
            pass

    # Показываем меню
    await callback.message.edit_text(
        "🔙 <b>Главное меню Организатора</b>",
        reply_markup=get_main_menu_keyboard("Организатор")
    )
    await callback.answer()