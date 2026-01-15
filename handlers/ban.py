from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile
from sqlalchemy import select
import pandas as pd
import os

from database import AsyncSessionLocal, User, Role
from config import TECH_SPECIALIST_ID, CHIEF_ADMIN_IDS
from states import BanReasonState  # должен существовать

router = Router()


# =========================
# 🔐 Проверка прав
# =========================
async def can_ban_unban(user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            return False

        return user.role in [
            Role.ADMIN.value,
            Role.CHIEF_ADMIN.value,
            Role.CHIEF_TECH.value
        ]


# =========================
# 🚫 НАЧАЛО БАНА
# =========================
@router.message(Command("ban"))
async def start_ban(message: types.Message, state: FSMContext):
    if not await can_ban_unban(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return

    try:
        _, target = message.text.split(maxsplit=1)
        target = target.lstrip("@")
    except ValueError:
        await message.answer("Использование: /ban @username или /ban ID")
        return

    await state.update_data(target=target, action="ban")

    if message.from_user.id == TECH_SPECIALIST_ID:
        await do_ban_unban(message, state, reason="Без причины (Глав Тех Специалист)")
    else:
        await state.set_state(BanReasonState.reason)
        await message.answer("Введите причину бана:")


# =========================
# 🔓 НАЧАЛО РАЗБАНА
# =========================
@router.message(Command("unban"))
async def start_unban(message: types.Message, state: FSMContext):
    if not await can_ban_unban(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return

    try:
        _, target = message.text.split(maxsplit=1)
        target = target.lstrip("@")
    except ValueError:
        await message.answer("Использование: /unban @username или /unban ID")
        return

    await state.update_data(target=target, action="unban")

    if message.from_user.id == TECH_SPECIALIST_ID:
        await do_ban_unban(message, state, reason="Без причины (Глав Тех Специалист)")
    else:
        await state.set_state(BanReasonState.reason)
        await message.answer("Введите причину разбана:")


# =========================
# ✏️ ОБРАБОТКА ПРИЧИНЫ
# =========================
@router.message(BanReasonState.reason)
async def process_reason(message: types.Message, state: FSMContext):
    await state.update_data(reason=message.text)
    await do_ban_unban(message, state, reason=message.text)


# =========================
# ⚙️ ВЫПОЛНЕНИЕ БАНА / РАЗБАНА
# =========================
async def do_ban_unban(message: types.Message, state: FSMContext, reason: str):
    data = await state.get_data()
    target = data["target"]
    action = data["action"]

    async with AsyncSessionLocal() as session:
        if target.isdigit():
            result = await session.execute(
                select(User).where(User.telegram_id == int(target))
            )
        else:
            result = await session.execute(
                select(User).where(User.full_name.ilike(f"%{target}%"))
            )

        user = result.scalar_one_or_none()

        if not user:
            await message.answer("Пользователь не найден.")
            await state.clear()
            return

        # ========= БАН =========
        if action == "ban":
            if user.is_banned:
                await message.answer(
                    f"Пользователь {user.full_name or user.telegram_id} уже забанен."
                )
                await state.clear()
                return

            user.is_banned = True
            user.ban_reason = reason

            await session.commit()

            action_text = "заблокирован"
            user_text = (
                "🚫 Вы заблокированы в боте MUN.\n"
                f"Причина: {reason}"
            )

        # ======== РАЗБАН ========
        else:
            if not user.is_banned:
                await message.answer(
                    f"Пользователь {user.full_name or user.telegram_id} не забанен."
                )
                await state.clear()
                return

            old_reason = user.ban_reason

            user.is_banned = False
            user.ban_reason = None

            # 🔥 ВАЖНО: ВСЕГДА ВОЗВРАЩАЕМ УЧАСТНИКА
            user.role = Role.PARTICIPANT.value

            await session.commit()

            action_text = "разблокирован"
            user_text = "✅ Вы разблокированы в боте MUN."

    await message.answer(
        f"Пользователь {user.full_name or user.telegram_id} {action_text}."
    )

    try:
        await message.bot.send_message(
            user.telegram_id,
            user_text
        )
    except:
        pass

    await state.clear()


# =========================
# 📄 СПИСОК ЗАБАНЕННЫХ (CSV)
# =========================
@router.message(Command("banned_list"))
async def banned_list(message: types.Message):
    if not await can_ban_unban(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.is_banned == True)
        )
        banned_users = result.scalars().all()

    if not banned_users:
        await message.answer("Забаненных пользователей нет.")
        return

    data = []
    for user in banned_users:
        data.append({
            "Telegram ID": user.telegram_id,
            "ФИО": user.full_name or "—",
            "Причина бана": user.ban_reason or "Не указана"
        })

    df = pd.DataFrame(data)
    filename = "banned_users.csv"
    df.to_csv(filename, index=False, encoding="utf-8-sig")

    with open(filename, "rb") as f:
        file = BufferedInputFile(f.read(), filename=filename)

    await message.answer_document(
        file,
        caption="📋 Список забаненных пользователей"
    )

    os.remove(filename)