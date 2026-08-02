#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check-in nuqtalarini seminar dasturiga moslash.

Dastur kunlariga mos nuqtalar qo'shadi (Signia / GN ReSound / Acoustic kuni),
har biriga uch tilli nom va shu kunning vaqt oynasini beradi, hamda hammasini
dastur tartibiga qo'yadi.

**Idempotent**: mavjud nuqta o'chirilmaydi va uning check-inlariga tegilmaydi;
qo'lda o'zgartirilgan nom yoki vaqt oynasi ustidan yozilmaydi.

    python tools/seed_checkpoints.py --dry-run
    python tools/seed_checkpoints.py
"""
import argparse
import json
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "sharm.db")

# Dastur bo'yicha kutilayotgan nuqtalar, tartibi bilan.
# `starts_at`/`ends_at` — tadbir vaqti (Misr, UTC+3).
PLAN = [
    {"key": "aeroport", "icon": "✈️",
     "label": {"uz": "Aeroport", "ru": "Аэропорт", "en": "Airport"},
     "starts_at": "2026-08-08T06:00", "ends_at": "2026-08-08T23:59"},
    {"key": "mehmonxona", "icon": "🏨",
     "label": {"uz": "Mehmonxona", "ru": "Отель", "en": "Hotel"},
     "starts_at": "2026-08-08T18:00", "ends_at": "2026-08-09T12:00"},
    {"key": "yahta", "icon": "⛵",
     "label": {"uz": "Yahtaga chiqish", "ru": "Морская прогулка", "en": "Yacht excursion"},
     "starts_at": "2026-08-09T07:00", "ends_at": "2026-08-09T23:59"},
    {"key": "signia_day", "icon": "🎤",
     "label": {"uz": "Signia kuni", "ru": "День Signia", "en": "Signia day"},
     "starts_at": "2026-08-10T08:00", "ends_at": "2026-08-10T23:59"},
    {"key": "resound_day", "icon": "🎧",
     "label": {"uz": "GN ReSound kuni", "ru": "День GN ReSound", "en": "GN ReSound day"},
     "starts_at": "2026-08-11T08:00", "ends_at": "2026-08-11T23:59"},
    {"key": "acoustic_day", "icon": "🎓",
     "label": {"uz": "Acoustic kuni", "ru": "День Acoustic", "en": "Acoustic day"},
     "starts_at": "2026-08-12T08:00", "ends_at": "2026-08-12T23:59"},
    {"key": "ekskursiya", "icon": "🏝",
     "label": {"uz": "Sayohat", "ru": "Экскурсия", "en": "Excursion"},
     "starts_at": "2026-08-13T07:00", "ends_at": "2026-08-13T23:59"},
    {"key": "madaniy", "icon": "🐫",
     "label": {"uz": "Madaniy dastur", "ru": "Культурная программа",
               "en": "Cultural programme"},
     "starts_at": "2026-08-14T07:00", "ends_at": "2026-08-14T23:59"},
    {"key": "qaytish", "icon": "🛫",
     "label": {"uz": "Qaytish", "ru": "Отъезд", "en": "Departure"},
     "starts_at": "2026-08-15T04:00", "ends_at": "2026-08-15T23:59"},
]

ORDER = [c["key"] for c in PLAN]


def label_matches(existing, planned):
    """Nom o'zgartirilmaganmi? Uch tilli ham, oddiy matn ham tekshiriladi."""
    if isinstance(existing, dict):
        return existing.get("uz") == planned["uz"]
    return existing == planned["uz"]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT value FROM settings WHERE key='checkpoints'").fetchone()
    current = json.loads(row["value"]) if row else []
    by_key = {c["key"]: c for c in current}

    # Eski "Yahtaga chiqish" tasodifiy kalit bilan yaratilgan bo'lishi mumkin.
    for c in current:
        if c["key"] not in ORDER and label_matches(c.get("label"), PLAN[2]["label"]):
            old_key = c["key"]
            c["key"] = "yahta"
            by_key.pop(old_key, None)
            by_key["yahta"] = c
            moved = con.execute("UPDATE checkins SET checkpoint='yahta' WHERE checkpoint=?",
                                (old_key,)).rowcount
            print(f"eski '{old_key}' nuqtasi 'yahta' kalitiga o'tkazildi "
                  f"({moved} ta check-in ko'chirildi)")

    added, filled = [], []
    for planned in PLAN:
        existing = by_key.get(planned["key"])
        if existing is None:
            current.append(dict(planned))
            by_key[planned["key"]] = current[-1]
            added.append(planned["label"]["uz"])
            continue
        # Bor nuqtaga faqat yetishmaydigan qismini qo'shamiz.
        for field in ("starts_at", "ends_at"):
            if not existing.get(field):
                existing[field] = planned[field]
                filled.append(f"{planned['label']['uz']}·{field}")
        if not existing.get("icon"):
            existing["icon"] = planned["icon"]

    # Dastur tartibi; ro'yxatda bo'lmagan qo'lda qo'shilgan nuqtalar oxirida qoladi.
    current.sort(key=lambda c: ORDER.index(c["key"]) if c["key"] in ORDER else len(ORDER))

    print("Yakuniy tartib:")
    for i, c in enumerate(current, 1):
        label = c["label"]["uz"] if isinstance(c["label"], dict) else c["label"]
        window = f"{c.get('starts_at','')} → {c.get('ends_at','')}".strip(" →")
        print(f"  {i}. {c.get('icon','•')} {label:22} {window}")
    print(f"\nqo'shildi: {added or '—'}")
    print(f"vaqt oynasi to'ldirildi: {filled or '—'}")

    if args.dry_run:
        print("\ndry-run — yozilmadi")
        return
    con.execute("INSERT INTO settings(key,value) VALUES('checkpoints',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(current, ensure_ascii=False),))
    con.commit()
    con.close()
    print("\nsaqlandi")


if __name__ == "__main__":
    main()
