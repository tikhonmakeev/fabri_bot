from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv


# =========================
# Configuration
# =========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Put it into .env")

# Phone number shown when user taps the hotline button (requirement #3)
HOTLINE_PHONE = os.getenv("HOTLINE_PHONE", "+7 (495) 123-45-67").strip()

# Phone number shown when user declines processing consent (requirement #1)
CONSENT_DECLINE_PHONE = os.getenv("CONSENT_DECLINE_PHONE", HOTLINE_PHONE).strip()

# Group chat where completed surveys are forwarded (bot must be a member)
# Example: GROUP_CHAT_ID=-1001234567890
GROUP_CHAT_ID_RAW = os.getenv("GROUP_CHAT_ID", "").strip()
GROUP_CHAT_ID: Optional[int] = int(GROUP_CHAT_ID_RAW) if GROUP_CHAT_ID_RAW else None


# =========================
# Logging
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("medical_intake_bot")


# =========================
# Global bot instance (initialized in main())
# =========================

bot: Optional[Bot] = None
admin_forwarding_enabled = True

class SurveyFSM(StatesGroup):
    waiting_consent = State()
    waiting_choice = State()
    waiting_text = State()
    collecting_additional = State()


# =========================
# Questionnaire model
# =========================

Condition = Callable[[dict[str, Any]], bool]
TextFn = Callable[[dict[str, Any]], str]
OptionsFn = Callable[[dict[str, Any]], list[str]]
Validator = Callable[[str, dict[str, Any]], tuple[bool, str]]  # ok, error_message


def _always(_: dict[str, Any]) -> bool:
    return True


@dataclass(frozen=True)
class Step:
    key: str
    kind: Literal["choice", "text", "collect"]
    text: TextFn
    options: Optional[OptionsFn] = None
    condition: Condition = _always
    validator: Optional[Validator] = None


# =========================
# Helpers (validation, keyboards, flow)
# =========================

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def validate_age(text: str, _: dict[str, Any]) -> tuple[bool, str]:
    t = _normalize_spaces(text)
    # Strip trailing "лет", "год", "года" etc.
    cleaned = re.sub(r"\s*(лет|год[а]?)\s*$", "", t, flags=re.IGNORECASE).strip()
    if not cleaned.isdigit():
        return False, "Пожалуйста, введите возраст числом (например: 35)."
    age = int(cleaned)
    if age < 1 or age > 120:
        return False, "Пожалуйста, проверьте возраст (допустимый диапазон: 1–120)."
    return True, ""


def validate_nonempty(text: str, _: dict[str, Any]) -> tuple[bool, str]:
    if not _normalize_spaces(text):
        return False, "Пожалуйста, введите ответ текстом."
    return True, ""


def validate_full_name(text: str, _: dict[str, Any]) -> tuple[bool, str]:
    t = _normalize_spaces(text)
    if len(t) < 2:
        return False, "Пожалуйста, укажите ваше ФИО."
    # Only letters, spaces, hyphens, dots allowed
    if not re.match(r"^[А-Яа-яЁёA-Za-z\s.\-]+$", t):
        return False, "ФИО может содержать только буквы, пробелы, дефисы и точки."
    return True, ""


def validate_phone(text: str, _: dict[str, Any]) -> tuple[bool, str]:
    t = _normalize_spaces(text)
    # Allow only digits, +, -, (, ), spaces
    if not re.match(r"^[\d\s+\-()\,]+$", t):
        return False, "Номер телефона содержит недопустимые символы. Используйте только цифры, +, -, (, )."
    digits = re.sub(r"\D", "", t)
    if len(digits) < 10:
        return False, "Не вижу номера телефона. Можно в формате +7XXXXXXXXXX или 8XXXXXXXXXX."
    if len(digits) > 15:
        return False, "Слишком длинный номер. Проверьте, пожалуйста."
    return True, ""


def hotline_keyboard_row(builder: InlineKeyboardBuilder) -> None:
    builder.row(
        InlineKeyboardButton(
            text="📞 Позвонить на горячую линию",
            callback_data="hotline",
        )
    )


def consent_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Я согласен на обработку данных", callback_data="consent|yes")
    kb.button(text="❌ Я не согласен", callback_data="consent|no")
    kb.adjust(1)
    hotline_keyboard_row(kb)
    return kb


def choice_keyboard(step_index: int, options: list[str]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for i, opt in enumerate(options):
        # callback_data must be 1–64 bytes (Telegram Bot API limitation)
        kb.button(text=opt, callback_data=f"ans|{step_index}|{i}")
    # One option per row for accessibility and to avoid very wide keyboards.
    kb.adjust(1)
    hotline_keyboard_row(kb)
    return kb


def text_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    hotline_keyboard_row(kb)
    return kb


def phone_reply_keyboard() -> ReplyKeyboardMarkup:
    """Reply keyboard with 'Share contact' button for the phone step."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def collect_keyboard(step_index: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Продолжить", callback_data=f"collect_done|{step_index}")
    kb.adjust(1)
    hotline_keyboard_row(kb)
    return kb


async def get_data(state: FSMContext) -> dict[str, Any]:
    return await state.get_data()


async def set_step_index(state: FSMContext, idx: int) -> None:
    await state.update_data(step_index=idx)


async def _track_msg(state: FSMContext, *msg_ids: int) -> None:
    """Add message IDs to the deletable list."""
    data = await state.get_data()
    ids = list(data.get("_del_ids", []))
    ids.extend(msg_ids)
    await state.update_data(_del_ids=ids)


async def _delete_tracked(chat_id: int, state: FSMContext) -> None:
    """Delete all tracked messages and clear the list."""
    data = await state.get_data()
    ids = data.get("_del_ids", [])
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass
    if ids:
        await state.update_data(_del_ids=[])


async def save_answer(state: FSMContext, key: str, value: Any) -> None:
    data = await get_data(state)
    answers = dict(data.get("answers", {}))
    answers[key] = value
    await state.update_data(answers=answers)


def _sex_dependent_maternal_phrase(data: dict[str, Any]) -> str:
    # Based on the source script note: for men add “по материнской линии”.
    sex = data.get("sex")  # "Мужской"/"Женский"
    if sex == "Мужской":
        return " по материнской линии"
    return ""

def _sex_dependent_paternal_phrase(data: dict[str, Any]) -> str:
    # Requirement 1.6: for women add "по отцовской линии".
    sex = data.get("sex")  # "Мужской"/"Женский"
    if sex == "Женский":
        return " по отцовской линии"
    return ""

def _step_text_role(_: dict[str, Any]) -> str:
    return "Кто заполняет анкету?"


def _step_options_role(_: dict[str, Any]) -> list[str]:
    return ["Пациент", "Врач (представитель пациента)"]


def _step_text_sex(_: dict[str, Any]) -> str:
    return "Укажите, пожалуйста, Ваш пол:"


def _step_options_sex(_: dict[str, Any]) -> list[str]:
    return ["Мужской", "Женский"]


def _step_text_age(_: dict[str, Any]) -> str:
    return (
        "Укажите Ваш возраст:\n"
        "[     ] – лет\n\n"
        "Важно: у мужчин симптомы проявляются раньше и тяжелее, у женщин – вариабельно"
    )


def _step_text_genetic(_: dict[str, Any]) -> str:
    return "Имеется ли у вас генетически подтвержденный диагноз Болезнь Фабри?"


def _opts_yes_no(_: dict[str, Any]) -> list[str]:
    return ["Да", "Нет"]


def _opts_yes_no_dk(_: dict[str, Any]) -> list[str]:
    return ["Да", "Нет", "Не знаю"]


def _step_text_relatives_dx(data: dict[str, Any]) -> str:
    extra = _sex_dependent_paternal_phrase(data)
    return f"Есть ли у вас кровные родственники{extra} с диагностированной Болезнью Фабри?"


def _step_text_relatives_kidney_heart_stroke(data: dict[str, Any]) -> str:
    extra = _sex_dependent_maternal_phrase(data)
    return (
        f"Есть ли у вас кровные родственники{extra} с заболеваниями почек, "
        "с заболеваниями сердца, перенесшие инсульт в молодом возрасте (до 50 лет)?"
    )


def _step_text_pain(_: dict[str, Any]) -> str:
    return (
        "Неврологические и болевые синдромы (Акропарестезии)\n"
        "Важно: это один из самых ранних признаков\n\n"
        "Испытываете ли вы жгучие, покалывающие или «простреливающие» боли в ладонях и стопах?"
    )


def _step_opts_pain(_: dict[str, Any]) -> list[str]:
    return [
        "Никогда",
        "Редко (во время простуды/жары)",
        "Часто (ежедневно/еженедельно)",
    ]


def _step_text_crises(_: dict[str, Any]) -> str:
    return (
        "Усиливаются ли эти боли при физической нагрузке, смене погоды, стрессе "
        "или после горячей ванны (так называемые «кризы Фабри»)?"
    )


def _step_text_sweating(_: dict[str, Any]) -> str:
    return (
        "Бывает ли у вас сниженное потоотделение (гипогидроз)? Например, вы почти не потеете "
        "в спортзале или в жару, перегреваетесь?"
    )


def _step_opts_sweating(_: dict[str, Any]) -> list[str]:
    return ["Да, потею очень мало", "Потею нормально", "Потею чрезмерно"]


def _step_text_gi(_: dict[str, Any]) -> str:
    return (
        "Желудочно-кишечный тракт\n\n"
        "Беспокоят ли вас вздутие живота, диарея или боли в животе сразу после еды, "
        "особенно жирной?"
    )


def _step_opts_gi(_: dict[str, Any]) -> list[str]:
    return ["Нет", "Иногда", "Регулярно, с детства"]


def _step_text_satiety(_: dict[str, Any]) -> str:
    return "Ощущаете ли вы чувство быстрого насыщения (наедаетесь маленькой порцией)?"


def _step_opts_satiety(_: dict[str, Any]) -> list[str]:
    return ["Да, часто", "Иногда", "Нет"]


def _step_text_skin(_: dict[str, Any]) -> str:
    return (
        "Кожа (Ангиокератомы)\n\n"
        "Замечали ли вы у себя небольшие (1-3 мм) темно-красные, почти черные, "
        "безболезненные узелки на коже, особенно в области:"
    )


def _step_opts_skin(_: dict[str, Any]) -> list[str]:
    return [
        "Между пупком и коленями («зона плавок»)",
        "На бедрах, ягодицах, в паху",
        "На губах и слизистой рта",
        "Нет, не замечал(а)",
    ]


def _step_text_tachy(_: dict[str, Any]) -> str:
    return (
        "Сердечно-сосудистая система\n\n"
        "Бывает ли у вас учащенное сердцебиение (тахикардия) или перебои в работе сердца "
        "без видимой причины?"
    )


def _step_text_dyspnea(_: dict[str, Any]) -> str:
    return (
        "Есть ли у вас одышка при привычных нагрузках (подъем по лестнице), которая "
        "не объясняется лишним весом?"
    )


def _step_text_edema(_: dict[str, Any]) -> str:
    return "Почки\n\nЕсть ли у вас отеки (ног, под глазами) по утрам?"


def _step_text_proteinuria(_: dict[str, Any]) -> str:
    return (
        "Знаете ли вы свой уровень белка в моче (протеинурия) или креатинин "
        "(были отклонения в анализах мочи или крови)?"
    )


def _step_opts_proteinuria(_: dict[str, Any]) -> list[str]:
    return ["Да, были отклонения", "Нет, все в норме", "Не проверял(а)"]


def _step_text_hearing(_: dict[str, Any]) -> str:
    return (
        "Слух и вестибулярный аппарат\n\n"
        "Замечали ли вы снижение слуха или шум в ушах (тиннитус)?"
    )


def _step_opts_hearing(_: dict[str, Any]) -> list[str]:
    return ["Да (с молодости)", "Да (с возрастом)", "Нет"]


def _step_text_dizziness(_: dict[str, Any]) -> str:
    return (
        "Сопровождаются ли эти симптомы (или бывают ли отдельно) приступами сильного "
        "головокружения, ощущения неустойчивости?"
    )


def _step_text_eyes(_: dict[str, Any]) -> str:
    return (
        "Глаза (Специфический признак)\n\n"
        "Говорили ли вам офтальмологи о наличии специфических поражений роговицы "
        "(так называемая «вихревидная кератопатия» или помутнение роговицы) "
        "или изменении сосудов глазного дна?"
    )


def _step_opts_eyes(_: dict[str, Any]) -> list[str]:
    return ["Да, находили", "Нет, не находили", "Не помню", "Не проверял глаза"]


def _step_text_city(_: dict[str, Any]) -> str:
    return "Укажите пожалуйста Ваш город?"


def _step_text_spec(_: dict[str, Any]) -> str:
    return "Укажите пожалуйста Вашу специализацию и должность?"


def _step_text_workplace(_: dict[str, Any]) -> str:
    return "Укажите пожалуйста место работы?"


def _is_doctor(data: dict[str, Any]) -> bool:
    return data.get("role") == "Врач (представитель пациента)"


def _step_text_additional(_: dict[str, Any]) -> str:
    return (
        "Имеются ли у вас дополнительные сведения, результаты анализов, которые вы хотите указать?\n\n"
        "Можно отправить несколько сообщений: текст, фото, документы.\n"
        "Когда закончите — нажмите «✅ Продолжить»."
    )


def _step_text_callback_pref(_: dict[str, Any]) -> str:
    return "Хотите ли вы, чтобы специалист перезвонил вам по результатам анкеты?"


def _step_opts_callback_pref(_: dict[str, Any]) -> list[str]:
    return ["Да, я жду обратного звонка", "Нет, звонок не нужен"]


def _wants_callback(data: dict[str, Any]) -> bool:
    return data.get("callback_pref") == "Да, я жду обратного звонка"


def _step_text_full_name(_: dict[str, Any]) -> str:
    return "Укажите пожалуйста ваше ФИО (Фамилия Имя Отчество):"


def _step_text_phone(_: dict[str, Any]) -> str:
    return (
        "Укажите пожалуйста ваш номер телефона.\n"
        "Пример: +7 999 123-45-67"
    )





def _should_ask_pain_triggers(data: dict[str, Any]) -> bool:
    """Show pain triggers question only if user has pain (not "Никогда")."""
    pain = data.get("pain_hands_feet")
    return pain is not None and pain != "Никогда"


HOTLINE_REMINDERS: dict[int, str] = {
    6: (
        "📞 Напоминаем: если у вас возникнут вопросы, вы можете в любой момент "
        f"позвонить на горячую линию: {HOTLINE_PHONE}"
    ),
    19: (
        "📞 Медицинская часть анкеты завершена. Если вы хотите обсудить "
        f"результаты со специалистом — звоните на горячую линию: {HOTLINE_PHONE}"
    ),
}

STEPS: list[Step] = [
    Step(key="role", kind="choice", text=_step_text_role, options=_step_options_role),
    Step(key="sex", kind="choice", text=_step_text_sex, options=_step_options_sex),
    Step(key="age", kind="text", text=_step_text_age, validator=validate_age),
    Step(key="fabry_confirmed", kind="choice", text=_step_text_genetic, options=_opts_yes_no),
    Step(key="relatives_fabry", kind="choice", text=_step_text_relatives_dx, options=_opts_yes_no_dk),
    Step(key="relatives_kidney_heart_stroke", kind="choice", text=_step_text_relatives_kidney_heart_stroke, options=_opts_yes_no_dk),
    Step(key="pain_hands_feet", kind="choice", text=_step_text_pain, options=_step_opts_pain),
    Step(key="pain_triggers", kind="choice", text=_step_text_crises, options=_opts_yes_no, condition=_should_ask_pain_triggers),
    Step(key="sweating", kind="choice", text=_step_text_sweating, options=_step_opts_sweating),
    Step(key="gi_after_meals", kind="choice", text=_step_text_gi, options=_step_opts_gi),
    Step(key="early_satiety", kind="choice", text=_step_text_satiety, options=_step_opts_satiety),
    Step(key="angiokeratomas", kind="choice", text=_step_text_skin, options=_step_opts_skin),
    Step(key="tachycardia", kind="choice", text=_step_text_tachy, options=_opts_yes_no),
    Step(key="dyspnea", kind="choice", text=_step_text_dyspnea, options=_opts_yes_no),
    Step(key="edema", kind="choice", text=_step_text_edema, options=_opts_yes_no),
    Step(key="proteinuria_creatinine", kind="choice", text=_step_text_proteinuria, options=_step_opts_proteinuria),
    Step(key="hearing_tinnitus", kind="choice", text=_step_text_hearing, options=_step_opts_hearing),
    Step(key="dizziness", kind="choice", text=_step_text_dizziness, options=_opts_yes_no),
    Step(key="eye_sign", kind="choice", text=_step_text_eyes, options=_step_opts_eyes),
    Step(key="city", kind="text", text=_step_text_city, validator=validate_nonempty),
    Step(key="specialization_position", kind="text", text=_step_text_spec, condition=_is_doctor, validator=validate_nonempty),
    Step(key="workplace", kind="text", text=_step_text_workplace, condition=_is_doctor, validator=validate_nonempty),
    Step(key="additional_info", kind="collect", text=_step_text_additional),
    Step(key="callback_pref", kind="choice", text=_step_text_callback_pref, options=_step_opts_callback_pref),
    Step(key="full_name", kind="text", text=_step_text_full_name, validator=validate_full_name),
    Step(key="phone", kind="text", text=_step_text_phone, validator=validate_phone),
]


def next_step_index(start_from: int, data: dict[str, Any]) -> Optional[int]:
    for i in range(start_from, len(STEPS)):
        if STEPS[i].condition(data):
            return i
    return None


def step_by_index(idx: int) -> Step:
    return STEPS[idx]


async def send_step(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    idx = data.get("step_index", 0)

    # Ensure idx points to a valid conditional step.
    valid_idx = next_step_index(idx, data)
    if valid_idx is None:
        await finish_survey(message, state)
        return

    if valid_idx != idx:
        idx = valid_idx
        await state.update_data(step_index=idx)

    # Delete previous question/answer messages
    await _delete_tracked(message.chat.id, state)

    track_ids: list[int] = []

    if idx in HOTLINE_REMINDERS:
        reminder = await message.answer(HOTLINE_REMINDERS[idx])
        track_ids.append(reminder.message_id)

    step = step_by_index(idx)
    text = step.text(data)

    if step.kind == "choice":
        opts = step.options(data) if step.options else []
        markup = choice_keyboard(idx, opts).as_markup()
        await state.set_state(SurveyFSM.waiting_choice)
    elif step.kind == "text":
        markup = text_keyboard().as_markup()
        await state.set_state(SurveyFSM.waiting_text)
    else:
        markup = collect_keyboard(idx).as_markup()
        await state.set_state(SurveyFSM.collecting_additional)

    sent = await message.answer(text, reply_markup=markup)
    track_ids.append(sent.message_id)

    # For the phone step, also show a reply keyboard with "Share contact" button
    if step.key == "phone":
        share_msg = await message.answer(
            "Или нажмите кнопку ниже, чтобы поделиться номером:",
            reply_markup=phone_reply_keyboard(),
        )
        track_ids.append(share_msg.message_id)

    await _track_msg(state, *track_ids)


def format_summary(data: dict[str, Any]) -> str:
    answers = data.get("answers", {})
    lines: list[str] = []
    for k, v in answers.items():
        lines.append(f"{k}: {v}")
    additional = data.get("additional_payload", [])
    if additional:
        lines.append("\nadditional_payload:")
        for item in additional:
            lines.append(json.dumps(item, ensure_ascii=False))
    return "\n".join(lines)[:3900]


def calculate_fabry_score(answers: dict[str, Any]) -> int:
    """
    Calculate cumulative Fabry disease risk score based on survey answers.
    Score indicates likelihood of Fabry disease symptoms.
    
    Score interpretation:
    0-5: Low risk
    6-15: Moderate risk  
    16-30: High risk
    31+: Very high risk
    (
    Returns: integer score (0-60+)
    """
    score = 0
    
    # Genetic confirmation (most important)
    if answers.get("fabry_confirmed") == "Да":
        score += 10
    
    # Family history - Fabry diagnosis
    if answers.get("relatives_fabry") == "Да":
        score += 5
    elif answers.get("relatives_fabry") == "Не знаю":
        score += 2
    
    # Family history - kidney/heart/stroke
    if answers.get("relatives_kidney_heart_stroke") == "Да":
        score += 2
    elif answers.get("relatives_kidney_heart_stroke") == "Не знаю":
        score += 1
    
    # Neurological pain (acroparesthesia) - early sign
    pain = answers.get("pain_hands_feet")
    if pain == "Часто (ежедневно/еженедельно)":
        score += 5
    elif pain == "Редко (во время простуды/жары)":
        score += 2
    
    # Pain crisis triggers
    if answers.get("pain_triggers") == "Да":
        score += 3
    
    # Sweating abnormality (hypohidrosis - early sign)
    if answers.get("sweating") == "Да, потею очень мало":
        score += 3
    elif answers.get("sweating") == "Потею чрезмерно":
        score += 1
    
    # GI symptoms
    gi = answers.get("gi_after_meals")
    if gi == "Регулярно, с детства":
        score += 3
    elif gi == "Иногда":
        score += 1
    
    # Early satiety
    if answers.get("early_satiety") == "Да, часто":
        score += 2
    elif answers.get("early_satiety") == "Иногда":
        score += 1
    
    # Angiokeratomas (skin - pathognomonic sign)
    angiokeratomas = answers.get("angiokeratomas")
    if angiokeratomas and angiokeratomas != "Нет, не замечал(а)":
        score += 5
    
    # Cardiovascular
    if answers.get("tachycardia") == "Да":
        score += 2
    if answers.get("dyspnea") == "Да":
        score += 2
    if answers.get("edema") == "Да":
        score += 2
    
    # Kidney involvement
    if answers.get("proteinuria_creatinine") == "Да, были отклонения":
        score += 3
    elif answers.get("proteinuria_creatinine") == "Не проверял(а)":
        score += 0
    
    # Hearing and vestibular
    hearing = answers.get("hearing_tinnitus")
    if hearing == "Да (с молодости)":
        score += 3
    elif hearing == "Да (с возрастом)":
        score += 1
    
    # Dizziness/vertigo
    if answers.get("dizziness") == "Да":
        score += 2
    
    # Eye findings (corneal involvement - pathognomonic)
    eyes = answers.get("eye_sign")
    if eyes == "Да, находили":
        score += 4
    
    return score


def get_score_interpretation(score: int) -> str:
    """Get interpretation of Fabry risk score."""
    if score == 0:
        return "No risk indicators"
    elif score <= 5:
        return "Low risk"
    elif score <= 15:
        return "Moderate risk"
    elif score <= 30:
        return "High risk"
    else:
        return "Very high risk"


async def finish_survey(message: Message, state: FSMContext) -> None:
    global admin_forwarding_enabled

    data = await state.get_data()
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Delete previous question messages
    await _delete_tracked(chat_id, state)

    await message.answer(
        "Спасибо за ответы! Ваши данные переданы специалисту. Ожидайте звонка в ближайшее время."
    )

    role = data.get("role", "Пациент")
    if role == "Пациент":
        info = (
            "На основе ваших ответов выявлено сходство некоторых признаков с Болезнью Фабри. "
            "Болезнь Фабри – это редкое генетическое заболевание.\n\n"
            "Рекомендуем вам распечатать результаты этого диалога и записаться на прием к врачу-неврологу или генетику.\n\n"
            "Для точной диагностики необходимо сдать генетический анализ и провести анализ уровня фермента альфа-галактозидазы.\n\n"
            f"Вы также можете позвонить по телефону горячей линии: {HOTLINE_PHONE}."
        )
    else:
        info = (
            "На основе ваших ответов выявлено сходство некоторых признаков с Болезнью Фабри. "
            "Болезнь Фабри – это редкое генетическое заболевание.\n\n"
            "Для точной диагностики необходимо направить пациента на генетический анализ и провести анализ уровня фермента альфа-галактозидазы.\n\n"
            f"Позвоните по телефону горячей линии: {HOTLINE_PHONE} и мы поможем Вам оформить пациента на анализы."
        )
    await message.answer(info)

    # Calculate Fabry disease risk score
    answers = data.get("answers", {})
    fabry_score = calculate_fabry_score(answers)
    score_interpretation = get_score_interpretation(fabry_score)
    
    # Store score in data
    data["fabry_score"] = fabry_score
    data["score_interpretation"] = score_interpretation
    
    logger.info(
        "Survey completed for user_id=%s chat_id=%s | Fabry Risk Score: %s (%s)\n%s",
        user_id,
        chat_id,
        fabry_score,
        score_interpretation,
        json.dumps(data, ensure_ascii=False, indent=2),
    )

    if GROUP_CHAT_ID and bot and admin_forwarding_enabled:
        try:
            await bot.send_message(
                GROUP_CHAT_ID,
                "🩺 Новая анкета\n"
                f"Fabry Risk Score: {fabry_score} ({score_interpretation})\n"
                f"user_id: {user_id}\nchat_id: {chat_id}\n\n"
                f"{format_summary(data)}",
            )
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                admin_forwarding_enabled = False
                logger.warning(
                    "Group forwarding disabled: chat not found for GROUP_CHAT_ID=%s. "
                    "Проверьте ID группы и убедитесь, что бот добавлен в группу.",
                    GROUP_CHAT_ID,
                )
            else:
                logger.exception("Failed to send data to group chat")
        except Exception:
            logger.exception("Failed to send data to group chat")

    await state.clear()


router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SurveyFSM.waiting_consent)

    welcome = (
        "Здравствуйте!\n\n"
        "Этот бот помогает провести первичное медицинское анкетирование. "
        "Мы собираем сведения для организации медицинской помощи и связи со специалистом.\n\n"
        "Пожалуйста, подтвердите согласие на обработку персональных данных (ФЗ-152).\n"
        "Вы можете связаться с оператором по горячей линии на любом этапе."
    )
    await message.answer(welcome, reply_markup=consent_keyboard().as_markup())


@router.callback_query(F.data == "hotline")
async def cb_hotline(callback: CallbackQuery, state: FSMContext) -> None:
    sent = await callback.message.answer(f"Позвоните нам по номеру: {HOTLINE_PHONE}")
    await _track_msg(state, sent.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith("consent|"))
async def cb_consent(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data == "consent|no":
        await callback.answer()
        await state.clear()
        await callback.message.answer(
            "К сожалению, без вашего согласия мы не можем продолжить.\n"
            f"Для получения помощи позвоните на горячую линию: {CONSENT_DECLINE_PHONE}"
        )
        return

    await callback.answer()
    await state.update_data(
        consent=True,
        consent_timestamp_utc=_utc_iso(),
        answers={},
        additional_payload=[],
        step_index=0,
    )
    thanks = await callback.message.answer("Спасибо! Начинаем анкетирование.")
    await callback.message.answer(
        f"📞 На любом этапе анкетирования вы можете позвонить на горячую линию: {HOTLINE_PHONE}"
    )
    await _track_msg(state, thanks.message_id)
    await send_step(callback.message, state)


@router.callback_query(SurveyFSM.waiting_choice, F.data.startswith("ans|"))
async def cb_choice_answer(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    parts = (callback.data or "").split("|")
    if len(parts) != 3:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    step_idx = int(parts[1])
    opt_idx = int(parts[2])

    current_idx = data.get("step_index")
    if current_idx != step_idx:
        await callback.answer("Эта кнопка относится к предыдущему вопросу.", show_alert=False)
        return

    step = step_by_index(step_idx)
    opts = step.options(data) if step.options else []
    if not (0 <= opt_idx < len(opts)):
        await callback.answer("Некорректный вариант.", show_alert=True)
        return

    await callback.answer()

    value = opts[opt_idx]
    answers = dict(data.get("answers", {}))
    answers[step.key] = value

    patch: dict[str, Any] = {"answers": answers}
    if step.key in ("role", "sex", "callback_pref"):
        patch[step.key] = value
    await state.update_data(**patch)

    new_data = await state.get_data()
    nxt = next_step_index(step_idx + 1, new_data)
    if nxt is None:
        await finish_survey(callback.message, state)
        return

    await state.update_data(step_index=nxt)
    await send_step(callback.message, state)


@router.message(SurveyFSM.waiting_choice)
async def wrong_input_in_choice(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    idx = data.get("step_index", 0)
    step = step_by_index(idx)
    opts = step.options(data) if step.options else []
    sent = await message.answer(
        "Пожалуйста, выберите вариант из предложенных кнопок ниже. 👇",
        reply_markup=choice_keyboard(idx, opts).as_markup(),
    )
    await _track_msg(state, message.message_id, sent.message_id)


@router.message(SurveyFSM.waiting_text)
async def text_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    idx = data.get("step_index", 0)
    step = step_by_index(idx)

    # Handle shared contact for the phone step
    if step.key == "phone" and message.contact:
        phone = message.contact.phone_number
        if not phone.startswith("+"):
            phone = f"+{phone}"
        raw = phone
    elif message.text is not None:
        raw = _normalize_spaces(message.text)
    else:
        sent = await message.answer(
            "Пожалуйста, отправьте ответ текстом.",
            reply_markup=text_keyboard().as_markup(),
        )
        await _track_msg(state, message.message_id, sent.message_id)
        return

    if step.validator:
        ok, err = step.validator(raw, data)
        if not ok:
            sent = await message.answer(err, reply_markup=text_keyboard().as_markup())
            await _track_msg(state, message.message_id, sent.message_id)
            return

    # Track user message so it gets deleted with the next step
    await _track_msg(state, message.message_id)

    answers = dict(data.get("answers", {}))
    answers[step.key] = raw

    patch: dict[str, Any] = {"answers": answers}
    if step.key in ("role", "sex"):
        patch[step.key] = raw
    await state.update_data(**patch)

    # Remove reply keyboard if it was shown (phone step)
    if step.key == "phone":
        rm_msg = await message.answer("✓", reply_markup=ReplyKeyboardRemove())
        await _track_msg(state, rm_msg.message_id)

    new_data = await state.get_data()
    nxt = next_step_index(idx + 1, new_data)
    if nxt is None:
        await finish_survey(message, state)
        return

    await state.update_data(step_index=nxt)
    await send_step(message, state)


@router.message(SurveyFSM.collecting_additional)
async def collect_additional(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    idx = data.get("step_index", 0)

    payload_item: dict[str, Any] = {"ts_utc": _utc_iso(), "message_id": message.message_id}

    if message.text:
        payload_item["type"] = "text"
        payload_item["text"] = _normalize_spaces(message.text)
    elif message.document:
        payload_item["type"] = "document"
        payload_item["file_id"] = message.document.file_id
        payload_item["file_name"] = message.document.file_name
        payload_item["mime_type"] = message.document.mime_type
    elif message.photo:
        payload_item["type"] = "photo"
        payload_item["file_id"] = message.photo[-1].file_id
    elif message.voice:
        payload_item["type"] = "voice"
        payload_item["file_id"] = message.voice.file_id
    elif message.audio:
        payload_item["type"] = "audio"
        payload_item["file_id"] = message.audio.file_id
        payload_item["file_name"] = message.audio.file_name
    else:
        payload_item["type"] = "other"
        payload_item["note"] = "Unsupported content type"

    additional = list(data.get("additional_payload", []))
    additional.append(payload_item)
    await state.update_data(additional_payload=additional)

    sent = await message.answer(
        "Добавлено. Можете отправить еще сообщения или нажать «✅ Продолжить».",
        reply_markup=collect_keyboard(idx).as_markup(),
    )
    await _track_msg(state, message.message_id, sent.message_id)


@router.callback_query(SurveyFSM.collecting_additional, F.data.startswith("collect_done|"))
async def cb_collect_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    parts = (callback.data or "").split("|")
    if len(parts) != 2:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    step_idx = int(parts[1])
    current_idx = data.get("step_index")
    if current_idx != step_idx:
        await callback.answer("Эта кнопка относится к предыдущему шагу.", show_alert=False)
        return

    await callback.answer()

    additional_payload = data.get("additional_payload", [])
    answers = dict(data.get("answers", {}))
    answers["additional_info"] = f"{len(additional_payload)} item(s)"
    await state.update_data(answers=answers)

    new_data = await state.get_data()
    nxt = next_step_index(step_idx + 1, new_data)
    if nxt is None:
        await finish_survey(callback.message, state)
        return

    await state.update_data(step_index=nxt)
    await send_step(callback.message, state)


@router.callback_query()
async def cb_fallback(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка больше не актуальна.", show_alert=False)


async def main() -> None:
    global bot
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Starting bot polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
