from aiogram.fsm.state import State, StatesGroup

# Форма регистрации участника (анкета)
class ParticipantRegistration(StatesGroup):
    full_name = State()
    age = State()
    email = State()
    institution = State()
    experience = State()
    committee = State()             # Желаемый комитет

# Форма создания конференции (заявка на модерацию Админу)
class CreateConferenceRequest(StatesGroup):
    name = State()
    description = State()
    city = State()
    date = State()                  # ← Теперь только одна дата проведения
    fee = State()
    qr_code = State()               # Фото QR-кода или 'нет'
    poster = State()                # Фото постера (можно 'нет')

# Причина отклонения заявки (Организатор)
class RejectReason(StatesGroup):
    waiting = State()

# Редактирование существующей конференции (Организатор)
class EditConference(StatesGroup):
    name = State()
    description = State()
    city = State()
    date = State()                  # ← Теперь только одна дата проведения
    fee = State()
    qr_code = State()               # Новое фото QR или 'нет'
    poster = State()                # Новое фото постера или 'нет'

# Массовые рассылки участникам конференции (Организатор)
class Broadcast(StatesGroup):
    message_text = State()          # Убрано conference_id — теперь передаётся в data

# Техподдержка — обращение от пользователя
class SupportAppeal(StatesGroup):
    message = State()

# Техподдержка — ответ от Глав Тех Специалиста
class SupportResponse(StatesGroup):
    request_id = State()
    response_text = State()

# Бан/разбан с причиной
class BanReasonState(StatesGroup):
    target = State()
    action = State()  # "ban" или "unban"
    reason = State()

# Ответ на обращение (только Глав Тех Специалист)
class SupportReply(StatesGroup):
    waiting = State()  # ← Новое состояние: ожидание текста ответа на конкретное обращение

class BanStates(StatesGroup):
    waiting_reason = State()

# Рассылка от Глав Тех Специалиста всем пользователям

class TechBroadcast(StatesGroup):
    waiting_text = State()
    waiting_media = State()
    confirm = State()

