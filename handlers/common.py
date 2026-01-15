from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func
from datetime import datetime, timedelta
import os

from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from database import (
    AsyncSessionLocal,
    Conference,
    Application,
    User,
    Role,
    ConferenceCreationRequest,
    SupportRequest,
    get_or_create_user
)

from keyboards import (
    get_conferences_keyboard,
    get_cancel_keyboard,
    get_main_menu_keyboard
)

from states import ParticipantRegistration, CreateConferenceRequest, SupportAppeal
from config import CHIEF_ADMIN_IDS, TECH_SPECIALIST_ID

from aiogram import BaseMiddleware
import logging

router = Router()

os.makedirs("qr_codes", exist_ok=True)
os.makedirs("posters", exist_ok=True)
os.makedirs("support_screenshots", exist_ok=True)
# =========================
# 🔒 GLOBAL BAN MIDDLEWARE
# =========================


# Валидация даты: минимум завтра, максимум 5 лет
def validate_conference_date(date_str: str) -> str | None:
    today = datetime.now().date()
    try:
        conf_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        return "Неверный формат даты. Используйте строго ГГГГ-ММ-ДД."

    min_date = today + timedelta(days=1)
    max_date = today + timedelta(days=5 * 365 + 1)

    if conf_date < min_date:
        return f"Дата проведения не может быть раньше завтрашнего дня ({min_date.strftime('%d.%m.%Y')})."
    if conf_date > max_date:
        return "Дата проведения не может быть позже, чем через 5 лет."

    return None

# Форматирование даты
def format_conference_date(date_str: str) -> str:
    try:
        conf_date = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return f"Дата проведения: {conf_date.strftime('%d %B %Y')}"
    except:
        return f"Дата: {date_str}"

# Список конференций
@router.message(Command("conferences"))
async def cmd_conferences(message: types.Message):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Conference).where(Conference.is_active == True)
        )
        conferences = result.scalars().all()

        if not conferences:
            await message.answer(
                "😔 Пока нет актуальных конференций.\n"
                "Следите за обновлениями или создайте свою!"
            )
            return

        for conf in conferences:
            text = f"<b>{conf.name}</b>\n"
            text += f"📍 {conf.city or 'Онлайн'}\n"
            text += f"📅 {format_conference_date(conf.date)}\n"
            fee_text = f"💸 Оргвзнос: {conf.fee} руб." if conf.fee > 0 else "🆓 Бесплатно"
            text += f"{fee_text}\n\n"
            if conf.description:
                text += f"<i>{conf.description}</i>\n\n"
            text += "Нажмите кнопку ниже, чтобы подать заявку:"

            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="Подать заявку", callback_data=f"select_conf_{conf.id}"))

            if conf.poster_path and os.path.exists(conf.poster_path):
                photo = FSInputFile(conf.poster_path)
                await message.answer_photo(photo, caption=text, reply_markup=builder.as_markup())
            else:
                await message.answer(text, reply_markup=builder.as_markup())

# Регистрация
@router.message(Command("register"))
async def cmd_register(message: types.Message):
    await cmd_conferences(message)

# Выбор конференции
@router.callback_query(F.data.startswith("select_conf_"))
async def select_conference(callback: types.CallbackQuery, state: FSMContext):
    conf_id = int(callback.data.split("_")[-1])

    async with AsyncSessionLocal() as session:
        conf = await session.get(Conference, conf_id)
        if not conf:
            await callback.answer("Конференция не найдена.", show_alert=True)
            return

        today = datetime.now().date()
        try:
            conf_date = datetime.strptime(conf.date.strip(), "%Y-%m-%d").date()
        except ValueError:
            await callback.answer("Ошибка в дате конференции.", show_alert=True)
            return

        if conf_date < today:
            await callback.answer("Нельзя подать заявку на конференцию, которая уже прошла.", show_alert=True)
            return

    await state.update_data(conference_id=conf_id)
    await state.set_state(ParticipantRegistration.full_name)

    await callback.message.edit_text(
        "✅ Конференция выбрана!\n\n"
        "<b>Заполните анкету участника</b>\n\n"
        "1. ФИО (полностью):",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()

# Анкета участника — без изменений (все функции как в твоём коде)
@router.message(ParticipantRegistration.full_name)
async def process_full_name(message: types.Message, state: FSMContext):
    await state.update_data(full_name=message.text.strip())
    await state.set_state(ParticipantRegistration.age)
    await message.answer("2. Возраст (от 11 до 99 лет):", reply_markup=get_cancel_keyboard())

@router.message(ParticipantRegistration.age)
async def process_age(message: types.Message, state: FSMContext):
    text = message.text.strip()
    try:
        age = int(text)
        if age < 11 or age > 99:
            await message.answer("Возраст должен быть от 11 до 99 лет. Повторите ввод:")
            return
    except ValueError:
        await message.answer("Введите возраст цифрами (от 11 до 99 лет):")
        return

    await state.update_data(age=age)
    await state.set_state(ParticipantRegistration.email)
    await message.answer("3. Email:", reply_markup=get_cancel_keyboard())

@router.message(ParticipantRegistration.email)
async def process_email(message: types.Message, state: FSMContext):
    await state.update_data(email=message.text.strip())
    await state.set_state(ParticipantRegistration.institution)
    await message.answer("4. Учебное заведение:", reply_markup=get_cancel_keyboard())

@router.message(ParticipantRegistration.institution)
async def process_institution(message: types.Message, state: FSMContext):
    await state.update_data(institution=message.text.strip())
    await state.set_state(ParticipantRegistration.experience)
    await message.answer("5. Опыт участия в MUN (кратко, если есть):", reply_markup=get_cancel_keyboard())

@router.message(ParticipantRegistration.experience)
async def process_experience(message: types.Message, state: FSMContext):
    await state.update_data(experience=message.text.strip())
    await state.set_state(ParticipantRegistration.committee)
    await message.answer("6. Желаемый комитет:", reply_markup=get_cancel_keyboard())

@router.message(ParticipantRegistration.committee)
async def process_committee(message: types.Message, state: FSMContext):
    data = await state.get_data()
    data["committee"] = message.text.strip()

    async with AsyncSessionLocal() as session:
        user_result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_result.scalar_one()

        user.full_name = data.get("full_name")
        user.age = data.get("age")
        user.email = data.get("email")
        user.institution = data.get("institution")
        user.experience = data.get("experience")

        application = Application(
            user_id=user.id,
            conference_id=data["conference_id"],
            committee=data["committee"],
            status="pending"
        )
        session.add(application)
        await session.commit()
        await session.refresh(application)

        conf = await session.get(Conference, data["conference_id"])

        notify_text = (
            f"🔔 <b>Новая заявка на участие!</b>\n\n"
            f"Конференция: <b>{conf.name}</b>\n\n"
            f"<b>Анкета участника:</b>\n"
            f"• ФИО: {data.get('full_name')}\n"
            f"• Возраст: {data.get('age')}\n"
            f"• Email: {data.get('email')}\n"
            f"• Учебное заведение: {data.get('institution')}\n"
            f"• Опыт в MUN: {data.get('experience')}\n"
            f"• Комитет: {data['committee']}\n\n"
            f"ID заявки: <code>{application.id}</code>"
        )

        if conf.organizer_id:
            try:
                await message.bot.send_message(conf.organizer.telegram_id, notify_text)
            except:
                pass

    db_user = await get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        "✅ <b>Заявка успешно отправлена!</b>\n\n"
        "Организатор рассмотрит её в ближайшее время.\n"
        "Вы получите уведомление о результате.",
        reply_markup=get_main_menu_keyboard(db_user.role)
    )
    await state.clear()

# Создание конференции — с валидацией
@router.message(F.text == "Создать конференцию")
async def cmd_create_conference(message: types.Message, state: FSMContext):
    async with AsyncSessionLocal() as session:
        user_result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_result.scalar_one_or_none()

        if not user or user.role != "Участник":
            await message.answer("Эта функция доступна только Участникам.")
            return

        conf_count = await session.scalar(
            select(func.count(Conference.id)).where(Conference.organizer_id == user.id)
        )
        if conf_count > 0:
            await message.answer(
                "У вас уже есть активная конференция.\n"
                "Удалите её или дождитесь завершения, чтобы создать новую."
            )
            return

    await state.set_state(CreateConferenceRequest.name)
    await message.answer("Создание конференции. Введите название:", reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.name)
async def process_conf_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(CreateConferenceRequest.description)
    await message.answer("Введите описание конференции:", reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.description)
async def process_conf_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(CreateConferenceRequest.city)
    await message.answer("Город проведения (или 'Онлайн'):", reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.city)
async def process_conf_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await state.set_state(CreateConferenceRequest.date)
    await message.answer("Дата проведения (формат: ГГГГ-ММ-ДД):", reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.date)
async def process_conf_date(message: types.Message, state: FSMContext):
    date_str = message.text.strip()

    error = validate_conference_date(date_str)
    if error:
        await message.answer(f"Ошибка: {error}\nВведите дату заново (ГГГГ-ММ-ДД):")
        return

    await state.update_data(date=date_str)
    await state.set_state(CreateConferenceRequest.fee)
    await message.answer("Орг. взнос в сомах (0 — бесплатно):", reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.fee)
async def process_conf_fee(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not text.replace('.', '', 1).replace('-', '', 1).isdigit():
        await message.answer("Введите корректное число (0 для бесплатной).")
        return
    await state.update_data(fee=float(text))
    await state.set_state(CreateConferenceRequest.qr_code)
    await message.answer(
        "Если конференция платная — отправьте фото QR-кода для оплаты.\n"
        "Если бесплатная — напишите 'нет'.",
        reply_markup=get_cancel_keyboard()
    )

@router.message(CreateConferenceRequest.qr_code, F.photo)
async def process_conf_qr_photo(message: types.Message, state: FSMContext):
    file_info = await message.bot.get_file(message.photo[-1].file_id)
    qr_path = f"qr_codes/qr_{message.from_user.id}_{message.message_id}.jpg"
    await message.bot.download_file(file_info.file_path, qr_path)
    await state.update_data(qr_code_path=qr_path)
    await state.set_state(CreateConferenceRequest.poster)
    await message.answer("Отправьте постер конференции (фото). Можно пропустить, написав 'нет':",
                         reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.qr_code, F.text)
async def process_conf_qr_skip(message: types.Message, state: FSMContext):
    await state.update_data(qr_code_path=None)
    await state.set_state(CreateConferenceRequest.poster)
    await message.answer("Отправьте постер конференции (фото). Можно пропустить, написав 'нет':",
                         reply_markup=get_cancel_keyboard())

@router.message(CreateConferenceRequest.poster, F.photo)
async def process_conf_poster(message: types.Message, state: FSMContext):
    file_info = await message.bot.get_file(message.photo[-1].file_id)
    poster_path = f"posters/poster_{message.from_user.id}_{message.message_id}.jpg"
    await message.bot.download_file(file_info.file_path, poster_path)
    await state.update_data(poster_path=poster_path)
    await finish_conference_creation(message, state)

@router.message(CreateConferenceRequest.poster, F.text)
async def process_conf_poster_skip(message: types.Message, state: FSMContext):
    if message.text.lower().strip() == "нет":
        await state.update_data(poster_path=None)
        await finish_conference_creation(message, state)
    else:
        await message.answer("Отправьте фото постера или напишите 'нет'")

async def finish_conference_creation(message: types.Message, state: FSMContext):
    data = await state.get_data()

    async with AsyncSessionLocal() as session:
        # Получаем или создаём пользователя
        result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name or message.from_user.first_name
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)  # чтобы получить user.id

        user_id = user.id
        req = ConferenceCreationRequest(
            user_id=user_id,
            data=data,
            status="pending"
        )
        session.add(req)
        await session.commit()

        user = await session.get(User, user_id)
        notify_text = (
            f"🔔 <b>Новая заявка на создание конференции!</b>\n\n"
            f"От: {user.full_name or user.telegram_id}\n"
            f"Название: {data['name']}\n"
            f"Город: {data.get('city', 'Онлайн')}\n"
            f"Дата проведения: {data['date']}\n"
            f"Оргвзнос: {data.get('fee', 0)} руб.\n\n"
            f"ID заявки: <code>{req.id}</code>"
        )

        admins = (await session.execute(
            select(User.telegram_id).where(User.role.in_(["Админ", "Главный Админ"]))
        )).scalars().all()

        for admin_id in set(admins + CHIEF_ADMIN_IDS):
            try:
                await message.bot.send_message(admin_id, notify_text)
            except:
                pass

    await message.answer(
        "✅ <b>Заявка на создание конференции отправлена!</b>\n\n"
        f"Название: {data['name']}\n"
        f"Город: {data.get('city') or 'Онлайн'}\n"
        f"Дата проведения: {format_conference_date(data['date'])}\n"
        f"Оргвзнос: {data.get('fee', 0)} руб.\n\n"
        "Ожидайте одобрения Администратора.",
        reply_markup=get_main_menu_keyboard("Участник")
    )
    await state.clear()

# Обращение к тех. специалисту — с сохранением скриншота
@router.message(F.text == "📩 Обращение к тех. специалисту")
async def start_support_appeal(message: types.Message, state: FSMContext):
    await state.set_state(SupportAppeal.message)
    await message.answer(
        "📩 <b>Обращение в техподдержку</b>\n\n"
        "Опишите вашу проблему или вопрос.\n"
        "По желанию можете прикрепить скриншот (фото).",
        reply_markup=get_cancel_keyboard()
    )

@router.message(SupportAppeal.message, F.photo)
async def save_support_appeal_with_photo(message: types.Message, state: FSMContext):
    file_info = await message.bot.get_file(message.photo[-1].file_id)
    screenshot_path = f"support_screenshots/support_{message.from_user.id}_{message.message_id}.jpg"
    await message.bot.download_file(file_info.file_path, screenshot_path)

    text = message.caption or "Без текста (только скриншот)"

    # 🔹 Гарантируем, что пользователь есть
    db_user = await get_or_create_user(
        message.from_user.id,
        message.from_user.full_name
    )

    async with AsyncSessionLocal() as session:
        req = SupportRequest(
            user_id=db_user.id,
            message=text,
            screenshot_path=screenshot_path,
            status="pending"
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)

        notify_text = (
            f"🆘 Новое обращение в техподдержку!\n\n"
            f"От: {message.from_user.full_name or message.from_user.id}\n"
            f"Текст: {text}\n"
            f"ID обращения: <code>{req.id}</code>"
        )

        try:
            await message.bot.send_photo(
                TECH_SPECIALIST_ID,
                message.photo[-1].file_id,
                caption=notify_text
            )
        except Exception as e:
            print(f"Ошибка отправки фото теху: {e}")

    await message.answer(
        "✅ Ваше обращение с скриншотом отправлено в техподдержку.\n"
        "Мы ответим вам в ближайшее время.",
        reply_markup=get_main_menu_keyboard(db_user.role)
    )

    await state.clear()

@router.message(SupportAppeal.message, F.text)
async def save_support_appeal_text_only(message: types.Message, state: FSMContext):
    # Используем готовую функцию
    db_user = await get_or_create_user(
        message.from_user.id,
        message.from_user.full_name or message.from_user.first_name
    )

    async with AsyncSessionLocal() as session:
        req = SupportRequest(
            user_id=db_user.id,
            message=message.text,
            screenshot_path=None,
            status="pending"
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)

        notify_text = (
            f"🆘 Новое обращение в техподдержку!\n\n"
            f"От: {message.from_user.full_name or message.from_user.id}\n"
            f"Текст: {message.text}\n"
            f"ID обращения: <code>{req.id}</code>"
        )
        try:
            await message.bot.send_message(TECH_SPECIALIST_ID, notify_text)
        except Exception as e:
            print(f"Ошибка отправки текста теху: {e}")

    await message.answer(
        "✅ Ваше обращение отправлено в техподдержку.\n"
        "Мы ответим вам в ближайшее время.",
        reply_markup=get_main_menu_keyboard(db_user.role)
    )
    await state.clear()

# Помощь
@router.message(Command("help"))
async def cmd_help(message: types.Message):
    db_user = await get_or_create_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        "ℹ️ <b>Помощь</b>\n\n"
        "Если у вас проблемы с ботом — используйте кнопку \"Обращение к тех. специалисту\"\n"
        "По вопросам MUN — обратитесь к организатору вашей конференции.",
        reply_markup=get_main_menu_keyboard(db_user.role)
    )

# Отмена
@router.callback_query(F.data == "cancel_form")
async def cancel_form(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Форма отменена.", reply_markup=get_main_menu_keyboard("Участник"))
    await callback.answer()

async def is_user_banned(telegram_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        return bool(user and user.is_banned)


async def block_if_banned(event):
    user_id = event.from_user.id

    if await is_user_banned(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer(
                "🚫 Вы заблокированы и не можете пользоваться ботом.",
                show_alert=True
            )
        elif isinstance(event, Message):
            await event.answer(
                "🚫 Вы заблокированы и не можете пользоваться ботом."
            )
        return True


    return False

@router.message(Command("stats"))
async def stats(message: Message):
    async with AsyncSessionLocal() as session:
        users = await session.scalar(select(func.count(User.id)))
        banned = await session.scalar(select(func.count(User.id)).where(User.is_banned))
        confs = await session.scalar(select(func.count(Conference.id)))
        apps = await session.scalar(select(func.count(Application.id)))

    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👤 Пользователи: {users}\n"
        f"🚫 Забанены: {banned}\n"
        f"🏛 Конференции: {confs}\n"
        f"📄 Заявки: {apps}"
    )