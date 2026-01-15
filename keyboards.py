from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Главное меню — строго по ролям
def get_main_menu_keyboard(role: str):
    builder = ReplyKeyboardBuilder()

    # Общая кнопка для всех
    builder.row(KeyboardButton(text="🔄 Обновить"))

    if role == "Участник":
        builder.row(
            KeyboardButton(text="🔍 Просмотр конференций"),
            KeyboardButton(text="📝 Подать заявку на участие")
        )
        builder.row(KeyboardButton(text="➕ Создать конференцию"))
        builder.row(KeyboardButton(text="📩 Обращение к тех. специалисту"))

    elif role == "Организатор":
        builder.row(
            KeyboardButton(text="📋 Мои конференции"),
            KeyboardButton(text="📩 Заявки участников")
        )
        builder.row(KeyboardButton(text="🗃 Архив заявок"))
        builder.row(KeyboardButton(text="📩 Обращение к тех. специалисту"))

    elif role == "Глав Тех Специалист":
        # Управление пользователями
        builder.row(
            KeyboardButton(text="⚠ Бан/разбан пользователей"),
            KeyboardButton(text="🔑 Назначить роль другим пользователям")
        )

        # Обращения и экспорт
        builder.row(
            KeyboardButton(text="📩 Обращения пользователей"),  # ← НОВАЯ КНОПКА: просмотр обращений
            KeyboardButton(text="📤 Экспорт обращений")         # ← НОВАЯ КНОПКА: экспорт в файл
        )

        # Данные и статистика
        builder.row(
            KeyboardButton(text="📤 Экспорт данных бота"),
            KeyboardButton(text="📊 Статистика")
        )

        # Конференции
        builder.row(
            KeyboardButton(text="🗂 Все конференции"),
            KeyboardButton(text="🗑 Удалить конференцию")
        )
        # ... (существующие строки)
        # Новая кнопка для рассылки
        builder.row(KeyboardButton(text="📢 Рассылка всем пользователям"))
        # Управление ботом
    elif role == "Админ":
        builder.row(
            KeyboardButton(text="📩 Просмотр заявок на конференции"),
        )
        builder.row(
            KeyboardButton(text="🗂 Все конференции"),
            KeyboardButton(text="🗑 Удалить конференцию")
        )
        builder.row(KeyboardButton(text="⚠ Бан/разбан пользователей"))
        builder.row(KeyboardButton(text="📊 Статистика"))
        builder.row(KeyboardButton(text="📩 Обращение к тех. специалисту"))

    elif role == "Главный Админ":
        builder.row(
            KeyboardButton(text="📩 Просмотр заявок на конференции"),
        )
        builder.row(
            KeyboardButton(text="📥 Посмотреть апелляции"),
            KeyboardButton(text="🗂 Все конференции")
        )
        builder.row(KeyboardButton(text="📊 Статистика"))
        builder.row(KeyboardButton(text="⚠ Бан/разбан пользователей"))
        builder.row(KeyboardButton(text="📤 Экспорт данных бота"))
        builder.row(KeyboardButton(text="📩 Обращение к тех. специалисту"))

    else:
        builder.row(KeyboardButton(text="📩 Обращение к тех. специалисту"))

    return builder.as_markup(resize_keyboard=True, one_time_keyboard=False)

# Инлайн-клавиатура со списком конференций
def get_conferences_keyboard(conferences):
    builder = InlineKeyboardBuilder()
    for conf in conferences:
        text = f"{conf.name}"
        details = []
        if conf.city:
            details.append(conf.city)
        if conf.date:  # Одна дата
            details.append(conf.date)
        if details:
            text += f" ({', '.join(details)})"
        builder.button(text=text, callback_data=f"select_conf_{conf.id}")
    builder.adjust(1)
    return builder.as_markup()

# Кнопка отмены
def get_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_form")]
    ])