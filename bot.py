import asyncio
import io
import logging
import os
from datetime import date, datetime

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatPermissions,
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

import broadcast
import api_client
import config
import drive
import messaging
import seminar_store as sheets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Jadvalga yozishlarni ketma-ket bajarish uchun (parallel yozuvlar to'qnashmasin)
sheet_lock = asyncio.Lock()


def tx(value, lang="uz"):
    """Matn maydoni {uz,ru,en} bo'lishi ham, oddiy satr bo'lishi ham mumkin."""
    if isinstance(value, dict):
        return value.get(lang) or value.get("uz") or value.get("ru") or value.get("en") or ""
    return value or ""


async def send_personal_page(user_id: int, participant_id: str):
    """Send the live participant page and a QR pointing to the same URL.

    The link uses the passport-derived token, so neither the URL nor the QR on
    the badge exposes the passport number or the plain ACO id.
    """
    details = {}
    try:
        details = await asyncio.to_thread(api_client.participant, participant_id)
    except Exception:
        logger.exception("Shaxsiy sahifa ma'lumotini olishda xato: %s", participant_id)
    p = details.get("participant") or {}
    url = f"{config.PUBLIC_URL}/p/{p.get('token') or participant_id}"
    lines = ["🎫 <b>Shaxsiy seminar sahifangiz</b>", f'<a href="{url}">{url}</a>',
             "\n<i>Sahifa uz / ru / en tillarida ochiladi. Havola va QR kod "
             "o'zgarmaydi — guruhingiz almashsa ham eski QR ishlayveradi.</i>"]
    await bot.send_message(user_id, "\n".join(lines), disable_web_page_preview=True)
    try:
        import qrcode
        qr = qrcode.make(url)
        buf = io.BytesIO()
        qr.save(buf, format="PNG")
        await bot.send_photo(user_id, BufferedInputFile(buf.getvalue(), filename=f"{participant_id}-qr.png"),
                             caption="QR-kod — shaxsiy seminar sahifangiz")
    except Exception:
        logger.exception("QR yaratish/yuborishda xato: %s", participant_id)


async def _regroup_safe():
    """Jadvalni sheriklar ketma-ket turadigan qilib qayta tartiblaydi (xato bo'lsa yutadi)."""
    try:
        await asyncio.to_thread(sheets.regroup_partners)
    except Exception:
        logger.exception("Avto qayta-tartiblashda xato")


# ──────────────────────────── Holatlar ────────────────────────────
class Reg(StatesGroup):
    role = State()
    name = State()
    surname = State()
    birthdate = State()
    parent_confirm = State()  # 21 yoshgacha — ota/ona hamrohligi tasdiqi
    phone = State()
    passport = State()
    passport_series = State()
    passport_expiry = State()
    room_size = State()
    # Sherik (xodim / shifokor)
    comp_type = State()
    comp_registered = State()
    comp_series = State()
    # Oila a'zosi (start orqali) — avval kimning oila a'zosi ekani (sherigi) belgilanadi
    fam_owner = State()
    # Oila a'zosi (xodim sherik sifatida shu yerda to'ldiradi — inline)
    fam_name = State()
    fam_surname = State()
    fam_birthdate = State()
    fam_passport = State()
    fam_series = State()
    fam_expiry = State()
    confirm = State()


class Upd(StatesGroup):
    """Mavjud ma'lumotni yangilash (tahrirlash) oqimi."""
    find = State()   # pasport seriyasini so'rab, qatorni topish
    menu = State()   # qaysi maydonni tahrirlash
    value = State()  # yangi qiymatni kiritish


class Admin(StatesGroup):
    """Admin panel — ommaviy xabar yuborish oqimi."""
    broadcast = State()  # e'lon matnini/rasmini kutish


class Join(StatesGroup):
    """Ro'yxat yopilgandan keyin: pasport seriyasini tekshirib, guruhga taklif."""
    series = State()


# ──────────────────────────── Yordamchilar ────────────────────────────
def parse_date(text: str):
    text = text.strip().replace("/", ".").replace("-", ".")
    # Asosiy format: kun-oy-yil (dd-mm-yyyy). Eski yil-oy-kun ham qabul qilinadi.
    for fmt in ("%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def fmt_date(d: date) -> str:
    """Sanani dd-mm-yyyy ko'rinishida qaytaradi."""
    return d.strftime("%d-%m-%Y")


def calc_age(bd: date) -> int:
    today = date.today()
    return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))


def kb(rows, contact=False) -> ReplyKeyboardMarkup:
    buttons = [[KeyboardButton(text=t, request_contact=contact and t == rows[0][0]) for t in row] for row in rows]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)


ROLE_KB = kb([["🧑‍💼 Xodim", "🩺 Shifokor/Diller"], ["👨‍👩‍👧 Oila a'zosi"], ["✏️ Ma'lumotni yangilash"]])
ROOM_KB = kb([config.ROOM_SIZES])
COMP_TYPE_KB = kb([["👥 Xodim", "🩺 Shifokor/Diller"], ["👨‍👩‍👧 Oila a'zosi"]])
COMP_REGISTERED_KB = kb([["✅ Kiritilgan", "🆕 Hali kiritilmagan"]])
YESNO_KB = kb([["✅ Ha", "❌ Yo'q"]])
PHONE_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

# Update'da tahrirlanadigan maydonlar: (tugma matni, sheets ustun indeksi, tur)
UPD_FIELDS = [
    ("Ism", 4, "text"),
    ("Familya", 5, "text"),
    ("Tug'ilgan sana", 6, "date"),
    ("Telefon", 8, "phone"),
    ("Pasport seriya", 9, "series"),
    ("Amal muddati", 10, "expiry"),
    ("Xona", 11, "room"),
    ("Pasport rasmi", 13, "photo"),
]


def upd_menu_kb() -> ReplyKeyboardMarkup:
    labels = [f[0] for f in UPD_FIELDS]
    rows = [labels[i : i + 2] for i in range(0, len(labels), 2)]
    rows.append(["✅ Saqlash", "❌ Bekor"])
    buttons = [[KeyboardButton(text=t) for t in r] for r in rows]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def upd_summary(row: list) -> str:
    photo_state = "✅ bor" if row[13] else "❌ yo'q"
    return (
        "✏️ <b>Ma'lumotni tahrirlash</b>\n\n"
        f"<b>Ism:</b> {row[4]}\n"
        f"<b>Familya:</b> {row[5]}\n"
        f"<b>Tug'ilgan sana:</b> {row[6]} ({row[7]} yosh)\n"
        f"<b>Telefon:</b> {row[8]}\n"
        f"<b>Pasport seriya:</b> {row[9]}\n"
        f"<b>Amal muddati:</b> {row[10]}\n"
        f"<b>Xona:</b> {row[11]}\n"
        f"<b>Pasport rasmi:</b> {photo_state}\n\n"
        "O'zgartirmoqchi bo'lgan maydon tugmasini tanlang yoki «✅ Saqlash»."
    )


EGYPT_WARNING = (
    "⚠️ <b>Diqqat!</b> Misr davlati talabiga ko'ra, <b>21 yoshgacha</b> bo'lganlar faqat "
    "ota-onasi yoki ulardan biri bilan birga borishi mumkin. Boshqa shaxslar bilan borishga ruxsat berilmaydi."
)

EXPIRY_HINT = (
    "📆 <b>Pasport amal qilish muddatini</b> kiriting (masalan: <code>15-08-2027</code>).\n"
    f"⚠️ Misr talabi: pasport kamida <b>{fmt_date(config.PASSPORT_MIN_EXPIRY)}</b> gacha amal qilishi shart "
    "(safardan keyin 6 oy)."
)


def check_expiry(text: str):
    """(date, xato_xabari) qaytaradi."""
    d = parse_date(text)
    if not d:
        return None, "❌ Sana noto'g'ri. Namuna: <code>15-08-2027</code>"
    if d < config.PASSPORT_MIN_EXPIRY:
        return None, (
            f"❌ Pasport muddati juda erta tugaydi. Kamida "
            f"<b>{fmt_date(config.PASSPORT_MIN_EXPIRY)}</b> gacha amal qilishi kerak (Misr talabi: 6 oy)."
        )
    return d, None


async def series_exists(series: str) -> bool:
    """Pasport seriyasi jadvalda allaqachon bormi — Google Sheet orqali tekshiradi."""
    s = (series or "").strip().upper()
    if not s:
        return False
    try:
        existing = await asyncio.to_thread(sheets.fetch_series)
    except Exception:
        logger.exception("Seriyalarni o'qishda xato")
        return False
    return s in existing


async def extract_passport(message: Message):
    if message.photo:
        return {"type": "photo", "file_id": message.photo[-1].file_id, "mime": "image/jpeg", "ext": ".jpg"}
    if message.document:
        doc = message.document
        ext = os.path.splitext(doc.file_name or "")[1] or ".bin"
        return {"type": "document", "file_id": doc.file_id, "mime": doc.mime_type or "application/octet-stream", "ext": ext}
    return None


async def upload_passport_to_drive(passport: dict, person_label: str) -> str:
    if not passport:
        return ""
    try:
        tg_file = await bot.get_file(passport["file_id"])
        buf = await bot.download_file(tg_file.file_path)
        data = buf.read()
        safe = person_label.replace(" ", "_").replace("/", "-")
        filename = f"{safe}{passport['ext']}"
        return await asyncio.to_thread(drive.upload_bytes, data, filename, passport["mime"])
    except Exception:
        logger.exception("Drive'ga yuklashda xato")
        return ""


# ──────────────────────────── /start ────────────────────────────
@dp.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    # Allaqachon tanilgan bo'lsa ham guruh kartasini qaytadan ko'rsatamiz:
    # guruhlar keyin taqsimlangani uchun eski foydalanuvchilar uni ko'rmagan.
    me = await whoami(message.from_user.id)
    if me.get("role") in {"admin", "leader", "member"}:
        if me.get("id"):
            await send_group_card(message.from_user.id, me["id"])
        await show_menu(message.from_user.id, me,
                        f"👋 Xush kelibsiz{', ' + me['fio'] if me.get('fio') else ''}!")
        return
    await message.answer(
        "👋 <b>Assalomu alaykum!</b>\n\n"
        "Sizni ro'yxatdan topish uchun <b>pasport seriya va raqamingizni</b> kiriting.\n"
        "Masalan: <code>FA1234567</code>\n\n"
        "<i>Pasportingiz yoningizda bo'lmasa, tug'ilgan sanangizni ham yozishingiz "
        "mumkin (kun-oy-yil, masalan 21-05-1990).</i>",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Join.series)


async def send_group_card(user_id: int, participant_id: str):
    """Guruh nomi, mas'uli va xona ma'lumoti — bitta ixcham karta."""
    try:
        details = await asyncio.to_thread(api_client.participant, participant_id)
    except Exception:
        logger.exception("Guruh kartasini olishda xato: %s", participant_id)
        return
    p = details.get("participant") or {}
    lines = [f"👤 <b>{p.get('fio') or ''}</b>"]
    if p.get("group"):
        group = f"{p['group']}-guruh"
        if p.get("group_name"):
            group += f" · {p['group_name']}"
        lines.append(f"👥 Guruhingiz: <b>{group}</b>")
    else:
        lines.append("👥 Guruhingiz: <i>hali biriktirilmagan</i>")
    if details.get("groupLeader"):
        lines.append(f"⭐️ Guruh mas'uli: <b>{details['groupLeader']}</b>")
    if p.get("room"):
        lines.append(f"🛏 Xona: <b>{p['room']}</b>")
    elif p.get("xona_turi"):
        lines.append(f"🛏 Xona turi: <b>{p['xona_turi']}</b>")
    roommates = details.get("roommates") or []
    if roommates:
        lines.append("🤝 Xona sheriklaringiz: <b>" + ", ".join(roommates) + "</b>")
    lang = (p.get("lang") or "uz")
    roles = ", ".join(tx(r.get("label"), lang) for r in details.get("roles", []) if r.get("label"))
    if roles:
        lines.append(f"📌 Vazifalari: <b>{roles}</b>")
    await bot.send_message(user_id, "\n".join(lines))


@dp.message(Command("bekor"), F.chat.type == "private")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Bekor qilindi. Qaytadan boshlash uchun /start", reply_markup=ReplyKeyboardRemove())


@dp.message(Command("royxat"), F.chat.type == "private")
async def cmd_register(message: Message, state: FSMContext):
    """Open the full registration flow without changing the /start lookup flow."""
    await state.clear()
    await message.answer("📋 <b>Ro‘yxatdan o‘tish</b>\n\nToifangizni tanlang:", reply_markup=ROLE_KB)
    await state.set_state(Reg.role)


@dp.message(Command("yangilash"), F.chat.type == "private")
async def cmd_update(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✏️ Pasport seriya raqamingizni kiriting:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Upd.find)


# ──────────────── Guruhga qo'shilish (ro'yxat yopilgandan keyingi asosiy oqim) ────────────────
async def _do_join(user_id: int, full_name: str, idx: int, username: str = ""):
    """Topilgan qatorga Telegram ID ni saqlab, foydalanuvchini guruhga taklif qiladi."""
    try:
        async with sheet_lock:
            participant = await asyncio.to_thread(sheets.set_telegram_id, idx, user_id, username)
    except api_client.ApiError as exc:
        logger.warning("Telegram ID bog'lanmadi: %s", exc)
        if exc.status == 409:
            await bot.send_message(
                user_id,
                "⚠️ Bu Telegram akkaunt boshqa ishtirokchiga biriktirilgan. "
                "Administrator bilan bog‘laning.")
        else:
            await bot.send_message(user_id, "⚠️ Ma’lumotni saqlashda xato. Keyinroq qayta urinib ko‘ring.")
        return
    except Exception:
        logger.exception("Telegram ID saqlashda xato")
        await bot.send_message(user_id, "⚠️ Ma’lumotni saqlashda xato. Keyinroq qayta urinib ko‘ring.")
        return

    await bot.send_message(user_id, f"✅ <b>Topildi:</b> {full_name}\nRo'yxatdan o'tganingiz tasdiqlandi.")
    pid = (participant or {}).get("id") or str(idx)
    # Guruh qulflangan bo'lsa, tasdiqlangan odamga yozish ruxsati shu yerda ochiladi.
    if await allow_talking(user_id):
        await bot.send_message(user_id, "💬 Endi safar guruhida yozishingiz mumkin.")
    await send_group_card(user_id, pid)
    await send_personal_page(user_id, pid)
    # Hujjatlari tayyor bo'lsa darrov yuboriladi; bo'lmasa jim o'tadi.
    await send_documents(user_id, participant_id=pid, silent=True)

    if not config.GROUP_CHAT_ID:
        await bot.send_message(user_id, "ℹ️ Guruh havolasi hozircha mavjud emas. Administrator bilan bog'laning.")
        return
    if await broadcast.is_in_group(bot, user_id):
        await bot.send_message(user_id, "👥 Siz allaqachon safar guruhidasiz. ✅")
        return
    ok = await broadcast.send_group_invite_to(bot, user_id, full_name)
    if not ok:
        await bot.send_message(user_id, "⚠️ Guruh havolasini yaratishda muammo bo'ldi. Administrator bilan bog'laning.")


async def _join_and_menu(user_id: int, full_name: str, idx: str, username: str = ""):
    """Bog'lanishdan keyin foydalanuvchiga o'z roliga mos menyuni ochadi."""
    await _do_join(user_id, full_name, idx, username)
    await show_menu(user_id)


@dp.message(Join.series, F.text)
async def join_check_series(message: Message, state: FSMContext):
    text = message.text.strip()
    bd = parse_date(text)

    if bd:
        # ── Tug'ilgan sana bo'yicha qidiruv ──
        matches = await asyncio.to_thread(sheets.find_rows_by_birthdate, text)
        if not matches:
            await message.answer(
                f"❌ Bu tug'ilgan sana (<code>{fmt_date(bd)}</code>) bo'yicha ro'yxatda hech kim topilmadi.\n"
                "Tekshirib qayta kiriting yoki pasport seriyangizni yozing."
            )
            return
        if len(matches) == 1:
            idx, row = matches[0]
            await state.clear()
            await _join_and_menu(message.from_user.id, f"{row[5]} {row[4]}".strip(), idx,
                                 f"@{message.from_user.username}" if message.from_user.username else "")
            return
        # Bir nechta odam shu sanada — o'zini tanlasin
        names = {}
        kb_rows = []
        for idx, row in matches:
            nm = f"{row[5]} {row[4]}".strip() or f"#{idx}"
            names[str(idx)] = nm
            kb_rows.append([InlineKeyboardButton(text=nm, callback_data=f"join:{idx}")])
        kb_rows.append([InlineKeyboardButton(text="❌ Bekor", callback_data="join:cancel")])
        await state.update_data(join_names=names)
        await message.answer(
            "👥 Shu tug'ilgan sanada <b>bir nechta</b> odam ro'yxatda bor.\n"
            "Iltimos, ro'yxatdan <b>o'zingizni</b> tanlang:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        )
        return

    # ── Pasport seriyasi bo'yicha qidiruv ──
    idx, row = await asyncio.to_thread(sheets.find_row_by_series, text)
    if not idx:
        await message.answer(
            f"❌ Bunday pasport seriyasi (<code>{text}</code>) ro'yxatda topilmadi.\n"
            "Pasport seriyangizni yoki tug'ilgan sanangizni (kun-oy-yil) kiriting."
        )
        return
    await state.clear()
    await _join_and_menu(message.from_user.id, f"{row[5]} {row[4]}".strip(), idx,
                         f"@{message.from_user.username}" if message.from_user.username else "")


@dp.callback_query(F.data.startswith("join:"))
async def join_pick(call: CallbackQuery, state: FSMContext):
    val = call.data.split(":", 1)[1]
    if val == "cancel":
        await state.clear()
        await call.message.edit_text("❌ Bekor qilindi. Qaytadan boshlash uchun /start")
        return await call.answer()
    # Ishtirokchi identifikatori — "ACO-020" ko'rinishidagi matn, son emas.
    idx = val
    data = await state.get_data()
    full_name = (data.get("join_names") or {}).get(val, "")
    await state.clear()
    await call.message.edit_text(f"✅ Tanlandi: <b>{full_name or idx}</b>")
    await call.answer()
    await _join_and_menu(call.from_user.id, full_name, idx,
                         f"@{call.from_user.username}" if call.from_user.username else "")


@dp.message(Join.series)
async def join_series_invalid(message: Message):
    await message.answer(
        "Iltimos, <b>pasport seriya raqamini</b> (masalan: <code>AA1234567</code>) yoki "
        "<b>tug'ilgan sanangizni</b> (masalan: <code>21-05-1990</code>) <b>matn</b> ko'rinishida kiriting."
    )


@dp.message(Command("joyla"), F.chat.type == "private")
async def cmd_regroup(message: Message):
    """Faqat admin: jadvaldagi sheriklarni ketma-ket joylashtirib qayta tartiblaydi."""
    if message.from_user.id not in config.ADMIN_IDS:
        return
    await message.answer("⏳ Jadval qayta tartiblanmoqda (sheriklar ketma-ket)...")
    try:
        async with sheet_lock:
            moved = await asyncio.to_thread(sheets.regroup_partners)
        await message.answer(f"✅ Tayyor. Joyi o'zgargan qatorlar: <b>{moved}</b>.")
    except Exception:
        logger.exception("Regroup xatosi")
        await message.answer("⚠️ Qayta tartiblashda xato bo'ldi, log'ni tekshiring.")


@dp.message(Command("id"))
async def cmd_chat_id(message: Message):
    """Sozlash uchun: guruhda yuborilsa, guruh ID sini qaytaradi."""
    await message.answer(
        f"<b>Chat turi:</b> {message.chat.type}\n<b>Chat ID:</b> <code>{message.chat.id}</code>"
    )


# ──────────────────────────── Admin panel ────────────────────────────
def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 E'lon yuborish", callback_data="adm:bc")],
        [InlineKeyboardButton(text="🛏 Xona sheriklarini xabardor qilish", callback_data="adm:rooms")],
        [InlineKeyboardButton(text="➕ Guruhga taklif (qo'shilmaganlarga)", callback_data="adm:invite")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="adm:stats")],
    ])


@dp.message(Command("admin"), F.chat.type == "private")
async def cmd_admin(message: Message, state: FSMContext):
    """Admin panel (faqat ADMIN_IDS)."""
    if message.from_user.id not in config.ADMIN_IDS:
        return
    await state.clear()
    await message.answer(
        "🛠 <b>Admin panel</b>\n\nKerakli amalni tanlang:",
        reply_markup=admin_menu_kb(),
    )


async def send_group_invite(message: Message, full_name: str):
    """Ro'yxatdan o'tgan kishiga guruhga qo'shilish uchun shaxsiy (bir martalik) havola yuboradi."""
    if not config.GROUP_CHAT_ID:
        return
    url = ""
    try:
        link = await bot.create_chat_invite_link(
            chat_id=config.GROUP_CHAT_ID,
            name=(full_name or "Ro'yxat")[:32],
            member_limit=1,
        )
        url = link.invite_link
    except Exception:
        logger.exception("Guruh taklif havolasini yaratishda xato")
        url = config.GROUP_INVITE_LINK  # zaxira havola (agar sozlangan bo'lsa)
    if not url:
        return
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="➕ Guruhga qo'shilish", url=url)]]
    )
    await message.answer(
        "👥 Quyidagi tugma orqali safar guruhiga qo'shiling.\n"
        "<i>Havola faqat siz uchun amal qiladi.</i>",
        reply_markup=markup,
    )


# ──────────────────────────── Rol ────────────────────────────
@dp.message(Reg.role, F.text.in_(["🧑‍💼 Xodim", "🩺 Shifokor/Diller"]))
async def set_role(message: Message, state: FSMContext):
    role = "Xodim" if "Xodim" in message.text else "Shifokor/Diller"
    await state.update_data(role=role, partner=None)
    await message.answer(f"Tanlandi: <b>{role}</b>\n\n📝 <b>Ismingizni</b> kiriting:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Reg.name)


@dp.message(Reg.role, F.text.contains("Oila"))
async def role_family(message: Message, state: FSMContext):
    await state.update_data(role="Oila a'zosi", partner=None)
    await message.answer(
        "👨‍👩‍👧 <b>Oila a'zosi</b> sifatida ro'yxatdan o'tyapsiz.\n"
        "⚠️ Oila a'zosi uchun <b>100% to'lov</b> amalga oshiriladi.\n\n"
        "Avval <b>kimning oila a'zosi</b> ekaningizni belgilaymiz. Sizni olib boradigan "
        "<b>xodim/shifokorning pasport seriyasini</b> kiriting "
        "(u avval ro'yxatdan o'tgan bo'lishi shart, masalan: <code>AA1234567</code>):",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Reg.fam_owner)


@dp.message(Reg.fam_owner, F.text)
async def fam_set_owner(message: Message, state: FSMContext):
    series = message.text.strip()
    idx, row = await asyncio.to_thread(sheets.find_row_by_series, series)
    if not idx:
        await message.answer(
            f"❌ Bunday seriya (<code>{series}</code>) ro'yxatda topilmadi.\n"
            "Sizni olib boradigan xodim/shifokor avval ro'yxatdan o'tgan bo'lishi kerak. "
            "Seriyani tekshirib qayta kiriting yoki /bekor."
        )
        return
    owner_name = f"{row[5]} {row[4]}".strip()
    await state.update_data(
        _owner_series=series.upper(), _owner_name=owner_name, _owner_phone=row[8]
    )
    await message.answer(
        f"✅ Topildi: <b>{owner_name}</b> ({series.upper()}).\n"
        "Siz shu insonning oila a'zosi sifatida belgilanasiz.\n\n"
        "Endi o'zingiz haqingizdagi ma'lumotni kiriting.\n📝 <b>Ismingizni</b> kiriting:"
    )
    await state.set_state(Reg.name)


@dp.message(Reg.role, F.text.contains("yangilash"))
async def start_update(message: Message, state: FSMContext):
    await message.answer(
        "✏️ <b>Ma'lumotni yangilash.</b>\n\n"
        "Ro'yxatdan o'tgan <b>pasport seriya raqamingizni</b> kiriting "
        "(masalan: <code>AA1234567</code>):",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Upd.find)


@dp.message(Reg.role)
async def role_invalid(message: Message):
    await message.answer("Iltimos, tugmalardan birini tanlang.", reply_markup=ROLE_KB)


# ──────────────────────────── Asosiy shaxs ────────────────────────────
@dp.message(Reg.name, F.text)
async def set_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("📝 <b>Familyangizni</b> kiriting:")
    await state.set_state(Reg.surname)


@dp.message(Reg.surname, F.text)
async def set_surname(message: Message, state: FSMContext):
    await state.update_data(surname=message.text.strip())
    await message.answer("📅 <b>Tug'ilgan sanangizni</b> kiriting (masalan: <code>21-05-1990</code>):")
    await state.set_state(Reg.birthdate)


@dp.message(Reg.birthdate, F.text)
async def set_birthdate(message: Message, state: FSMContext):
    bd = parse_date(message.text)
    if not bd:
        await message.answer("❌ Sana noto'g'ri. Namuna: <code>21-05-1990</code>")
        return
    age = calc_age(bd)
    await state.update_data(birthdate=fmt_date(bd), age=age)
    data = await state.get_data()
    # Oila a'zosi xodim bilan birga boradi — ota/ona tasdiqi so'ralmaydi
    if data.get("role") != "Oila a'zosi" and age < config.MIN_AGE_ALONE:
        await message.answer(EGYPT_WARNING)
        await message.answer(
            "👨‍👩‍👧 Siz 21 yoshga to'lmagansiz. <b>Ota yoki onangiz</b> shu safarda siz bilan "
            "<b>birga boradimi</b>?",
            reply_markup=YESNO_KB,
        )
        await state.set_state(Reg.parent_confirm)
        return
    await message.answer("📱 <b>Telefon raqamingizni</b> yuboring:", reply_markup=PHONE_KB)
    await state.set_state(Reg.phone)


@dp.message(Reg.parent_confirm, F.text.contains("Ha"))
async def parent_confirm_yes(message: Message, state: FSMContext):
    await state.update_data(parent_accompany=True)
    await message.answer(
        "✅ Tasdiqlandi: ota yoki onangiz siz bilan birga boradi.\n\n"
        "📱 <b>Telefon raqamingizni</b> yuboring:",
        reply_markup=PHONE_KB,
    )
    await state.set_state(Reg.phone)


@dp.message(Reg.parent_confirm, F.text.contains("Yo'q"))
async def parent_confirm_no(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Afsus, Misr davlati talabiga ko'ra <b>21 yoshgacha</b> bo'lganlar faqat "
        "<b>ota yoki onasi bilan birga</b> ro'yxatdan o'ta oladi.\n\n"
        "Ota yoki onangiz bilan birga bo'lsangiz, qaytadan /start bosing.",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(Reg.parent_confirm)
async def parent_confirm_invalid(message: Message):
    await message.answer("Iltimos, «✅ Ha» yoki «❌ Yo'q» ni tanlang.", reply_markup=YESNO_KB)


@dp.message(Reg.phone, F.contact)
async def set_phone_contact(message: Message, state: FSMContext):
    await state.update_data(phone=message.contact.phone_number)
    await ask_passport(message, state)


@dp.message(Reg.phone, F.text)
async def set_phone_text(message: Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    await ask_passport(message, state)


async def ask_passport(message: Message, state: FSMContext):
    await message.answer("📷 <b>Pasportingiz skanerini</b> (rasm yoki fayl) yuboring:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Reg.passport)


@dp.message(Reg.passport, F.photo | F.document)
async def set_passport(message: Message, state: FSMContext):
    await state.update_data(passport=await extract_passport(message))
    await message.answer("📇 <b>Pasport seriya raqamini</b> kiriting (masalan: <code>AA1234567</code>):")
    await state.set_state(Reg.passport_series)


@dp.message(Reg.passport)
async def passport_invalid(message: Message):
    await message.answer("Iltimos, pasport <b>rasmini</b> yoki <b>faylini</b> yuboring.")


@dp.message(Reg.passport_series, F.text)
async def set_passport_series(message: Message, state: FSMContext):
    series = message.text.strip()
    if await series_exists(series):
        await message.answer(
            "⚠️ <b>Siz avval ro'yxatga kiritilgansiz.</b>\n"
            f"Bu pasport seriyasi (<code>{series}</code>) allaqachon mavjud. Qaytadan kiritish shart emas.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.clear()
        return
    await state.update_data(passport_series=series)
    await message.answer(EXPIRY_HINT)
    await state.set_state(Reg.passport_expiry)


@dp.message(Reg.passport_expiry, F.text)
async def set_passport_expiry(message: Message, state: FSMContext):
    d, err = check_expiry(message.text)
    if err:
        await message.answer(err)
        return
    await state.update_data(passport_expiry=fmt_date(d))
    data = await state.get_data()
    if data.get("role") == "Oila a'zosi":
        # Oila a'zosi xodim bilan bog'lanadi — xona avtomatik 2 kishilik, sherik so'ralmaydi
        await state.update_data(room="2 kishilik")
        await show_confirm(message, state)
        return
    await message.answer("🏨 <b>Necha kishilik xona</b> kerak?", reply_markup=ROOM_KB)
    await state.set_state(Reg.room_size)


# ──────────────────────────── Xona ────────────────────────────
@dp.message(Reg.room_size, F.text.in_(config.ROOM_SIZES))
async def set_room(message: Message, state: FSMContext):
    await state.update_data(room=message.text)
    await ask_comp_type(message, state, first=True)


@dp.message(Reg.room_size)
async def room_invalid(message: Message):
    await message.answer("Iltimos, tugmalardan birini tanlang.", reply_markup=ROOM_KB)


# ──────────────────────────── Sherik turi ────────────────────────────
async def ask_comp_type(message: Message, state: FSMContext, first: bool):
    txt = "👥 <b>Xona sherigingiz</b> kim?" if first else "👥 <b>Keyingi sherigingiz</b> kim?"
    await message.answer(txt, reply_markup=COMP_TYPE_KB)
    await state.set_state(Reg.comp_type)


@dp.message(Reg.comp_type, F.text.func(lambda t: t and ("Xodim" in t or "Shifokor" in t or "Oila" in t)))
async def set_comp_type(message: Message, state: FSMContext):
    if "Oila" in message.text:
        ctype, pay100 = "Oila a'zosi", True
    elif "Shifokor" in message.text:
        ctype, pay100 = "Shifokor/Diller", False
    else:
        ctype, pay100 = "Xodim", False
    await state.update_data(_c_type=ctype, _c_pay100=pay100)

    if ctype == "Oila a'zosi":
        # Oila a'zosini xodim shu yerda to'liq to'ldiradi — ikkalasi birga yakunlanadi
        await message.answer(
            "⚠️ <b>Diqqat!</b> Sherigingiz xodim emas, oila a'zosi bo'lgani uchun u uchun "
            "<b>100% to'lov</b> amalga oshiriladi."
        )
        await message.answer(
            "👨‍👩‍👧 Endi <b>oila a'zosi</b> ma'lumotini kiritamiz.\n\n"
            "📝 Oila a'zosining <b>ismini</b> kiriting:",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.set_state(Reg.fam_name)
        return

    await message.answer(
        f"👥 Sherigingiz (<b>{ctype}</b>) ro'yxatga <b>kiritilganmi</b>?\n\n"
        "Agar u allaqachon ushbu bot orqali ro'yxatdan o'tgan bo'lsa — «✅ Kiritilgan», "
        "aks holda «🆕 Hali kiritilmagan» ni tanlang. Hali kiritilmagan sherik o'zini keyinroq "
        "alohida kiritadi — uning ma'lumotini siz to'ldirmaysiz.",
        reply_markup=COMP_REGISTERED_KB,
    )
    await state.set_state(Reg.comp_registered)


@dp.message(Reg.comp_type)
async def comp_type_invalid(message: Message):
    await message.answer("Iltimos, tugmalardan birini tanlang.", reply_markup=COMP_TYPE_KB)


# ──────────────────────────── Sherik kiritilganmi? ────────────────────────────
@dp.message(Reg.comp_registered, F.text.contains("Hali kiritilmagan"))
async def comp_not_registered(message: Message, state: FSMContext):
    await state.update_data(partner=None)
    await message.answer(
        "✅ Tushunarli. Sherigingiz o'zini keyinroq alohida (/start orqali) kiritadi.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await show_confirm(message, state)


@dp.message(Reg.comp_registered, F.text.contains("Kiritilgan"))
async def comp_already_registered(message: Message, state: FSMContext):
    await message.answer(
        "📇 Sherigingizning <b>pasport seriya raqamini</b> kiriting (masalan: <code>AA1234567</code>):",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Reg.comp_series)


@dp.message(Reg.comp_registered)
async def comp_registered_invalid(message: Message):
    await message.answer("Iltimos, tugmalardan birini tanlang.", reply_markup=COMP_REGISTERED_KB)


@dp.message(Reg.comp_series, F.text)
async def set_comp_series(message: Message, state: FSMContext):
    series = message.text.strip()
    if not await series_exists(series):
        await message.answer(
            f"❌ Bunday seriya (<code>{series}</code>) ro'yxatda topilmadi.\n"
            "Sherigingiz hali kiritilmagan bo'lishi mumkin. Seriyani tekshirib qayta kiriting "
            "yoki u o'zini avval kiritsin."
        )
        return
    data = await state.get_data()
    await state.update_data(
        room=data.get("room", "2 kishilik"),
        partner={
            "type": data["_c_type"],
            "pay100": data["_c_pay100"],
            "series": series,
        },
    )
    await show_confirm(message, state)


# ──────────────────── Oila a'zosi (xodim sherigi sifatida inline) ────────────────────
@dp.message(Reg.fam_name, F.text)
async def fam_set_name(message: Message, state: FSMContext):
    await state.update_data(_f_name=message.text.strip())
    await message.answer("📝 Oila a'zosining <b>familyasini</b> kiriting:")
    await state.set_state(Reg.fam_surname)


@dp.message(Reg.fam_surname, F.text)
async def fam_set_surname(message: Message, state: FSMContext):
    await state.update_data(_f_surname=message.text.strip())
    await message.answer(
        "📅 Oila a'zosining <b>tug'ilgan sanasini</b> kiriting (masalan: <code>21-05-2012</code>):"
    )
    await state.set_state(Reg.fam_birthdate)


@dp.message(Reg.fam_birthdate, F.text)
async def fam_set_birthdate(message: Message, state: FSMContext):
    bd = parse_date(message.text)
    if not bd:
        await message.answer("❌ Sana noto'g'ri. Namuna: <code>21-05-2012</code>")
        return
    await state.update_data(_f_birthdate=fmt_date(bd), _f_age=calc_age(bd))
    await message.answer("📷 Oila a'zosining <b>pasport skanerini</b> (rasm yoki fayl) yuboring:")
    await state.set_state(Reg.fam_passport)


@dp.message(Reg.fam_passport, F.photo | F.document)
async def fam_set_passport(message: Message, state: FSMContext):
    await state.update_data(_f_passport=await extract_passport(message))
    await message.answer(
        "📇 Oila a'zosining <b>pasport seriya raqamini</b> kiriting (masalan: <code>AA1234567</code>):"
    )
    await state.set_state(Reg.fam_series)


@dp.message(Reg.fam_passport)
async def fam_passport_invalid(message: Message):
    await message.answer("Iltimos, oila a'zosining pasport <b>rasmini</b> yoki <b>faylini</b> yuboring.")


@dp.message(Reg.fam_series, F.text)
async def fam_set_series(message: Message, state: FSMContext):
    series = message.text.strip()
    data = await state.get_data()
    if series.upper() == (data.get("passport_series") or "").upper():
        await message.answer("❌ Bu sizning seriyangiz. Oila a'zosining <b>boshqa</b> seriyasini kiriting.")
        return
    if await series_exists(series):
        await message.answer(
            f"⚠️ Bu pasport seriyasi (<code>{series}</code>) allaqachon ro'yxatda mavjud.\n"
            "Tekshirib boshqa seriya kiriting."
        )
        return
    await state.update_data(_f_series=series)
    await message.answer(EXPIRY_HINT)
    await state.set_state(Reg.fam_expiry)


@dp.message(Reg.fam_expiry, F.text)
async def fam_set_expiry(message: Message, state: FSMContext):
    d, err = check_expiry(message.text)
    if err:
        await message.answer(err)
        return
    data = await state.get_data()
    await state.update_data(
        room="2 kishilik",  # oila a'zosi bog'langani uchun ikkalasi ham 2 kishilik
        partner={
            "type": data["_c_type"],
            "pay100": data["_c_pay100"],
            "inline": True,
            "name": data["_f_name"],
            "surname": data["_f_surname"],
            "birthdate": data["_f_birthdate"],
            "age": data["_f_age"],
            "series": data["_f_series"],
            "expiry": fmt_date(d),
            "passport": data["_f_passport"],
        },
    )
    await show_confirm(message, state)


# ──────────────────────────── Tasdiqlash ────────────────────────────
def summary_text(data: dict) -> str:
    age_note = " 👨‍👩‍👧 ota/ona bilan" if data.get("parent_accompany") else ""
    lines = [
        "📋 <b>Ma'lumotlarni tekshiring:</b>\n",
        f"<b>Rol:</b> {data['role']}",
        f"<b>F.I.O:</b> {data['surname']} {data['name']}",
        f"<b>Tug'ilgan sana:</b> {data['birthdate']} ({data['age']} yosh){age_note}",
        f"<b>Telefon:</b> {data['phone']}",
        f"<b>Pasport seriya:</b> {data['passport_series']} | <b>Amal muddati:</b> {data['passport_expiry']}",
        f"<b>Xona:</b> {data['room']}",
    ]
    if data.get("role") == "Oila a'zosi" and data.get("_owner_name"):
        lines.append(
            f"\n👨‍👩‍👧 <b>{data['_owner_name']}</b> ning oila a'zosi"
            f" ({data.get('_owner_series', '')}) — 💰100% to'lov"
        )
    p = data.get("partner")
    if p:
        pay = " — 💰100% to'lov" if p["pay100"] else ""
        if p.get("inline"):
            lines.append(
                f"\n👨‍👩‍👧 <b>Oila a'zosi{pay}:</b>\n"
                f"   F.I.O: {p['surname']} {p['name']}\n"
                f"   Tug'ilgan sana: {p['birthdate']} ({p['age']} yosh)\n"
                f"   Pasport seriya: {p['series']} | Amal muddati: {p['expiry']}\n"
                f"   ({data['surname']} {data['name']} ning oila a'zosi)"
            )
        else:
            lines.append(
                f"\n👥 <b>Sherik ({p['type']}){pay}:</b>\n"
                f"   Pasport seriya: {p['series']} (ro'yxatda mavjud ✅)"
            )
    return "\n".join(lines)


async def show_confirm(message: Message, state: FSMContext):
    data = await state.get_data()
    await message.answer(summary_text(data), reply_markup=kb([["✅ Tasdiqlash", "🔄 Qaytadan"]]))
    await state.set_state(Reg.confirm)


@dp.message(Reg.confirm, F.text.contains("Qaytadan"))
async def confirm_restart(message: Message, state: FSMContext):
    await cmd_start(message, state)


@dp.message(Reg.confirm, F.text.contains("Tasdiqlash"))
async def confirm_save(message: Message, state: FSMContext):
    data = await state.get_data()
    user = message.from_user
    who = f"@{user.username}" if user.username else f"{user.full_name} ({user.id})"
    now = datetime.now().strftime("%d-%m-%Y %H:%M")

    await message.answer("⏳ Ma'lumotlar saqlanmoqda, pasport Drive'ga yuklanmoqda...")

    is_family = data["role"] == "Oila a'zosi"
    label = "Oila" if is_family else "Asosiy"
    main_link = await upload_passport_to_drive(data["passport"], f"{data['surname']}_{data['name']}_{label}")

    p = data.get("partner")
    # 100% to'lov: start orqali kirgan oila a'zosida — o'zining qatorida; inline holatda — oila a'zosi qatorida
    main_pay = "Ha" if (is_family or (p and p["pay100"] and not p.get("inline"))) else ""
    main_row = [
        now, who, data["role"], ("Sherik (oila a'zosi)" if is_family else "Asosiy"),
        data["name"], data["surname"], data["birthdate"], data["age"], data["phone"],
        data["passport_series"], data["passport_expiry"], data["room"],
        main_pay, main_link,
        (data["_owner_series"] if is_family else (p["series"] if p else "")),
        str(user.id),  # Telegram ID — xabar yuborish uchun
    ]

    try:
        saved_person = None
        async with sheet_lock:
            if is_family:
                # Oila a'zosi (start orqali) — uni olib boradigan xodim (sherigi) bilan bog'laymiz
                saved_person = await asyncio.to_thread(sheets.append_linked, main_row, data["_owner_series"])
            elif p and p.get("inline"):
                # Oila a'zosi shu yerda to'ldirilgan — uning qatorini yasab, xodim bilan ketma-ket qo'shamiz
                fam_link = await upload_passport_to_drive(p["passport"], f"{p['surname']}_{p['name']}_Oila")
                fam_row = [
                    now, who, p["type"], "Sherik (oila a'zosi)",
                    p["name"], p["surname"], p["birthdate"], p["age"], data["phone"],
                    p["series"], p["expiry"], "2 kishilik",
                    ("Ha" if p["pay100"] else ""), fam_link,
                    data["passport_series"],  # kimning oila a'zosi ekani — xodim seriyasi
                    str(user.id),  # oila a'zosini kiritgan xodimning Telegram ID si
                ]
                saved_person, _ = await asyncio.to_thread(sheets.append_pair, main_row, fam_row)
            elif p:
                # Sherik bog'langan — A va sherigini jadvalda ketma-ket joylaymiz
                saved_person = await asyncio.to_thread(sheets.append_linked, main_row, p["series"])
            else:
                saved_person = (await asyncio.to_thread(sheets.append_rows, [main_row]))[0]
            # Yangi yozuvdan keyin butun jadvalni sheriklar ketma-ket turadigan qilib tekshiramiz
            await _regroup_safe()
        saved = True
    except Exception:
        logger.exception("Seminar API ga yozishda xato")
        saved = False
        saved_person = None

    await message.answer(
        "✅ <b>Ro'yxatdan o'tdingiz!</b> Ma'lumotlaringiz qabul qilindi."
        + ("" if saved else "\n\n⚠️ (Jadvalga yozishda muammo bo'ldi, admin tekshiradi.)"),
        reply_markup=ReplyKeyboardRemove(),
    )
    await notify_admins(data, who)
    if saved:
        await send_personal_page(user.id, saved_person["id"])
        await send_group_invite(message, f"{data['surname']} {data['name']}")
    await state.clear()


@dp.message(Reg.confirm)
async def confirm_invalid(message: Message):
    await message.answer("Iltimos, «Tasdiqlash» yoki «Qaytadan» ni tanlang.")


# ──────────────────────────── Admin xabarnomasi ────────────────────────────
async def notify_admins(data: dict, who: str):
    body = summary_text(data).replace("📋 <b>Ma'lumotlarni tekshiring:</b>\n\n", "")
    text = f"🆕 <b>Yangi ro'yxat</b>\n<b>Kim kiritdi:</b> {who}\n\n{body}"
    passports = [("Asosiy", data["passport"])]
    p = data.get("partner")
    if p and p.get("inline"):
        passports.append(("Oila a'zosi", p["passport"]))

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
            for label, p in passports:
                if not p:
                    continue
                if p["type"] == "photo":
                    await bot.send_photo(admin_id, p["file_id"], caption=f"📷 Pasport — {label}")
                else:
                    await bot.send_document(admin_id, p["file_id"], caption=f"📷 Pasport — {label}")
        except Exception:
            logger.exception("Admin %s ga yuborishda xato", admin_id)


# ──────────────────────────── Yangilash (update) oqimi ────────────────────────────
@dp.message(Upd.find, F.text)
async def upd_find(message: Message, state: FSMContext):
    series = message.text.strip()
    idx, row = await asyncio.to_thread(sheets.find_row_by_series, series)
    if not idx:
        await message.answer(
            f"❌ Bunday seriya (<code>{series}</code>) ro'yxatda topilmadi.\n"
            "Seriyani tekshirib qayta kiriting yoki /bekor bosing."
        )
        return
    await state.update_data(_idx=idx, _row=row)
    await message.answer(upd_summary(row), reply_markup=upd_menu_kb())
    await state.set_state(Upd.menu)


@dp.message(Upd.menu, F.text.contains("Bekor"))
async def upd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Yangilash bekor qilindi. Boshlash uchun /start", reply_markup=ReplyKeyboardRemove())


@dp.message(Upd.menu, F.text.contains("Saqlash"))
async def upd_save(message: Message, state: FSMContext):
    data = await state.get_data()
    idx, row = data["_idx"], data["_row"]
    # Telegram ID ustunini (16-ustun) tahrirlovchining ID si bilan to'ldiramiz/yangilaymiz
    tg_col = sheets.HEADER.index("Telegram ID")
    while len(row) <= tg_col:
        row.append("")
    row[tg_col] = str(message.from_user.id)
    await message.answer("⏳ Yangilangan ma'lumotlar saqlanmoqda...")
    try:
        async with sheet_lock:
            await asyncio.to_thread(sheets.update_row, idx, row)
            # Tahrirda sherik seriyasi o'zgargan bo'lishi mumkin — qaytadan tartiblaymiz
            await _regroup_safe()
        ok = True
    except Exception:
        logger.exception("Update yozishda xato")
        ok = False
    await message.answer(
        "✅ <b>Ma'lumotlaringiz yangilandi!</b>" if ok
        else "⚠️ Saqlashda muammo bo'ldi, admin tekshiradi.",
        reply_markup=ReplyKeyboardRemove(),
    )
    if ok:
        await notify_admins_update(message.from_user, row)
    await state.clear()


@dp.message(Upd.menu, F.text)
async def upd_pick_field(message: Message, state: FSMContext):
    field = next((f for f in UPD_FIELDS if f[0] == message.text.strip()), None)
    if not field:
        await message.answer("Iltimos, tugmalardan birini tanlang.", reply_markup=upd_menu_kb())
        return
    name, _col, kind = field
    await state.update_data(_field=name)
    if kind == "room":
        await message.answer(f"🏨 Yangi <b>{name}</b> ni tanlang:", reply_markup=ROOM_KB)
    elif kind == "photo":
        await message.answer(
            "📷 Yangi <b>pasport rasmini</b> (rasm yoki fayl) yuboring:",
            reply_markup=ReplyKeyboardRemove(),
        )
    elif kind == "phone":
        await message.answer("📱 Yangi <b>telefon raqamini</b> yuboring yoki yozing:", reply_markup=PHONE_KB)
    else:
        hint = {
            "date": " (masalan: <code>21-05-1990</code>)",
            "expiry": " (masalan: <code>15-08-2027</code>)",
            "series": " (masalan: <code>AA1234567</code>)",
        }.get(kind, "")
        await message.answer(f"✏️ Yangi <b>{name}</b> ni kiriting{hint}:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Upd.value)


@dp.message(Upd.value, F.photo | F.document)
async def upd_value_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("_field") != "Pasport rasmi":
        await message.answer("Iltimos, so'ralgan ma'lumotni matn ko'rinishida kiriting.")
        return
    row = data["_row"]
    await message.answer("⏳ Rasm Drive'ga yuklanmoqda...")
    link = await upload_passport_to_drive(await extract_passport(message), f"{row[5]}_{row[4]}_Yangilangan")
    if not link:
        await message.answer("⚠️ Rasmni yuklashda xato. Qayta urinib ko'ring.")
        return
    row[13] = link
    await state.update_data(_row=row)
    await message.answer("✅ Pasport rasmi yangilandi.")
    await message.answer(upd_summary(row), reply_markup=upd_menu_kb())
    await state.set_state(Upd.menu)


@dp.message(Upd.value, F.contact)
async def upd_value_contact(message: Message, state: FSMContext):
    await _apply_value(message, state, message.contact.phone_number)


@dp.message(Upd.value, F.text)
async def upd_value_text(message: Message, state: FSMContext):
    await _apply_value(message, state, message.text.strip())


async def _apply_value(message: Message, state: FSMContext, raw: str):
    data = await state.get_data()
    row = data["_row"]
    field = next((f for f in UPD_FIELDS if f[0] == data.get("_field")), None)
    if not field:
        await message.answer(upd_summary(row), reply_markup=upd_menu_kb())
        await state.set_state(Upd.menu)
        return
    name, col, kind = field

    if kind == "photo":
        await message.answer("Iltimos, pasport <b>rasmini</b> yoki <b>faylini</b> yuboring.")
        return
    if kind == "date":
        d = parse_date(raw)
        if not d:
            await message.answer("❌ Sana noto'g'ri. Namuna: <code>21-05-1990</code>")
            return
        row[col] = fmt_date(d)
        row[7] = str(calc_age(d))  # Yosh ustunini yangilaymiz
    elif kind == "expiry":
        d, err = check_expiry(raw)
        if err:
            await message.answer(err)
            return
        row[col] = fmt_date(d)
    elif kind == "series":
        s = raw.strip().upper()
        if not s:
            await message.answer("❌ Seriya bo'sh bo'lmasligi kerak.")
            return
        if s != (row[col] or "").strip().upper() and await series_exists(s):
            await message.answer(
                f"⚠️ Bu seriya (<code>{s}</code>) allaqachon boshqa ro'yxatda mavjud. Boshqa seriya kiriting."
            )
            return
        row[col] = s
    elif kind == "room":
        if raw not in config.ROOM_SIZES:
            await message.answer("Iltimos, tugmalardan birini tanlang.", reply_markup=ROOM_KB)
            return
        row[col] = raw
    elif kind == "phone":
        row[col] = raw
    else:  # text
        if not raw:
            await message.answer("❌ Bo'sh bo'lmasligi kerak.")
            return
        row[col] = raw

    await state.update_data(_row=row)
    await message.answer(f"✅ <b>{name}</b> yangilandi.")
    await message.answer(upd_summary(row), reply_markup=upd_menu_kb())
    await state.set_state(Upd.menu)


async def notify_admins_update(user, row: list):
    who = f"@{user.username}" if user.username else f"{user.full_name} ({user.id})"
    text = (
        "✏️ <b>Ma'lumot yangilandi</b>\n"
        f"<b>Kim:</b> {who}\n\n"
        f"<b>Toifa:</b> {row[2]}\n"
        f"<b>F.I.O:</b> {row[5]} {row[4]}\n"
        f"<b>Tug'ilgan sana:</b> {row[6]} ({row[7]} yosh)\n"
        f"<b>Telefon:</b> {row[8]}\n"
        f"<b>Pasport seriya:</b> {row[9]} | <b>Amal muddati:</b> {row[10]}\n"
        f"<b>Xona:</b> {row[11]}"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.exception("Admin %s ga (update) yuborishda xato", admin_id)


# ──────────────────────────── Admin amallari ────────────────────────────
def _is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


def _bc_confirm_kb(n: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Hammaga yuborish ({n})", callback_data="admbc:all")],
        [InlineKeyboardButton(text="🧪 Faqat menga (test)", callback_data="admbc:test")],
        [InlineKeyboardButton(text="❌ Bekor", callback_data="admbc:cancel")],
    ])


def _confirm_kb(go_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, yuborish", callback_data=go_data)],
        [InlineKeyboardButton(text="❌ Bekor", callback_data="adm:cancel")],
    ])


@dp.callback_query(F.data == "adm:cancel")
async def adm_cancel(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    await state.clear()
    await call.message.edit_text("❌ Bekor qilindi. /admin")
    await call.answer()


# ── E'lon yuborish ──
@dp.callback_query(F.data == "adm:bc")
async def adm_bc_start(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    await state.set_state(Admin.broadcast)
    await call.message.edit_text(
        "📢 <b>E'lon yuborish.</b>\n\n"
        "Yubormoqchi bo'lgan xabarni (matn, rasm, fayl — istalgan) shu yerga yuboring.\n"
        "Bekor qilish: /bekor"
    )
    await call.answer()


@dp.message(Admin.broadcast)
async def adm_bc_capture(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    regs = await broadcast.fetch_registrants()
    recips = broadcast.distinct_recipients(regs)
    n = len(recips)
    await state.update_data(bc_chat=message.chat.id, bc_msg=message.message_id)
    if n == 0:
        await message.answer(
            "⚠️ Hozircha xabar yuborib bo'ladigan foydalanuvchi yo'q "
            "(Telegram ID saqlangan yozuvlar yo'q). «🧪 Faqat menga» bilan testlashingiz mumkin.",
            reply_markup=_bc_confirm_kb(0),
        )
        return
    await message.answer(
        f"👆 Yuqoridagi xabar <b>{n}</b> ta foydalanuvchiga yuboriladi.\n\nTasdiqlaysizmi?",
        reply_markup=_bc_confirm_kb(n),
    )


@dp.callback_query(F.data.in_(["admbc:all", "admbc:test", "admbc:cancel"]))
async def adm_bc_confirm(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    data = await state.get_data()
    bc_chat, bc_msg = data.get("bc_chat"), data.get("bc_msg")
    await state.clear()

    if call.data == "admbc:cancel" or not bc_chat:
        await call.message.edit_text("❌ E'lon bekor qilindi. /admin")
        return await call.answer()

    if call.data == "admbc:test":
        try:
            await bot.copy_message(call.from_user.id, bc_chat, bc_msg)
            await call.message.edit_text("🧪 Test: xabar faqat sizga yuborildi. /admin")
        except Exception:
            logger.exception("Test e'lon yuborishda xato")
            await call.message.edit_text("⚠️ Test yuborishda xato. /admin")
        return await call.answer()

    # Hammaga
    await call.answer("Yuborilmoqda...")
    await call.message.edit_text("⏳ E'lon yuborilmoqda...")
    regs = await broadcast.fetch_registrants()
    recips = broadcast.distinct_recipients(regs)
    rep = await broadcast.broadcast_copy(bot, bc_chat, bc_msg, recips)
    await call.message.edit_text(
        "📢 <b>E'lon yuborildi.</b>\n\n"
        f"✅ Yetkazildi: <b>{rep['sent']}</b>\n"
        f"⚠️ Yuborilmadi: <b>{rep['failed']}</b>\n"
        f"👥 Jami: <b>{rep['total']}</b>\n\n/admin"
    )


# ── Xona sheriklari ──
@dp.callback_query(F.data == "adm:rooms")
async def adm_rooms_preview(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    await call.answer("Hisoblanmoqda...")
    groups = await broadcast.fetch_groups()
    pv = broadcast.roommate_preview(groups)
    if pv["reachable"] == 0:
        await call.message.edit_text(
            "🛏 Xabar yuboriladigan xona a'zosi topilmadi "
            f"(to'liq xonalar: {pv['rooms']}, ID siz: {pv['unreachable']}).\n\n/admin"
        )
        return
    await call.message.edit_text(
        "🛏 <b>Xona sheriklarini xabardor qilish</b>\n\n"
        f"To'liq biriktirilgan xonalar: <b>{pv['rooms']}</b>\n"
        f"Xabar yetadi (ID bor): <b>{pv['reachable']}</b> kishiga\n"
        f"ID siz (yetmaydi): <b>{pv['unreachable']}</b> kishi\n\n"
        "Har bir kishiga o'z xonasidagi sheriklar ro'yxati yuboriladi. Davom etamizmi?",
        reply_markup=_confirm_kb("admrooms:go"),
    )


@dp.callback_query(F.data == "admrooms:go")
async def adm_rooms_go(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    await call.answer("Yuborilmoqda...")
    await call.message.edit_text("⏳ Xona xabarlari yuborilmoqda...")
    groups = await broadcast.fetch_groups()
    rep = await broadcast.notify_roommates(bot, groups)
    await call.message.edit_text(
        "🛏 <b>Xona xabarlari yuborildi.</b>\n\n"
        f"✅ Yetkazildi: <b>{rep['sent']}</b>\n"
        f"⚠️ Yuborilmadi: <b>{rep['failed']}</b>\n"
        f"❔ ID siz (yetmadi): <b>{rep['unreachable']}</b>\n\n/admin"
    )


# ── Guruhga taklif ──
@dp.callback_query(F.data == "adm:invite")
async def adm_invite_preview(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    if not config.GROUP_CHAT_ID:
        await call.message.edit_text(
            "⚠️ Guruh sozlanmagan (GROUP_CHAT_ID yo'q). Avval .env da sozlang.\n\n/admin"
        )
        return await call.answer()
    await call.answer("Hisoblanmoqda...")
    regs = await broadcast.fetch_registrants()
    recips = broadcast.distinct_recipients(regs)
    await call.message.edit_text(
        "➕ <b>Guruhga taklif</b>\n\n"
        f"ID si bor <b>{len(recips)}</b> foydalanuvchi tekshiriladi. "
        "Guruhga qo'shilmaganlarga shaxsiy taklif havolasi yuboriladi.\n\nDavom etamizmi?",
        reply_markup=_confirm_kb("adminvite:go"),
    )


@dp.callback_query(F.data == "adminvite:go")
async def adm_invite_go(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    await call.answer("Yuborilmoqda...")
    await call.message.edit_text("⏳ Tekshirilmoqda va takliflar yuborilmoqda...")
    regs = await broadcast.fetch_registrants()
    recips = broadcast.distinct_recipients(regs)
    rep = await broadcast.invite_non_members(bot, recips)
    await call.message.edit_text(
        "➕ <b>Guruh takliflari yakunlandi.</b>\n\n"
        f"📨 Taklif yuborildi: <b>{rep['invited']}</b>\n"
        f"✅ Allaqachon guruhda: <b>{rep['already']}</b>\n"
        f"⚠️ Yuborilmadi: <b>{rep['failed']}</b>\n"
        f"👥 Tekshirildi: <b>{rep['total']}</b>\n\n/admin"
    )


# ── Statistika ──
@dp.callback_query(F.data == "adm:stats")
async def adm_stats(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    await call.answer("Hisoblanmoqda...")
    s = await asyncio.to_thread(api_client.stats)
    text = (
        "📊 <b>Statistika</b>\n\n"
        f"👤 Jami ro‘yxatda: <b>{s['total']}</b>\n"
        f"✉️ Telegram ID bor: <b>{s['telegram']}</b>\n"
        f"❔ Telegram ID yo‘q: <b>{s['no_telegram']}</b>\n\n"
        f"🛏 DBL: <b>{s['dbl']}</b> ta yozuv\n"
        f"🛏 TRPL: <b>{s['trpl']}</b> ta yozuv\n"
        f"✅ To‘liq xonalar: <b>{s['rooms_ready']}</b>\n"
        f"⏳ Sherigi belgilanmaganlar: <b>{s['without_roommate']}</b>"
    )
    await call.message.edit_text(text + "\n\n/admin")


# ═════════════════════ Rol menyusi va ikki tomonlama xabar ═════════════════════
# Rol serverdan (`/api/bot/whoami`) olinadi: admin · guruh rahbari · a'zo.
# Kim kimga yoza olishini ham server hal qiladi, bot faqat yetkazadi.

class Msg(StatesGroup):
    body = State()    # xabar matnini/rasmini kutish
    person = State()  # "bitta odamga" — kimga yuborishni so'rash
    reply = State()   # "↩️ Javob berish" bosilgandan keyingi matn


async def whoami(user_id: int) -> dict:
    try:
        return await asyncio.to_thread(api_client.whoami, user_id)
    except api_client.ApiError:
        logger.exception("whoami xatosi: %s", user_id)
        return {"role": "guest"}


def _me_line(me: dict) -> str:
    title = {"admin": "Administrator", "leader": "Guruh rahbari", "member": "Ishtirokchi"}
    parts = [me.get("fio") or title.get(me.get("role"), ""), title.get(me.get("role"), "")]
    if me.get("group"):
        parts.append(f"{me['group']}-guruh" + (f" · {me['group_name']}" if me.get("group_name") else ""))
    return " · ".join(p for p in dict.fromkeys(parts) if p)


async def show_menu(user_id: int, me: dict | None = None, note: str = "") -> dict:
    """Foydalanuvchiga o'z roliga mos menyuni ko'rsatadi."""
    me = me or await whoami(user_id)
    if me.get("role") == "guest":
        await bot.send_message(
            user_id,
            "🔍 Sizni ro'yxatdan topa olmadik.\n"
            "Pasport seriyangiz yoki tug'ilgan sanangiz bilan /start orqali kiring.",
            reply_markup=ReplyKeyboardRemove())
        return me
    text = (note + "\n\n" if note else "") + f"👤 <b>{_me_line(me)}</b>\n\nQuyidagi menyudan tanlang:"
    await bot.send_message(user_id, text, reply_markup=messaging.role_menu(me["role"]))
    return me


@dp.message(Command("menyu"), F.chat.type == "private")
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await show_menu(message.from_user.id)


@dp.message(Command("skaner"), F.chat.type == "private")
@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_SCAN)
async def cmd_scanner(message: Message, state: FSMContext):
    """Check-in skanerini ochadi — mini-app inline tugma orqali."""
    await state.clear()
    me = await whoami(message.from_user.id)
    if me.get("role") not in {"admin", "leader"}:
        return await show_menu(message.from_user.id, me,
                               "⛔ QR skaner faqat admin va guruh mas'ullari uchun.")
    scope = ("O'z guruhingiz a'zolarini belgilay olasiz."
             if me.get("role") == "leader" and me.get("leader_scope") == "group"
             else "Barcha ishtirokchilarni belgilay olasiz.")
    await message.answer(
        "📷 <b>Check-in — QR skanlash</b>\n\n"
        f"{scope}\nTugmani bosing, skaner Telegram ichida ochiladi va beyjik QR ini "
        "o'qishi bilan check-in bo'ladi.",
        reply_markup=messaging.scan_inline_kb())


async def _ask_text(message: Message, state: FSMContext, scope: str, value=None, prompt: str = ""):
    await state.set_state(Msg.body)
    await state.update_data(scope=scope, value=value)
    await message.answer(
        (prompt or "✍️ Xabar matnini yozing.") +
        "\n\n<i>Rasm yoki hujjat ham yuborishingiz mumkin. Bekor qilish — /bekor</i>",
        reply_markup=ReplyKeyboardRemove())


# ── Admin tugmalari ──
@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_ALL)
async def menu_all(message: Message, state: FSMContext):
    me = await whoami(message.from_user.id)
    if me.get("role") != "admin":
        return await show_menu(message.from_user.id, me, "⛔ Bu bo'lim faqat adminlar uchun.")
    await _ask_text(message, state, "all", prompt="📢 <b>Hammaga xabar</b> — matnni yozing.")


@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_GROUP)
async def menu_group(message: Message, state: FSMContext):
    me = await whoami(message.from_user.id)
    if me.get("role") != "admin":
        return await show_menu(message.from_user.id, me, "⛔ Bu bo'lim faqat adminlar uchun.")
    try:
        groups = await asyncio.to_thread(api_client.groups)
    except api_client.ApiError:
        return await message.answer("⚠️ Guruhlar ro'yxatini olib bo'lmadi.")
    rows = [[InlineKeyboardButton(
        text=f"{g['id']}-guruh · {g.get('name') or '—'} ({g['with_telegram']}/{g['total']})",
        callback_data=f"mgrp:{g['id']}")] for g in groups]
    rows.append([InlineKeyboardButton(text="❌ Bekor", callback_data="mgrp:cancel")])
    await message.answer("👥 Qaysi guruhga yuboramiz?\n<i>Qavsda — Telegram ID si borlar soni.</i>",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("mgrp:"))
async def menu_group_pick(call: CallbackQuery, state: FSMContext):
    value = call.data.split(":", 1)[1]
    await call.answer()
    if value == "cancel":
        await call.message.edit_text("❌ Bekor qilindi.")
        return await show_menu(call.from_user.id)
    await call.message.edit_text(f"📢 <b>{value}-guruhga</b> xabar tayyorlanmoqda.")
    await state.set_state(Msg.body)
    await state.update_data(scope="group", value=value)
    await bot.send_message(call.from_user.id,
                           "✍️ Xabar matnini yozing.\n\n<i>Rasm/hujjat ham mumkin. Bekor — /bekor</i>",
                           reply_markup=ReplyKeyboardRemove())


@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_ONE)
async def menu_one(message: Message, state: FSMContext):
    me = await whoami(message.from_user.id)
    if me.get("role") != "admin":
        return await show_menu(message.from_user.id, me, "⛔ Bu bo'lim faqat adminlar uchun.")
    await state.set_state(Msg.person)
    await message.answer("👤 Kimga? <b>ACO-042</b> ko'rinishidagi ID, QR token yoki ism-familya yozing.",
                         reply_markup=ReplyKeyboardRemove())


@dp.message(Msg.person, F.text)
async def menu_one_pick(message: Message, state: FSMContext):
    raw = message.text.strip()
    try:
        found = await asyncio.to_thread(api_client.find, name=raw) if " " in raw or not raw.upper().startswith("ACO-") else []
    except api_client.ApiError:
        found = []
    target = raw
    if found:
        if len(found) > 1:
            names = "\n".join(f"• <code>{p['id']}</code> — {p['fio']}" for p in found[:12])
            return await message.answer(f"👥 Bir nechta odam topildi, ID sini yozing:\n{names}")
        target = found[0]["id"]
        await message.answer(f"👤 Tanlandi: <b>{found[0]['fio']}</b> ({target})")
    await _ask_text(message, state, "one", target)


# ── Guruh rahbari tugmalari ──
@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_MYGROUP)
async def menu_my_group(message: Message, state: FSMContext):
    me = await whoami(message.from_user.id)
    if me.get("role") != "leader":
        return await show_menu(message.from_user.id, me, "⛔ Bu bo'lim faqat guruh rahbarlari uchun.")
    await _ask_text(message, state, "group", me.get("group"),
                    prompt=f"📢 <b>{me.get('group')}-guruhingizga</b> xabar — matnni yozing.")


@dp.message(StateFilter(None), F.chat.type == "private",
            F.text.in_({messaging.BTN_ROSTER, messaging.BTN_ATT}))
async def menu_roster(message: Message):
    me = await whoami(message.from_user.id)
    if me.get("role") not in {"leader", "admin"} or not me.get("group"):
        return await show_menu(message.from_user.id, me, "⛔ Bu bo'lim faqat guruh rahbarlari uchun.")
    try:
        data = await asyncio.to_thread(api_client.group_members, me["group"])
    except api_client.ApiError:
        return await message.answer("⚠️ Guruh ro'yxatini olib bo'lmadi.")
    attendance = message.text == messaging.BTN_ATT
    lines = [f"👥 <b>{data['group']}-guruh · {data.get('name') or '—'}</b> "
             f"({len(data['members'])} kishi)\n"]
    for i, m in enumerate(data["members"], 1):
        marks = m.get("checkins") or {}
        if attendance:
            state_icon = "✅" if marks else "⬜️"
            when = " · ".join(f"{k}:{v}" for k, v in sorted(marks.items())) or "belgilanmagan"
            lines.append(f"{state_icon} {m['fio']} — <i>{when}</i>")
        else:
            tag = " 👑" if m["leader"] else ""
            tg = "" if m.get("telegram_id") else " · <i>Telegramsiz</i>"
            room = f" · xona {m['room']}" if m.get("room") else ""
            lines.append(f"{i}. {m['fio']}{tag}{room}{tg}")
    if attendance:
        came = sum(1 for m in data["members"] if m.get("checkins"))
        lines.append(f"\n✅ Keldi: <b>{came}</b> · ⬜️ Yo'q: <b>{len(data['members']) - came}</b>")
    text = "\n".join(lines)
    for chunk in [text[i:i + 3500] for i in range(0, len(text), 3500)]:
        await message.answer(chunk)


# ── A'zo tugmalari ──
@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_ASK)
async def menu_ask(message: Message, state: FSMContext):
    me = await whoami(message.from_user.id)
    if me.get("role") == "guest":
        return await show_menu(message.from_user.id, me)
    await _ask_text(message, state, "leader",
                    prompt="✍️ <b>Guruh rahbaringizga savol</b> — matnni yozing.")


# ═══════════════════════ Hujjatlar (voucher, chipta) ═══════════════════════
DOC_CAPTION = {"voucher": "🏨 Mehmonxona voucheri", "ticket": "✈️ Aviachipta",
               "other": "📎 Hujjat"}


async def send_documents(user_id: int, *, participant_id=None, silent=False) -> int:
    """Odamning hujjatlarini yuboradi. Nechta yuborilganini qaytaradi.

    ``silent`` — hujjat bo'lmasa hech narsa yozmaydi (ro'yxatdan o'tish oqimida
    ortiqcha xabar chiqmasin).
    """
    try:
        data = await asyncio.to_thread(
            api_client.docs_for,
            telegram_id=None if participant_id else user_id,
            participant_id=participant_id)
    except api_client.ApiError:
        logger.exception("Hujjatlarni olishda xato: %s", user_id)
        return 0

    ready, pending = data.get("documents") or [], data.get("pending") or []
    if not ready:
        if not silent:
            await bot.send_message(user_id, (
                "⏳ Hujjatlaringiz hali tayyor emas — tayyor bo'lishi bilan "
                "bot o'zi yuboradi." if pending else
                "📎 Sizga biriktirilgan hujjat topilmadi. "
                "Savol bo'lsa guruh mas'ulingizga yozing."))
        return 0

    # Turga biriktirilgan izoh (masalan reys vaqti o'zgargani) o'sha turdagi
    # birinchi fayldan oldin, bir marta yuboriladi.
    notes, announced = data.get("notes") or {}, set()
    sent = []
    for doc in sorted(ready, key=lambda d: d["kind"]):
        kind = doc["kind"]
        if kind in notes and kind not in announced:
            announced.add(kind)
            try:
                await bot.send_message(user_id, notes[kind], disable_web_page_preview=True)
            except Exception:
                logger.debug("Izoh yuborilmadi: %s", user_id)
        try:
            blob = await asyncio.to_thread(api_client.docs_file, doc["id"])
            await bot.send_document(
                user_id, BufferedInputFile(blob, filename=doc["file_name"]),
                caption=DOC_CAPTION.get(kind, DOC_CAPTION["other"]))
            sent.append(doc["id"])
        except Exception as exc:
            logger.warning("Hujjat yuborilmadi (%s → %s): %s", doc["id"], user_id, exc)
        await asyncio.sleep(0.2)
    if sent:
        try:
            await asyncio.to_thread(api_client.docs_mark_sent, sent)
        except api_client.ApiError:
            logger.debug("Yuborilgan hujjatni belgilab bo'lmadi")
    if pending and not silent:
        await bot.send_message(user_id, f"⏳ Yana <b>{len(pending)}</b> ta hujjat "
                                        "tayyorlanmoqda — tayyor bo'lishi bilan yuboriladi.")
    return len(sent)


class Docs(StatesGroup):
    """Botga tashlangan fayl egasini aniqlash."""
    owner = State()   # "kimga tegishli?" javobini kutish


def _owners_text(saved) -> str:
    names = {}
    for item in saved:
        names.setdefault(item["pid"], item)
    return ", ".join(f"{pid}" for pid in names)


async def _describe(pids) -> str:
    """ACO raqamlarini ism bilan ko'rsatadi."""
    out = []
    for pid in pids:
        try:
            data = await asyncio.to_thread(api_client.participant, pid)
            person = data.get("participant") or {}
            group = f" · {person.get('group')}-guruh" if person.get("group") else ""
            out.append(f"{pid} — {person.get('fio') or ''}{group}")
        except Exception:
            out.append(pid)
    return "\n".join(f"• {x}" for x in out)


@dp.message(StateFilter(None, Docs.owner), F.chat.type == "private", F.document | F.photo)
async def admin_document(message: Message, state: FSMContext):
    """Admin botga voucher/chipta tashlaganda saqlaydi va egasini topadi.

    Fayl nomida yoki izohda ism, ACO raqami yoki pasport bo'lsa — egasi o'zi
    aniqlanadi. Bitta faylda bir necha kishi bo'lsa hammasiga biriktiriladi.
    """
    if not _is_admin(message.from_user.id):
        me = await whoami(message.from_user.id)
        if me.get("role") not in {"admin", "manager"}:
            return await message.answer(
                "📎 Hujjat yuborish faqat adminlar uchun.\n"
                "O'z hujjatlaringizni olish uchun «📎 Hujjatlarim» tugmasini bosing.")

    # Oldingi fayl uchun "kimga tegishli?" savoli javobsiz qolgan bo'lsa —
    # yangi fayl kelgani uni bekor qiladi, lekin qaysi fayl qolganini aytamiz.
    skipped = (await state.get_data()).get("file_name") if await state.get_state() else None
    await state.clear()

    if message.document:
        tg_file, name = message.document, message.document.file_name or "hujjat.pdf"
        mime = message.document.mime_type or ""
    else:
        tg_file, name, mime = message.photo[-1], f"rasm_{message.photo[-1].file_unique_id}.jpg", "image/jpeg"
    caption = (message.caption or "").strip()

    note = await message.answer("⏳ Saqlanmoqda — PDF ichidan ismlar o'qilmoqda…")
    try:
        buf = await bot.download(tg_file)
        blob = buf.read()
    except Exception as exc:
        return await note.edit_text(f"⚠️ Faylni olib bo'lmadi: <code>{exc}</code>")

    try:
        result = await asyncio.to_thread(
            api_client.docs_upload, [(name, blob, mime)],
            hint=caption, uploader=str(message.from_user.id))
    except api_client.ApiError as exc:
        return await note.edit_text(f"⚠️ Saqlab bo'lmadi: <code>{exc}</code>")

    saved = result.get("saved") or []
    dupes = result.get("duplicates") or []
    if saved or dupes:
        pids = list(dict.fromkeys(s["pid"] for s in saved))
        split = result.get("split") or []
        lines = []
        if saved:
            kind = saved[0]["kind"]
            lines.append(f"✅ <b>{name}</b> — {DOC_CAPTION.get(kind, '📎')}")
            if split:
                lines.append(f"📄 {split[0]['parts']} ta bo'lakka ajratildi")
            lines.append(await _describe(pids))
        if dupes:
            already = list(dict.fromkeys(d["pid"] for d in dupes))
            lines.append(f"🔁 <b>{name}</b> — allaqachon bor "
                         f"({len(already)} kishida), qayta saqlanmadi")
        if skipped:
            lines.append(f"\n⚠️ <b>{skipped}</b> egasiz qoldi — uni qaytadan yuboring.")
        await note.edit_text("\n".join(lines))
        return

    # Egasi topilmadi — faylni yodda tutamiz va kimligini so'raymiz.
    await state.set_state(Docs.owner)
    await state.update_data(file_name=name, mime=mime, blob=blob.hex(), caption=caption)
    await note.edit_text(
        f"❓ <b>{name}</b> — egasi topilmadi.\n\n"
        "Kimga tegishli? Ism-familya, <b>ACO raqami</b> yoki pasportni yozing.\n"
        "Bir nechta bo'lsa vergul bilan: <code>ACO-004, ACO-104</code>\n\n"
        "<i>Javob bermay yangi fayl yuborsangiz, bu fayl saqlanmay qoladi.</i>"
        + (f"\n⚠️ <b>{skipped}</b> ham egasiz qoldi." if skipped else ""))


@dp.message(Docs.owner, F.text)
async def admin_document_owner(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    blob = bytes.fromhex(data.get("blob") or "")
    if not blob:
        return await message.answer("⚠️ Fayl saqlanmagan, qaytadan yuboring.")
    try:
        result = await asyncio.to_thread(
            api_client.docs_upload, [(data["file_name"], blob, data.get("mime") or "")],
            hint=f"{data.get('caption','')} {message.text}",
            uploader=str(message.from_user.id))
    except api_client.ApiError as exc:
        return await message.answer(f"⚠️ Saqlab bo'lmadi: <code>{exc}</code>")

    saved = result.get("saved") or []
    if not saved:
        return await message.answer(
            "❌ Bu ism bo'yicha ham topilmadi.\n"
            "Aniq <b>ACO raqami</b> bilan urinib ko'ring (masalan <code>ACO-042</code>) "
            "yoki paneldagi «Hujjatlar» bo'limidan qo'lda biriktiring.")
    pids = list(dict.fromkeys(s["pid"] for s in saved))
    split = result.get("split") or []
    extra = (f"\n📄 PDF {split[0]['parts']} ta bo'lakka ajratildi." if split else "")
    await message.answer(f"✅ <b>{data['file_name']}</b> saqlandi.{extra}\n\n"
                         f"Egalari ({len(pids)}):\n{await _describe(pids)}")
    await show_menu(message.from_user.id)


@dp.message(Command("hujjatlarim"), F.chat.type == "private")
@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_DOCS)
async def menu_docs(message: Message, state: FSMContext):
    await state.clear()
    me = await whoami(message.from_user.id)
    if not me.get("id"):
        return await show_menu(message.from_user.id, me)
    await send_documents(message.from_user.id)


@dp.message(Command("hujjat_holat"), F.chat.type == "private")
async def cmd_docs_state(message: Message):
    """Hujjatlar bo'yicha umumiy holat: nima bor, nima yetishmaydi."""
    if not _is_admin(message.from_user.id):
        return
    try:
        d = await asyncio.to_thread(api_client.docs_state)
    except api_client.ApiError as exc:
        return await message.answer(f"⚠️ <code>{exc}</code>")
    lines = ["📎 <b>Hujjatlar holati</b>\n",
             ("🔒 <b>Tarqatish ushlab turilgan</b> — hech kimga yuborilmaydi."
              if d["hold"] else "🔓 Tarqatish ochiq — so'raganlar oladi."),
             f"\n📄 Fayl: <b>{d['files']}</b> · yozuv: <b>{d['rows']}</b> · "
             f"yuborilmagan: <b>{d['unsent']}</b>\n",
             f"✅ Voucher + chipta: <b>{d['both']}</b>",
             f"🏨 Faqat voucher: <b>{d['only_voucher']}</b>",
             f"✈️ Faqat chipta: <b>{d['only_ticket']}</b>",
             f"❌ Hech narsa yo'q: <b>{len(d['none'])}</b>"]
    if d["none"]:
        lines.append("\n<b>Hujjatsizlar:</b>")
        lines += [f"• {x['fio']}" + (f" ({x['group']}-guruh)" if x["group"] else "")
                  for x in d["none"]]
    if d["missing_ticket"]:
        lines.append(f"\n<b>Chiptasi yo'q ({len(d['missing_ticket'])}):</b>")
        by_group = {}
        for x in d["missing_ticket"]:
            by_group.setdefault(x["group"], []).append(x["fio"] + ("★" if x["leader"] else ""))
        for group in sorted(by_group, key=lambda g: (g is None, g)):
            lines.append(f"{group}-guruh: " + ", ".join(by_group[group]))
    lines.append("\n/hujjat_yuborish — hammaga tarqatish")
    text = "\n".join(lines)
    for chunk in [text[i:i + 3500] for i in range(0, len(text), 3500)]:
        await message.answer(chunk)


@dp.message(Command("hujjat_ushla"), F.chat.type == "private")
async def cmd_docs_hold(message: Message):
    """Tarqatishni ushlab turadi — fayllarni yuklab bo'lguncha."""
    if not _is_admin(message.from_user.id):
        return
    await asyncio.to_thread(api_client.docs_hold, True)
    await message.answer(
        "🔒 <b>Hujjatlar ushlab turildi.</b>\n\n"
        "Endi hech kimga yuborilmaydi — ro'yxatdan o'tganlarga ham, "
        "«📎 Hujjatlarim» bosganlarga ham.\n\n"
        "Fayllarni bemalol yuklayvering. Tayyor bo'lganda /hujjat_yuborish.")


@dp.message(Command("hujjat_yuborish"), F.chat.type == "private")
async def cmd_docs_broadcast(message: Message):
    """Hujjati bor, lekin hali olmagan hammaga yuborish."""
    if not _is_admin(message.from_user.id):
        return
    state = await asyncio.to_thread(api_client.docs_state)
    if state["hold"]:
        return await message.answer(
            "🔒 <b>Hujjatlar hozir ushlab turilgan.</b>\n\n"
            f"📄 Tayyor fayl: <b>{state['files']}</b>\n"
            f"✅ Voucher + chipta: <b>{state['both']}</b> · "
            f"🏨 faqat voucher: <b>{state['only_voucher']}</b> · "
            f"❌ hech narsasi yo'q: <b>{len(state['none'])}</b>\n\n"
            "Tarqatishni ochib, hammaga yuboraymi?",
            reply_markup=_confirm_kb("docsopen:go"))
    data = await asyncio.to_thread(api_client.docs_pending)
    people = data.get("people") or []
    waiting = data.get("not_registered") or 0
    if not people:
        return await message.answer(
            "✅ Yuborilmagan hujjat qolmadi.\n\n"
            + (f"⏳ <b>{waiting}</b> kishining hujjati bor, lekin ular hali botda "
               "tasdiqlamagan — tasdiqlashi bilan o'zi yuboriladi." if waiting else ""))
    total = sum(len(p["documents"]) for p in people)
    await message.answer(
        f"📎 <b>Hujjatlarni yuborish</b>\n\n"
        f"👤 Odam: <b>{len(people)}</b>\n📄 Fayl: <b>{total}</b>\n"
        + (f"⏳ Hali tasdiqlamaganlar: <b>{waiting}</b> (ular tasdiqlaganda o'zi boradi)\n"
           if waiting else "") + "\nDavom etamizmi?",
        reply_markup=_confirm_kb("docsend:go"))


@dp.callback_query(F.data == "docsopen:go")
async def docs_open_and_send(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    await call.answer("Ochilmoqda…")
    await asyncio.to_thread(api_client.docs_hold, False)
    await call.message.edit_text("🔓 Tarqatish ochildi. ⏳ Yuborilmoqda…")
    await _docs_broadcast(call.message)


@dp.callback_query(F.data == "docsend:go")
async def docs_broadcast_go(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    await call.answer("Yuborilmoqda…")
    await call.message.edit_text("⏳ Hujjatlar yuborilmoqda…")
    await _docs_broadcast(call.message)


async def _docs_broadcast(target: Message):
    data = await asyncio.to_thread(api_client.docs_pending)
    ok, failed = 0, []
    for person in data.get("people") or []:
        count = await send_documents(int(person["telegram_id"]),
                                     participant_id=person["id"], silent=True)
        if count:
            ok += count
        else:
            failed.append(person["fio"])
        await asyncio.sleep(0.15)
    text = (f"📎 <b>Yuborildi:</b> {ok} ta fayl\n"
            f"👤 Odam: <b>{len(data.get('people') or []) - len(failed)}</b>")
    if data.get("not_registered"):
        text += (f"\n⏳ Hujjati bor, lekin botda tasdiqlamagan: "
                 f"<b>{data['not_registered']}</b> — tasdiqlaganda o'zi boradi.")
    if failed:
        text += (f"\n⚠️ Yetmadi: <b>{len(failed)}</b>\n"
                 + "\n".join(f"• {f}" for f in failed[:15]))
    await target.edit_text(text + "\n\n/hujjat_holat")


@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_PAGE)
async def menu_page(message: Message):
    me = await whoami(message.from_user.id)
    if not me.get("id"):
        return await show_menu(message.from_user.id, me)
    await send_personal_page(message.from_user.id, me["id"])


@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_STATS)
async def menu_stats(message: Message):
    me = await whoami(message.from_user.id)
    if me.get("role") != "admin":
        return await show_menu(message.from_user.id, me, "⛔ Bu bo'lim faqat adminlar uchun.")
    s = await asyncio.to_thread(api_client.stats)
    groups = await asyncio.to_thread(api_client.groups)
    done, total = s["telegram"], s["total"]
    percent = round(done * 100 / total) if total else 0
    bar = "█" * (percent // 10) + "░" * (10 - percent // 10)
    lines = ["📊 <b>Umumiy statistika</b>\n",
             f"👤 Jami ro'yxatda: <b>{total}</b>",
             f"✅ Botda tasdiqlagan: <b>{done}</b> · ⏳ hali yo'q: <b>{s['no_telegram']}</b>",
             f"<code>{bar}</code> {percent}%\n",
             "<b>Guruhlar — tasdiqlagan / jami</b>"]
    for g in groups:
        leader = (g.get("leader") or {}).get("fio") or "mas'ul belgilanmagan"
        lines.append(f"• {g['id']}-guruh · {g.get('name') or '—'} — "
                     f"<b>{g['with_telegram']}/{g['total']}</b> tasdiqlagan, "
                     f"keldi {g['checked_in']} · 👑 {leader}")
    lines.append("\n/tasdiqlanmaganlar — kim hali tasdiqlamagan\n/guruh_holat — guruh nazorati")
    await message.answer("\n".join(lines))


@dp.message(Command("tasdiqlanmaganlar"), F.chat.type == "private")
@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_PENDING)
async def cmd_pending(message: Message, state: FSMContext):
    """Kim hali botda tasdiqlamagan — guruh bo'yicha ro'yxat.

    Admin hammasini, guruh mas'uli faqat o'z guruhini ko'radi.  Bu odamlar bilan
    bevosita bog'lanib bo'lmaydi (Telegramlari hali noma'lum), shuning uchun
    ro'yxat telefon raqami bilan chiqadi — qo'ng'iroq qilib chaqirish uchun.
    """
    await state.clear()
    me = await whoami(message.from_user.id)
    if me.get("role") not in {"admin", "leader"}:
        return await show_menu(message.from_user.id, me,
                               "⛔ Bu bo'lim faqat admin va guruh mas'ullari uchun.")
    try:
        groups = await asyncio.to_thread(api_client.groups)
    except api_client.ApiError:
        return await message.answer("⚠️ Ma'lumotni olib bo'lmadi.")
    if me["role"] == "leader":
        groups = [g for g in groups if g["id"] == me.get("group")]

    lines, waiting = ["⏳ <b>Hali tasdiqlamaganlar</b>\n"], 0
    for g in groups:
        data = await asyncio.to_thread(api_client.group_members, g["id"])
        pending = [m for m in data["members"] if not m.get("telegram_id")]
        waiting += len(pending)
        lines.append(f"\n<b>{g['id']}-guruh · {g.get('name') or '—'}</b> "
                     f"({len(pending)} ta kutilmoqda)")
        for m in pending:
            phone = f" · {m['phone']}" if m.get("phone") else ""
            lines.append(f"• {m['fio']}{phone}")
        if not pending:
            lines.append("✅ hammasi tasdiqlagan")
    if not waiting:
        lines = ["✅ <b>Hamma tasdiqlagan.</b>"]
    else:
        lines.append(f"\nJami kutilmoqda: <b>{waiting}</b>")
    text = "\n".join(lines)
    for chunk in [text[i:i + 3500] for i in range(0, len(text), 3500)]:
        await message.answer(chunk)


# ── Xabar matnini qabul qilish va yuborish ──
@dp.message(Msg.body, F.text | F.photo | F.document)
async def msg_body(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await message.answer("⏳ Yuborilmoqda…")
    rep = await messaging.deliver(bot, message.from_user.id, data.get("scope", "all"),
                                  data.get("value"), message)
    await show_menu(message.from_user.id, note=messaging.report(rep))


@dp.message(Msg.body)
async def msg_body_invalid(message: Message):
    await message.answer("Iltimos, matn, rasm yoki hujjat yuboring. Bekor qilish — /bekor")


# ── "↩️ Javob berish" — javob DOIM asl yuboruvchiga qaytadi ──
@dp.callback_query(F.data.startswith("reply:"))
async def reply_start(call: CallbackQuery, state: FSMContext):
    try:
        parent = int(call.data.split(":", 1)[1])
    except ValueError:
        return await call.answer()
    await state.set_state(Msg.reply)
    await state.update_data(parent=parent)
    await call.answer()
    await bot.send_message(call.from_user.id,
                           "↩️ <b>Javobingizni yozing</b> — u faqat xabarni yuborgan odamga boradi.\n"
                           "<i>Bekor qilish — /bekor</i>",
                           reply_markup=ReplyKeyboardRemove())


@dp.message(Msg.reply, F.text | F.photo | F.document)
async def reply_send(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    rep = await messaging.deliver_reply(bot, message.from_user.id, data.get("parent"), message)
    note = "✅ Javobingiz yuborildi." if rep.get("sent") else \
        f"⚠️ Javob yetkazilmadi: <code>{rep.get('error') or 'noma’lum xato'}</code>"
    await show_menu(message.from_user.id, note=note)


@dp.message(Msg.reply)
async def reply_invalid(message: Message):
    await message.answer("Iltimos, javobni matn, rasm yoki hujjat ko'rinishida yuboring. /bekor")


# ═══════════════════════ Telegram guruhini nazorat qilish ════════════════════
# Telegram Bot API guruh a'zolarini ro'yxatlab bermaydi — faqat umumiy son,
# adminlar va bitta odamni tekshirish mumkin.  Shuning uchun bot guruhda
# ko'rgan hamma narsani yozib boradi: kirdi/chiqdi hodisalari va guruhda
# yozganlar.  Qaror faqat shu ko'rilganlar bo'yicha chiqariladi; jim turgan
# eski a'zolar tasdiqlash chaqiruvidan keyin o'zi ko'rinadi.


# Guruhni "faqat tasdiqlaganlar yozadi" holatiga qo'yish.
# Telegramda shaxsiy cheklov guruhning umumiy sozlamasidan ustun turadi, shuning
# uchun umumiy sozlamani yopib, tasdiqlagan har bir odamga alohida ruxsat
# beramiz — bu botning a'zolarni ro'yxatlab ololmasligini ham chetlab o'tadi.
#
# Guruhning o'z sozlamasi qanday bo'lsa, shundayligicha saqlanadi: qulflashdan
# oldin nusxasi olinadi va ruxsat qaytarilganda aynan o'sha tiklanadi.
PERM_FIELDS = [f for f in ChatPermissions.model_fields]

MUTE = ChatPermissions(**{f: False for f in PERM_FIELDS})

# Nusxa olinmagan bo'lsa ishlatiladigan zaxira: faqat matn.
FALLBACK_TALK = ChatPermissions(can_send_messages=True)


def _perms_to_dict(perms) -> dict:
    return {f: getattr(perms, f, None) for f in PERM_FIELDS}


async def saved_permissions() -> ChatPermissions:
    """Qulflashdan oldingi guruh sozlamasi."""
    try:
        stored = await asyncio.to_thread(api_client.get_setting, "group_permissions")
    except api_client.ApiError:
        stored = None
    if not stored:
        return FALLBACK_TALK
    return ChatPermissions(**{k: v for k, v in stored.items() if k in PERM_FIELDS})


async def allow_talking(telegram_id) -> bool:
    """Tasdiqlagan odamga guruhda yozish ruxsatini qaytaradi.

    Guruh qulflanmagan bo'lsa hech narsa qilmaydi — bekorga cheklov qo'ymaslik
    uchun.
    """
    if not config.GROUP_CHAT_ID:
        return False
    try:
        locked = await asyncio.to_thread(api_client.get_setting, "group_locked")
    except api_client.ApiError:
        locked = None
    if not locked:
        return False
    try:
        await bot.restrict_chat_member(config.GROUP_CHAT_ID, int(telegram_id),
                                       permissions=await saved_permissions())
        return True
    except Exception as exc:
        # Guruhda bo'lmasa yoki admin bo'lsa — normal holat, jimgina o'tamiz.
        logger.debug("Yozish ruxsatini berib bo'lmadi (%s): %s", telegram_id, exc)
        return False


def _person_label(user) -> str:
    name = " ".join(filter(None, [user.first_name, user.last_name])).strip()
    return name or (f"@{user.username}" if user.username else str(user.id))


async def _remember(chat_id, user, status="member", source="?"):
    if user is None or getattr(user, "is_bot", False):
        return
    try:
        await asyncio.to_thread(
            api_client.group_seen, chat_id, user.id,
            username=f"@{user.username}" if user.username else "",
            full_name=_person_label(user), status=status, source=source)
    except api_client.ApiError:
        logger.debug("Guruh a'zosini yozib bo'lmadi: %s", user.id)


@dp.chat_member()
async def on_chat_member(update: ChatMemberUpdated):
    """Kirgan/chiqqan a'zoni yozib boradi va yangi kelganga yo'l ko'rsatadi."""
    if str(update.chat.id) != str(config.GROUP_CHAT_ID):
        return
    status = getattr(update.new_chat_member.status, "value", update.new_chat_member.status)
    user = update.new_chat_member.user
    await _remember(update.chat.id, user, status=status, source="chat_member")
    if status not in {"member", "administrator", "creator"}:
        return
    me = await whoami(user.id)
    if me.get("role") != "guest":
        return
    try:
        await bot.send_message(
            user.id,
            "👋 Salom! Siz <b>Acoustic 2026</b> guruhiga qo'shildingiz.\n\n"
            "Guruhda yozish uchun ro'yxatdan o'tganingizni tasdiqlang: "
            "/start bosing va <b>pasport seriya va raqamingizni</b> kiriting.\n"
            "<i>Tasdiqlamaganlar guruhda yoza olmaydi va keyinchalik chiqariladi.</i>")
    except Exception:
        logger.debug("Yangi a'zoga yozib bo'lmadi (botni ochmagan): %s", user.id)


class GroupSilence(BaseMiddleware):
    """Bot guruhda hech qachon javob bermaydi — faqat kuzatadi.

    Bu tekshiruv hamma handlerdan oldin ishlaydi, shuning uchun buyruq bo'ladimi,
    yarim qolgan ro'yxatdan o'tish oqimi bo'ladimi — guruhda hech biri ishlamaydi.
    Bot qaysi guruhga qo'shilgan bo'lsa ham jim turadi.
    """

    async def __call__(self, handler, event, data):
        chat = getattr(event, "chat", None)
        if chat is None or chat.type == "private":
            return await handler(event, data)
        if str(chat.id) == str(config.GROUP_CHAT_ID):
            await _remember(chat.id, event.from_user, status="member", source="message")
            for user in (getattr(event, "new_chat_members", None) or []):
                await _remember(chat.id, user, status="member", source="joined")
        return None  # guruhda hech qanday javob yo'q


dp.message.outer_middleware(GroupSilence())
dp.edited_message.outer_middleware(GroupSilence())


def _audit_lines(audit) -> str:
    inside, outside = audit.get("in_list", []), audit.get("not_in_list", [])
    lines = ["👥 <b>Guruh holati</b>\n",
             f"👁 Bot ko'rgan a'zolar: <b>{len(inside) + len(outside)}</b>",
             f"✅ Ro'yxatda bor va tasdiqlangan: <b>{len(inside)}</b>",
             f"❌ Ro'yxatda yo'q: <b>{len(outside)}</b>"]
    if audit.get("admins_skipped"):
        lines.append(f"🛡 Admin (tegilmaydi): <b>{len(audit['admins_skipped'])}</b>")
    if outside:
        lines.append("\n<b>Ro'yxatda yo'qlar:</b>")
        for s in outside[:40]:
            who = s.get("full_name") or s.get("username") or s["telegram_id"]
            lines.append(f"• {who} {s.get('username') or ''} <code>{s['telegram_id']}</code>")
        if len(outside) > 40:
            lines.append(f"… va yana {len(outside) - 40} ta")
    return "\n".join(lines)


@dp.message(Command("guruh_holat"), F.chat.type == "private")
async def cmd_group_status(message: Message):
    if not _is_admin(message.from_user.id):
        return
    try:
        total = await bot.get_chat_member_count(config.GROUP_CHAT_ID)
    except Exception:
        total = None
    audit = await asyncio.to_thread(api_client.group_audit, config.GROUP_CHAT_ID)
    text = _audit_lines(audit)
    if total is not None:
        seen = len(audit.get("in_list", [])) + len(audit.get("not_in_list", [])) \
            + len(audit.get("admins_skipped", []))
        text += (f"\n\n📊 Guruhda jami: <b>{total}</b> · bot taniydi: <b>{seen}</b>"
                 f" · hali jim: <b>{max(0, total - seen)}</b>")
        text += ("\n<i>Telegram botga a'zolar ro'yxatini bermaydi — jim turganlar "
                 "tasdiqlash chaqiruvidan keyin ko'rinadi.</i>")
    text += ("\n\n/guruh_chaqiruv — tasdiqlashga chaqirish"
             "\n/guruh_qulf — faqat tasdiqlaganlar yozsin"
             "\n/guruh_ochiq — qulfni olib tashlash"
             "\n/guruh_tozala — ro'yxatda yo'qlarni chiqarish")
    for chunk in [text[i:i + 3500] for i in range(0, len(text), 3500)]:
        await message.answer(chunk)


@dp.message(Command("guruh_chaqiruv"), F.chat.type == "private")
async def cmd_group_call(message: Message):
    """Guruhga tasdiqlash chaqiruvini yuboradi."""
    if not _is_admin(message.from_user.id):
        return
    me = await bot.get_me()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="✅ Tasdiqlash — botni ochish", url=f"https://t.me/{me.username}?start=guruh")]])
    try:
        await bot.send_message(
            config.GROUP_CHAT_ID,
            "📋 <b>Ro'yxatni tasdiqlash</b>\n\n"
            "Hurmatli ishtirokchilar! Guruhda faqat safar ro'yxatidagi odamlar qolishi kerak.\n\n"
            "Quyidagi tugmani bosing → botda <b>pasport seriya va raqamingizni</b> kiriting. "
            "Shundan keyin guruhingiz, xonangiz va shaxsiy QR sahifangizni olasiz, "
            "hamda <b>guruhda yozish imkoniyati ochiladi</b>.\n\n"
            "<i>Tasdiqlamaganlar guruhda yoza olmaydi va keyinchalik chiqariladi.</i>",
            reply_markup=kb)
        await message.answer("✅ Chaqiruv guruhga yuborildi.\n\n"
                             "Odamlar tasdiqlagani sayin /guruh_holat da ko'rinib boradi.")
    except Exception as exc:
        await message.answer(f"⚠️ Guruhga yuborib bo'lmadi: <code>{exc}</code>")


async def _group_gap():
    """Botda tasdiqlagan, lekin guruhda yo'q odamlar.

    Yo'l-yo'lakay har bir tekshirilgan odamning holati `group_members` ga
    yoziladi — bot boshqa yo'l bilan ko'ra olmaydigan jim a'zolar ham shu
    tariqa hisobga tushadi.
    """
    people = [p for p in await asyncio.to_thread(api_client.recipients)
              if str(p.get("telegram_id") or "").lstrip("-").isdigit()]
    inside, outside = [], []
    for person in people:
        uid = int(person["telegram_id"])
        try:
            member = await bot.get_chat_member(config.GROUP_CHAT_ID, uid)
            status = getattr(member.status, "value", member.status)
            # Guruh qulflanganda ruxsat berilgan odam `restricted` bo'ladi —
            # u guruhda, faqat yozish huquqi alohida boshqariladi.
            here = (status in broadcast.IN_GROUP_STATUSES
                    or (status == "restricted" and getattr(member, "is_member", False)))
        except Exception:
            status, here = "left", False
        (inside if here else outside).append(person)
        try:
            await asyncio.to_thread(api_client.group_seen, config.GROUP_CHAT_ID, uid,
                                    full_name=person.get("fio") or "",
                                    status=status, source="lookup")
        except api_client.ApiError:
            pass
        await asyncio.sleep(0.05)
    return inside, outside


@dp.message(Command("guruh_taklif"), F.chat.type == "private")
@dp.message(StateFilter(None), F.chat.type == "private", F.text == messaging.BTN_INVITE)
async def cmd_group_invite(message: Message, state: FSMContext):
    """Guruhga qo'shilmagan tasdiqlanganlarga shaxsiy taklif havolasi."""
    await state.clear()
    if not _is_admin(message.from_user.id):
        me = await whoami(message.from_user.id)
        return await show_menu(message.from_user.id, me, "⛔ Bu bo'lim faqat adminlar uchun.")
    me = await bot.get_me()
    rights = await bot.get_chat_member(config.GROUP_CHAT_ID, me.id)
    if not getattr(rights, "can_invite_users", False):
        return await message.answer(
            "⛔ Botda <b>taklif havolasi yaratish</b> huquqi yo'q.\n"
            "Guruh sozlamalari → Administratorlar → @" + (me.username or "bot") +
            " → <b>Foydalanuvchilarni taklif qilish</b> ni yoqing.")

    await message.answer("⏳ Kim guruhda yo'qligi tekshirilmoqda…")
    inside, outside = await _group_gap()
    if not outside:
        return await message.answer(
            f"✅ Tasdiqlaganlarning hammasi guruhda ({len(inside)} kishi).")
    names = "\n".join(f"• {p['fio']}" for p in outside[:30])
    more = f"\n… va yana {len(outside) - 30} ta" if len(outside) > 30 else ""
    await message.answer(
        "➕ <b>Guruhga taklif</b>\n\n"
        f"✅ Guruhda: <b>{len(inside)}</b>\n"
        f"📨 Guruhda yo'q: <b>{len(outside)}</b>\n\n{names}{more}\n\n"
        "Har biriga <b>faqat o'zi uchun</b> amal qiladigan havola yuboriladi. "
        "Davom etamizmi?",
        reply_markup=_confirm_kb("grpinv:go"))


@dp.callback_query(F.data == "grpinv:go")
async def group_invite_go(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    await call.answer("Yuborilmoqda…")
    await call.message.edit_text("⏳ Takliflar yuborilmoqda…")
    _, outside = await _group_gap()
    sent, failed = 0, []
    for person in outside:
        ok = await broadcast.send_group_invite_to(bot, int(person["telegram_id"]),
                                                  person.get("fio") or "")
        if ok:
            sent += 1
        else:
            failed.append(person.get("fio") or person["telegram_id"])
        await asyncio.sleep(broadcast.SEND_DELAY)
    text = (f"➕ <b>Takliflar yuborildi</b>\n\n📨 Yuborildi: <b>{sent}</b>\n"
            f"⚠️ Yetmadi: <b>{len(failed)}</b>")
    if failed:
        text += ("\n\n<i>Botni bloklagan yoki hech qachon ochmaganlar:</i>\n"
                 + "\n".join(f"• {f}" for f in failed[:15]))
    await call.message.edit_text(text + "\n\n/guruh_holat")


@dp.message(Command("guruh_qulf"), F.chat.type == "private")
async def cmd_group_lock(message: Message):
    """Guruhni yopadi: faqat tasdiqlaganlar yoza oladi."""
    if not _is_admin(message.from_user.id):
        return
    me = await bot.get_me()
    rights = await bot.get_chat_member(config.GROUP_CHAT_ID, me.id)
    if not getattr(rights, "can_restrict_members", False):
        return await message.answer(
            "⛔ Botda <b>foydalanuvchilarni bloklash</b> huquqi yo'q — qulflay olmayman.")

    # Avval guruhning hozirgi sozlamasini saqlab qo'yamiz, keyin yopamiz —
    # ruxsat qaytarilganda aynan shu holat tiklanadi.
    try:
        chat = await bot.get_chat(config.GROUP_CHAT_ID)
        if chat.permissions:
            await asyncio.to_thread(api_client.set_setting, "group_permissions",
                                    _perms_to_dict(chat.permissions))
        await bot.set_chat_permissions(config.GROUP_CHAT_ID, permissions=MUTE)
        await asyncio.to_thread(api_client.set_setting, "group_locked", True)
    except Exception as exc:
        return await message.answer(f"⚠️ Qulflab bo'lmadi: <code>{exc}</code>")

    await message.answer("🔒 Guruh yopildi. Tasdiqlaganlarga ruxsat qaytarilmoqda…")
    verified = [p for p in await asyncio.to_thread(api_client.recipients) if p.get("telegram_id")]
    opened = 0
    for person in verified:
        if await allow_talking(person["telegram_id"]):
            opened += 1
        await asyncio.sleep(0.1)
    await message.answer(
        "🔒 <b>Guruh qulflandi</b>\n\n"
        f"✅ Yozish ruxsati berilgan (tasdiqlaganlar): <b>{opened}</b>\n"
        "🔇 Qolganlar tasdiqlamaguncha yoza olmaydi.\n\n"
        "<i>Adminlarga cheklov tegmaydi. Har kim /start bosib pasportini "
        "kiritishi bilan ruxsat o'zi ochiladi — guruhning avvalgi sozlamasi "
        "qanday bo'lsa, shundayligicha qaytariladi.</i>\n\n"
        "/guruh_ochiq — qulfni butunlay olib tashlash")


@dp.message(Command("guruh_ochiq"), F.chat.type == "private")
async def cmd_group_unlock(message: Message):
    """Qulfni bekor qiladi: guruh avvalgi holatiga qaytadi."""
    if not _is_admin(message.from_user.id):
        return
    try:
        await bot.set_chat_permissions(config.GROUP_CHAT_ID,
                                       permissions=await saved_permissions())
        await asyncio.to_thread(api_client.set_setting, "group_locked", False)
        await message.answer("🔓 <b>Qulf olib tashlandi</b> — guruh avvalgi "
                             "sozlamasiga qaytdi, hamma yoza oladi.")
    except Exception as exc:
        await message.answer(f"⚠️ Ochib bo'lmadi: <code>{exc}</code>")


@dp.message(Command("guruh_tozala"), F.chat.type == "private")
async def cmd_group_clean(message: Message):
    if not _is_admin(message.from_user.id):
        return
    audit = await asyncio.to_thread(api_client.group_audit, config.GROUP_CHAT_ID)
    outside = audit.get("not_in_list", [])
    if not outside:
        return await message.answer("✅ Bot ko'rgan a'zolar orasida ro'yxatda yo'qlari yo'q.")
    me = await bot.get_me()
    rights = await bot.get_chat_member(config.GROUP_CHAT_ID, me.id)
    if not getattr(rights, "can_restrict_members", False):
        return await message.answer(
            "⛔ Botda <b>a'zolarni chiqarish huquqi yo'q</b>.\n\n"
            "Guruh sozlamalari → Administratorlar → @" + (me.username or "bot") +
            " → <b>Foydalanuvchilarni bloklash</b> ni yoqing va qaytadan urinib ko'ring.")
    await message.answer(
        _audit_lines(audit) + f"\n\n<b>{len(outside)}</b> kishi guruhdan chiqariladi. Davom etamizmi?",
        reply_markup=_confirm_kb("grpclean:go"))


@dp.callback_query(F.data == "grpclean:go")
async def group_clean_go(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    await call.answer("Chiqarilmoqda…")
    await call.message.edit_text("⏳ Guruh tozalanmoqda…")
    audit = await asyncio.to_thread(api_client.group_audit, config.GROUP_CHAT_ID)
    removed, failed = 0, []
    for person in audit.get("not_in_list", []):
        uid = int(person["telegram_id"])
        try:
            await bot.ban_chat_member(config.GROUP_CHAT_ID, uid)
            # Darhol blokdan chiqaramiz: maqsad chiqarish, umrbod bloklash emas —
            # ro'yxatda ekani aniqlansa qaytadan kira olsin.
            await bot.unban_chat_member(config.GROUP_CHAT_ID, uid, only_if_banned=True)
            await asyncio.to_thread(api_client.group_seen, config.GROUP_CHAT_ID, uid,
                                    status="kicked", source="cleanup")
            removed += 1
        except Exception as exc:
            failed.append(f"{person.get('full_name') or uid}: {exc}")
        await asyncio.sleep(0.2)
    text = (f"🧹 <b>Tozalash yakunlandi</b>\n\n✅ Chiqarildi: <b>{removed}</b>\n"
            f"⚠️ Chiqarib bo'lmadi: <b>{len(failed)}</b>")
    if failed:
        text += "\n\n" + "\n".join(f"• {f}" for f in failed[:10])
    await call.message.edit_text(text + "\n\n/guruh_holat")


# ──────────────────────────── Fallback ────────────────────────────
# Faqat shaxsiy chatga javob beramiz — guruhda bot jim turadi (admin bo'lsa ham).
@dp.message(F.chat.type == "private")
async def fallback(message: Message):
    me = await whoami(message.from_user.id)
    if me.get("role") in {"admin", "leader", "member"}:
        return await show_menu(message.from_user.id, me)
    await message.answer("Boshlash uchun /start buyrug'ini bosing.")


async def main():
    logger.info("Bot ishga tushdi")
    # chat_member yangilanishlari standart holatda kelmaydi — ro'yxatdagi
    # handlerlarga qarab kerakli turlarni aniq so'raymiz.
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
