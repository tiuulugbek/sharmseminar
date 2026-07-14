"""Admin uchun ommaviy xabar yuborish mantig'i: e'lon, xona sheriklari, guruh taklifi.

Bu yerda faqat logika — handlerlar bot.py da. Funksiyalar `bot` obyektini oladi va
yuborish natijasini (hisobot dict) qaytaradi.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import seminar_store as sheets

logger = logging.getLogger(__name__)

# Guruhda "ichida" hisoblanadigan a'zolik holatlari
IN_GROUP_STATUSES = {"creator", "administrator", "member"}

# Telegram flood-limitiga tushmaslik uchun yuborishlar orasidagi pauza
SEND_DELAY = 0.06


def _full_name(p: dict) -> str:
    return f"{p['surname']} {p['name']}".strip() or "—"


async def fetch_registrants() -> list[dict]:
    return await asyncio.to_thread(sheets.fetch_registrants)


async def fetch_groups() -> list[list[dict]]:
    return await asyncio.to_thread(sheets.room_groups)


def distinct_recipients(regs: list[dict]) -> list[dict]:
    """Takrorlanmaydigan Telegram ID lar (xabar yuborish mumkin bo'lganlar)."""
    seen: set[str] = set()
    out: list[dict] = []
    for p in regs:
        tg = p.get("tg_id")
        if tg and tg.lstrip("-").isdigit() and tg not in seen:
            seen.add(tg)
            out.append(p)
    return out


# ──────────────────────────── E'lon (broadcast) ────────────────────────────
async def broadcast_copy(bot: Bot, from_chat_id: int, message_id: int, recipients: list[dict]) -> dict:
    """Berilgan xabarni (admin yuborgan) har bir oluvchiga nusxalaydi."""
    sent, failed = 0, 0
    for p in recipients:
        try:
            await bot.copy_message(
                chat_id=int(p["tg_id"]),
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning("E'lon yuborilmadi (%s): %s", p.get("tg_id"), e)
        await asyncio.sleep(SEND_DELAY)
    return {"sent": sent, "failed": failed, "total": len(recipients)}


# ──────────────────────────── Xona sheriklari ────────────────────────────
def roommate_message(group: list[dict]) -> str:
    room = group[0].get("room") or "—"
    lines = [
        f"🛏 <b>Xonangiz biriktirildi — {room}</b>\n",
        "Xona a'zolari (siz va sheriklaringiz):",
    ]
    for i, p in enumerate(group, 1):
        phone = f" — {p['phone']}" if p.get("phone") else ""
        note = ""
        if "oila" in (p.get("toifa", "").lower() + p.get("label", "").lower()):
            note = " 👨‍👩‍👧"
        lines.append(f"{i}. {_full_name(p)}{phone}{note}")
    lines.append("\nSavollaringiz bo'lsa administrator bilan bog'laning.")
    return "\n".join(lines)


def roommate_preview(groups: list[list[dict]]) -> dict:
    """Yuborishdan oldingi hisob: nechta xona to'liq, nechta odamga yetadi, nechtasi ID siz."""
    rooms_ready = 0
    reachable = 0
    unreachable = 0
    for g in groups:
        if len(g) < 2:
            continue  # yakka — hali sherigi yo'q
        rooms_ready += 1
        ids = {p["tg_id"] for p in g if p.get("tg_id") and p["tg_id"].lstrip("-").isdigit()}
        reachable += len(ids)
        unreachable += sum(1 for p in g if not (p.get("tg_id") and p["tg_id"].lstrip("-").isdigit()))
    return {"rooms": rooms_ready, "reachable": reachable, "unreachable": unreachable}


async def notify_roommates(bot: Bot, groups: list[list[dict]]) -> dict:
    """Har bir to'liq xonaning har bir (ID si bor) a'zosiga xona ro'yxatini yuboradi."""
    sent, failed, unreachable = 0, 0, 0
    for g in groups:
        if len(g) < 2:
            continue
        text = roommate_message(g)
        sent_ids: set[str] = set()
        for p in g:
            tg = p.get("tg_id")
            if not (tg and tg.lstrip("-").isdigit()):
                unreachable += 1
                continue
            if tg in sent_ids:
                continue  # bitta chatga (masalan xodim+oila a'zosi) bir marta
            sent_ids.add(tg)
            try:
                await bot.send_message(int(tg), text)
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning("Xona xabari yuborilmadi (%s): %s", tg, e)
            await asyncio.sleep(SEND_DELAY)
    return {"sent": sent, "failed": failed, "unreachable": unreachable}


# ──────────────────────────── Guruhga taklif ────────────────────────────
async def is_in_group(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(config.GROUP_CHAT_ID, user_id)
    except Exception:
        return False  # topilmadi / hech qachon kirmagan → a'zo emas
    status = getattr(member, "status", None)
    status = getattr(status, "value", status)  # enum bo'lsa
    if status in IN_GROUP_STATUSES:
        return True
    if status == "restricted":
        return bool(getattr(member, "is_member", False))
    return False


async def send_group_invite_to(bot: Bot, user_id: int, full_name: str) -> bool:
    """Bitta foydalanuvchiga shaxsiy (bir martalik) guruh havolasini yuboradi."""
    url = ""
    try:
        link = await bot.create_chat_invite_link(
            chat_id=config.GROUP_CHAT_ID,
            name=(full_name or "Ro'yxat")[:32],
            member_limit=1,
        )
        url = link.invite_link
    except Exception:
        logger.exception("Taklif havolasini yaratishda xato")
        url = config.GROUP_INVITE_LINK
    if not url:
        return False
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="➕ Guruhga qo'shilish", url=url)]]
    )
    try:
        await bot.send_message(
            user_id,
            "👥 Siz hali safar guruhiga qo'shilmagansiz. Quyidagi tugma orqali qo'shiling.\n"
            "<i>Havola faqat siz uchun amal qiladi.</i>",
            reply_markup=markup,
        )
        return True
    except Exception as e:
        logger.warning("Taklif yuborilmadi (%s): %s", user_id, e)
        return False


async def invite_non_members(bot: Bot, recipients: list[dict]) -> dict:
    """Guruhga qo'shilmaganlarga taklif yuboradi."""
    invited, already, failed = 0, 0, 0
    for p in recipients:
        uid = int(p["tg_id"])
        if await is_in_group(bot, uid):
            already += 1
            continue
        ok = await send_group_invite_to(bot, uid, _full_name(p))
        if ok:
            invited += 1
        else:
            failed += 1
        await asyncio.sleep(SEND_DELAY)
    return {"invited": invited, "already": already, "failed": failed, "total": len(recipients)}


# ──────────────────────────── Statistika ────────────────────────────
def build_stats(regs: list[dict], groups: list[list[dict]]) -> str:
    total = len(regs)
    reachable = len(distinct_recipients(regs))
    dbl = sum(1 for p in regs if p.get("room") == "2 kishilik")
    trpl = sum(1 for p in regs if p.get("room") == "3 kishilik")
    rooms_ready = sum(1 for g in groups if len(g) >= 2)
    solo = sum(1 for g in groups if len(g) == 1)
    return (
        "📊 <b>Statistika</b>\n\n"
        f"👤 Jami ro'yxatda: <b>{total}</b>\n"
        f"✉️ Xabar yuborish mumkin (ID bor): <b>{reachable}</b>\n"
        f"❔ ID siz (eski yozuvlar): <b>{total - reachable}</b>\n\n"
        f"🛏 DBL (2 kishilik): <b>{dbl}</b> ta yozuv\n"
        f"🛏 TRPL (3 kishilik): <b>{trpl}</b> ta yozuv\n\n"
        f"✅ To'liq biriktirilgan xonalar: <b>{rooms_ready}</b>\n"
        f"⏳ Sherigi hali yo'q (yakka): <b>{solo}</b>"
    )
