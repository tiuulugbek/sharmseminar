#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Acoustic — Sharm seminar boshqaruv paneli (backend).
Flask + SQLite. Admin panel + ishtirokchi sahifasi (/p/<id>).

    pip install -r requirements.txt
    python server.py
    http://SERVER_IP:8000
"""
import os, io, json, sqlite3, datetime, re, hmac, hashlib, secrets
from urllib.parse import parse_qsl
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "data", "sharm.db")
SEED_P   = os.path.join(BASE_DIR, "data", "seed_participants.json")
SEED_PR  = os.path.join(BASE_DIR, "data", "seed_program.json")
SEED_G   = os.path.join(BASE_DIR, "data", "seed_groups.json")
STATIC   = os.path.join(BASE_DIR, "static")

DEFAULT_CHECKPOINTS = [
    {"key": "aeroport",    "label": "Aeroport",    "icon": "\u2708\ufe0f"},
    {"key": "mehmonxona",  "label": "Mehmonxona",  "icon": "\U0001F3E8"},
    {"key": "seminar",     "label": "Seminar",     "icon": "\U0001F3A4"},
    {"key": "ovqatlanish", "label": "Ovqatlanish", "icon": "\U0001F37D\ufe0f"},
]
DEFAULT_ROLES = [
    {"id": "coord", "label": "Koordinator",              "color": "#2E2C6E"},
    {"id": "prep",  "label": "Seminar tayyorgarligi",    "color": "#E4508B"},
    {"id": "logi",  "label": "Transfer / logistika",     "color": "#12B886"},
    {"id": "sea",   "label": "Dengiz / qayiq sayohati",  "color": "#1B9AAA"},
    {"id": "city",  "label": "Shahar aylanish / madaniy","color": "#F07E1A"},
    {"id": "event", "label": "Gala / kechalar",          "color": "#8A63D2"},
]
DEFAULT_META = {"title": "ACOUSTIC 2026", "subtitle": "Xalqaro seminar-trening",
                "dates": "8\u201315 avgust 2026", "location": "Sharm El Sheikh, Misr"}

app = Flask(__name__, static_folder=None)
_db_initialized = False

BOT_COLUMNS = {
    "telegram_id": "TEXT", "telegram_username": "TEXT",
    "category": "TEXT", "passport_series": "TEXT", "passport_number": "TEXT",
    "passport_expiry": "TEXT", "dob": "TEXT", "phone": "TEXT",
    "passport_file_url": "TEXT", "main_series": "TEXT",
    "payment_full": "INTEGER DEFAULT 0", "registered_at": "TEXT",
    "roommate_series": "TEXT",
    # Passport-derived QR token and the remaining spreadsheet fields.
    "token": "TEXT", "passport_issued": "TEXT", "passport_issuer": "TEXT",
    "doc_type": "TEXT", "seminar": "TEXT", "international": "INTEGER DEFAULT 0",
    "izoh": "TEXT",
    # Panelga kirish roli: bo'sh bo'lsa leader/member avtomatik aniqlanadi.
    "panel_role": "TEXT",
    # Ishtirokchining asosiy tili: shaxsiy sahifa shu tilda ochiladi.
    "lang": "TEXT",
}

LANGS = ("uz", "ru", "en")

# Panel rollari, kuchsizdan kuchligiga qarab.
PANEL_ROLES = ["member", "leader", "manager", "admin"]
ROLE_RANK = {name: index for index, name in enumerate(PANEL_ROLES)}

DEFAULT_GROUPS = [{"id": 1, "name": "Sazanchik"}, {"id": 2, "name": "Meduza"},
                  {"id": 3, "name": "Akula"}, {"id": 4, "name": "Delfin"},
                  {"id": 5, "name": "Nemo"}]


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    return con


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS participants(
        id TEXT PRIMARY KEY, fio TEXT, jinsi TEXT, fuqarolik TEXT,
        xona_turi TEXT, xona_guruhi TEXT, kelish TEXT,
        grp INTEGER, leader INTEGER DEFAULT 0,
        room TEXT DEFAULT '', branch TEXT DEFAULT '', telegram TEXT DEFAULT '',
        roles TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS checkins(
        pid TEXT, checkpoint TEXT, ts TEXT, PRIMARY KEY(pid, checkpoint));
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_id TEXT, from_telegram_id TEXT, from_role TEXT,
        to_scope TEXT, to_value TEXT, text TEXT, kind TEXT DEFAULT 'text',
        created_at TEXT, parent_id INTEGER,
        delivered INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, replied INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS msg_targets(
        msg_id INTEGER, pid TEXT, telegram_id TEXT, telegram_msg_id TEXT,
        status TEXT DEFAULT 'pending', PRIMARY KEY(msg_id, pid));
    CREATE INDEX IF NOT EXISTS ix_msg_targets_msg ON msg_targets(msg_id);
    CREATE TABLE IF NOT EXISTS documents(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pid TEXT, kind TEXT, file_name TEXT, stored TEXT, mime TEXT, size INTEGER,
        uploaded_at TEXT, uploaded_by TEXT, sent_at TEXT, sha TEXT);
    CREATE INDEX IF NOT EXISTS ix_documents_pid ON documents(pid);
    CREATE TABLE IF NOT EXISTS group_members(
        chat_id TEXT, telegram_id TEXT, username TEXT, full_name TEXT,
        status TEXT, source TEXT, first_seen TEXT, last_seen TEXT,
        PRIMARY KEY(chat_id, telegram_id));
    """)
    # Idempotent migrations: existing databases keep all rows and values.
    cols = [r["name"] for r in con.execute("PRAGMA table_info(participants)").fetchall()]
    if "roles" not in cols:
        con.execute("ALTER TABLE participants ADD COLUMN roles TEXT DEFAULT '[]'")
        cols.append("roles")
    for name, sql_type in BOT_COLUMNS.items():
        if name not in cols:
            con.execute(f"ALTER TABLE participants ADD COLUMN {name} {sql_type}")
    # Eski bazada `documents.sha` bo'lmasligi mumkin — avval ustun, keyin indeks.
    doc_cols = [r["name"] for r in con.execute("PRAGMA table_info(documents)")]
    if "sha" not in doc_cols:
        con.execute("ALTER TABLE documents ADD COLUMN sha TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS ix_documents_sha ON documents(sha)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_participant_passport "
                "ON participants(passport_series, passport_number) "
                "WHERE passport_series IS NOT NULL AND passport_series<>'' "
                "AND passport_number IS NOT NULL AND passport_number<>''")
    # seed participants
    if con.execute("SELECT COUNT(*) c FROM participants").fetchone()["c"] == 0 and os.path.exists(SEED_P):
        for p in load_json(SEED_P, []):
            con.execute("INSERT INTO participants(id,fio,jinsi,fuqarolik,xona_turi,xona_guruhi,kelish,grp,leader,roles,"
                        "passport_series,passport_number,passport_expiry,dob,phone,telegram_username,telegram_id,category,"
                        "roommate_series,main_series,passport_issued,passport_issuer,doc_type,seminar,izoh)"
                        " VALUES(?,?,?,?,?,?,?,?,?,'[]',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (p["id"], p["fio"], p.get("jinsi"), p.get("fuqarolik"),
                         p.get("xona_turi"), p.get("xona_guruhi"), p.get("kelish"),
                         p.get("group"), 1 if p.get("leader") else 0,
                         p.get("passport_series"), p.get("passport_number"), p.get("passport_expiry"),
                         p.get("dob"), p.get("phone"), p.get("telegram_username"),
                         p.get("telegram_id"), p.get("category"), p.get("roommate_series"),
                         p.get("main_series"), p.get("passport_issued"), p.get("passport_issuer"),
                         p.get("doc_type"), p.get("seminar"), p.get("izoh")))
        print("Seeded participants.")
    # Backfill roommate_series from existing room groups. This is safe to repeat.
    room_groups = con.execute(
        "SELECT DISTINCT xona_guruhi FROM participants "
        "WHERE xona_guruhi IS NOT NULL AND xona_guruhi<>''").fetchall()
    for group_row in room_groups:
        members = con.execute(
            "SELECT id,passport_series,passport_number,roommate_series FROM participants "
            "WHERE xona_guruhi=? ORDER BY id", (group_row["xona_guruhi"],)).fetchall()
        if len(members) < 2:
            continue
        for pos, member in enumerate(members):
            if member["roommate_series"]:
                continue
            other = members[(pos + 1) % len(members)]
            other_passport = f"{other['passport_series'] or ''}{other['passport_number'] or ''}"
            if other_passport:
                con.execute("UPDATE participants SET roommate_series=? WHERE id=?",
                            (other_passport, member["id"]))
    # seed settings
    def seed_setting(k, v):
        if not con.execute("SELECT 1 FROM settings WHERE key=?", (k,)).fetchone():
            con.execute("INSERT INTO settings(key,value) VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
    program = load_json(SEED_PR, [])
    seed_setting("program", program)
    seed_setting("checkpoints", DEFAULT_CHECKPOINTS)
    seed_setting("roles", DEFAULT_ROLES)
    seed_setting("meta", DEFAULT_META)
    seed_setting("groups", load_json(SEED_G, DEFAULT_GROUPS))
    # Group leaders may scan anybody ("all") or only their own group ("group").
    seed_setting("leader_scope", "group")
    seed_setting("copy_member_questions", False)
    ensure_tokens(con)
    con.commit(); con.close()


# ------------------------------------------------------- passport → QR token
def _token_secret(con=None):
    """Secret behind every QR token.

    ``SECRET`` from ``.env`` is authoritative.  When it is missing we fall back
    to a random value persisted in ``settings`` so tokens stay unguessable
    instead of silently becoming a plain hash of the passport number.
    """
    env = os.environ.get("SECRET", "").strip()
    if env:
        return env
    close_after = con is None
    con = con or db()
    try:
        value = sget(con, "token_secret")
        if not value:
            value = secrets.token_hex(32)
            sset(con, "token_secret", value)
            con.commit()
        return value
    finally:
        if close_after:
            con.close()


def make_token(series, number, pid, con=None):
    """token = hmac_sha256(SECRET, passport)[:16]; falls back to the id."""
    passport = f"{series or ''}{number or ''}".upper()
    base = passport or f"ID:{pid}"
    return hmac.new(_token_secret(con).encode(), base.encode(), hashlib.sha256).hexdigest()[:16]


def ensure_tokens(con):
    """Fill in any missing token.  Idempotent: existing tokens are never changed."""
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_participant_token "
                "ON participants(token) WHERE token IS NOT NULL AND token<>''")
    rows = con.execute("SELECT id,passport_series,passport_number,token FROM participants "
                       "WHERE token IS NULL OR token=''").fetchall()
    if not rows:
        return 0
    secret = _token_secret(con)
    taken = {r["token"] for r in con.execute(
        "SELECT token FROM participants WHERE token IS NOT NULL AND token<>''")}
    filled = 0
    for r in rows:
        token = make_token(r["passport_series"], r["passport_number"], r["id"], con)
        if token in taken:  # two people sharing a passport number would collide
            token = hmac.new(secret.encode(), f"{token}:{r['id']}".encode(),
                             hashlib.sha256).hexdigest()[:16]
        taken.add(token)
        con.execute("UPDATE participants SET token=? WHERE id=?", (token, r["id"]))
        filled += 1
    return filled


@app.after_request
def no_html_cache(response):
    """HTML sahifalar keshlanmasin.

    Panel bitta `index.html` ichida yashaydi: brauzer uni keshlab qo'ysa,
    serverda yangilangan kod foydalanuvchiga umuman yetib bormaydi va u eski
    tugmalarni ko'rib turadi.  Rasm va kutubxonalar (`/vendor/…`) keshlanaveradi.
    """
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-cache, must-revalidate, max-age=0"
    return response


@app.before_request
def ensure_database():
    """Initialize/migrate once per process, including under gunicorn."""
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True


def sget(con, key, default=None):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def sset(con, key, value):
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))


def part_dict(r):
    d = dict(r)
    d["leader"] = bool(d["leader"])
    d["group"] = d.pop("grp")
    try: d["roles"] = json.loads(d.get("roles") or "[]")
    except Exception: d["roles"] = []
    return d


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _passport(value):
    """Return normalized (series, number), preserving leading zeroes in number."""
    value = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    m = re.fullmatch(r"([A-Z]*)([0-9]+)", value)
    return (m.group(1), m.group(2)) if m else (value, "")


def _passport_key(series, number=""):
    s, embedded = _passport(f"{series or ''}{number or ''}")
    n = embedded.lstrip("0") or "0" if embedded else ""
    return s, n


def _date_key(value):
    parts = re.split(r"[./-]", str(value or "").strip())
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return ""
    if len(parts[0]) == 4:
        year, month, day = parts
    else:
        day, month, year = parts
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def bot_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        expected = os.environ.get("BOT_API_TOKEN", "")
        supplied = request.headers.get("X-Bot-Token", "")
        if not expected or supplied != expected:
            return jsonify(error="unauthorized"), 401
        return fn(*args, **kwargs)
    return wrapped


def _bot_participant(r):
    p = part_dict(r)
    return {
        "id": p["id"], "fio": p["fio"], "dob": p.get("dob"), "token": p.get("token"),
        "leader": bool(p.get("leader")),
        "group": p.get("group"), "telegram_id": p.get("telegram_id"),
        "telegram_username": p.get("telegram_username"),
        "category": p.get("category"), "passport_series": p.get("passport_series"),
        "passport_number": p.get("passport_number"), "passport_expiry": p.get("passport_expiry"),
        "phone": p.get("phone"), "passport_file_url": p.get("passport_file_url"),
        "xona_turi": p.get("xona_turi"), "xona_guruhi": p.get("xona_guruhi"),
        "room": p.get("room"), "main_series": p.get("main_series"),
        "payment_full": bool(p.get("payment_full")), "roommate_series": p.get("roommate_series"),
    }


# ═══════════════════════ Check-in: bir marta va o'z vaqtida ═══════════════════
# Tadbir Misrda (UTC+3) o'tadi, server esa boshqa mintaqada bo'lishi mumkin.
# Vaqt oynalari va yozilgan soat tadbir vaqtida hisoblanadi.
DEFAULT_TZ_OFFSET = 3


def _event_now(con=None):
    close_after = con is None
    con = con or db()
    try:
        offset = sget(con, "tz_offset", DEFAULT_TZ_OFFSET)
    finally:
        if close_after:
            con.close()
    try:
        offset = float(offset)
    except (TypeError, ValueError):
        offset = DEFAULT_TZ_OFFSET
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=offset)


def _parse_when(value):
    """`YYYY-MM-DDTHH:MM` (tadbir vaqti) -> naive datetime, aks holda None."""
    text = str(value or "").strip().replace(" ", "T")
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def checkin_decision(con, row, cp_key, force=False):
    """Bu odamni shu nuqtada belgilash mumkinmi?

    Qaytadi ``{status, detail, ts, warnings}``:

    ``already``   — allaqachon belgilangan, ikkinchi marta yozilmaydi;
    ``not_open``  — nuqta vaqti hali kelmagan;
    ``closed``    — nuqta vaqti o'tib ketgan;
    ``ok``        — belgilash mumkin.

    Oldingi nuqtalarni o'tkazib yuborish **taqiqlanmaydi**: odam aeroportda
    belgilanmagan bo'lsa ham yahtada belgilanaveradi, o'tkazib yuborilgani
    shunchaki ogohlantirish sifatida qaytadi.
    """
    cps = sget(con, "checkpoints", DEFAULT_CHECKPOINTS)
    order = {c["key"]: i for i, c in enumerate(cps)}
    if cp_key not in order:
        return {"status": "unknown_checkpoint", "warnings": []}

    marks = {r["checkpoint"]: r["ts"] for r in
             con.execute("SELECT checkpoint,ts FROM checkins WHERE pid=?", (row["id"],))}
    if cp_key in marks and not force:
        return {"status": "already", "ts": marks[cp_key], "warnings": []}

    cp = cps[order[cp_key]]
    now = _event_now(con).replace(tzinfo=None)
    if not force:
        starts, ends = _parse_when(cp.get("starts_at")), _parse_when(cp.get("ends_at"))
        if starts and now < starts:
            return {"status": "not_open", "detail": starts.strftime("%d.%m %H:%M"), "warnings": []}
        if ends and now > ends:
            return {"status": "closed", "detail": ends.strftime("%d.%m %H:%M"), "warnings": []}

    missed = [{"key": c["key"], "label": c.get("label")}
              for c in cps[:order[cp_key]] if c["key"] not in marks]
    return {"status": "ok", "warnings": missed}


def _record_checkin(con, pid, cp_key):
    ts = _event_now(con).strftime("%H:%M")
    con.execute("INSERT INTO checkins(pid,checkpoint,ts) VALUES(?,?,?) "
                "ON CONFLICT(pid,checkpoint) DO UPDATE SET ts=excluded.ts", (pid, cp_key, ts))
    return ts


def _checkin_payload(con, row, cp_key, decision, ts):
    marks = {r["checkpoint"]: r["ts"] for r in
             con.execute("SELECT checkpoint,ts FROM checkins WHERE pid=?", (row["id"],))}
    return {
        "ok": decision["status"] == "ok",
        "status": decision["status"],
        "detail": decision.get("detail"),
        "ts": ts or decision.get("ts"),
        "checkpoint": cp_key,
        "warnings": decision.get("warnings", []),
        "checkins": marks,
        "participant": {"id": row["id"], "fio": row["fio"], "group": row["grp"],
                        "group_name": _group_names(con).get(str(row["grp"])),
                        "room": row["room"], "xona_turi": row["xona_turi"],
                        "leader": bool(row["leader"])},
    }


def _resolve(con, key):
    """Look a participant up by ``ACO-xxx`` id **or** by QR token."""
    key = str(key or "").strip()
    if not key:
        return None
    # A scanner may hand us a whole URL — keep only the last path segment.
    key = key.rstrip("/").split("/")[-1].split("?")[0]
    row = con.execute("SELECT * FROM participants WHERE id=?", (key,)).fetchone()
    if row:
        return row
    return con.execute("SELECT * FROM participants WHERE token=? AND token<>''",
                       (key.lower(),)).fetchone()


def _admin_ids():
    raw = os.environ.get("ADMIN_IDS", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def _group_names(con):
    return {str(g.get("id")): g.get("name") or "" for g in sget(con, "groups", DEFAULT_GROUPS)}


def _whoami(con, telegram_id):
    """Resolve a Telegram user into {role, participant}.

    ``admin`` wins over ``leader`` wins over ``member``; anybody unknown to the
    database is a ``guest`` and may only read public pages.
    """
    tid = str(telegram_id or "").strip()
    row = con.execute("SELECT * FROM participants WHERE telegram_id=? AND telegram_id<>''",
                      (tid,)).fetchone() if tid else None
    admins = _admin_ids() | {str(x) for x in sget(con, "admins", []) or []}
    # Panel roli botga ham o'tadi, shunda rollar bitta joyda (bazada) boshqariladi:
    # `admin` va `manager` botda ham hamma guruh bilan ishlaydi.
    panel = panel_role(con, row) if row else None
    if tid and tid in admins:
        role = "admin"
    elif panel in ("admin", "manager"):
        role = "admin"
    elif row and row["leader"]:
        role = "leader"
    elif row:
        role = "member"
    else:
        role = "guest"
    names = _group_names(con)
    group = row["grp"] if row else None
    return {
        "role": role,
        "telegram_id": tid,
        "id": row["id"] if row else None,
        "fio": row["fio"] if row else None,
        "token": row["token"] if row else None,
        "group": group,
        "group_name": names.get(str(group)) if group else None,
        "leader_scope": sget(con, "leader_scope", "group"),
    }


def _may_checkin(con, actor, target_row):
    """Only an admin, or a leader over their own group, may check somebody in."""
    if actor["role"] == "admin":
        return True
    if actor["role"] != "leader":
        return False
    if sget(con, "leader_scope", "group") == "all":
        return True
    return bool(actor["group"]) and target_row["grp"] == actor["group"]


def _label(con, row, names=None):
    """`ACO-042 · Aziz Karimov (Sazanchik)` — who a message came from."""
    names = names if names is not None else _group_names(con)
    group = names.get(str(row["grp"])) if row["grp"] else None
    suffix = f" ({group})" if group else ""
    return f"{row['id']} · {row['fio']}{suffix}"


# ══════════════════════ Panelga kirish (maxfiy kod = pasport) ══════════════════
SESSION_COOKIE = "sharm_session"
SESSION_MAX_AGE = 30 * 24 * 3600          # 30 kun
LOGIN_WINDOW = 600                        # 10 daqiqa
LOGIN_MAX_FAILURES = 12                   # shu oynada bir IP dan
_login_failures = {}                      # {ip: [timestamp, ...]} — jarayon ichida


def _code_variants(series, number):
    """Bir ishtirokchini topish mumkin bo'lgan kod ko'rinishlari.

    Odam beyjigidagi pasportni turlicha ko'chiradi: to'liq (``FA1177095``),
    faqat raqam (``1177095``) yoki boshidagi nollarsiz.  Hammasini qabul
    qilamiz, lekin faqat bitta odamga to'g'ri kelsa.
    """
    series = re.sub(r"[^A-Z0-9]", "", str(series or "").upper())
    number = re.sub(r"[^0-9]", "", str(number or ""))
    short = number.lstrip("0")
    out = {series + number, number}
    if short:
        out |= {series + short, short}
    return {v for v in out if v}


def _find_by_code(con, code):
    """Maxfiy kod bo'yicha ishtirokchi. ``(row, error)`` qaytaradi."""
    code = re.sub(r"[^A-Z0-9]", "", str(code or "").upper())
    if len(code) < 5:
        return None, "too_short"
    matches = [r for r in con.execute("SELECT * FROM participants")
               if code in _code_variants(r["passport_series"], r["passport_number"])]
    if not matches:
        return None, "not_found"
    if len(matches) > 1:
        # Faqat raqam bir nechta odamga to'g'ri keldi — seriyasi bilan yozsin.
        exact = [r for r in matches
                 if code == re.sub(r"[^A-Z0-9]", "", (r["passport_series"] or "").upper())
                 + re.sub(r"[^0-9]", "", r["passport_number"] or "")]
        if len(exact) != 1:
            return None, "ambiguous"
        matches = exact
    return matches[0], None


def _panel_admin_ids():
    raw = os.environ.get("PANEL_ADMIN_IDS", "")
    return {x.strip().upper() for x in raw.split(",") if x.strip()}


def panel_role(con, row):
    """Panel roli.

    Ustuvorlik: `PANEL_ADMIN_IDS` → botning `ADMIN_IDS` idagi Telegram hisobi →
    `panel_role` ustuni → guruh mas'uli → oddiy ishtirokchi.  Botda admin bo'lgan
    odam panelda ham admin bo'ladi, alohida ro'yxat yuritish shart emas.
    """
    if row["id"].upper() in _panel_admin_ids():
        return "admin"
    if row["telegram_id"] and str(row["telegram_id"]) in _admin_ids():
        return "admin"
    explicit = str(row["panel_role"] or "").strip().lower()
    if explicit in ROLE_RANK:
        return explicit
    return "leader" if row["leader"] else "member"


def _sign_session(pid, role):
    issued = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    payload = f"{pid}|{role}|{issued}"
    signature = hmac.new(_token_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}|{signature}"


def _read_session(raw):
    """Imzolangan cookie'ni ochadi; soxta yoki eskirgan bo'lsa ``None``."""
    try:
        pid, role, issued, signature = str(raw or "").split("|")
    except ValueError:
        return None
    payload = f"{pid}|{role}|{issued}"
    expected = hmac.new(_token_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        age = datetime.datetime.now(datetime.timezone.utc).timestamp() - int(issued)
    except ValueError:
        return None
    if age > SESSION_MAX_AGE:
        return None
    return {"id": pid, "role": role}


def current_session(con=None):
    """Joriy foydalanuvchi ``{id, role, fio, group, group_name}`` yoki ``None``.

    Rol har so'rovda bazadan qayta o'qiladi — rol o'zgarsa cookie'ni kutib
    o'tirmaydi.
    """
    session = _read_session(request.cookies.get(SESSION_COOKIE))
    if not session:
        return None
    close_after = con is None
    con = con or db()
    try:
        row = con.execute("SELECT * FROM participants WHERE id=?", (session["id"],)).fetchone()
        if not row:
            return None
        role = panel_role(con, row)
        return {"id": row["id"], "role": role, "fio": row["fio"], "group": row["grp"],
                "group_name": _group_names(con).get(str(row["grp"])) if row["grp"] else None,
                "token": row["token"], "rank": ROLE_RANK[role]}
    finally:
        if close_after:
            con.close()


def panel_auth(minimum="member"):
    """Panel endpointlarini rol bo'yicha yopadi."""
    def wrapper(fn):
        @wraps(fn)
        def guarded(*args, **kwargs):
            user = current_session()
            if not user:
                return jsonify(error="auth_required"), 401
            if user["rank"] < ROLE_RANK[minimum]:
                return jsonify(error="forbidden", role=user["role"]), 403
            request.panel_user = user
            return fn(*args, **kwargs)
        return guarded
    return wrapper


def _throttled(ip):
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    attempts = [t for t in _login_failures.get(ip, []) if now - t < LOGIN_WINDOW]
    _login_failures[ip] = attempts
    return len(attempts) >= LOGIN_MAX_FAILURES


def _note_failure(ip):
    _login_failures.setdefault(ip, []).append(
        datetime.datetime.now(datetime.timezone.utc).timestamp())


@app.post("/api/auth/login")
def auth_login():
    ip = request.headers.get("X-Real-IP") or request.remote_addr or "?"
    if _throttled(ip):
        return jsonify(error="too_many_attempts"), 429
    code = (request.get_json(silent=True) or {}).get("code")
    con = db()
    row, error = _find_by_code(con, code)
    if error:
        con.close()
        _note_failure(ip)
        return jsonify(error=error), 401 if error != "ambiguous" else 409
    role = panel_role(con, row)
    con.close()
    response = jsonify(ok=True, user={"id": row["id"], "fio": row["fio"], "role": role})
    response.set_cookie(SESSION_COOKIE, _sign_session(row["id"], role),
                        max_age=SESSION_MAX_AGE, httponly=True, samesite="Lax",
                        secure=request.headers.get("X-Forwarded-Proto") == "https")
    return response


@app.post("/api/auth/logout")
def auth_logout():
    response = jsonify(ok=True)
    response.delete_cookie(SESSION_COOKIE)
    return response


def _build_stamp():
    """Panel fayli qachon yangilangani — brauzerdagi nusxa eskiligini bilish uchun."""
    try:
        mtime = os.path.getmtime(os.path.join(STATIC, "index.html"))
    except OSError:
        return "?"
    return datetime.datetime.fromtimestamp(mtime).strftime("%d.%m %H:%M")


@app.get("/api/auth/me")
def auth_me():
    user = current_session()
    if not user:
        return jsonify(error="auth_required"), 401
    return jsonify(user=user)


# ═══════════════════ Guruh o'zgarganda ishtirokchiga xabar ═══════════════════
GROUP_CHANGED = {
    "uz": ("👥 <b>Guruhingiz o'zgartirildi</b>\n\nYangi guruh: <b>{group}</b>\n"
           "Guruh mas'uli: <b>{leader}</b>\n\nShaxsiy sahifangiz va QR kodingiz "
           "<b>o'zgarmadi</b> — eski QR ishlashda davom etadi."),
    "ru": ("👥 <b>Ваша группа изменена</b>\n\nНовая группа: <b>{group}</b>\n"
           "Староста группы: <b>{leader}</b>\n\nВаша личная страница и QR-код "
           "<b>не изменились</b> — старый QR продолжает работать."),
    "en": ("👥 <b>Your group has changed</b>\n\nNew group: <b>{group}</b>\n"
           "Group leader: <b>{leader}</b>\n\nYour personal page and QR code are "
           "<b>unchanged</b> — the old QR still works."),
}


def _telegram_send(chat_id, text):
    """Telegramga bitta xabar. Bot alohida jarayon, shuning uchun to'g'ridan-to'g'ri API."""
    token = os.environ.get("BOT_TOKEN", "")
    if not token or not chat_id:
        return
    import urllib.request
    payload = json.dumps({"chat_id": str(chat_id), "text": text,
                          "parse_mode": "HTML"}).encode()
    request_obj = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request_obj, timeout=10).read()
    except Exception as exc:  # xabar yetmasa ham panel ishlashda davom etsin
        app.logger.warning("Guruh xabari yuborilmadi (%s): %s", chat_id, exc)


def notify_group_change(pid, new_group):
    """Guruhi o'zgargan odamga DM yuboradi — fonда, panelni kutkazmasdan."""
    con = db()
    try:
        row = con.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
        if not row or not row["telegram_id"]:
            return
        leader = con.execute("SELECT fio FROM participants WHERE grp=? AND leader=1",
                             (new_group,)).fetchone()
        name = _group_names(con).get(str(new_group)) or ""
        label = f"{new_group}-guruh" + (f" · {name}" if name else "")
        lang = (row["lang"] or "uz").lower()
        text = GROUP_CHANGED.get(lang, GROUP_CHANGED["uz"]).format(
            group=label, leader=leader["fio"] if leader else "—")
        chat_id = row["telegram_id"]
    finally:
        con.close()
    import threading
    threading.Thread(target=_telegram_send, args=(chat_id, text), daemon=True).start()


ROOM_CHANGED = {
    "uz": ("🛏 <b>Xonangiz o'zgartirildi</b>\n\n{room}{mates}\n\nShaxsiy sahifangiz va "
           "QR kodingiz <b>o'zgarmadi</b>."),
    "ru": ("🛏 <b>Ваш номер изменён</b>\n\n{room}{mates}\n\nВаша личная страница и "
           "QR-код <b>не изменились</b>."),
    "en": ("🛏 <b>Your room has changed</b>\n\n{room}{mates}\n\nYour personal page and "
           "QR code are <b>unchanged</b>."),
}
ROOMMATES_LABEL = {"uz": "🤝 Xonadoshlaringiz: ", "ru": "🤝 Ваши соседи: ",
                   "en": "🤝 Your roommates: "}


def notify_room_change(pid):
    """Xonasi almashgan odamga DM — kim bilan turishini bilib qo'ysin."""
    con = db()
    try:
        row = con.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
        if not row or not row["telegram_id"]:
            return
        mates = [r["fio"] for r in con.execute(
            "SELECT fio FROM participants WHERE xona_guruhi=? AND xona_guruhi<>'' AND id<>?",
            (row["xona_guruhi"] or "", pid))]
        lang = (row["lang"] or "uz").lower()
        room = row["room"] or row["xona_guruhi"] or "—"
        text = ROOM_CHANGED.get(lang, ROOM_CHANGED["uz"]).format(
            room=f"🛏 <b>{room}</b>" + (f" · {row['xona_turi']}" if row["xona_turi"] else ""),
            mates=("\n" + ROOMMATES_LABEL.get(lang, ROOMMATES_LABEL["uz"])
                   + "<b>" + ", ".join(mates) + "</b>") if mates else "")
        chat_id = row["telegram_id"]
    finally:
        con.close()
    import threading
    threading.Thread(target=_telegram_send, args=(chat_id, text), daemon=True).start()


def _find_by_passport(con, value):
    series, number = _passport(value)
    key = number.lstrip("0") or "0"
    rows = con.execute("SELECT * FROM participants WHERE UPPER(COALESCE(passport_series,''))=?",
                       (series,)).fetchall()
    return [r for r in rows if ((r["passport_number"] or "").lstrip("0") or "0") == key]


# ═══════════════════════ Hujjatlar (voucher, ticket) ═══════════════════════
# Fayllar diskda `data/docs/<ACO-id>/` ostida yotadi, bazada faqat yozuvi.
# Har bir hujjat bitta odamga tegishli — bot uni faqat egasiga yuboradi.
DOCS_DIR = os.path.join(BASE_DIR, "data", "docs")
DOCS_FILES = os.path.join(DOCS_DIR, "_files")
DOC_KINDS = ("voucher", "ticket", "other")
MAX_DOC_BYTES = 20 * 1024 * 1024


def _safe_name(name):
    name = os.path.basename(str(name or "fayl"))
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "fayl"
    return name[:120]


VOUCHER_WORDS = ("voucher", "vaucher", "vouch", "hotel", "mehmonxona", "ваучер", "отель")
TICKET_WORDS = ("ticket", "tiket", "avia", "flight", "bilet", "chipta", "билет", "рейс",
                "boarding", "passenger", "reservation number")


def guess_kind(text):
    """Matndan turini taxmin qiladi; topilmasa `other`."""
    low = str(text or "").lower()
    if any(w in low for w in VOUCHER_WORDS):
        return "voucher"
    if any(w in low for w in TICKET_WORDS):
        return "ticket"
    return "other"


def kind_from_pdf(blob, limit=2):
    """Hujjat turini PDF ning o'z matnidan aniqlaydi.

    Fayl nomida "voucher"/"ticket" yozilmagan bo'lishi mumkin, lekin hujjatning
    ichida deyarli har doim yozilgan bo'ladi.
    """
    if not blob or blob[:5] != b"%PDF-":
        return "other"
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(blob))
        text = " ".join((page.extract_text() or "") for page in reader.pages[:limit])
    except Exception:
        return "other"
    return guess_kind(text)


def match_participants(con, text):
    """Matndan (fayl nomi yoki izoh) egalarini topadi.

    Bitta voucherda ikki-uch kishining ismi bo'lishi mumkin, shuning uchun
    **ro'yxat** qaytadi.  Qidiruv tartibi: ACO raqamlari → pasportlar → ism.
    Ism bo'yicha faqat to'liq (ism va familya) mos kelgani olinadi, shunda
    "Siddikov" degan bir so'z uch kishiga tegib ketmaydi.
    """
    # Matn fayl nomi ham, izoh ham bo'lishi mumkin — kengaytmani (".pdf")
    # olib tashlaymiz, qolganiga tegmaymiz.
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}(?=\s|$)", " ", str(text or ""))
    upper = stem.upper()
    found, seen = [], set()

    def add(pid):
        if pid and pid not in seen:
            seen.add(pid)
            found.append(pid)

    for number in re.findall(r"ACO[-_ ]?(\d{1,4})", upper):
        pid = f"ACO-{int(number):03d}"
        if con.execute("SELECT 1 FROM participants WHERE id=?", (pid,)).fetchone():
            add(pid)

    digits = re.sub(r"[^A-Z0-9]", "", upper)
    for row in con.execute("SELECT id,passport_series,passport_number FROM participants "
                           "WHERE passport_number IS NOT NULL AND passport_number<>''"):
        full = f"{row['passport_series'] or ''}{row['passport_number'] or ''}".upper()
        if full and full in digits:
            add(row["id"])

    # Ism bo'yicha: odamning barcha ism so'zlari matnda uchrasa — o'shaniki.
    # Bitta faylda bir necha kishi bo'lsa, hammasi topiladi.
    words = {w.lower() for w in re.split(r"[^A-Za-zА-Яа-яЎўҚқҒғҲҳ]+", stem) if len(w) > 2}
    if len(words) >= 2:
        for row in con.execute("SELECT id,fio FROM participants"):
            parts = {w.lower() for w in str(row["fio"]).split() if len(w) > 2}
            if len(parts) >= 2 and parts <= words:
                add(row["id"])
    return found


# ─────────────── PDF ichidan ismlarni o'qib, sahifalarga bo'lish ───────────────
def _name_index(con):
    """{ishtirokchi id: ism so'zlari to'plami} — matn ichidan qidirish uchun."""
    index = {}
    for row in con.execute("SELECT id,fio FROM participants"):
        words = {w.lower() for w in re.split(r"[^\wА-Яа-яЎўҚқҒғҲҳ]+", str(row["fio"] or ""))
                 if len(w) > 2}
        if len(words) >= 2:
            index[row["id"]] = words
    return index


def _people_in_text(index, text):
    """Matnda to'liq ism-familyasi uchragan ishtirokchilar."""
    words = {w.lower() for w in re.split(r"[^\wА-Яа-яЎўҚқҒғҲҳ]+", text or "") if len(w) > 2}
    return [pid for pid, name in index.items() if name <= words]


def split_pdf_by_participants(con, blob):
    """PDF ni egalari bo'yicha bo'lakларга ajratadi.

    Qaytadi: ``[(baytlar, [pid, ...], sahifalar_soni), ...]``.

    Har sahifadagi matndan ismlar o'qiladi.  Ketma-ket sahifalar bir xil
    odam(lar)ga tegishli bo'lsa — bitta bo'lakka birlashadi, ya'ni ikki
    sahifali voucher bo'linib ketmaydi.  Bir necha odam bitta sahifada bo'lsa
    (xonadoshlar) — bo'lak hammasiga biriktiriladi.

    Matni yo'q (skanerlangan) PDF da hech kim topilmaydi — ``None`` qaytadi va
    yuklash fayl nomi/izohiga qaytadi.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return None
    try:
        reader = PdfReader(io.BytesIO(blob))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception:
        return None
    if not pages:
        return None

    index = _name_index(con)
    per_page = [tuple(sorted(_people_in_text(index, text))) for text in pages]
    if not any(per_page):
        return None

    # Ismi topilmagan sahifa oldingisiga qo'shiladi — ko'p sahifali hujjatning
    # davomi bo'lishi mumkin.
    runs, current, page_numbers = [], None, []
    for number, owners in enumerate(per_page):
        if owners and owners != current:
            if current:
                runs.append((current, page_numbers))
            current, page_numbers = owners, [number]
        else:
            if current is None:
                continue          # boshidagi ismsiz sahifalarni tashlab ketamiz
            page_numbers.append(number)
    if current:
        runs.append((current, page_numbers))
    if not runs:
        return None

    # Butun hujjat bitta odam(lar)ga tegishli bo'lsa — bo'lish shart emas.
    if len(runs) == 1 and len(runs[0][1]) == len(pages):
        return [(blob, list(runs[0][0]), len(pages))]

    out = []
    for owners, numbers in runs:
        writer = PdfWriter()
        for number in numbers:
            writer.add_page(reader.pages[number])
        buffer = io.BytesIO()
        writer.write(buffer)
        out.append((buffer.getvalue(), list(owners), len(numbers)))
    return out


def _doc_row(r, con=None):
    return {"id": r["id"], "pid": r["pid"], "kind": r["kind"], "file_name": r["file_name"],
            "size": r["size"], "uploaded_at": r["uploaded_at"], "sent_at": r["sent_at"]}


def docs_released(con, kind):
    """Shu turdagi hujjatlarni tarqatish mumkinmi?

    `docs_hold` yoqilgan bo'lsa hech nima chiqmaydi — fayllarni yuklab
    bo'lgunicha ushlab turish uchun.  Aks holda tur bo'yicha qo'yilgan vaqtga
    qaraladi; vaqt qo'yilmagan bo'lsa darrov tayyor.
    """
    if sget(con, "docs_hold", False):
        return False
    plan = sget(con, "docs_release", {}) or {}
    when = _parse_when(plan.get(kind) or plan.get("all"))
    if not when:
        return True
    return _event_now(con).replace(tzinfo=None) >= when


@app.post("/api/docs/upload")
@panel_auth("manager")
def docs_upload():
    """Fayllarni yuklaydi va egalariga biriktiradi.

    Bitta faylda bir necha kishining ismi bo'lishi mumkin (masalan uch kishilik
    xona voucheri) — fayl diskda **bir marta** saqlanadi, har bir egasiga esa
    alohida yozuv ochiladi, shunda bot har biriga o'z nusxasini yuboradi va kim
    olganini alohida belgilaydi.

    `pids` (yoki `pid`) berilsa egalari majburan shular; aks holda fayl nomidan
    va `hint` matnidan topiladi.  Hech kim topilmasa fayl saqlanmaydi.
    """
    forced = [x.strip().upper() for x in
              re.split(r"[,\s]+", request.form.get("pids") or request.form.get("pid") or "")
              if x.strip()]
    hint = str(request.form.get("hint") or "")
    kind_hint = str(request.form.get("kind") or "").strip().lower()
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify(error="no_files"), 400

    con = db()
    os.makedirs(DOCS_FILES, exist_ok=True)
    saved, unmatched, split_note, duplicates = [], [], [], []

    def store(blob, name, kind, owners, mime):
        digest = hashlib.sha256(blob).hexdigest()
        # Ayni shu fayl shu odamda allaqachon bo'lsa — qayta yozmaymiz.
        fresh = [pid for pid in owners
                 if not con.execute("SELECT 1 FROM documents WHERE pid=? AND sha=?",
                                    (pid, digest)).fetchone()]
        for pid in set(owners) - set(fresh):
            duplicates.append({"pid": pid, "file_name": _safe_name(name)})
        if not fresh:
            return
        # Fayl diskda bo'lishi mumkin (boshqa odam uchun saqlangan).
        row = con.execute("SELECT stored FROM documents WHERE sha=? LIMIT 1", (digest,)).fetchone()
        stored = row["stored"] if row else f"{secrets.token_hex(6)}_{_safe_name(name)}"
        if not row:
            with open(os.path.join(DOCS_FILES, stored), "wb") as fh:
                fh.write(blob)
        for pid in fresh:
            cur = con.execute(
                "INSERT INTO documents(pid,kind,file_name,stored,mime,size,uploaded_at,"
                "uploaded_by,sha) VALUES(?,?,?,?,?,?,?,?,?)",
                (pid, kind, _safe_name(name), stored, mime, len(blob), _now(),
                 request.panel_user["id"], digest))
            saved.append({"id": cur.lastrowid, "pid": pid, "kind": kind,
                          "file_name": _safe_name(name)})

    for storage in files:
        name = storage.filename or "fayl"
        blob = storage.read()
        if len(blob) > MAX_DOC_BYTES:
            unmatched.append(f"{name} (juda katta)")
            continue
        mime = storage.mimetype or ""
        kind = kind_hint if kind_hint in DOC_KINDS else guess_kind(name + " " + hint)
        if kind == "other":
            # Nomda yozilmagan bo'lsa hujjatning o'zidan o'qiymiz.
            kind = kind_from_pdf(blob)

        if forced:
            owners = [p for p in forced
                      if con.execute("SELECT 1 FROM participants WHERE id=?", (p,)).fetchone()]
            if owners:
                store(blob, name, kind, owners, mime)
            else:
                unmatched.append(name)
            continue

        # Avval FAYL NOMI va izoh: odam ataylab yozgan nom eng ishonchli manba.
        owners = match_participants(con, name + " " + hint)
        owners = [p for p in owners
                  if con.execute("SELECT 1 FROM participants WHERE id=?", (p,)).fetchone()]
        if owners:
            store(blob, name, kind, owners, mime)
            continue

        # Nom hech kimni ko'rsatmasa — PDF ichidagi matndan qidiramiz va
        # kerak bo'lsa sahifalarga ajratamiz.
        parts = split_pdf_by_participants(con, blob) if blob[:5] == b"%PDF-" else None
        if parts:
            base = os.path.splitext(_safe_name(name))[0]
            for index, (chunk, chunk_owners, _pages) in enumerate(parts, 1):
                piece = name if len(parts) == 1 else f"{base}_{index}.pdf"
                store(chunk, piece, kind, chunk_owners, "application/pdf")
            if len(parts) > 1:
                split_note.append({"file": name, "parts": len(parts)})
            continue

        unmatched.append(name)

    con.commit(); con.close()
    return jsonify(ok=True, saved=saved, unmatched=unmatched, split=split_note,
                   duplicates=duplicates)


@app.get("/api/docs")
@panel_auth("member")
def docs_list():
    """Hujjatlar ro'yxati. A'zo faqat o'zinikini, mas'ul o'z guruhinikini ko'radi."""
    user = request.panel_user
    con = db()
    allowed = {r["id"] for r in _scope_rows(con, user)}
    pid = str(request.args.get("pid") or "").strip().upper()
    if pid:
        rows = con.execute("SELECT * FROM documents WHERE pid=? ORDER BY kind,id", (pid,)).fetchall()
    else:
        rows = con.execute("SELECT * FROM documents ORDER BY pid,kind,id").fetchall()
    out = [_doc_row(r) for r in rows if r["pid"] in allowed]
    release = sget(con, "docs_release", {}) or {}
    hold = bool(sget(con, "docs_hold", False))
    con.close()
    return jsonify(documents=out, release=release, hold=hold)


@app.get("/api/docs/file/<int:doc_id>")
@panel_auth("member")
def docs_download(doc_id):
    user = request.panel_user
    con = db()
    row = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    allowed = {r["id"] for r in _scope_rows(con, user)} if row else set()
    con.close()
    if not row or row["pid"] not in allowed:
        abort(404)
    return send_from_directory(DOCS_FILES, row["stored"],
                               as_attachment=True, download_name=row["file_name"])


@app.post("/api/docs/delete")
@panel_auth("manager")
def docs_delete():
    doc_id = (request.get_json(silent=True) or {}).get("id")
    con = db()
    row = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        con.close()
        return jsonify(error="not_found"), 404
    con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    con.commit()
    # Fayl bir necha odamga tegishli bo'lishi mumkin — oxirgi yozuv ketgandagina
    # diskdan o'chiramiz.
    others = con.execute("SELECT 1 FROM documents WHERE stored=?", (row["stored"],)).fetchone()
    con.close()
    if not others:
        try:
            os.remove(os.path.join(DOCS_FILES, row["stored"]))
        except OSError:
            pass
    return jsonify(ok=True)


@app.post("/api/docs/assign")
@panel_auth("manager")
def docs_assign():
    """Hujjatning egasini yoki turini o'zgartirish."""
    d = request.get_json(silent=True) or {}
    con = db()
    row = con.execute("SELECT * FROM documents WHERE id=?", (d.get("id"),)).fetchone()
    if not row:
        con.close()
        return jsonify(error="not_found"), 404
    pid = str(d.get("pid") or row["pid"]).strip().upper()
    kind = str(d.get("kind") or row["kind"]).lower()
    if kind not in DOC_KINDS:
        kind = row["kind"]
    if not con.execute("SELECT 1 FROM participants WHERE id=?", (pid,)).fetchone():
        con.close()
        return jsonify(error="participant_not_found"), 404
    # Egasi o'zgarsa yuborilgan belgisi tushadi; faqat turi o'zgarsa —
    # allaqachon olgan odamga qayta yubormaymiz.
    if pid != row["pid"]:
        con.execute("UPDATE documents SET pid=?,kind=?,sent_at=NULL WHERE id=?",
                    (pid, kind, row["id"]))
    else:
        con.execute("UPDATE documents SET kind=? WHERE id=?", (kind, row["id"]))
    con.commit(); con.close()
    return jsonify(ok=True)


@app.post("/api/docs/share")
@panel_auth("manager")
def docs_share():
    """Mavjud faylni yana bir necha odamga biriktiradi.

    Bir voucherda ikki-uch kishi bo'lsa, fayl qayta yuklanmaydi — shu yozuvdan
    nusxa olinadi va har kimga o'zi yuboriladi.
    """
    d = request.get_json(silent=True) or {}
    con = db()
    row = con.execute("SELECT * FROM documents WHERE id=?", (d.get("id"),)).fetchone()
    if not row:
        con.close()
        return jsonify(error="not_found"), 404
    wanted = d.get("pids") or []
    if isinstance(wanted, str):
        wanted = re.split(r"[,\s]+", wanted)
    added, skipped = [], []
    for pid in [str(x).strip().upper() for x in wanted if str(x).strip()]:
        if not con.execute("SELECT 1 FROM participants WHERE id=?", (pid,)).fetchone():
            skipped.append(pid)
            continue
        if con.execute("SELECT 1 FROM documents WHERE stored=? AND pid=?",
                       (row["stored"], pid)).fetchone():
            continue
        con.execute("INSERT INTO documents(pid,kind,file_name,stored,mime,size,uploaded_at,"
                    "uploaded_by) VALUES(?,?,?,?,?,?,?,?)",
                    (pid, row["kind"], row["file_name"], row["stored"], row["mime"],
                     row["size"], _now(), request.panel_user["id"]))
        added.append(pid)
    con.commit(); con.close()
    return jsonify(ok=True, added=added, skipped=skipped)


@app.post("/api/docs/release")
@panel_auth("admin")
def docs_release_set():
    """Qaysi turdagi hujjat qachondan boshlab tarqatilishi (va ushlab turish)."""
    payload = request.get_json(silent=True) or {}
    con = db()
    if "hold" in payload:
        sset(con, "docs_hold", bool(payload["hold"]))
    if "release" in payload:
        plan = payload.get("release") or {}
        clean = {k: str(v or "").strip() for k, v in plan.items() if k in DOC_KINDS + ("all",)}
        sset(con, "docs_release", clean)
    out = {"release": sget(con, "docs_release", {}) or {}, "hold": bool(sget(con, "docs_hold", False))}
    con.commit(); con.close()
    return jsonify(ok=True, **out)


@app.get("/api/bot/docs/state")
@bot_auth
def bot_docs_state():
    """Hujjatlar bo'yicha umumiy holat — botdagi hisobot uchun."""
    con = db()
    people = {r["id"]: r for r in con.execute("SELECT id,fio,grp,leader,telegram_id FROM participants")}
    kinds = {}
    for r in con.execute("SELECT pid,kind FROM documents"):
        kinds.setdefault(r["pid"], set()).add(r["kind"])
    both = [p for p in people if {"voucher", "ticket"} <= kinds.get(p, set())]
    only_v = [p for p in people if kinds.get(p, set()) == {"voucher"}]
    only_t = [p for p in people if kinds.get(p, set()) == {"ticket"}]
    none_ = [p for p in people if p not in kinds]
    files = con.execute("SELECT COUNT(*) c, COUNT(DISTINCT stored) f FROM documents").fetchone()
    unsent = con.execute("SELECT COUNT(*) c FROM documents WHERE sent_at IS NULL").fetchone()["c"]
    out = {
        "hold": bool(sget(con, "docs_hold", False)),
        "release": sget(con, "docs_release", {}) or {},
        "rows": files["c"], "files": files["f"], "unsent": unsent,
        "both": len(both), "only_voucher": len(only_v), "only_ticket": len(only_t),
        "none": [{"id": p, "fio": people[p]["fio"], "group": people[p]["grp"]} for p in none_],
        "missing_ticket": [{"id": p, "fio": people[p]["fio"], "group": people[p]["grp"],
                            "leader": bool(people[p]["leader"])} for p in only_v],
        "missing_voucher": [{"id": p, "fio": people[p]["fio"], "group": people[p]["grp"]}
                            for p in only_t],
    }
    con.close()
    return jsonify(out)


@app.post("/api/bot/docs/hold")
@bot_auth
def bot_docs_hold():
    con = db()
    sset(con, "docs_hold", bool((request.get_json(silent=True) or {}).get("hold")))
    hold = bool(sget(con, "docs_hold", False))
    con.commit(); con.close()
    return jsonify(ok=True, hold=hold)


# ---------------------------------------------------------------- admin API
# Pasport ma'lumoti — faqat rahbar/adminlarga va odamning o'ziga.
PASSPORT_FIELDS = ("passport_series", "passport_number", "passport_expiry",
                   "passport_issued", "passport_issuer", "doc_type",
                   "passport_file_url", "dob", "phone", "telegram_id",
                   "telegram_username", "main_series", "roommate_series")


def _visible(participant, user):
    """Bir ishtirokchi yozuvidan foydalanuvchi ko'rishi mumkin bo'lgan qismi."""
    if user["rank"] >= ROLE_RANK["manager"] or participant["id"] == user["id"]:
        return participant
    return {k: v for k, v in participant.items() if k not in PASSPORT_FIELDS}


def _scope_rows(con, user):
    """Rol qamrovidagi ishtirokchilar: admin/rahbar — hammasi, guruh mas'uli —
    o'z guruhi, oddiy ishtirokchi — faqat o'zi."""
    if user["rank"] >= ROLE_RANK["manager"]:
        return con.execute("SELECT * FROM participants").fetchall()
    if user["role"] == "leader" and user["group"]:
        return con.execute("SELECT * FROM participants WHERE grp=?", (user["group"],)).fetchall()
    return con.execute("SELECT * FROM participants WHERE id=?", (user["id"],)).fetchall()


@app.get("/api/bootstrap")
@panel_auth("member")
def bootstrap():
    user = request.panel_user
    con = db()
    rows = _scope_rows(con, user)
    visible_ids = {r["id"] for r in rows}
    parts = [_visible(part_dict(r), user) for r in rows]
    cps = sget(con, "checkpoints", DEFAULT_CHECKPOINTS)
    checkins = {c["key"]: {} for c in cps}
    for r in con.execute("SELECT * FROM checkins").fetchall():
        if r["pid"] in visible_ids:
            checkins.setdefault(r["checkpoint"], {})[r["pid"]] = r["ts"]
    out = {"user": user, "build": _build_stamp(),
           "participants": parts, "checkins": checkins,
           "checkpoints": cps, "program": sget(con, "program", []),
           "groups": sget(con, "groups", DEFAULT_GROUPS),
           "roles": sget(con, "roles", DEFAULT_ROLES), "meta": sget(con, "meta", DEFAULT_META),
           "badge": sget(con, "badge", None), "seminarLogo": sget(con, "seminarLogo", None),
           "pageBase": sget(con, "pageBase", None)}
    con.close()
    return jsonify(out)


@app.post("/api/participant")
@panel_auth("manager")
def upd_participant():
    d = request.get_json(force=True)
    pid, patch = d["id"], d.get("patch", {})
    con = db()
    if patch.get("leader"):
        row = con.execute("SELECT grp FROM participants WHERE id=?", (pid,)).fetchone()
        grp = patch.get("group", row["grp"] if row else None)
        if grp:
            con.execute("UPDATE participants SET leader=0 WHERE grp=?", (grp,))
    # Guruh va guruh mas'uli — faqat texnik admin o'zgartiradi. Bir marta
    # to'g'rilangan taqsimot tasodifan aralashib ketmasligi kerak.
    if request.panel_user["rank"] < ROLE_RANK["admin"] and ({"group", "leader"} & set(patch)):
        con.close()
        return jsonify(error="forbidden", field="group",
                       detail="guruhni faqat admin o'zgartira oladi"), 403
    before = con.execute("SELECT grp,xona_guruhi FROM participants WHERE id=?", (pid,)).fetchone()
    m = {"group": "grp", "leader": "leader", "room": "room", "branch": "branch",
         "telegram": "telegram", "lang": "lang",
         "xona_guruhi": "xona_guruhi", "xona_turi": "xona_turi"}
    for k, col in m.items():
        if k in patch:
            v = patch[k]
            if k == "leader": v = 1 if v else 0
            if k == "lang": v = v if v in LANGS else None
            if k == "xona_guruhi": v = str(v or "").strip().upper()
            con.execute("UPDATE participants SET %s=? WHERE id=?" % col, (v, pid))
    if "xona_guruhi" in patch:
        # Eski pasport-asosidagi sheriklik endi to'g'ri kelmaydi — tozalaymiz,
        # xonadoshlar `xona_guruhi` bo'yicha hisoblanadi.
        con.execute("UPDATE participants SET roommate_series=NULL WHERE id=?", (pid,))
    if "roles" in patch:
        con.execute("UPDATE participants SET roles=? WHERE id=?",
                    (json.dumps(patch["roles"], ensure_ascii=False), pid))
    con.commit(); con.close()
    moved = "group" in patch and before and before["grp"] != patch["group"] and patch["group"]
    if moved:
        notify_group_change(pid, patch["group"])
    if "xona_guruhi" in patch and before and \
            (before["xona_guruhi"] or "") != (patch["xona_guruhi"] or "").strip().upper():
        notify_room_change(pid)
    return jsonify(ok=True, notified=bool(moved))


@app.post("/api/participant/unlink")
@panel_auth("manager")
def unlink_participant():
    """Bitta ishtirokchining Telegram bog'lanishini uzadi.

    Noto'g'ri odam ro'yxatdan o'tkazib qo'yilganda kerak bo'ladi: bog'lanish
    o'chgach, haqiqiy egasi botda /start bosib o'zi tasdiqlaydi.  Guruh, xona,
    rol va QR token tegilmaydi.
    """
    pid = str((request.get_json(silent=True) or {}).get("id") or "").strip()
    con = db()
    row = con.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
    if not row:
        con.close()
        return jsonify(error="participant_not_found"), 404
    was = {"telegram_id": row["telegram_id"], "telegram_username": row["telegram_username"]}
    con.execute("UPDATE participants SET telegram_id=NULL, telegram_username=NULL, "
                "registered_at=NULL WHERE id=?", (pid,))
    con.commit(); con.close()
    app.logger.info("Telegram bog'lanishi uzildi: %s (%s)", pid, was)
    return jsonify(ok=True, id=pid, was=was)


@app.post("/api/participants/bulk")
@panel_auth("admin")
def bulk_participants():
    """Ko'p ishtirokchining guruhini birdan o'zgartirish — faqat admin.

    Bu yerdan avtomatik taqsimlash va tozalash o'tadi, ya'ni bir harakat bilan
    butun taqsimotni almashtirib yuborishi mumkin.
    """
    moved = []
    for it in request.get_json(force=True).get("items", []):
        con = db()
        before = con.execute("SELECT grp FROM participants WHERE id=?", (it["id"],)).fetchone()
        con.execute("UPDATE participants SET grp=?, leader=? WHERE id=?",
                    (it.get("group"), 1 if it.get("leader") else 0, it["id"]))
        con.commit(); con.close()
        if it.get("group") and before and before["grp"] != it.get("group"):
            moved.append((it["id"], it.get("group")))
    for pid, group in moved:
        notify_group_change(pid, group)
    return jsonify(ok=True, notified=len(moved))


@app.post("/api/checkin")
@panel_auth("leader")
def checkin():
    """Check-in paneldan. ``id`` yoki kameradan o'qilgan ``token`` qabul qilinadi."""
    d = request.get_json(force=True)
    cp, on = d["checkpoint"], d.get("on", True)
    con = db()
    target = _resolve(con, d.get("id") or d.get("token"))
    if not target:
        con.close()
        return jsonify(error="participant_not_found", status="not_found"), 404
    pid = target["id"]
    user = request.panel_user
    if user["rank"] < ROLE_RANK["manager"]:
        # Guruh mas'uli faqat o'z guruhini belgilaydi.
        if not user["group"] or target["grp"] != user["group"]:
            con.close()
            return jsonify(error="forbidden", status="forbidden"), 403
    if not on:
        con.execute("DELETE FROM checkins WHERE pid=? AND checkpoint=?", (pid, cp))
        con.commit()
        out = _checkin_payload(con, target, cp, {"status": "removed"}, None)
        con.close()
        return jsonify(out)

    force = bool(d.get("force")) and user["rank"] >= ROLE_RANK["admin"]
    decision = checkin_decision(con, target, cp, force=force)
    if decision["status"] == "unknown_checkpoint":
        con.close()
        return jsonify(error="unknown_checkpoint", status="unknown_checkpoint"), 400
    ts = _record_checkin(con, pid, cp) if decision["status"] == "ok" else None
    con.commit()
    out = _checkin_payload(con, target, cp, decision, ts)
    out["id"] = pid
    con.close()
    return jsonify(out)


@app.post("/api/checkpoints")
@panel_auth("admin")
def save_checkpoints():
    cps = request.get_json(force=True).get("checkpoints", [])
    keys = {c["key"] for c in cps}
    con = db()
    sset(con, "checkpoints", cps)
    # remove check-ins for deleted checkpoints
    for r in con.execute("SELECT DISTINCT checkpoint FROM checkins").fetchall():
        if r["checkpoint"] not in keys:
            con.execute("DELETE FROM checkins WHERE checkpoint=?", (r["checkpoint"],))
    con.commit(); con.close()
    return jsonify(ok=True)


def _save(key):
    con = db(); sset(con, key, request.get_json(force=True)); con.commit(); con.close()
    return jsonify(ok=True)

@app.post("/api/program")
@panel_auth("admin")
def save_program(): return _save("program")

@app.post("/api/roles")
@panel_auth("admin")
def save_roles(): return _save("roles")

@app.post("/api/meta")
@panel_auth("admin")
def save_meta(): return _save("meta")

@app.post("/api/groups")
@panel_auth("admin")
def save_groups():
    """Group names (and optionally their leaders) from the admin panel."""
    payload = request.get_json(force=True)
    groups = payload.get("groups", payload) if isinstance(payload, dict) else payload
    con = db()
    sset(con, "groups", groups)
    for g in groups:
        leader_id = g.get("leader") if isinstance(g, dict) else None
        if leader_id:
            con.execute("UPDATE participants SET leader=0 WHERE grp=?", (g.get("id"),))
            con.execute("UPDATE participants SET leader=1 WHERE id=?", (leader_id,))
    con.commit(); con.close()
    return jsonify(ok=True)

@app.post("/api/badge")
@panel_auth("admin")
def save_badge(): return _save("badge")

@app.post("/api/pagebase")
@panel_auth("admin")
def save_pagebase():
    con = db(); sset(con, "pageBase", request.get_json(force=True).get("base")); con.commit(); con.close()
    return jsonify(ok=True)

@app.post("/api/seminar-logo")
@panel_auth("admin")
def save_logo():
    con = db(); sset(con, "seminarLogo", request.get_json(force=True).get("dataUrl")); con.commit(); con.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- bot API
@app.post("/api/bot/find")
@bot_auth
def bot_find():
    d = request.get_json(silent=True) or {}
    con = db()
    if d.get("series"):
        rows = _find_by_passport(con, d["series"])
    elif d.get("dob"):
        target = _date_key(d["dob"])
        rows = [r for r in con.execute("SELECT * FROM participants WHERE dob IS NOT NULL").fetchall()
                if _date_key(r["dob"]) == target]
    elif d.get("name"):
        rows = con.execute("SELECT * FROM participants WHERE LOWER(fio) LIKE ?",
                           (f"%{d['name'].strip().lower()}%",)).fetchall()
    else:
        con.close()
        return jsonify(error="series, dob or name is required"), 400
    out = [_bot_participant(r) for r in rows]
    con.close()
    return jsonify(participants=out)


@app.post("/api/bot/link")
@bot_auth
def bot_link():
    d = request.get_json(silent=True) or {}
    pid, tid = str(d.get("id") or "").strip(), str(d.get("telegram_id") or "").strip()
    if not pid or not tid:
        return jsonify(error="id and telegram_id are required"), 400
    con = db()
    owner = con.execute("SELECT id FROM participants WHERE telegram_id=? AND id<>?", (tid, pid)).fetchone()
    if owner:
        con.close()
        return jsonify(error="telegram_id_already_linked", participant_id=owner["id"]), 409
    if not con.execute("SELECT 1 FROM participants WHERE id=?", (pid,)).fetchone():
        con.close()
        return jsonify(error="participant_not_found"), 404
    con.execute("UPDATE participants SET telegram_id=?,telegram_username=?,registered_at=? WHERE id=?",
                (tid, str(d.get("telegram_username") or "").strip(), _now(), pid))
    con.commit()
    row = con.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
    out = _bot_participant(row)
    con.close()
    return jsonify(ok=True, participant=out)


@app.post("/api/bot/register")
@bot_auth
def bot_register():
    d = request.get_json(silent=True) or {}
    fio = str(d.get("fio") or "").strip()
    ps, pn = _passport(f"{d.get('passport_series','')}{d.get('passport_number','')}")
    if not fio or not ps or not pn:
        return jsonify(error="fio and passport are required"), 400
    con = db()
    if _find_by_passport(con, ps + pn):
        con.close()
        return jsonify(error="passport_already_exists"), 409
    used = {r["id"] for r in con.execute("SELECT id FROM participants WHERE id LIKE 'ACO-%'")}
    seq = 1
    while f"ACO-{seq:03d}" in used:
        seq += 1
    pid = f"ACO-{seq:03d}"
    fields = ["id", "fio", "jinsi", "fuqarolik", "xona_turi", "xona_guruhi", "kelish",
              "telegram_id", "telegram_username", "category", "passport_series", "passport_number",
              "passport_expiry", "dob", "phone", "passport_file_url", "main_series", "payment_full",
              "registered_at", "roommate_series", "token"]
    values = [pid, fio, d.get("jinsi"), d.get("fuqarolik"), d.get("xona_turi"),
              d.get("xona_guruhi"), d.get("kelish"), str(d.get("telegram_id") or ""),
              str(d.get("telegram_username") or ""), d.get("category"), ps, pn,
              d.get("passport_expiry"), d.get("dob"), d.get("phone"), d.get("passport_file_url"),
              d.get("main_series"), 1 if d.get("payment_full") else 0, _now(),
              d.get("roommate_series"), make_token(ps, pn, pid, con)]
    con.execute(f"INSERT INTO participants({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", values)
    con.commit()
    out = _bot_participant(con.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone())
    con.close()
    return jsonify(ok=True, participant=out), 201


@app.post("/api/bot/update")
@bot_auth
def bot_update():
    d, patch = request.get_json(silent=True) or {}, {}
    incoming = d.get("patch") or {}
    allowed = {"fio", "dob", "phone", "passport_expiry", "xona_turi", "passport_file_url",
               "telegram_username", "category", "main_series", "payment_full"}
    patch.update({k: v for k, v in incoming.items() if k in allowed})
    if "passport" in incoming or "passport_series" in incoming or "passport_number" in incoming:
        ps, pn = _passport(incoming.get("passport") or
                           f"{incoming.get('passport_series','')}{incoming.get('passport_number','')}")
        patch.update(passport_series=ps, passport_number=pn)
    con = db()
    if d.get("id"):
        row = con.execute("SELECT * FROM participants WHERE id=?", (d["id"],)).fetchone()
    else:
        found = _find_by_passport(con, d.get("series")) if d.get("series") else []
        row = found[0] if len(found) == 1 else None
    if not row:
        con.close()
        return jsonify(error="participant_not_found"), 404
    if not patch:
        con.close()
        return jsonify(error="empty_patch"), 400
    if "passport_series" in patch and _find_by_passport(con, patch["passport_series"] + patch["passport_number"]):
        clash = _find_by_passport(con, patch["passport_series"] + patch["passport_number"])
        if any(r["id"] != row["id"] for r in clash):
            con.close()
            return jsonify(error="passport_already_exists"), 409
    if "passport_series" in patch:
        # The token is derived from the passport, so a corrected passport means a
        # new token — and a badge that has to be reprinted.
        patch["token"] = make_token(patch["passport_series"], patch["passport_number"],
                                    row["id"], con)
    cols = list(patch)
    con.execute(f"UPDATE participants SET {','.join(k+'=?' for k in cols)} WHERE id=?",
                [patch[k] for k in cols] + [row["id"]])
    con.commit()
    out = _bot_participant(con.execute("SELECT * FROM participants WHERE id=?", (row["id"],)).fetchone())
    con.close()
    return jsonify(ok=True, changed=True, participant=out)


@app.post("/api/bot/roommate")
@bot_auth
def bot_roommate():
    d = request.get_json(silent=True) or {}
    con = db()
    left = _find_by_passport(con, d.get("series")) if d.get("series") else []
    right = _find_by_passport(con, d.get("roommate_series")) if d.get("roommate_series") else []
    if len(left) != 1:
        con.close()
        return jsonify(error="participant_not_found"), 404
    if len(right) != 1:
        con.execute("UPDATE participants SET roommate_series=? WHERE id=?",
                    (d.get("roommate_series"), left[0]["id"]))
        con.commit(); con.close()
        return jsonify(ok=True, status="waiting")
    a, b = left[0], right[0]
    group = a["xona_guruhi"] or b["xona_guruhi"] or f"ROOM-{min(a['id'], b['id'])}"
    con.execute("UPDATE participants SET roommate_series=?,xona_guruhi=? WHERE id=?",
                ((b["passport_series"] or "") + (b["passport_number"] or ""), group, a["id"]))
    con.execute("UPDATE participants SET roommate_series=?,xona_guruhi=? WHERE id=?",
                ((a["passport_series"] or "") + (a["passport_number"] or ""), group, b["id"]))
    con.commit(); con.close()
    return jsonify(ok=True, status="linked", xona_guruhi=group)


@app.get("/api/bot/whoami")
@bot_auth
def bot_whoami():
    con = db()
    out = _whoami(con, request.args.get("telegram_id"))
    con.close()
    return jsonify(out)


@app.get("/api/bot/groups")
@bot_auth
def bot_groups():
    """Group list with names, leader and member/check-in counts."""
    con = db()
    names = _group_names(con)
    checked = {r["pid"] for r in con.execute("SELECT DISTINCT pid FROM checkins")}
    out = []
    for gid in sorted({r["grp"] for r in con.execute("SELECT DISTINCT grp FROM participants")
                       if r["grp"]}):
        members = con.execute("SELECT * FROM participants WHERE grp=? ORDER BY fio", (gid,)).fetchall()
        leader = next((m for m in members if m["leader"]), None)
        out.append({
            "id": gid, "name": names.get(str(gid)),
            "leader": {"id": leader["id"], "fio": leader["fio"],
                       "telegram_id": leader["telegram_id"]} if leader else None,
            "total": len(members),
            "with_telegram": sum(1 for m in members if m["telegram_id"]),
            "checked_in": sum(1 for m in members if m["id"] in checked),
        })
    con.close()
    return jsonify(groups=out)


@app.get("/api/bot/group/<int:gid>")
@bot_auth
def bot_group_members(gid):
    """Members of one group with their check-in state — the leader's roster."""
    con = db()
    cps = sget(con, "checkpoints", DEFAULT_CHECKPOINTS)
    marks = {}
    for r in con.execute("SELECT pid,checkpoint,ts FROM checkins").fetchall():
        marks.setdefault(r["pid"], {})[r["checkpoint"]] = r["ts"]
    members = []
    for r in con.execute("SELECT * FROM participants WHERE grp=? ORDER BY leader DESC,fio", (gid,)):
        members.append({"id": r["id"], "fio": r["fio"], "token": r["token"],
                        "leader": bool(r["leader"]), "room": r["room"],
                        "telegram_id": r["telegram_id"], "phone": r["phone"],
                        "checkins": marks.get(r["id"], {})})
    out = {"group": gid, "name": _group_names(con).get(str(gid)),
           "checkpoints": cps, "members": members}
    con.close()
    return jsonify(out)


@app.post("/api/bot/checkin")
@bot_auth
def bot_checkin():
    """Check somebody in by QR token (or id), on behalf of a leader/admin."""
    d = request.get_json(silent=True) or {}
    con = db()
    actor = _whoami(con, d.get("by_telegram_id"))
    row = _resolve(con, d.get("token") or d.get("id"))
    if not row:
        con.close()
        return jsonify(error="participant_not_found", status="not_found"), 404
    if not _may_checkin(con, actor, row):
        con.close()
        return jsonify(error="forbidden", status="forbidden", role=actor["role"]), 403
    cp = str(d.get("checkpoint") or "").strip()
    on = d.get("on", True)
    if not on:
        con.execute("DELETE FROM checkins WHERE pid=? AND checkpoint=?", (row["id"], cp))
        con.commit()
        out = _checkin_payload(con, row, cp, {"status": "removed"}, None)
        con.close()
        return jsonify(out)

    force = bool(d.get("force")) and actor["role"] == "admin"
    decision = checkin_decision(con, row, cp, force=force)
    if decision["status"] == "unknown_checkpoint":
        con.close()
        return jsonify(error="unknown_checkpoint", status="unknown_checkpoint"), 400
    ts = _record_checkin(con, row["id"], cp) if decision["status"] == "ok" else None
    con.commit()
    out = _checkin_payload(con, row, cp, decision, ts)
    con.close()
    return jsonify(out)


# ---------------------------------------------------------- messaging (3 roles)
def _resolve_targets(con, actor, scope, value):
    """Who a message from *actor* with this scope may reach.

    Returns ``(rows, error)``.  Permission rules live here so both the bot API
    and any future caller obey the same limits:
    admin → everybody / one group / one person; leader → own group only;
    member → own group leader only.
    """
    scope = (scope or "").strip() or "all"
    if actor["role"] == "guest":
        return [], "unknown_sender"

    if scope == "all":
        if actor["role"] != "admin":
            return [], "forbidden"
        rows = con.execute("SELECT * FROM participants WHERE telegram_id IS NOT NULL "
                           "AND telegram_id<>'' ORDER BY grp,fio").fetchall()
    elif scope == "group":
        try:
            gid = int(value) if value not in (None, "") else actor["group"]
        except (TypeError, ValueError):
            return [], "bad_group"
        if not gid:
            return [], "bad_group"
        if actor["role"] == "leader" and gid != actor["group"]:
            return [], "forbidden"
        if actor["role"] == "member":
            return [], "forbidden"
        rows = con.execute("SELECT * FROM participants WHERE grp=? AND telegram_id IS NOT NULL "
                           "AND telegram_id<>'' ORDER BY fio", (gid,)).fetchall()
    elif scope == "one":
        row = _resolve(con, value)
        if not row:
            return [], "participant_not_found"
        if actor["role"] == "leader" and row["grp"] != actor["group"]:
            return [], "forbidden"
        if actor["role"] == "member":
            return [], "forbidden"
        rows = [row]
    elif scope == "leader":
        # A member asking their own group leader a question.
        gid = actor["group"]
        if not gid:
            return [], "no_group"
        rows = con.execute("SELECT * FROM participants WHERE grp=? AND leader=1", (gid,)).fetchall()
        if not rows:
            return [], "leader_not_set"
    else:
        return [], "bad_scope"

    # Never send to somebody we cannot reach, and never back to the sender.
    return [r for r in rows if r["telegram_id"] and str(r["telegram_id"]) != actor["telegram_id"]], None


@app.post("/api/bot/message")
@bot_auth
def bot_message():
    """Record an outgoing message and return the list of recipients.

    The bot itself does the Telegram sending, then reports the per-recipient
    result back through ``/api/bot/message/sent``.
    """
    d = request.get_json(silent=True) or {}
    text = str(d.get("text") or "").strip()
    con = db()
    actor = _whoami(con, d.get("from_telegram_id"))
    rows, error = _resolve_targets(con, actor, d.get("scope"), d.get("value"))
    if error:
        con.close()
        return jsonify(error=error, role=actor["role"]), 403 if error == "forbidden" else 400
    if not text and not d.get("kind"):
        con.close()
        return jsonify(error="empty_text"), 400

    cur = con.execute(
        "INSERT INTO messages(from_id,from_telegram_id,from_role,to_scope,to_value,text,kind,"
        "created_at,parent_id) VALUES(?,?,?,?,?,?,?,?,NULL)",
        (actor["id"], actor["telegram_id"], actor["role"], d.get("scope") or "all",
         str(d.get("value") or ""), text, d.get("kind") or "text", _now()))
    msg_id = cur.lastrowid
    for r in rows:
        con.execute("INSERT OR REPLACE INTO msg_targets(msg_id,pid,telegram_id,status) "
                    "VALUES(?,?,?,'pending')", (msg_id, r["id"], str(r["telegram_id"])))

    # Optionally mirror member questions to the admins.
    copies = []
    if actor["role"] == "member" and sget(con, "copy_member_questions", False):
        admins = _admin_ids() | {str(x) for x in sget(con, "admins", []) or []}
        copies = [a for a in sorted(admins)
                  if a != actor["telegram_id"] and a not in {str(r["telegram_id"]) for r in rows}]
    con.commit()
    names = _group_names(con)
    out = {"ok": True, "message_id": msg_id, "from_role": actor["role"],
           "sender_label": _label(con, con.execute("SELECT * FROM participants WHERE id=?",
                                                   (actor["id"],)).fetchone(), names)
           if actor["id"] else "Administrator",
           "recipients": [{"id": r["id"], "fio": r["fio"], "telegram_id": str(r["telegram_id"]),
                           "group": r["grp"]} for r in rows],
           "copy_to": copies}
    con.close()
    return jsonify(out)


@app.post("/api/bot/message/sent")
@bot_auth
def bot_message_sent():
    """Store the Telegram message ids (and failures) of one broadcast."""
    d = request.get_json(silent=True) or {}
    msg_id = d.get("message_id")
    if not msg_id:
        return jsonify(error="message_id is required"), 400
    con = db()
    delivered = failed = 0
    for item in d.get("results") or []:
        ok = bool(item.get("ok"))
        delivered, failed = delivered + (1 if ok else 0), failed + (0 if ok else 1)
        con.execute("INSERT INTO msg_targets(msg_id,pid,telegram_id,telegram_msg_id,status) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(msg_id,pid) DO UPDATE SET "
                    "telegram_msg_id=excluded.telegram_msg_id,status=excluded.status",
                    (msg_id, item.get("id"), str(item.get("telegram_id") or ""),
                     str(item.get("telegram_msg_id") or ""), "sent" if ok else "failed"))
    con.execute("UPDATE messages SET delivered=?,failed=? WHERE id=?", (delivered, failed, msg_id))
    con.commit(); con.close()
    return jsonify(ok=True, delivered=delivered, failed=failed)


@app.post("/api/bot/reply")
@bot_auth
def bot_reply():
    """Route a reply back to whoever sent the original message — and only there.

    Everything stays in private chats: the answer goes to the original sender's
    Telegram id, never to a group chat.
    """
    d = request.get_json(silent=True) or {}
    parent_id = d.get("parent_msg_id")
    text = str(d.get("text") or "").strip()
    con = db()
    actor = _whoami(con, d.get("from_telegram_id"))
    parent = con.execute("SELECT * FROM messages WHERE id=?", (parent_id,)).fetchone()
    if not parent:
        con.close()
        return jsonify(error="message_not_found"), 404
    # Only a recipient of that message (or its author) may reply to it.
    allowed = {str(r["telegram_id"]) for r in
               con.execute("SELECT telegram_id FROM msg_targets WHERE msg_id=?", (parent_id,))}
    allowed.add(str(parent["from_telegram_id"]))
    if actor["telegram_id"] not in allowed:
        con.close()
        return jsonify(error="forbidden"), 403
    if not text and not d.get("kind"):
        con.close()
        return jsonify(error="empty_text"), 400

    destination = str(parent["from_telegram_id"])
    cur = con.execute(
        "INSERT INTO messages(from_id,from_telegram_id,from_role,to_scope,to_value,text,kind,"
        "created_at,parent_id) VALUES(?,?,?,'reply',?,?,?,?,?)",
        (actor["id"], actor["telegram_id"], actor["role"], destination, text,
         d.get("kind") or "text", _now(), parent_id))
    msg_id = cur.lastrowid
    if actor["id"]:
        con.execute("INSERT OR REPLACE INTO msg_targets(msg_id,pid,telegram_id,status) "
                    "VALUES(?,?,?,'pending')", (msg_id, parent["from_id"] or "", destination))
    con.execute("UPDATE messages SET replied=replied+1 WHERE id=?", (parent_id,))
    con.commit()

    me = con.execute("SELECT * FROM participants WHERE id=?", (actor["id"],)).fetchone() \
        if actor["id"] else None
    out = {"ok": True, "message_id": msg_id, "to_telegram_id": destination,
           "from_role": actor["role"],
           "sender_label": _label(con, me) if me else "Administrator",
           "parent_excerpt": (parent["text"] or "")[:160]}
    con.close()
    return jsonify(out)


# ─────────────────────── Telegram guruh a'zolarini nazorat ───────────────────
# Telegram Bot API guruh a'zolarini ro'yxatlash imkonini bermaydi: faqat
# umumiy son, adminlar va bitta odamni tekshirish mumkin.  Shuning uchun bot
# kuzatgan hamma narsani (kirdi/chiqdi hodisalari va guruhda yozganlar) shu
# jadvalga yig'ib boradi va faqat o'zi ko'rgan odamlar bo'yicha qaror qiladi.
IN_GROUP = {"creator", "administrator", "member", "restricted"}


@app.post("/api/bot/group/seen")
@bot_auth
def bot_group_seen():
    """Guruhda ko'rilgan odamni yozib qo'yadi (kirdi, yozdi yoki chiqdi)."""
    d = request.get_json(silent=True) or {}
    tid = str(d.get("telegram_id") or "").strip()
    chat = str(d.get("chat_id") or "").strip()
    if not tid or not chat:
        return jsonify(error="chat_id and telegram_id are required"), 400
    con = db()
    con.execute(
        "INSERT INTO group_members(chat_id,telegram_id,username,full_name,status,source,"
        "first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(chat_id,telegram_id) DO UPDATE SET "
        "username=COALESCE(NULLIF(excluded.username,''),group_members.username),"
        "full_name=COALESCE(NULLIF(excluded.full_name,''),group_members.full_name),"
        "status=excluded.status, source=excluded.source, last_seen=excluded.last_seen",
        (chat, tid, str(d.get("username") or ""), str(d.get("full_name") or ""),
         str(d.get("status") or "member"), str(d.get("source") or "?"), _now(), _now()))
    con.commit(); con.close()
    return jsonify(ok=True)


# Bot saqlab qo'yishi mumkin bo'lgan sozlamalar — boshqasiga tegmaydi.
BOT_SETTING_KEYS = {"group_permissions", "group_locked"}


@app.get("/api/bot/setting/<key>")
@bot_auth
def bot_get_setting(key):
    if key not in BOT_SETTING_KEYS:
        return jsonify(error="unknown_key"), 400
    con = db()
    value = sget(con, key)
    con.close()
    return jsonify(key=key, value=value)


@app.post("/api/bot/setting/<key>")
@bot_auth
def bot_set_setting(key):
    if key not in BOT_SETTING_KEYS:
        return jsonify(error="unknown_key"), 400
    con = db()
    sset(con, key, (request.get_json(silent=True) or {}).get("value"))
    con.commit(); con.close()
    return jsonify(ok=True)


@app.get("/api/bot/group/audit")
@bot_auth
def bot_group_audit():
    """Kim ro'yxatda bor, kim yo'q — bot ko'rgan a'zolar bo'yicha."""
    chat = str(request.args.get("chat_id") or "").strip()
    con = db()
    rows = con.execute("SELECT * FROM group_members WHERE chat_id=?", (chat,)).fetchall()
    by_tid = {str(r["telegram_id"]): r for r in con.execute(
        "SELECT id,fio,grp,telegram_id FROM participants "
        "WHERE telegram_id IS NOT NULL AND telegram_id<>''")}
    known, strangers, left = [], [], 0
    for r in rows:
        if r["status"] not in IN_GROUP:
            left += 1
            continue
        person = by_tid.get(str(r["telegram_id"]))
        entry = {"telegram_id": r["telegram_id"], "username": r["username"],
                 "full_name": r["full_name"], "status": r["status"],
                 "first_seen": r["first_seen"], "last_seen": r["last_seen"]}
        if person:
            entry.update(id=person["id"], fio=person["fio"], group=person["grp"])
            known.append(entry)
        else:
            strangers.append(entry)
    admins = {str(x) for x in _admin_ids()} | {str(x) for x in sget(con, "admins", []) or []}
    verified_total = con.execute(
        "SELECT COUNT(*) c FROM participants WHERE telegram_id IS NOT NULL "
        "AND telegram_id<>''").fetchone()["c"]
    con.close()
    return jsonify(chat_id=chat, seen=len(rows), left=left,
                   in_list=sorted(known, key=lambda x: (x.get("group") or 99, x.get("fio") or "")),
                   not_in_list=[s for s in strangers if s["telegram_id"] not in admins],
                   admins_skipped=[s for s in strangers if s["telegram_id"] in admins],
                   verified_participants=verified_total)


@app.get("/api/bot/docs")
@bot_auth
def bot_docs():
    """Bir odamning tarqatishga tayyor hujjatlari.

    ``telegram_id`` yoki ``id`` bo'yicha topiladi.  Vaqti kelmagan turdagi
    hujjatlar ro'yxatga tushmaydi — bot ularni yubormaydi.
    """
    con = db()
    tid = str(request.args.get("telegram_id") or "").strip()
    pid = str(request.args.get("id") or "").strip().upper()
    if tid and not pid:
        row = con.execute("SELECT id FROM participants WHERE telegram_id=? AND telegram_id<>''",
                          (tid,)).fetchone()
        pid = row["id"] if row else ""
    if not pid:
        con.close()
        return jsonify(documents=[], pending=[], participant=None)
    person = con.execute("SELECT id,fio,lang FROM participants WHERE id=?", (pid,)).fetchone()
    ready, pending = [], []
    for r in con.execute("SELECT * FROM documents WHERE pid=? ORDER BY kind,id", (pid,)):
        (ready if docs_released(con, r["kind"]) else pending).append(_doc_row(r))
    con.close()
    return jsonify(documents=ready, pending=pending,
                   participant=dict(person) if person else None)


@app.get("/api/bot/docs/pending")
@bot_auth
def bot_docs_pending():
    """Hujjati bor, lekin hali yuborilmagan odamlar — ommaviy tarqatish uchun."""
    con = db()
    out = {}
    for r in con.execute(
            "SELECT d.*, p.telegram_id, p.fio FROM documents d "
            "JOIN participants p ON p.id=d.pid "
            "WHERE d.sent_at IS NULL AND p.telegram_id IS NOT NULL AND p.telegram_id<>''"):
        if not docs_released(con, r["kind"]):
            continue
        entry = out.setdefault(r["pid"], {"id": r["pid"], "fio": r["fio"],
                                          "telegram_id": str(r["telegram_id"]), "documents": []})
        entry["documents"].append(_doc_row(r))
    waiting = con.execute(
        "SELECT COUNT(DISTINCT pid) c FROM documents WHERE pid NOT IN "
        "(SELECT id FROM participants WHERE telegram_id IS NOT NULL AND telegram_id<>'')"
    ).fetchone()["c"]
    con.close()
    return jsonify(people=list(out.values()), not_registered=waiting)


@app.post("/api/bot/docs/upload")
@bot_auth
def bot_docs_upload():
    """Botdan kelgan faylni saqlaydi — admin telegramga tashlaganini."""
    request.panel_user = {"id": str(request.form.get("uploader") or "bot")}
    return docs_upload.__wrapped__()


@app.post("/api/bot/docs/share")
@bot_auth
def bot_docs_share():
    request.panel_user = {"id": "bot"}
    return docs_share.__wrapped__()


@app.post("/api/bot/docs/delete")
@bot_auth
def bot_docs_delete():
    request.panel_user = {"id": "bot"}
    return docs_delete.__wrapped__()


@app.get("/api/bot/docs/file/<int:doc_id>")
@bot_auth
def bot_docs_file(doc_id):
    con = db()
    row = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    con.close()
    if not row:
        abort(404)
    return send_from_directory(DOCS_FILES, row["stored"],
                               as_attachment=True, download_name=row["file_name"])


@app.post("/api/bot/docs/sent")
@bot_auth
def bot_docs_sent():
    """Yuborilgan hujjatlarni belgilaydi — ikkinchi marta yuborilmasin."""
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    if not ids:
        return jsonify(ok=True, marked=0)
    con = db()
    con.executemany("UPDATE documents SET sent_at=? WHERE id=?", [(_now(), i) for i in ids])
    con.commit(); con.close()
    return jsonify(ok=True, marked=len(ids))


@app.get("/api/bot/recipients")
@bot_auth
def bot_recipients():
    con = db()
    scope = (request.args.get("scope") or "").strip()
    value = request.args.get("value")
    if scope:
        # Scoped form used by the messaging flows: [{id, fio, telegram_id, group}]
        actor = _whoami(con, request.args.get("telegram_id"))
        rows, error = _resolve_targets(con, actor, scope, value)
        if error:
            con.close()
            return jsonify(error=error), 403 if error == "forbidden" else 400
        out = [{"id": r["id"], "fio": r["fio"], "telegram_id": str(r["telegram_id"]),
                "group": r["grp"], "token": r["token"]} for r in rows]
        con.close()
        return jsonify(recipients=out)
    rows = con.execute("SELECT * FROM participants ORDER BY xona_guruhi,id").fetchall()
    by_room = {}
    for r in rows:
        if r["xona_guruhi"]:
            by_room.setdefault(r["xona_guruhi"], []).append(r["fio"])
    out = []
    for r in rows:
        out.append({"id": r["id"], "fio": r["fio"], "telegram_id": r["telegram_id"],
                    "token": r["token"], "leader": bool(r["leader"]),
                    "group": r["grp"], "room": r["room"], "xona_turi": r["xona_turi"],
                    "xona_guruhi": r["xona_guruhi"], "category": r["category"],
                    "phone": r["phone"], "passport_series": r["passport_series"],
                    "passport_number": r["passport_number"],
                    "passport_expiry": r["passport_expiry"], "dob": r["dob"],
                    "passport_file_url": r["passport_file_url"],
                    "roommate_series": r["roommate_series"],
                    "roommates": [x for x in by_room.get(r["xona_guruhi"], []) if x != r["fio"]]})
    con.close()
    return jsonify(recipients=out)


@app.get("/api/bot/stats")
@bot_auth
def bot_stats():
    con = db()
    rows = con.execute("SELECT * FROM participants").fetchall()
    room_counts = {}
    for r in rows:
        if r["xona_guruhi"]:
            room_counts[r["xona_guruhi"]] = room_counts.get(r["xona_guruhi"], 0) + 1
    def cap(v):
        text = str(v or "").lower()
        return 3 if "3" in text or "tr" in text else 2 if "2" in text or "dbl" in text or "dub" in text else 1
    ready = sum(1 for g, count in room_counts.items()
                if count >= max(cap(r["xona_turi"]) for r in rows if r["xona_guruhi"] == g))
    out = {"total": len(rows), "telegram": sum(bool(r["telegram_id"]) for r in rows),
           "no_telegram": sum(not r["telegram_id"] for r in rows),
           "dbl": sum(cap(r["xona_turi"]) == 2 for r in rows),
           "trpl": sum(cap(r["xona_turi"]) == 3 for r in rows),
           "rooms_ready": ready,
           "without_roommate": sum(cap(r["xona_turi"]) > 1 and
                                    room_counts.get(r["xona_guruhi"], 0) < 2 for r in rows)}
    con.close()
    return jsonify(out)


# ------------------------------------------------- Telegram WebApp (mini-app)
WEBAPP_MAX_AGE = 24 * 3600  # initData older than a day is rejected


def verify_init_data(init_data, bot_token):
    """Validate ``Telegram.WebApp.initData`` server-side.

    Follows the documented scheme: the signing key is
    ``HMAC_SHA256("WebAppData", bot_token)`` and the payload is every field
    except ``hash``, sorted by key and joined with newlines.  Returns the parsed
    user dict, or ``None`` when the signature is missing, forged or stale.
    """
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    received = pairs.pop("hash", "")
    if not received:
        return None
    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        issued = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if issued and abs(datetime.datetime.now(datetime.timezone.utc).timestamp() - issued) > WEBAPP_MAX_AGE:
        return None
    try:
        return json.loads(pairs.get("user") or "{}")
    except json.JSONDecodeError:
        return None


def webapp_user():
    """Telegram user behind the current mini-app request, or ``None``."""
    init_data = (request.get_json(silent=True) or {}).get("init_data") \
        or request.headers.get("X-Telegram-Init-Data", "") \
        or request.args.get("init_data", "")
    return verify_init_data(init_data, os.environ.get("BOT_TOKEN", ""))


@app.post("/api/webapp/whoami")
def webapp_whoami():
    user = webapp_user()
    if not user:
        return jsonify(error="invalid_init_data"), 401
    con = db()
    out = _whoami(con, user.get("id"))
    out["telegram_username"] = user.get("username")
    out["checkpoints"] = sget(con, "checkpoints", DEFAULT_CHECKPOINTS)
    con.close()
    return jsonify(out)


@app.post("/api/webapp/checkin")
def webapp_checkin():
    """Check-in from the in-Telegram QR scanner.

    The caller is trusted only after ``initData`` verifies — a forged
    ``by_telegram_id`` cannot get past this.
    """
    user = webapp_user()
    if not user:
        return jsonify(error="invalid_init_data", status="invalid_init_data"), 401
    d = request.get_json(silent=True) or {}
    con = db()
    actor = _whoami(con, user.get("id"))
    row = _resolve(con, d.get("token") or d.get("id"))
    if not row:
        con.close()
        return jsonify(error="participant_not_found", status="not_found"), 404
    if not _may_checkin(con, actor, row):
        con.close()
        return jsonify(error="forbidden", status="forbidden", role=actor["role"],
                       reason="boshqa guruh a'zosi" if actor["role"] == "leader"
                       else "ruxsat yo'q"), 403
    cp = str(d.get("checkpoint") or "").strip()
    force = bool(d.get("force")) and actor["role"] == "admin"
    decision = checkin_decision(con, row, cp, force=force)
    if decision["status"] == "unknown_checkpoint":
        con.close()
        return jsonify(error="unknown_checkpoint", status="unknown_checkpoint"), 400
    ts = _record_checkin(con, row["id"], cp) if decision["status"] == "ok" else None
    con.commit()
    out = _checkin_payload(con, row, cp, decision, ts)
    con.close()
    return jsonify(out)


@app.get("/scan")
def scan_page():
    return send_from_directory(STATIC, "scan.html")


# ---------------------------------------------------------------- participant view
@app.get("/api/p/<pid>")
def participant_view(pid):
    """Public participant page data.  Accepts an id or a QR token.

    The passport itself is never part of the response — the token in the URL is
    all the badge exposes.
    """
    con = db()
    row = _resolve(con, pid)
    if not row:
        con.close(); abort(404)
    pid = row["id"]
    p = part_dict(row)
    roles_def = {r["id"]: r for r in sget(con, "roles", DEFAULT_ROLES)}
    my_roles = [roles_def[rid] for rid in p["roles"] if rid in roles_def]
    leader = None
    roommates = []
    if p["group"]:
        lr = con.execute("SELECT fio FROM participants WHERE grp=? AND leader=1", (p["group"],)).fetchone()
        leader = lr["fio"] if lr else None
    for r in con.execute("SELECT id,fio FROM participants WHERE xona_guruhi=? AND id<>?",
                         (p["xona_guruhi"], pid)).fetchall():
        roommates.append(r["fio"])
    checkins = {}
    for r in con.execute("SELECT checkpoint,ts FROM checkins WHERE pid=?", (pid,)).fetchall():
        checkins[r["checkpoint"]] = r["ts"]
    person = {k: p[k] for k in ("id", "fio", "group", "leader", "room",
                                "xona_turi", "xona_guruhi", "fuqarolik")}
    person["token"] = p.get("token")
    person["lang"] = p.get("lang") or None
    person["group_name"] = _group_names(con).get(str(p["group"])) if p["group"] else None
    out = {"participant": person,
           "roles": my_roles, "groupLeader": leader, "roommates": roommates,
           "program": sget(con, "program", []), "checkpoints": sget(con, "checkpoints", DEFAULT_CHECKPOINTS),
           "checkins": checkins, "meta": sget(con, "meta", DEFAULT_META),
           "seminarLogo": sget(con, "seminarLogo", None)}
    con.close()
    return jsonify(out)


@app.get("/p/<pid>")
def participant_page(pid):
    return send_from_directory(STATIC, "participant.html")


# ---------------------------------------------------------------- static
@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")

@app.get("/<path:path>")
def assets(path):
    return send_from_directory(STATIC, path)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
