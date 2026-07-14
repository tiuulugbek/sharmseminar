import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Sharm seminar Flask API — botning asosiy ma'lumot manbasi.
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://sharm.acoustic.uz").rstrip("/")
LEGACY_SHEETS = os.getenv("LEGACY_SHEETS", "false").lower() in {"1", "true", "yes"}

# Adminlar — yangi ro'yxat haqida xabar oladi
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "280034232,108154197,1773950979").split(",")]

# Google Sheets
SHEET_ID = os.getenv("SHEET_ID", "1rOEyFNGXiMcQdKPu2_uagNwWTyEhPdImdeENXa5-UmA")
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", os.path.join(os.path.dirname(__file__), "acoustic-marketing.json"))
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Roʻyxat")

# Google Drive — pasport fayllari yuklanadigan papka
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "1TUG1g7GFJaHbhu4wyK4t6iyTQuQrl-Bb")

# Drive OAuth (acousticzakaz@gmail.com hisobi bilan)
DRIVE_CLIENT_FILE = os.getenv("DRIVE_CLIENT_FILE", os.path.join(os.path.dirname(__file__), "oauth_client.json"))
DRIVE_TOKEN_FILE = os.getenv("DRIVE_TOKEN_FILE", os.path.join(os.path.dirname(__file__), "token.json"))

# Telegram guruh — ro'yxatdan o'tganlarga qo'shilish taklifi yuboriladi.
# GROUP_CHAT_ID: guruhning ID si (masalan -1001234567890). Botni guruhga admin qilib,
# guruhda /id buyrug'ini yuborib, chiqqan raqamni shu yerga (yoki .env ga) qo'ying.
_gid = os.getenv("GROUP_INVITE_CHAT_ID", os.getenv("GROUP_CHAT_ID", "")).strip()
GROUP_CHAT_ID = int(_gid) if _gid.lstrip("-").isdigit() else _gid
# Zaxira: agar shaxsiy havola yaratib bo'lmasa (bot admin emas), shu doimiy havola yuboriladi.
GROUP_INVITE_LINK = os.getenv("GROUP_INVITE_LINK", "")

# Xona variantlari
ROOM_SIZES = ["2 kishilik", "3 kishilik"]

# Misr talabi — minimal yosh
MIN_AGE_ALONE = 21

# Pasport amal qilish muddati: safar (avgust) + 6 oy. Kamida shu sanadan keyin tugashi kerak.
# Kerak bo'lsa shu sanani o'zgartiring.
PASSPORT_MIN_EXPIRY = date(2027, 2, 1)
