"""
Max messenger bot for Fabry disease screening questionnaires.

Thin adapter over maxapi that reuses all business logic (STEPS, validators,
scoring, reports, PDF generation) from core.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from dotenv import load_dotenv
from maxapi import Bot, Dispatcher
from maxapi.context import MemoryContext
from maxapi.context.state_machine import State, StatesGroup
from maxapi.filters.command import CommandStart
from maxapi.types.attachments.buttons import CallbackButton
from maxapi.types.input_media import InputMediaBuffer
from maxapi.types.updates.bot_started import BotStarted
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

# Import shared business logic from core.py (no aiogram dependency)
from core import (
    CONSENT_DECLINE_PHONE,
    HOTLINE_PHONE,
    HOTLINE_REMINDERS,
    STEPS,
    _is_doctor,
    _normalize_spaces,
    _utc_iso,
    _wants_callback,
    _wants_sms,
    _should_recommend_doctor_followup,
    build_group_report,
    build_survey_result,
    calculate_fabry_score_details,
    generate_pdf_report,
    get_score_interpretation,
    next_step_index,
    step_by_index,
)

# =========================
# Configuration
# =========================

load_dotenv()

MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "").strip()
if not MAX_BOT_TOKEN:
    raise RuntimeError("MAX_BOT_TOKEN is not set. Put it into .env")

MAX_GROUP_CHAT_ID_RAW = os.getenv("MAX_GROUP_CHAT_ID", "").strip()
MAX_GROUP_CHAT_ID: Optional[int] = int(MAX_GROUP_CHAT_ID_RAW) if MAX_GROUP_CHAT_ID_RAW else None

# =========================
# Logging
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("max_fabry_bot")


# =========================
# FSM States
# =========================

class SurveyFSM(StatesGroup):
    waiting_consent = State()
    waiting_choice = State()
    waiting_text = State()
    collecting_additional = State()


# =========================
# Global state
# =========================

bot: Optional[Bot] = None
admin_forwarding_enabled = True
_pdf_data_cache: dict[int, dict[str, Any]] = {}


# =========================
# Keyboard helpers
# =========================

def consent_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="✅ Я согласен на обработку данных", payload="consent|yes"))
    kb.row(CallbackButton(text="❌ Я не согласен", payload="consent|no"))
    kb.row(CallbackButton(text="📞 Позвонить на горячую линию", payload="hotline"))
    return kb


def choice_keyboard(step_index: int, options: list[str]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for i, opt in enumerate(options):
        kb.row(CallbackButton(text=opt, payload=f"ans|{step_index}|{i}"))
    kb.row(CallbackButton(text="📞 Позвонить на горячую линию", payload="hotline"))
    return kb


def text_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="📞 Позвонить на горячую линию", payload="hotline"))
    return kb


def collect_keyboard(step_index: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="✅ Продолжить", payload=f"collect_done|{step_index}"))
    kb.row(CallbackButton(text="📞 Позвонить на горячую линию", payload="hotline"))
    return kb


# =========================
# Context helpers
# =========================

async def get_data(ctx: MemoryContext) -> dict[str, Any]:
    return await ctx.get_data()


async def save_answer(ctx: MemoryContext, key: str, value: Any) -> None:
    data = await get_data(ctx)
    answers = dict(data.get("answers", {}))
    answers[key] = value
    await ctx.update_data(answers=answers)


async def _track_msg(ctx: MemoryContext, *msg_ids: str) -> None:
    """Track message IDs (strings in Max) for later deletion."""
    data = await ctx.get_data()
    ids = list(data.get("_del_ids", []))
    ids.extend(msg_ids)
    await ctx.update_data(_del_ids=ids)


async def _delete_tracked(ctx: MemoryContext) -> None:
    """Delete all tracked messages."""
    data = await ctx.get_data()
    ids = data.get("_del_ids", [])
    for mid in ids:
        try:
            await bot.delete_message(message_id=mid)
        except Exception:
            logger.warning("Failed to delete message %s", mid, exc_info=True)
    if ids:
        await ctx.update_data(_del_ids=[])


# =========================
# Flow control
# =========================

async def send_step(chat_id: int, user_id: int, ctx: MemoryContext) -> None:
    data = await ctx.get_data()
    idx = data.get("step_index", 0)

    valid_idx = next_step_index(idx, data)
    if valid_idx is None:
        await finish_survey(chat_id, user_id, ctx)
        return

    if valid_idx != idx:
        idx = valid_idx
        await ctx.update_data(step_index=idx)

    await _delete_tracked(ctx)

    track_ids: list[str] = []

    if idx in HOTLINE_REMINDERS:
        result = await bot.send_message(chat_id=chat_id, text=HOTLINE_REMINDERS[idx])
        if result and result.message:
            track_ids.append(result.message.body.mid)

    step = step_by_index(idx)
    text = step.text(data)

    if step.kind == "choice":
        opts = step.options(data) if step.options else []
        markup = choice_keyboard(idx, opts).as_markup()
        await ctx.set_state(SurveyFSM.waiting_choice)
    elif step.kind == "text":
        markup = text_keyboard().as_markup()
        await ctx.set_state(SurveyFSM.waiting_text)
    else:
        markup = collect_keyboard(idx).as_markup()
        await ctx.set_state(SurveyFSM.collecting_additional)

    result = await bot.send_message(chat_id=chat_id, text=text, attachments=[markup])
    if result and result.message:
        track_ids.append(result.message.body.mid)

    await _track_msg(ctx, *track_ids)


async def finish_survey(chat_id: int, user_id: int, ctx: MemoryContext) -> None:
    global admin_forwarding_enabled

    data = await ctx.get_data()
    username = data.get("_username")

    await _delete_tracked(ctx)

    wants_cb = _wants_callback(data)

    if wants_cb:
        await bot.send_message(
            chat_id=chat_id,
            text="Спасибо за ответы! Ваши данные переданы специалисту. Ожидайте звонка в ближайшее время.",
        )
    else:
        await bot.send_message(chat_id=chat_id, text="Спасибо за ответы! Ваши данные переданы специалисту.")

    answers = data.get("answers", {})
    fabry_score, score_breakdown = calculate_fabry_score_details(answers)
    score_interpretation = get_score_interpretation(fabry_score)

    is_doc = _is_doctor(data)
    doctor_followup_required = is_doc and _should_recommend_doctor_followup(answers)
    if doctor_followup_required:
        data["doctor_followup_reason"] = "family_history_fabry"

    diagnostics_needed = doctor_followup_required or fabry_score >= 3

    if not is_doc:
        if diagnostics_needed and wants_cb:
            info = (
                "На основе ваших ответов выявлено сходство некоторых признаков с Болезнью Фабри. "
                "Болезнь Фабри – это редкое генетическое заболевание.\n\n"
                "Рекомендуем вам распечатать результаты этого диалога и записаться на прием к врачу-неврологу или генетику.\n\n"
                "Для точной диагностики необходимо сдать генетический анализ и провести анализ уровня фермента альфа-галактозидазы.\n\n"
                f"Вы также можете позвонить по телефону горячей линии: {HOTLINE_PHONE}."
            )
        elif diagnostics_needed:
            info = (
                "На основе ваших ответов выявлено сходство некоторых признаков с Болезнью Фабри. "
                "Болезнь Фабри – это редкое генетическое заболевание.\n\n"
                "Рекомендуем вам распечатать результаты этого диалога и записаться на прием к врачу-неврологу или генетику.\n\n"
                "Для точной диагностики необходимо сдать генетический анализ и провести анализ уровня фермента альфа-галактозидазы."
            )
        elif wants_cb:
            info = (
                "По результатам анкеты выраженных признаков болезни Фабри не выявлено.\n\n"
                "Если хотите уточнить результаты, специалист может связаться с вами по телефону горячей линии."
            )
        else:
            info = (
                "По результатам анкеты выраженных признаков болезни Фабри не выявлено.\n\n"
                "При сохранении жалоб обратитесь к врачу для очной консультации."
            )
    else:
        if doctor_followup_required:
            info = (
                "У пациента есть кровные родственники с болезнью Фабри.\n\n"
                "Рекомендуем взять пациента на дальнейшую диагностику независимо от выраженности симптомов. "
                "Для уточнения диагноза необходимо направить пациента на генетический анализ и провести анализ уровня фермента альфа-галактозидазы.\n\n"
                f"Наберите на горячую линию: {HOTLINE_PHONE} и получите диагностический конверт."
            )
        elif diagnostics_needed:
            info = (
                "На основе ваших ответов выявлено сходство некоторых признаков с Болезнью Фабри. "
                "Болезнь Фабри – это редкое генетическое заболевание.\n\n"
                "Для точной диагностики необходимо направить пациента на генетический анализ и провести анализ уровня фермента альфа-галактозидазы.\n\n"
                f"Наберите на горячую линию: {HOTLINE_PHONE} и получите диагностический конверт."
            )
        else:
            info = (
                "По результатам анкеты выраженных признаков болезни Фабри у пациента не выявлено.\n\n"
                "Если клиническая картина изменится, рассмотрите повторную оценку или дообследование."
            )
    await bot.send_message(chat_id=chat_id, text=info)

    pdf_kb = InlineKeyboardBuilder()
    pdf_kb.row(CallbackButton(text="📄 Получить результаты анкеты в PDF", payload="get_pdf"))
    await bot.send_message(
        chat_id=chat_id,
        text="Вы можете скачать результаты анкетирования в формате PDF:",
        attachments=[pdf_kb.as_markup()],
    )

    data["fabry_score"] = fabry_score
    data["score_interpretation"] = score_interpretation
    data["score_breakdown"] = score_breakdown

    survey_json = build_survey_result(user_id, chat_id, username, data)
    logger.info(
        "Survey result JSON for user_id=%s:\n%s",
        user_id,
        json.dumps(survey_json, ensure_ascii=False, indent=2),
    )

    should_forward = is_doc or fabry_score >= 3
    if MAX_GROUP_CHAT_ID and bot and admin_forwarding_enabled and should_forward:
        try:
            await bot.send_message(
                chat_id=MAX_GROUP_CHAT_ID,
                text=build_group_report("🩺 Новая анкета (Max)", user_id, chat_id, data, username=username),
            )
        except Exception:
            logger.exception("Failed to send data to Max group chat")

    _pdf_data_cache[chat_id] = dict(data)
    await ctx.clear()


async def finish_with_confirmed_diagnosis(chat_id: int, user_id: int, ctx: MemoryContext) -> None:
    global admin_forwarding_enabled

    data = await ctx.get_data()
    username = data.get("_username")

    await _delete_tracked(ctx)

    await bot.send_message(
        chat_id=chat_id,
        text=(
            "Спасибо за ответ. Поскольку у вас уже диагностирована болезнь Фабри, "
            "мы передали информацию специалисту."
        ),
    )
    await bot.send_message(
        chat_id=chat_id,
        text=f"Для дальнейшей консультации и сопровождения свяжитесь с горячей линией:\n{HOTLINE_PHONE}",
    )

    pdf_kb = InlineKeyboardBuilder()
    pdf_kb.row(CallbackButton(text="📄 Получить результаты анкеты в PDF", payload="get_pdf"))
    await bot.send_message(
        chat_id=chat_id,
        text="Вы можете скачать результаты анкетирования в формате PDF:",
        attachments=[pdf_kb.as_markup()],
    )

    data["early_exit_reason"] = "confirmed_fabry_diagnosis"
    answers = data.get("answers", {})
    fabry_score, score_breakdown = calculate_fabry_score_details(answers)
    data["fabry_score"] = fabry_score
    data["score_interpretation"] = get_score_interpretation(fabry_score)
    data["score_breakdown"] = score_breakdown

    survey_json = build_survey_result(user_id, chat_id, username, data)
    logger.info(
        "Survey result JSON (early exit) for user_id=%s:\n%s",
        user_id,
        json.dumps(survey_json, ensure_ascii=False, indent=2),
    )

    if MAX_GROUP_CHAT_ID and bot and admin_forwarding_enabled:
        try:
            await bot.send_message(
                chat_id=MAX_GROUP_CHAT_ID,
                text=build_group_report(
                    "🩺 Анкета завершена досрочно (подтвержденный диагноз Фабри)",
                    user_id, chat_id, data, username=username,
                ),
            )
        except Exception:
            logger.exception("Failed to send early-finish data to Max group chat")

    _pdf_data_cache[chat_id] = dict(data)
    await ctx.clear()


# =========================
# Handlers
# =========================

dp = Dispatcher()


@dp.bot_started()
async def on_bot_started(event: BotStarted, context: MemoryContext) -> None:
    """Handle /start command (bot_started event in Max)."""
    chat_id = event.chat_id
    user_id = event.user.user_id

    await context.clear()
    await context.set_state(SurveyFSM.waiting_consent)
    await context.update_data(_username=event.user.username)

    welcome = (
        "Здравствуйте!\n\n"
        "Этот бот помогает провести первичное анкетирование. "
        "Мы собираем сведения для организации медицинской помощи и связи со специалистом.\n\n"
        "Пожалуйста, подтвердите согласие на обработку персональных данных (ФЗ-152).\n"
        "Вы можете связаться с оператором по горячей линии на любом этапе."
    )
    await bot.send_message(
        chat_id=chat_id,
        text=welcome,
        attachments=[consent_keyboard().as_markup()],
    )


@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated, context: MemoryContext) -> None:
    """Handle /start command sent as text message."""
    chat_id = event.message.recipient.chat_id
    user_id = event.message.sender.user_id if event.message.sender else None

    await context.clear()
    await context.set_state(SurveyFSM.waiting_consent)
    if event.message.sender:
        await context.update_data(_username=event.message.sender.username)

    welcome = (
        "Здравствуйте!\n\n"
        "Этот бот помогает провести первичное анкетирование. "
        "Мы собираем сведения для организации медицинской помощи и связи со специалистом.\n\n"
        "Пожалуйста, подтвердите согласие на обработку персональных данных (ФЗ-152).\n"
        "Вы можете связаться с оператором по горячей линии на любом этапе."
    )
    await bot.send_message(
        chat_id=chat_id,
        text=welcome,
        attachments=[consent_keyboard().as_markup()],
    )


@dp.message_callback(states=[SurveyFSM.waiting_consent])
async def cb_consent(event: MessageCallback, context: MemoryContext) -> None:
    payload = event.callback.payload or ""
    chat_id = event.message.recipient.chat_id if event.message else None
    user_id = event.callback.user.user_id

    if payload == "hotline":
        await bot.send_message(chat_id=chat_id, text=f"Позвоните нам по номеру: {HOTLINE_PHONE}")
        await event.answer()
        return

    if payload == "consent|no":
        await event.answer()
        await context.clear()
        await context.set_state(SurveyFSM.waiting_consent)
        reconsent_kb = InlineKeyboardBuilder()
        reconsent_kb.row(CallbackButton(text="✅ Хорошо, я даю своё согласие", payload="consent|yes"))
        reconsent_kb.row(CallbackButton(text="📞 Позвонить на горячую линию", payload="hotline"))
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "К сожалению, без вашего согласия мы не можем продолжить.\n"
                f"Для получения помощи позвоните на горячую линию: {CONSENT_DECLINE_PHONE}\n\n"
                "Либо, для продолжения использования бота, дайте свое согласие"
            ),
            attachments=[reconsent_kb.as_markup()],
        )
        return

    if payload == "consent|yes":
        await event.answer()
        await context.update_data(
            consent=True,
            consent_timestamp_utc=_utc_iso(),
            answers={},
            additional_payload=[],
            step_index=0,
        )
        await bot.send_message(chat_id=chat_id, text="Спасибо! Начинаем анкетирование.")
        await bot.send_message(
            chat_id=chat_id,
            text=f"📞 На любом этапе анкетирования вы можете позвонить на горячую линию: {HOTLINE_PHONE}",
        )
        await send_step(chat_id, user_id, context)
        return

    await event.answer()


@dp.message_callback(states=[SurveyFSM.waiting_choice])
async def cb_choice_answer(event: MessageCallback, context: MemoryContext) -> None:
    payload = event.callback.payload or ""
    chat_id = event.message.recipient.chat_id if event.message else None
    user_id = event.callback.user.user_id

    if payload == "hotline":
        await bot.send_message(chat_id=chat_id, text=f"Позвоните нам по номеру: {HOTLINE_PHONE}")
        await event.answer()
        return

    if not payload.startswith("ans|"):
        await event.answer(notification="Эта кнопка больше не актуальна.")
        return

    data = await context.get_data()
    parts = payload.split("|")
    if len(parts) != 3:
        await event.answer(notification="Некорректная кнопка.")
        return

    step_idx = int(parts[1])
    opt_idx = int(parts[2])

    current_idx = data.get("step_index")
    if current_idx != step_idx:
        await event.answer(notification="Эта кнопка относится к предыдущему вопросу.")
        return

    step = step_by_index(step_idx)
    opts = step.options(data) if step.options else []
    if not (0 <= opt_idx < len(opts)):
        await event.answer(notification="Некорректный вариант.")
        return

    await event.answer()

    value = opts[opt_idx]
    answers = dict(data.get("answers", {}))
    answers[step.key] = value

    patch: dict[str, Any] = {"answers": answers}
    if step.key in ("role", "sex", "callback_pref", "sms_pref"):
        patch[step.key] = value
    await context.update_data(**patch)

    new_data = await context.get_data()

    if step.key == "fabry_confirmed" and value == "Да":
        await finish_with_confirmed_diagnosis(chat_id, user_id, context)
        return

    nxt = next_step_index(step_idx + 1, new_data)
    if nxt is None:
        await finish_survey(chat_id, user_id, context)
        return

    await context.update_data(step_index=nxt)
    await send_step(chat_id, user_id, context)


@dp.message_created(states=[SurveyFSM.waiting_choice])
async def wrong_input_in_choice(event: MessageCreated, context: MemoryContext) -> None:
    chat_id = event.message.recipient.chat_id
    data = await context.get_data()
    idx = data.get("step_index", 0)
    step = step_by_index(idx)
    opts = step.options(data) if step.options else []
    result = await bot.send_message(
        chat_id=chat_id,
        text="Пожалуйста, выберите вариант из предложенных кнопок ниже. 👇",
        attachments=[choice_keyboard(idx, opts).as_markup()],
    )
    if result and result.message:
        await _track_msg(context, result.message.body.mid)
    if event.message.body:
        await _track_msg(context, event.message.body.mid)


@dp.message_created(states=[SurveyFSM.waiting_text])
async def text_answer(event: MessageCreated, context: MemoryContext) -> None:
    chat_id = event.message.recipient.chat_id
    user_id = event.message.sender.user_id if event.message.sender else None
    data = await context.get_data()
    idx = data.get("step_index", 0)
    step = step_by_index(idx)

    body = event.message.body
    if not body or not body.text:
        hint = "Пожалуйста, отправьте ответ текстом."
        result = await bot.send_message(
            chat_id=chat_id, text=hint, attachments=[text_keyboard().as_markup()]
        )
        if result and result.message:
            await _track_msg(context, result.message.body.mid)
        if body:
            await _track_msg(context, body.mid)
        return

    raw = _normalize_spaces(body.text)

    if step.validator:
        ok, err = step.validator(raw, data)
        if not ok:
            result = await bot.send_message(
                chat_id=chat_id, text=err, attachments=[text_keyboard().as_markup()]
            )
            if result and result.message:
                await _track_msg(context, result.message.body.mid)
            await _track_msg(context, body.mid)
            return

    await _track_msg(context, body.mid)

    answers = dict(data.get("answers", {}))
    answers[step.key] = raw

    patch: dict[str, Any] = {"answers": answers}
    if step.key in ("role", "sex"):
        patch[step.key] = raw
    await context.update_data(**patch)

    new_data = await context.get_data()
    nxt = next_step_index(idx + 1, new_data)
    if nxt is None:
        await finish_survey(chat_id, user_id, context)
        return

    await context.update_data(step_index=nxt)
    await send_step(chat_id, user_id, context)


@dp.message_created(states=[SurveyFSM.collecting_additional])
async def collect_additional(event: MessageCreated, context: MemoryContext) -> None:
    chat_id = event.message.recipient.chat_id
    data = await context.get_data()
    idx = data.get("step_index", 0)
    body = event.message.body

    payload_item: dict[str, Any] = {"ts_utc": _utc_iso()}
    if body:
        payload_item["message_id"] = body.mid

    if body and body.text:
        payload_item["type"] = "text"
        payload_item["text"] = _normalize_spaces(body.text)
    elif body and body.attachments:
        # Categorize by first attachment type
        first_att = body.attachments[0] if body.attachments else None
        att_type = getattr(first_att, "type", "other") if first_att else "other"
        payload_item["type"] = str(att_type)
    else:
        payload_item["type"] = "other"
        payload_item["note"] = "Unsupported content type"

    additional = list(data.get("additional_payload", []))
    additional.append(payload_item)
    await context.update_data(additional_payload=additional)

    result = await bot.send_message(
        chat_id=chat_id,
        text="Добавлено. Можете отправить еще сообщения или нажать «✅ Продолжить».",
        attachments=[collect_keyboard(idx).as_markup()],
    )
    if result and result.message:
        await _track_msg(context, result.message.body.mid)
    if body:
        await _track_msg(context, body.mid)


@dp.message_callback(states=[SurveyFSM.collecting_additional])
async def cb_collect_done(event: MessageCallback, context: MemoryContext) -> None:
    payload = event.callback.payload or ""
    chat_id = event.message.recipient.chat_id if event.message else None
    user_id = event.callback.user.user_id

    if payload == "hotline":
        await bot.send_message(chat_id=chat_id, text=f"Позвоните нам по номеру: {HOTLINE_PHONE}")
        await event.answer()
        return

    if not payload.startswith("collect_done|"):
        await event.answer(notification="Эта кнопка больше не актуальна.")
        return

    data = await context.get_data()
    parts = payload.split("|")
    if len(parts) != 2:
        await event.answer(notification="Некорректная кнопка.")
        return

    step_idx = int(parts[1])
    current_idx = data.get("step_index")
    if current_idx != step_idx:
        await event.answer(notification="Эта кнопка относится к предыдущему шагу.")
        return

    await event.answer()

    additional_payload = data.get("additional_payload", [])
    answers = dict(data.get("answers", {}))
    answers["additional_info"] = f"{len(additional_payload)} item(s)"
    await context.update_data(answers=answers)

    new_data = await context.get_data()
    nxt = next_step_index(step_idx + 1, new_data)
    if nxt is None:
        await finish_survey(chat_id, user_id, context)
        return

    await context.update_data(step_index=nxt)
    await send_step(chat_id, user_id, context)


@dp.message_callback()
async def cb_general(event: MessageCallback, context: MemoryContext) -> None:
    """Handle callbacks outside specific states (PDF, hotline, fallback)."""
    payload = event.callback.payload or ""
    chat_id = event.message.recipient.chat_id if event.message else None

    if payload == "hotline":
        await bot.send_message(chat_id=chat_id, text=f"Позвоните нам по номеру: {HOTLINE_PHONE}")
        await event.answer()
        return

    if payload == "get_pdf":
        data = _pdf_data_cache.get(chat_id)
        if not data:
            await event.answer(notification="Результаты не найдены. Пройдите анкету заново (/start).")
            return
        await event.answer(notification="Генерация PDF...")
        try:
            pdf_bytes = generate_pdf_report(data)
            media = InputMediaBuffer(buffer=pdf_bytes, filename="fabry_screening_results.pdf")
            await bot.send_message(
                chat_id=chat_id,
                text="Результаты анкетирования",
                attachments=[media],
            )
        except Exception:
            logger.exception("Failed to generate PDF for chat %s", chat_id)
            await bot.send_message(chat_id=chat_id, text="Произошла ошибка при генерации PDF. Попробуйте позже.")
        return

    await event.answer(notification="Эта кнопка больше не актуальна.")


@dp.message_created()
async def message_fallback(event: MessageCreated) -> None:
    chat_id = event.message.recipient.chat_id
    await bot.send_message(chat_id=chat_id, text="Чтобы начать анкетирование, отправьте команду /start")


# =========================
# Main
# =========================

async def main() -> None:
    global bot
    bot = Bot(token=MAX_BOT_TOKEN)

    logger.info(
        "Starting Max bot polling | group_chat_id=%s",
        MAX_GROUP_CHAT_ID,
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
