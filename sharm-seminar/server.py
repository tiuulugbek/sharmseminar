#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Acoustic — Sharm seminar boshqaruv paneli (backend).
Flask + SQLite. Admin panel + ishtirokchi sahifasi (/p/<id>).

    pip install -r requirements.txt
    python server.py
    http://SERVER_IP:8000
"""
import os, json, sqlite3, datetime, re
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "data", "sharm.db")
SEED_P   = os.path.join(BASE_DIR, "data", "seed_participants.json")
SEED_PR  = os.path.join(BASE_DIR, "data", "seed_program.json")
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
}


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
    """)
    # Idempotent migrations: existing databases keep all rows and values.
    cols = [r["name"] for r in con.execute("PRAGMA table_info(participants)").fetchall()]
    if "roles" not in cols:
        con.execute("ALTER TABLE participants ADD COLUMN roles TEXT DEFAULT '[]'")
        cols.append("roles")
    for name, sql_type in BOT_COLUMNS.items():
        if name not in cols:
            con.execute(f"ALTER TABLE participants ADD COLUMN {name} {sql_type}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_participant_passport "
                "ON participants(passport_series, passport_number) "
                "WHERE passport_series IS NOT NULL AND passport_series<>'' "
                "AND passport_number IS NOT NULL AND passport_number<>''")
    # seed participants
    if con.execute("SELECT COUNT(*) c FROM participants").fetchone()["c"] == 0 and os.path.exists(SEED_P):
        for p in load_json(SEED_P, []):
            con.execute("INSERT INTO participants(id,fio,jinsi,fuqarolik,xona_turi,xona_guruhi,kelish,grp,leader,roles,"
                        "passport_series,passport_number,passport_expiry,dob,phone,telegram_username,telegram_id,category,"
                        "roommate_series,main_series)"
                        " VALUES(?,?,?,?,?,?,?,NULL,0,'[]',?,?,?,?,?,?,?,?,?,?)",
                        (p["id"], p["fio"], p.get("jinsi"), p.get("fuqarolik"),
                         p.get("xona_turi"), p.get("xona_guruhi"), p.get("kelish"),
                         p.get("passport_series"), p.get("passport_number"), p.get("passport_expiry"),
                         p.get("dob"), p.get("phone"), p.get("telegram_username"),
                         p.get("telegram_id"), p.get("category"), p.get("roommate_series"),
                         p.get("main_series")))
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
    con.commit(); con.close()


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
        "id": p["id"], "fio": p["fio"], "dob": p.get("dob"),
        "group": p.get("group"), "telegram_id": p.get("telegram_id"),
        "telegram_username": p.get("telegram_username"),
        "category": p.get("category"), "passport_series": p.get("passport_series"),
        "passport_number": p.get("passport_number"), "passport_expiry": p.get("passport_expiry"),
        "phone": p.get("phone"), "passport_file_url": p.get("passport_file_url"),
        "xona_turi": p.get("xona_turi"), "xona_guruhi": p.get("xona_guruhi"),
        "room": p.get("room"), "main_series": p.get("main_series"),
        "payment_full": bool(p.get("payment_full")), "roommate_series": p.get("roommate_series"),
    }


def _find_by_passport(con, value):
    series, number = _passport(value)
    key = number.lstrip("0") or "0"
    rows = con.execute("SELECT * FROM participants WHERE UPPER(COALESCE(passport_series,''))=?",
                       (series,)).fetchall()
    return [r for r in rows if ((r["passport_number"] or "").lstrip("0") or "0") == key]


# ---------------------------------------------------------------- admin API
@app.get("/api/bootstrap")
def bootstrap():
    con = db()
    parts = [part_dict(r) for r in con.execute("SELECT * FROM participants").fetchall()]
    cps = sget(con, "checkpoints", DEFAULT_CHECKPOINTS)
    checkins = {c["key"]: {} for c in cps}
    for r in con.execute("SELECT * FROM checkins").fetchall():
        checkins.setdefault(r["checkpoint"], {})[r["pid"]] = r["ts"]
    out = {"participants": parts, "checkins": checkins,
           "checkpoints": cps, "program": sget(con, "program", []),
           "roles": sget(con, "roles", DEFAULT_ROLES), "meta": sget(con, "meta", DEFAULT_META),
           "badge": sget(con, "badge", None), "seminarLogo": sget(con, "seminarLogo", None),
           "pageBase": sget(con, "pageBase", None)}
    con.close()
    return jsonify(out)


@app.post("/api/participant")
def upd_participant():
    d = request.get_json(force=True)
    pid, patch = d["id"], d.get("patch", {})
    con = db()
    if patch.get("leader"):
        row = con.execute("SELECT grp FROM participants WHERE id=?", (pid,)).fetchone()
        grp = patch.get("group", row["grp"] if row else None)
        if grp:
            con.execute("UPDATE participants SET leader=0 WHERE grp=?", (grp,))
    m = {"group": "grp", "leader": "leader", "room": "room", "branch": "branch", "telegram": "telegram"}
    for k, col in m.items():
        if k in patch:
            v = patch[k]
            if k == "leader": v = 1 if v else 0
            con.execute("UPDATE participants SET %s=? WHERE id=?" % col, (v, pid))
    if "roles" in patch:
        con.execute("UPDATE participants SET roles=? WHERE id=?",
                    (json.dumps(patch["roles"], ensure_ascii=False), pid))
    con.commit(); con.close()
    return jsonify(ok=True)


@app.post("/api/participants/bulk")
def bulk_participants():
    for it in request.get_json(force=True).get("items", []):
        con = db()
        con.execute("UPDATE participants SET grp=?, leader=? WHERE id=?",
                    (it.get("group"), 1 if it.get("leader") else 0, it["id"]))
        con.commit(); con.close()
    return jsonify(ok=True)


@app.post("/api/checkin")
def checkin():
    d = request.get_json(force=True)
    pid, cp, on = d["id"], d["checkpoint"], d.get("on", True)
    con = db()
    if on:
        ts = datetime.datetime.now().strftime("%H:%M")
        con.execute("INSERT INTO checkins(pid,checkpoint,ts) VALUES(?,?,?) "
                    "ON CONFLICT(pid,checkpoint) DO UPDATE SET ts=excluded.ts", (pid, cp, ts))
    else:
        ts = None
        con.execute("DELETE FROM checkins WHERE pid=? AND checkpoint=?", (pid, cp))
    con.commit(); con.close()
    return jsonify(ok=True, ts=ts)


@app.post("/api/checkpoints")
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
def save_program(): return _save("program")

@app.post("/api/roles")
def save_roles(): return _save("roles")

@app.post("/api/meta")
def save_meta(): return _save("meta")

@app.post("/api/badge")
def save_badge(): return _save("badge")

@app.post("/api/pagebase")
def save_pagebase():
    con = db(); sset(con, "pageBase", request.get_json(force=True).get("base")); con.commit(); con.close()
    return jsonify(ok=True)

@app.post("/api/seminar-logo")
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
              "registered_at", "roommate_series"]
    values = [pid, fio, d.get("jinsi"), d.get("fuqarolik"), d.get("xona_turi"),
              d.get("xona_guruhi"), d.get("kelish"), str(d.get("telegram_id") or ""),
              str(d.get("telegram_username") or ""), d.get("category"), ps, pn,
              d.get("passport_expiry"), d.get("dob"), d.get("phone"), d.get("passport_file_url"),
              d.get("main_series"), 1 if d.get("payment_full") else 0, _now(), d.get("roommate_series")]
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


@app.get("/api/bot/recipients")
@bot_auth
def bot_recipients():
    con = db()
    rows = con.execute("SELECT * FROM participants ORDER BY xona_guruhi,id").fetchall()
    by_room = {}
    for r in rows:
        if r["xona_guruhi"]:
            by_room.setdefault(r["xona_guruhi"], []).append(r["fio"])
    out = []
    for r in rows:
        out.append({"id": r["id"], "fio": r["fio"], "telegram_id": r["telegram_id"],
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


# ---------------------------------------------------------------- participant view
@app.get("/api/p/<pid>")
def participant_view(pid):
    con = db()
    row = con.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
    if not row:
        con.close(); abort(404)
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
    out = {"participant": {k: p[k] for k in ("id", "fio", "group", "leader", "room",
                                             "xona_turi", "xona_guruhi", "fuqarolik")},
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
