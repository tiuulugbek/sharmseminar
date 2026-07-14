from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADER = [
    "Sana",
    "Kim kiritdi (Telegram)",
    "Toifa",
    "Asosiy / Sherik",
    "Ism",
    "Familya",
    "Tug'ilgan sana",
    "Yosh",
    "Telefon",
    "Pasport seriya",
    "Amal muddati",
    "Xona",
    "100% to'lov",
    "Pasport (Drive havola)",
    "Sherik (seriya)",
    "Telegram ID",
]

_worksheet = None


def _get_worksheet():
    global _worksheet
    if _worksheet is not None:
        return _worksheet

    creds = Credentials.from_service_account_file(config.CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    sh = client.open_by_key(config.SHEET_ID)

    try:
        ws = sh.worksheet(config.WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=config.WORKSHEET_NAME, rows=1000, cols=len(HEADER))

    # Sarlavhani tekshiramiz/yangilaymiz
    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(HEADER, value_input_option="USER_ENTERED")
    elif first_row != HEADER:
        ws.update([HEADER], "A1", value_input_option="USER_ENTERED")

    _worksheet = ws
    return ws


def append_rows(rows: list[list]):
    """Bir nechta qatorni Sheets'ga qo'shadi."""
    ws = _get_worksheet()
    for row in rows:
        ws.append_row(row, value_input_option="USER_ENTERED")


def fetch_series() -> set[str]:
    """Jadvalda allaqachon kiritilgan barcha pasport seriyalarini qaytaradi."""
    ws = _get_worksheet()
    col = HEADER.index("Pasport seriya") + 1
    values = ws.col_values(col)
    return {v.strip().upper() for v in values[1:] if v.strip()}


def find_row_by_series(series: str):
    """Pasport seriyasi bo'yicha qatorni topadi.
    (qator_raqami_1based, to'liq_qator_qiymatlari) yoki (None, None) qaytaradi.
    """
    ws = _get_worksheet()
    target = (series or "").strip().upper()
    if not target:
        return None, None
    series_col = HEADER.index("Pasport seriya")
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:  # sarlavha
            continue
        if len(row) > series_col and row[series_col].strip().upper() == target:
            full = list(row) + [""] * (len(HEADER) - len(row))
            return i + 1, full[: len(HEADER)]
    return None, None


def _parse_date_any(text: str):
    """Sanani turli formatlardan (dd-mm-yyyy, dd.mm.yyyy, yyyy-mm-dd) o'qiydi."""
    text = (text or "").strip().replace("/", ".").replace("-", ".")
    for fmt in ("%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def find_rows_by_birthdate(birthdate: str) -> list[tuple[int, list]]:
    """Tug'ilgan sana bo'yicha mos qatorlarni qaytaradi: [(qator_1based, to'liq_qator), ...].
    Format farqlariga chidamli (ikkala tomon ham sana sifatida solishtiriladi)."""
    target = _parse_date_any(birthdate)
    if not target:
        return []
    ws = _get_worksheet()
    bd_col = HEADER.index("Tug'ilgan sana")
    rows = ws.get_all_values()
    out = []
    for i, row in enumerate(rows):
        if i == 0:  # sarlavha
            continue
        if len(row) > bd_col and _parse_date_any(row[bd_col]) == target:
            full = list(row) + [""] * (len(HEADER) - len(row))
            out.append((i + 1, full[: len(HEADER)]))
    return out


def update_row(row_index: int, values: list):
    """Berilgan qatorni (1-asosli) to'liq yangi qiymatlar bilan ustiga yozadi."""
    ws = _get_worksheet()
    end = gspread.utils.rowcol_to_a1(row_index, len(HEADER))
    rng = f"A{row_index}:{end}"
    ws.update([values[: len(HEADER)]], rng, value_input_option="USER_ENTERED")


def set_telegram_id(row_index: int, tg_id) -> None:
    """Berilgan qatorning «Telegram ID» katagini yangilaydi (faqat shu katak)."""
    ws = _get_worksheet()
    col = HEADER.index("Telegram ID") + 1
    ws.update_cell(row_index, col, str(tg_id))


def append_pair(main_row: list, partner_row: list):
    """Xodim va uning (shu yerda to'ldirilgan yangi) oila a'zosini jadvalga ketma-ket qo'shadi."""
    ws = _get_worksheet()
    ws.append_row(main_row, value_input_option="USER_ENTERED")
    ws.append_row(partner_row, value_input_option="USER_ENTERED")


def _norm(s) -> str:
    return (s or "").strip().upper()


def _grouped_order(data: list[list]) -> list[int]:
    """Sheriklarni (Pasport seriya <-> Sherik (seriya)) bog'lanishi bo'yicha guruhlab,
    yangi qator tartibini (asl indekslar ro'yxati) qaytaradi. Bog'langan odamlar ketma-ket
    turadi; guruhlar eng erta a'zosi bo'yicha tartiblanadi; ichki tartib saqlanadi."""
    series_col = HEADER.index("Pasport seriya")
    partner_col = HEADER.index("Sherik (seriya)")

    series_of = [_norm(r[series_col]) if len(r) > series_col else "" for r in data]
    idx_by_series: dict[str, int] = {}
    for i, s in enumerate(series_of):
        if s and s not in idx_by_series:
            idx_by_series[s] = i

    parent = list(range(len(data)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i, row in enumerate(data):
        p = _norm(row[partner_col]) if len(row) > partner_col else ""
        if p and p in idx_by_series:
            union(i, idx_by_series[p])

    groups: dict[int, list[int]] = {}
    for i in range(len(data)):
        groups.setdefault(find(i), []).append(i)

    ordered = sorted(groups.values(), key=min)
    return [i for g in ordered for i in g]


def backup_worksheet(title_suffix: str) -> str:
    """Joriy varaqning zaxira nusxasini yaratadi (qayta tartiblashdan oldin xavfsizlik uchun)."""
    ws = _get_worksheet()
    backup_title = f"Zaxira_{title_suffix}"
    dup = ws.duplicate(new_sheet_name=backup_title)
    return dup.title


def regroup_partners() -> int:
    """Butun jadvalni sheriklar ketma-ket turadigan qilib qayta tartiblaydi.
    O'zgartirilgan (joyi siljigan) qatorlar sonini qaytaradi."""
    ws = _get_worksheet()
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return 0

    header = rows[0]
    data = [r for r in rows[1:] if any(c.strip() for c in r)]

    new_order = _grouped_order(data)
    moved = sum(1 for new, old in enumerate(new_order) if new != old)
    if moved == 0:
        return 0

    # Har bir qatorni HEADER uzunligiga tekislaymiz
    new_data = []
    for i in new_order:
        r = list(data[i])
        if len(r) < len(HEADER):
            r += [""] * (len(HEADER) - len(r))
        new_data.append(r[: len(HEADER)])

    end = gspread.utils.rowcol_to_a1(1 + len(new_data), len(HEADER))
    ws.update(new_data, f"A2:{end}", value_input_option="RAW")
    return moved


def append_linked(main_row: list, partner_series: str):
    """Asosiy odam (main_row) ni qo'shib, uning sherigini (partner_series bo'yicha topiladi)
    darhol uning tagiga ko'chiradi — ikkalasi jadvalda ketma-ket turadi.
    Ikkala qatorning xonasi «2 kishilik» qilib belgilanadi va o'zaro bog'lanadi.
    """
    ws = _get_worksheet()
    series_col = HEADER.index("Pasport seriya")
    room_col = HEADER.index("Xona")
    partner_col = HEADER.index("Sherik (seriya)")

    target = (partner_series or "").strip().upper()
    rows = ws.get_all_values()

    p_idx = None   # sherikning 1-asosli (1-based) qator raqami
    p_row = None
    for i, row in enumerate(rows):
        if i == 0:  # sarlavha
            continue
        if len(row) > series_col and row[series_col].strip().upper() == target:
            p_idx = i + 1
            p_row = list(row)
            break

    # A ni jadval oxiriga qo'shamiz (sherik qatori bundan yuqorida qoladi)
    ws.append_row(main_row, value_input_option="USER_ENTERED")

    if p_idx is None or p_row is None:
        return  # sherik topilmadi (kutilmagan holat) — A baribir yozildi

    # Sherik qatorini to'ldirib, xonasini yangilab, A dan keyinga ko'chiramiz
    while len(p_row) < len(HEADER):
        p_row.append("")
    room_col_main = HEADER.index("Xona")
    p_row[room_col] = main_row[room_col_main] if main_row[room_col_main] else "2 kishilik"
    p_row[partner_col] = main_row[series_col]  # o'zaro bog'lash
    ws.delete_rows(p_idx)
    ws.append_row(p_row, value_input_option="USER_ENTERED")


# ──────────────────────────── Xabar yuborish uchun o'qish ────────────────────────────
def fetch_registrants() -> list[dict]:
    """Jadvaldagi barcha ro'yxatdan o'tganlarni dict ko'rinishida qaytaradi.
    Har bir element: row, name, surname, phone, series, partner_series, room, tg_id, toifa, label.
    """
    ws = _get_worksheet()
    rows = ws.get_all_values()
    cols = {
        "toifa": HEADER.index("Toifa"),
        "label": HEADER.index("Asosiy / Sherik"),
        "name": HEADER.index("Ism"),
        "surname": HEADER.index("Familya"),
        "phone": HEADER.index("Telefon"),
        "series": HEADER.index("Pasport seriya"),
        "room": HEADER.index("Xona"),
        "partner": HEADER.index("Sherik (seriya)"),
        "tg": HEADER.index("Telegram ID"),
    }
    out = []
    for i, r in enumerate(rows):
        if i == 0:  # sarlavha
            continue
        if not any(c.strip() for c in r):
            continue
        r = list(r) + [""] * (len(HEADER) - len(r))
        out.append({
            "row": i + 1,
            "toifa": r[cols["toifa"]].strip(),
            "label": r[cols["label"]].strip(),
            "name": r[cols["name"]].strip(),
            "surname": r[cols["surname"]].strip(),
            "phone": r[cols["phone"]].strip(),
            "series": r[cols["series"]].strip().upper(),
            "room": r[cols["room"]].strip(),
            "partner_series": r[cols["partner"]].strip().upper(),
            "tg_id": r[cols["tg"]].strip(),
        })
    return out


def room_groups() -> list[list[dict]]:
    """Ro'yxatdagilarni xona sheriklari (Pasport seriya <-> Sherik (seriya)) bo'yicha
    guruhlarga ajratadi. Har bir guruh — bir xonadagi odamlar ro'yxati (dict'lar)."""
    regs = fetch_registrants()

    idx_by_series: dict[str, int] = {}
    for i, p in enumerate(regs):
        if p["series"] and p["series"] not in idx_by_series:
            idx_by_series[p["series"]] = i

    parent = list(range(len(regs)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i, p in enumerate(regs):
        ps = p["partner_series"]
        if ps and ps in idx_by_series:
            union(i, idx_by_series[ps])

    groups: dict[int, list[dict]] = {}
    for i in range(len(regs)):
        groups.setdefault(find(i), []).append(regs[i])

    # Guruhlarni eng erta qatori bo'yicha tartiblaymiz
    return [g for _, g in sorted(groups.items(), key=lambda kv: kv[1][0]["row"])]
