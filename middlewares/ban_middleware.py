from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from database import AsyncSessionLocal, User


class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = None

        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if not user_id:
            return await handler(event, data)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user_id)
            )
            user = result.scalar_one_or_none()

            if user and user.is_banned:
                # 🚫 ПОЛНАЯ БЛОКИРОВКА
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        "🚫 Вы заблокированы и не можете пользоваться ботом.",
                        show_alert=True
                    )
                elif isinstance(event, Message):
                    await event.answer(
                        "🚫 Вы заблокированы и не можете пользоваться ботом."
                    )
                return  # ❌ дальше код НЕ идёт

        return await handler(event, data)