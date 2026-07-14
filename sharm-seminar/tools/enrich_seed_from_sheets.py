#!/usr/bin/env python3
"""One-time migration: enrich the seminar seed from the legacy Yaxlit sheet.

The resulting JSON is self-contained; production runtime does not call Sheets.
"""
import json
import os
import re
import sys

import gspread
from google.oauth2.service_account import Credentials

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_ROOT = os.path.dirname(ROOT)
sys.path.insert(0, BOT_ROOT)
import config  # noqa: E402


def name_key(value):
    return " ".join(sorted(re.findall(r"[A-Z0-9]+", str(value or "").upper())))


def split_passport(series, number):
    series = re.sub(r"[^A-Z]", "", str(series or "").upper())
    number = re.sub(r"[^0-9]", "", str(number or ""))
    return series, number


def passport_key(value):
    value = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    match = re.fullmatch(r"([A-Z]*)([0-9]+)", value)
    return (match.group(1), int(match.group(2))) if match else (value, None)


def category(value):
    low = str(value or "").lower()
    if "oila" in low:
        return "oila"
    if "shifokor" in low or "diller" in low:
        return "shifokor_diller"
    return "xodim" if "xodim" in low else None


def main():
    seed_path = os.path.join(ROOT, "data", "seed_participants.json")
    with open(seed_path, encoding="utf-8") as fh:
        seed = json.load(fh)
    creds = Credentials.from_service_account_file(
        config.CREDENTIALS_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sheet = gspread.authorize(creds).open_by_key(config.SHEET_ID).worksheet("Yaxlit roʻyxat")
    rows = sheet.get_all_values()[1:]
    registry = gspread.authorize(creds).open_by_key(config.SHEET_ID).worksheet("Roʻyxat").get_all_values()
    registry = [r for r in registry if len(r) >= 10 and r[0] != "Sana"]
    registry_passport = {passport_key(r[9]): r for r in registry}
    registry_name = {name_key(f"{r[5]} {r[4]}"): r for r in registry}
    by_name = {name_key(row[0]): row for row in rows if row}
    matched = 0
    for participant in seed:
        row = by_name.get(name_key(participant.get("fio")))
        if not row:
            continue
        row += [""] * (13 - len(row))
        ps, pn = split_passport(row[3], row[4])
        participant.update({
            "passport_series": ps, "passport_number": pn,
            "passport_expiry": row[6], "dob": row[2],
            "phone": row[10], "telegram_username": row[11],
        })
        reg = registry_passport.get(passport_key(ps + pn)) or registry_name.get(name_key(participant["fio"]))
        if reg:
            participant["category"] = category(reg[2])
            participant["phone"] = participant.get("phone") or reg[8]
            participant["telegram_username"] = participant.get("telegram_username") or reg[1]
            if len(reg) > 15 and reg[15]:
                participant["telegram_id"] = reg[15]
            if len(reg) > 14 and reg[14]:
                participant["roommate_series"] = reg[14]
                if participant.get("category") == "oila":
                    participant["main_series"] = reg[14]
        matched += 1
    with open(seed_path, "w", encoding="utf-8") as fh:
        json.dump(seed, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"Enriched {matched}/{len(seed)} participants")


if __name__ == "__main__":
    main()
