#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import the authoritative participant list from ``acoustic2026seminar.xlsx``.

The spreadsheet is the single source of truth for a person's identity: full
name, sex, date of birth, citizenship and — most importantly — the passport
series/number that every QR token is derived from.  Everything the spreadsheet
does not know about (seminar group, group leader, category, room type/block,
Telegram link) is kept exactly as it already is in ``sharm.db``.

Matching strategy, in order:

1. by normalized full name — the common case, 100 of 104 rows;
2. by row position among the leftovers — a participant who dropped out and was
   replaced by somebody else inherits the same ``ACO-xxx`` slot together with
   its group and room, while the Telegram link is cleared because the seat now
   belongs to a different human.

The script is idempotent: running it twice changes nothing the second time.
It rewrites ``data/seed_participants.json`` so a fresh deployment seeds the
very same data, and never deletes rows or touches unrelated columns.

    python tools/import_xlsx.py [path/to/acoustic2026seminar.xlsx] [--dry-run]
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import unicodedata

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "sharm.db")
SEED_PATH = os.path.join(BASE_DIR, "data", "seed_participants.json")
DEFAULT_XLSX = os.path.join(BASE_DIR, "data", "acoustic2026seminar.xlsx")

# Spreadsheet header -> internal field.  Order follows the file itself.
COLUMNS = ["fio", "jinsi", "dob", "passport_series", "passport_number",
           "passport_issued", "passport_expiry", "passport_issuer",
           "fuqarolik", "doc_type"]

# Columns copied from the spreadsheet into the database.
DB_FIELDS = ["fio", "jinsi", "dob", "fuqarolik",
             "passport_series", "passport_number", "passport_expiry"]

# Columns of an existing row that survive a replacement (same seat, new person).
SEAT_FIELDS = ["grp", "leader", "room", "branch", "roles",
               "xona_turi", "xona_guruhi", "kelish", "category"]


def norm_name(value):
    """Fold a full name to a comparable key (case, spacing and accents)."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("'", "").replace("’", ""))
    return " ".join(text.split())


def norm_date(value):
    """Return DD.MM.YYYY, the format the rest of the project already uses."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%d.%m.%Y")
    parts = re.split(r"[./\-]", str(value or "").strip())
    if len(parts) != 3 or not all(p.strip().isdigit() for p in parts):
        return str(value or "").strip()
    day, month, year = (p.strip() for p in parts)
    if len(day) == 4:  # YYYY-MM-DD
        year, month, day = day, month, year
    return f"{int(day):02d}.{int(month):02d}.{int(year):04d}"


def norm_series(value):
    """Passport series: letters and digits, upper case, no separators."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def norm_number(value):
    """Passport number keeps its leading zeroes, so it is handled as text."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"[^0-9]", "", str(value or "").strip())


def read_xlsx(path):
    try:
        import openpyxl
    except ImportError:
        sys.exit("openpyxl kerak: pip install openpyxl")
    workbook = openpyxl.load_workbook(path, data_only=True)
    rows = []
    for raw in workbook.active.iter_rows(min_row=2, values_only=True):
        record = dict(zip(COLUMNS, list(raw) + [None] * len(COLUMNS)))
        if not str(record["fio"] or "").strip():
            continue  # trailing empty row
        record["fio"] = " ".join(str(record["fio"]).split())
        record["jinsi"] = str(record["jinsi"] or "").strip()
        record["fuqarolik"] = str(record["fuqarolik"] or "").strip()
        record["doc_type"] = str(record["doc_type"] or "").strip()
        record["passport_issuer"] = str(record["passport_issuer"] or "").strip()
        record["passport_series"] = norm_series(record["passport_series"])
        record["passport_number"] = norm_number(record["passport_number"])
        for key in ("dob", "passport_issued", "passport_expiry"):
            record[key] = norm_date(record[key])
        rows.append(record)
    return rows


def match(sheet_rows, db_rows):
    """Pair spreadsheet rows with database rows.

    Returns ``(pairs, replaced)`` where *pairs* is a list of
    ``(db_row_or_None, sheet_row)`` and *replaced* lists the seats whose
    occupant changed.
    """
    by_name = {}
    for row in db_rows:
        by_name.setdefault(norm_name(row["fio"]), []).append(row)

    pairs, used = [None] * len(sheet_rows), set()
    for index, record in enumerate(sheet_rows):
        bucket = by_name.get(norm_name(record["fio"]), [])
        row = next((r for r in bucket if r["id"] not in used), None)
        if row is not None:
            used.add(row["id"])
            pairs[index] = row

    # Leftover spreadsheet rows take over leftover seats, keeping file order.
    free_seats = [r for r in db_rows if r["id"] not in used]
    replaced = []
    for index, record in enumerate(sheet_rows):
        if pairs[index] is not None or not free_seats:
            continue
        seat = free_seats.pop(0)
        pairs[index] = seat
        used.add(seat["id"])
        replaced.append((seat, record))
    return [(pairs[i], sheet_rows[i]) for i in range(len(sheet_rows))], replaced


def next_id(existing):
    seq = 1
    while f"ACO-{seq:03d}" in existing:
        seq += 1
    return f"ACO-{seq:03d}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", nargs="?", default=DEFAULT_XLSX)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--seed", default=SEED_PATH)
    parser.add_argument("--dry-run", action="store_true",
                        help="faqat hisobot; bazaga va seedga yozilmaydi")
    args = parser.parse_args()

    if not os.path.exists(args.xlsx):
        sys.exit(f"Fayl topilmadi: {args.xlsx}")
    sheet_rows = read_xlsx(args.xlsx)
    print(f"xlsx: {len(sheet_rows)} qator — {args.xlsx}")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    db_rows = list(con.execute("SELECT * FROM participants ORDER BY id"))
    print(f"baza: {len(db_rows)} ishtirokchi — {args.db}")

    pairs, replaced = match(sheet_rows, db_rows)
    for seat, record in replaced:
        print(f"  o'rin almashdi: {seat['id']} {seat['fio']!r} -> {record['fio']!r}")

    known_ids = {r["id"] for r in db_rows}
    replaced_ids = {seat["id"] for seat, _ in replaced}
    changed = created = 0
    seed = []
    for row, record in pairs:
        if row is None:
            pid = next_id(known_ids)
            known_ids.add(pid)
            created += 1
            current = {"id": pid}
        else:
            pid = row["id"]
            current = dict(row)

        patch = {field: record[field] for field in DB_FIELDS
                 if str(record[field] or "") != str(current.get(field) or "")}
        if pid in replaced_ids:
            # A new person on an old seat must not inherit the Telegram link.
            patch.update(telegram_id=None, telegram_username=None,
                         phone=None, registered_at=None, passport_file_url=None)

        if patch and not args.dry_run:
            if row is None:
                cols = ["id"] + list(patch)
                con.execute(f"INSERT INTO participants({','.join(cols)}) "
                            f"VALUES({','.join('?' for _ in cols)})",
                            [pid] + [patch[k] for k in patch])
            else:
                con.execute(f"UPDATE participants SET {','.join(k + '=?' for k in patch)} "
                            "WHERE id=?", [patch[k] for k in patch] + [pid])
        if patch:
            changed += 1
        current.update(patch)

        entry = {"id": pid}
        entry.update({field: record[field] for field in COLUMNS})
        for field in SEAT_FIELDS:
            value = current.get(field)
            if field == "roles":
                continue
            if value not in (None, "", 0) or field == "grp":
                entry[field] = value
        entry["group"] = entry.pop("grp", None)
        entry["leader"] = bool(current.get("leader"))
        seed.append(entry)

    if not args.dry_run:
        con.commit()
        with open(args.seed, "w", encoding="utf-8") as fh:
            json.dump(seed, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    con.close()

    missing = [e["id"] for e in seed if not e["passport_number"]]
    print(f"yangilandi: {changed} · qo'shildi: {created} · almashdi: {len(replaced)}")
    print(f"pasportsiz: {len(missing)} {missing[:10]}")
    print(f"seed yozildi: {args.seed}" if not args.dry_run else "dry-run — hech narsa yozilmadi")


if __name__ == "__main__":
    main()
