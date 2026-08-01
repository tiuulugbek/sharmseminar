#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Panelga kirish rolini belgilash.

    python tools/set_role.py                      # joriy rollarni ko'rsatadi
    python tools/set_role.py ACO-004 admin        # texnik admin
    python tools/set_role.py ACO-012 manager      # rahbar (barcha guruhlar)
    python tools/set_role.py "Musaev" leader      # guruh mas'uli (ism bo'yicha ham)
    python tools/set_role.py ACO-020 member       # oddiy ishtirokchi

Rollar:
    admin    — hamma narsa, sozlamalar ham
    manager  — barcha guruhlar, check-in, ro'yxatlar (sozlamalarsiz)
    leader   — faqat o'z guruhi
    member   — faqat o'zi haqidagi ma'lumot

`leader` rolini bu skript orqali qo'yish `participants.leader` bayrog'ini ham
yoqadi, ya'ni guruhda mas'ul sifatida ko'rinadi.
"""
import argparse
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "sharm.db")
ROLES = ("admin", "manager", "leader", "member")


def resolve(con, needle):
    """ACO-xxx, pasport yoki ism bo'lagi bo'yicha bitta ishtirokchi."""
    needle = needle.strip()
    row = con.execute("SELECT * FROM participants WHERE UPPER(id)=?", (needle.upper(),)).fetchone()
    if row:
        return row
    matches = con.execute("SELECT * FROM participants WHERE LOWER(fio) LIKE ?",
                          (f"%{needle.lower()}%",)).fetchall()
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit(f"Topilmadi: {needle}")
    sys.exit("Bir nechta odam mos keldi, ACO raqamini yozing:\n" +
             "\n".join(f"  {m['id']}  {m['fio']}" for m in matches[:15]))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("who", nargs="?", help="ACO-xxx yoki ism")
    parser.add_argument("role", nargs="?", choices=ROLES)
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    if not args.who:
        print("Panel rollari (bo'sh = oddiy ishtirokchi):\n")
        rows = con.execute("SELECT id,fio,grp,leader,panel_role FROM participants "
                           "WHERE (panel_role IS NOT NULL AND panel_role<>'') OR leader=1 "
                           "ORDER BY panel_role DESC, grp, id").fetchall()
        if not rows:
            print("  hech kimga rol berilmagan")
        for r in rows:
            role = r["panel_role"] or ("leader" if r["leader"] else "member")
            print(f"  {r['id']}  {role:8}  {r['fio']}"
                  + (f"  ({r['grp']}-guruh)" if r["grp"] else ""))
        env = os.environ.get("PANEL_ADMIN_IDS", "")
        if env:
            print(f"\n.env dagi PANEL_ADMIN_IDS: {env}")
        con.close()
        return

    if not args.role:
        sys.exit("Rolni ham yozing: " + " | ".join(ROLES))

    row = resolve(con, args.who)
    con.execute("UPDATE participants SET panel_role=? WHERE id=?", (args.role, row["id"]))
    if args.role == "leader":
        # Guruhda bitta mas'ul bo'ladi.
        if row["grp"]:
            con.execute("UPDATE participants SET leader=0 WHERE grp=?", (row["grp"],))
        con.execute("UPDATE participants SET leader=1 WHERE id=?", (row["id"],))
    con.commit()
    print(f"{row['id']} · {row['fio']} → {args.role}")
    con.close()


if __name__ == "__main__":
    main()
