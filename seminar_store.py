"""Compatibility data layer backed by the sharm-seminar HTTP API.

It keeps the old bot's row-shaped boundary small while all persistence goes
through :mod:`api_client`.  Google Sheets is not used by this module.
"""
import api_client

HEADER = [
    "Sana", "Kim kiritdi (Telegram)", "Toifa", "Asosiy / Sherik", "Ism",
    "Familya", "Tug'ilgan sana", "Yosh", "Telefon", "Pasport seriya",
    "Amal muddati", "Xona", "100% to'lov", "Pasport (Drive havola)",
    "Sherik (seriya)", "Telegram ID",
]


def _names(fio):
    parts = str(fio or "").strip().split()
    if len(parts) < 2:
        return (parts[0] if parts else ""), ""
    return " ".join(parts[1:]), parts[0]


def _category(value):
    value = str(value or "")
    low = value.lower()
    if "oila" in low:
        return "Oila a'zosi"
    if "shifokor" in low or "diller" in low:
        return "Shifokor/Diller"
    return "Xodim" if low == "xodim" else value


def _category_api(value):
    low = str(value or "").lower()
    if "oila" in low:
        return "oila"
    if "shifokor" in low or "diller" in low:
        return "shifokor_diller"
    return "xodim"


def _to_row(p):
    name, surname = _names(p.get("fio"))
    passport = f"{p.get('passport_series') or ''}{p.get('passport_number') or ''}"
    return [
        p.get("registered_at") or "", p.get("telegram_username") or "",
        _category(p.get("category")), "Sherik (oila a'zosi)" if p.get("category") == "oila" else "Asosiy",
        name, surname, p.get("dob") or "", "", p.get("phone") or "", passport,
        p.get("passport_expiry") or "", p.get("xona_turi") or "",
        "Ha" if p.get("payment_full") else "", p.get("passport_file_url") or "",
        p.get("roommate_series") or p.get("main_series") or "", p.get("telegram_id") or "",
    ]


def _payload(row):
    row = list(row) + [""] * (len(HEADER) - len(row))
    return {
        "fio": f"{row[5]} {row[4]}".strip(),
        "category": _category_api(row[2]), "dob": row[6], "phone": row[8],
        "passport_series": row[9], "passport_number": "", "passport_expiry": row[10],
        "xona_turi": row[11], "payment_full": str(row[12]).lower() in {"ha", "1", "true"},
        "passport_file_url": row[13], "main_series": row[14] if "oila" in row[2].lower() else "",
        "roommate_series": row[14], "telegram_id": row[15], "telegram_username": row[1],
    }


def find_row_by_series(series):
    found = api_client.find(series=series)
    return (found[0]["id"], _to_row(found[0])) if len(found) == 1 else (None, None)


def fetch_series():
    return {f"{p.get('passport_series') or ''}{p.get('passport_number') or ''}".upper()
            for p in api_client.recipients() if p.get("passport_series") or p.get("passport_number")}


def find_rows_by_birthdate(birthdate):
    return [(p["id"], _to_row(p)) for p in api_client.find(dob=birthdate)]


def set_telegram_id(participant_id, tg_id, username=""):
    return api_client.link(participant_id, tg_id, username)


def append_rows(rows):
    return [api_client.register(_payload(row)) for row in rows]


def append_linked(main_row, partner_series):
    person = api_client.register(_payload(main_row))
    own = f"{person.get('passport_series') or ''}{person.get('passport_number') or ''}"
    api_client.roommate(own, partner_series)
    return person


def append_pair(main_row, partner_row):
    main = api_client.register(_payload(main_row))
    partner = api_client.register(_payload(partner_row))
    a = f"{main.get('passport_series') or ''}{main.get('passport_number') or ''}"
    b = f"{partner.get('passport_series') or ''}{partner.get('passport_number') or ''}"
    api_client.roommate(a, b)
    return main, partner


def update_row(participant_id, row):
    p = _payload(row)
    patch = {"fio": p["fio"], "dob": p["dob"], "phone": p["phone"],
             "passport_series": p["passport_series"], "passport_number": "",
             "passport_expiry": p["passport_expiry"], "xona_turi": p["xona_turi"],
             "passport_file_url": p["passport_file_url"]}
    return api_client.update(patch, participant_id=participant_id)


def regroup_partners():
    # SQLite uses xona_guruhi, so physical row reordering is unnecessary.
    return 0


def fetch_registrants():
    out = []
    for p in api_client.recipients():
        name, surname = _names(p.get("fio"))
        out.append({"row": p["id"], "toifa": _category(p.get("category")),
                    "label": "Sherik (oila a'zosi)" if p.get("category") == "oila" else "Asosiy",
                    "name": name, "surname": surname, "phone": p.get("phone") or "",
                    "series": f"{p.get('passport_series') or ''}{p.get('passport_number') or ''}",
                    "room": p.get("xona_turi") or p.get("room") or "",
                    "partner_series": p.get("roommate_series") or "",
                    "tg_id": str(p.get("telegram_id") or ""), "xona_guruhi": p.get("xona_guruhi")})
    return out


def room_groups():
    groups = {}
    for p in fetch_registrants():
        groups.setdefault(p.get("xona_guruhi") or f"solo:{p['row']}", []).append(p)
    return list(groups.values())
