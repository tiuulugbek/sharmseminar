"""Uch rolli ikki tomonlama xabar tizimi.

Qoida bitta: **javob har doim xabar kimdan kelgan bo'lsa, o'shanga qaytadi** —
admin bo'ladimi, guruh rahbari bo'ladimi, hammasi shaxsiy chat (DM) orqali.
Ochiq guruh chatiga hech narsa yozilmaydi.

Kim kimga yoza olishi serverda (`/api/bot/message`) hal qilinadi; bu modul
faqat Telegramga yuboradi va har bir nusxaning `telegram_msg_id` sini qaytarib
yozadi, shunda keyinchalik javob to'g'ri manzilga boradi.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           Message, ReplyKeyboardMarkup, WebAppInfo)

import api_client
import config

logger = logging.getLogger(__name__)

# Telegram flood-limitiga tushmaslik uchun yuborishlar orasidagi pauza
SEND_DELAY = 0.06

ROLE_TITLE = {"admin": "Administrator", "leader": "Guruh rahbari", "member": "Ishtirokchi"}

KIND_LABEL = {"photo": "🖼", "video": "🎬", "audio": "🎵", "voice": "🎤",
              "video_note": "⭕️", "animation": "🎞", "sticker": "🙂", "document": "📎"}

SCOPE_TITLE = {
    "all":    "📢 Umumiy xabar",
    "group":  "📢 Guruh xabari",
    "one":    "✉️ Shaxsiy xabar",
    "leader": "✍️ Guruh a'zosidan savol",
    "reply":  "↩️ Javob",
}

# ── Menyu tugmalari (matnlar handlerlarda ham ishlatiladi) ──
BTN_ALL     = "📢 Hammaga xabar"
BTN_GROUP   = "📢 Bitta guruhga"
BTN_ONE     = "👤 Bitta odamga"
BTN_STATS   = "📊 Umumiy statistika"
BTN_SCAN    = "📷 QR skanlash"
BTN_MYGROUP = "📢 Guruhimga xabar"
BTN_ROSTER  = "👥 Guruhim ro'yxati"
BTN_ATT     = "📊 Kim keldi / kim yo'q"
BTN_ASK     = "✍️ Rahbarimga savol"
BTN_PAGE    = "📄 Mening sahifam"
BTN_DOCS    = "📎 Hujjatlarim"
BTN_PENDING = "⏳ Tasdiqlanmaganlar"
BTN_INVITE  = "➕ Guruhga taklif"


def scan_inline_kb() -> InlineKeyboardMarkup:
    """Mini-appni ochadigan inline tugma.

    Reply klaviaturadagi ``web_app`` tugmasi ba'zi mijozlarda ``initData`` siz
    ochiladi va server foydalanuvchini tanimaydi; inline tugma esa har doim
    imzolangan ma'lumot bilan keladi.  Shuning uchun menyu reply klaviaturada
    qoladi, mini-app esa faqat shu tugma orqali ochiladi.
    """
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="📷 Skanerni ochish", web_app=WebAppInfo(url=f"{config.WEBAPP_URL}/scan"))]])


def role_menu(role: str) -> ReplyKeyboardMarkup:
    """Rolga qarab asosiy menyu (spetsifikatsiya 2.2)."""
    if role == "admin":
        rows = [[KeyboardButton(text=BTN_ALL), KeyboardButton(text=BTN_GROUP)],
                [KeyboardButton(text=BTN_ONE), KeyboardButton(text=BTN_STATS)],
                [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_PENDING)],
                [KeyboardButton(text=BTN_INVITE), KeyboardButton(text=BTN_DOCS)]]
    elif role == "leader":
        rows = [[KeyboardButton(text=BTN_MYGROUP), KeyboardButton(text=BTN_ROSTER)],
                [KeyboardButton(text=BTN_SCAN), KeyboardButton(text=BTN_ATT)],
                [KeyboardButton(text=BTN_PENDING), KeyboardButton(text=BTN_PAGE)],
                [KeyboardButton(text=BTN_DOCS)]]
    else:
        rows = [[KeyboardButton(text=BTN_ASK)],
                [KeyboardButton(text=BTN_PAGE), KeyboardButton(text=BTN_DOCS)]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def reply_kb(msg_id: int) -> InlineKeyboardMarkup:
    """Har bir yuborilgan xabar tagidagi "↩️ Javob berish" tugmasi."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Javob berish", callback_data=f"reply:{msg_id}")]])


def header(scope: str, role: str, label: str) -> str:
    """`📢 Guruh xabari` + `ACO-042 · Aziz Karimov (Sazanchik) · Guruh rahbari`."""
    title = SCOPE_TITLE.get(scope, "✉️ Xabar")
    who = ROLE_TITLE.get(role, role or "")
    # Adminda label ham "Administrator" — rolni ikki marta yozmaymiz.
    suffix = f" · <i>{who}</i>" if who and who != label else ""
    return f"{title}\n<b>{label}</b>{suffix}"


# Telegram qo'llab-quvvatlaydigan turlar. `caption` — sarlavhani faylning
# izohiga qo'shib bo'ladimi; video_note va sticker izoh qabul qilmaydi,
# ular uchun sarlavha alohida xabar bo'lib ketadi.
MEDIA_KINDS = (
    ("photo", True), ("video", True), ("document", True), ("audio", True),
    ("voice", True), ("animation", True), ("video_note", False), ("sticker", False),
)


def media_of(source: Message | None):
    """Xabardagi media turi va izoh qo'yish mumkinligini qaytaradi."""
    if source is None:
        return None, False
    for name, captionable in MEDIA_KINDS:
        if getattr(source, name, None):
            return name, captionable
    return None, False


def kind_of(source: Message | None) -> str:
    kind, _ = media_of(source)
    return kind or "text"


def _text_of(source: Message | None) -> str:
    if source is None:
        return ""
    return (source.text or source.caption or "").strip()


async def _send_one(bot: Bot, chat_id: int, body: str, msg_id: int, source: Message | None):
    """Bitta manzilga yuboradi.

    Rasm, video, ovoz, hujjat — hammasi nusxalanadi.  Izoh qabul qiladiganlarga
    sarlavha izohga qo'shiladi; qabul qilmaydiganlarga (video_note, sticker)
    sarlavha alohida xabar bo'lib oldin ketadi.
    """
    markup = reply_kb(msg_id)
    kind, captionable = media_of(source)
    if not kind:
        return await bot.send_message(chat_id, body, reply_markup=markup,
                                      disable_web_page_preview=True)
    if captionable:
        caption = body if len(body) <= 1024 else body[:1021] + "…"
        return await bot.copy_message(chat_id=chat_id, from_chat_id=source.chat.id,
                                      message_id=source.message_id, caption=caption,
                                      reply_markup=markup)
    await bot.send_message(chat_id, body, disable_web_page_preview=True)
    return await bot.copy_message(chat_id=chat_id, from_chat_id=source.chat.id,
                                  message_id=source.message_id, reply_markup=markup)


async def deliver(bot: Bot, actor_tg: int, scope: str, value, source: Message | None,
                  text: str = "") -> dict:
    """Xabarni ro'yxatga oladi, Telegramga yuboradi va natijani qaytarib yozadi.

    Qaytadi: ``{message_id, sent, failed, total, error}``.  ``error`` bo'lsa
    (masalan rahbar begona guruhga yozmoqchi) — hech narsa yuborilmaydi.
    """
    text = text or _text_of(source)
    try:
        data = await asyncio.to_thread(api_client.message, actor_tg, scope, value,
                                       text, kind_of(source))
    except api_client.ApiError as exc:
        logger.warning("Xabarni ro'yxatga olishda xato: %s", exc)
        return {"error": str(exc), "sent": 0, "failed": 0, "total": 0}

    msg_id = data["message_id"]
    body = header(scope, data.get("from_role") or "", data.get("sender_label") or "")
    if text:
        body += "\n\n" + text

    results, sent, failed = [], 0, 0
    for r in data.get("recipients", []):
        try:
            posted = await _send_one(bot, int(r["telegram_id"]), body, msg_id, source)
            results.append({"id": r["id"], "telegram_id": r["telegram_id"],
                            "telegram_msg_id": getattr(posted, "message_id", ""), "ok": True})
            sent += 1
        except Exception as exc:
            logger.warning("Xabar yuborilmadi (%s): %s", r.get("telegram_id"), exc)
            results.append({"id": r["id"], "telegram_id": r["telegram_id"], "ok": False})
            failed += 1
        await asyncio.sleep(SEND_DELAY)

    # Admin nusxasi (settings.copy_member_questions=true bo'lsa)
    for admin_id in data.get("copy_to", []):
        try:
            await _send_one(bot, int(admin_id), "🔁 <i>nusxa</i>\n" + body, msg_id, source)
        except Exception:
            logger.debug("Admin nusxasi yuborilmadi: %s", admin_id)

    try:
        await asyncio.to_thread(api_client.message_sent, msg_id, results)
    except api_client.ApiError:
        logger.exception("Yuborish natijasini saqlashda xato")
    return {"message_id": msg_id, "sent": sent, "failed": failed,
            "total": len(data.get("recipients", [])), "error": None}


async def deliver_reply(bot: Bot, actor_tg: int, parent_msg_id: int,
                        source: Message | None, text: str = "") -> dict:
    """Javobni FAQAT asl yuboruvchiga qaytaradi (shaxsiy chatga)."""
    text = text or _text_of(source)
    try:
        data = await asyncio.to_thread(api_client.reply, actor_tg, parent_msg_id,
                                       text, kind_of(source))
    except api_client.ApiError as exc:
        logger.warning("Javobni ro'yxatga olishda xato: %s", exc)
        return {"error": str(exc), "sent": 0}

    excerpt = (data.get("parent_excerpt") or "").strip()
    body = header("reply", data.get("from_role") or "", data.get("sender_label") or "")
    if excerpt:
        body += f"\n<blockquote>{excerpt}</blockquote>"
    if text:
        body += "\n\n" + text

    msg_id = data["message_id"]
    try:
        posted = await _send_one(bot, int(data["to_telegram_id"]), body, msg_id, source)
    except Exception as exc:
        logger.warning("Javob yetkazilmadi (%s): %s", data.get("to_telegram_id"), exc)
        return {"error": "yetkazilmadi", "sent": 0, "message_id": msg_id}

    try:
        await asyncio.to_thread(api_client.message_sent, msg_id, [
            {"id": "", "telegram_id": data["to_telegram_id"],
             "telegram_msg_id": getattr(posted, "message_id", ""), "ok": True}])
    except api_client.ApiError:
        logger.debug("Javob natijasini saqlab bo'lmadi")
    return {"message_id": msg_id, "sent": 1, "error": None}


def report(rep: dict) -> str:
    if rep.get("error"):
        return f"⚠️ Yuborilmadi: <code>{rep['error']}</code>"
    if not rep.get("total"):
        return ("ℹ️ Hech kimga yuborilmadi — bu yo'nalishda Telegram ID si bor "
                "ishtirokchi topilmadi.")
    return (f"✅ Yuborildi: <b>{rep['sent']}</b>\n"
            f"⚠️ Yetmadi: <b>{rep['failed']}</b>\n"
            f"👥 Jami: <b>{rep['total']}</b>")
