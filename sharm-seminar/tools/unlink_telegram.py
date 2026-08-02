#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Barcha Telegram bog'lanishlarini tozalash.

Har bir ishtirokchining `telegram_id` va `telegram_username` maydonlari
bo'shatiladi, shunda hamma botga qaytadan `/start` bosib **pasport seriya va
raqami** bilan o'zini tasdiqlaydi.  Bu eski, ishonchsiz bog'lanishlarni
(bir odamga ikki kishi biriktirilgani, admin useri boshqalarga yozilgani)
tozalaydi.

O'chirilgan bog'lanishlar JSON faylga yozib qo'yiladi, kerak bo'lsa qaytarish
mumkin.  Ishtirokchilarning o'zi, guruhi, xonasi, roli va **QR tokeni**
tegilmaydi — token pasportdan olinadi, shuning uchun beyjiklar ishlashda
davom etadi.

    python tools/unlink_telegram.py --dry-run
    python tools/unlink_telegram.py
    python tools/unlink_telegram.py --restore data/telegram_links_backup.json
"""
import argparse
import json
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "sharm.db")
BACKUP = os.path.join(BASE_DIR, "data", "telegram_links_backup.json")


def linked(con):
    return [dict(r) for r in con.execute(
        "SELECT id,fio,telegram_id,telegram_username,registered_at FROM participants "
        "WHERE telegram_id IS NOT NULL AND telegram_id<>'' ORDER BY id")]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--backup", default=BACKUP)
    parser.add_argument("--restore", metavar="FAYL",
                        help="oldin saqlangan bog'lanishlarni qaytarish")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    if args.restore:
        if not os.path.exists(args.restore):
            sys.exit(f"Fayl topilmadi: {args.restore}")
        with open(args.restore, encoding="utf-8") as fh:
            rows = json.load(fh)
        for r in rows:
            con.execute("UPDATE participants SET telegram_id=?,telegram_username=?,registered_at=? "
                        "WHERE id=?", (r.get("telegram_id"), r.get("telegram_username"),
                                       r.get("registered_at"), r["id"]))
        con.commit(); con.close()
        print(f"Qaytarildi: {len(rows)} ta bog'lanish")
        return

    rows = linked(con)
    print(f"Bog'langan: {len(rows)} kishi")
    for r in rows:
        print(f"  {r['id']}  {r['fio']:32} {r['telegram_id']}  {r['telegram_username'] or ''}")

    # Bir xil telegram_id ikki odamga biriktirilgan holatlar — aynan shu tozalanadi.
    seen = {}
    for r in rows:
        seen.setdefault(str(r["telegram_id"]), []).append(r["id"])
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    if clashes:
        print("\nBir xil Telegram ID bir necha odamda:")
        for tid, ids in clashes.items():
            print(f"  {tid} -> {', '.join(ids)}")

    if args.dry_run:
        print("\ndry-run — hech narsa o'zgarmadi")
        con.close()
        return

    with open(args.backup, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    con.execute("UPDATE participants SET telegram_id=NULL, telegram_username=NULL, "
                "registered_at=NULL WHERE telegram_id IS NOT NULL AND telegram_id<>''")
    con.commit()
    left = len(linked(con))
    con.close()
    print(f"\nTozalandi. Qolgan bog'lanish: {left}")
    print(f"Zaxira: {args.backup}")
    print("Endi hamma botga /start bosib pasporti bilan qaytadan tasdiqlaydi.")


if __name__ == "__main__":
    main()
