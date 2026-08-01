#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dastur, check-in nuqtalari, mas'uliyatlar va tadbir ma'lumotini uch tilga o'tkazish.

Har bir matn maydoni ``"Kelish kuni"`` o'rniga
``{"uz": "Kelish kuni", "ru": "День приезда", "en": "Arrival day"}`` ko'rinishiga
keladi.  Eski oddiy matn ham ishlashda davom etadi (sahifa uni `uz` deb oladi),
shuning uchun migratsiya xavfsiz va **idempotent**: ikkinchi marta ishga
tushirilsa hech narsa o'zgarmaydi, qo'lda kiritilgan tarjima ustidan yozilmaydi.

    python tools/translate_content.py            # bazaga yozadi
    python tools/translate_content.py --dry-run  # faqat hisobot
"""
import argparse
import json
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "sharm.db")
LANGS = ("uz", "ru", "en")

# O'zbekcha matn -> (ruscha, inglizcha).  Rasmiy, sayqallangan uslub.
TR = {
    # ── kunlar ──
    "Kelish kuni": ("День приезда", "Arrival day"),
    "Yahtaga chiqish": ("Морская прогулка", "Yacht excursion"),
    "SIGNIA DAY": ("SIGNIA DAY", "SIGNIA DAY"),
    "GN RESOUND DAY": ("GN RESOUND DAY", "GN RESOUND DAY"),
    "ACOUSTIC DAY": ("ACOUSTIC DAY", "ACOUSTIC DAY"),
    "Sayohat va dam olish": ("Экскурсия и отдых", "Excursion and leisure"),
    "Madaniy va jamoaviy dastur": ("Культурная и командная программа",
                                   "Cultural and team programme"),
    "Qaytish kuni": ("День отъезда", "Departure day"),
    "Welcome to Acoustic 2026": ("Welcome to Acoustic 2026", "Welcome to Acoustic 2026"),
    "Acoustic Academy": ("Acoustic Academy", "Acoustic Academy"),
    # ── hafta kunlari ──
    "Dushanba": ("Понедельник", "Monday"),
    "Seshanba": ("Вторник", "Tuesday"),
    "Chorshanba": ("Среда", "Wednesday"),
    "Payshanba": ("Четверг", "Thursday"),
    "Juma": ("Пятница", "Friday"),
    "Shanba": ("Суббота", "Saturday"),
    "Yakshanba": ("Воскресенье", "Sunday"),
    # ── sanalar ──
    "8-avgust": ("8 августа", "8 August"),
    "9-avgust": ("9 августа", "9 August"),
    "10-avgust": ("10 августа", "10 August"),
    "11-avgust": ("11 августа", "11 August"),
    "12-avgust": ("12 августа", "12 August"),
    "13-avgust": ("13 августа", "13 August"),
    "14-avgust": ("14 августа", "14 August"),
    "15-avgust": ("15 августа", "15 August"),
    # ── bo'lim nomlari ──
    "Ertalab": ("Утро", "Morning"),
    "Kechqurun": ("Вечер", "Evening"),
    "Welcome Dinner": ("Welcome Dinner", "Welcome Dinner"),
    "Ekskursiya": ("Экскурсия", "Excursion"),
    "Variantlar": ("Варианты на выбор", "Options"),
    "Yakuniy sessiya": ("Заключительная сессия", "Closing session"),
    "Kechqurun — Farewell Dinner": ("Вечер — Farewell Dinner", "Evening — Farewell Dinner"),
    "Panel Discussion — “Acoustic 2030”": ("Панельная дискуссия — «Acoustic 2030»",
                                           "Panel discussion — “Acoustic 2030”"),
    # ── dastur bandlari ──
    "Toshkent xalqaro aeroportida kutib olish": (
        "Встреча в международном аэропорту Ташкента",
        "Welcome at Tashkent International Airport"),
    "Sharm Al-Sheyxga yetib borish": ("Прибытие в Шарм-эль-Шейх",
                                      "Arrival in Sharm El Sheikh"),
    "Mehmonxonaga transfer": ("Трансфер в отель", "Transfer to the hotel"),
    "Ro'yxatdan o'tish": ("Регистрация", "Registration"),
    "Xonalarga joylashish": ("Размещение в номерах", "Check-in to the rooms"),
    "Kechki ovqatga chiqish": ("Ужин", "Dinner"),
    "Receptionda to'planish": ("Сбор на ресепшене", "Meeting at the reception"),
    "Avtobuslarga o'tirib yo'lga chiqish": ("Посадка в автобусы и отправление",
                                            "Boarding the buses and departure"),
    "Mehmonxonaga qaytish": ("Возвращение в отель", "Return to the hotel"),
    "Erkin vaqt": ("Свободное время", "Free time"),
    "Tanishuv kechasi": ("Вечер знакомств", "Welcome evening"),
    "Ishtirokchilar bilan tanishish": ("Знакомство с участниками",
                                       "Getting to know the participants"),
    "Seminar dasturini taqdim etish": ("Презентация программы семинара",
                                       "Presentation of the seminar programme"),
    "Guruh fotosurati": ("Общая фотография", "Group photograph"),
    "Mavzu": ("Тема", "Session"),
    "Cofe break": ("Кофе-брейк", "Coffee break"),
    "Master class": ("Мастер-класс", "Master class"),
    "Tushlik": ("Обед", "Lunch"),
    "Signia xodimlari bilan umumiy surat": ("Общая фотография с командой Signia",
                                            "Group photograph with the Signia team"),
    "GN ReSound xodimlari bilan umumiy surat": (
        "Общая фотография с командой GN ReSound",
        "Group photograph with the GN ReSound team"),
    "Acoustic Vision": ("Acoustic Vision", "Acoustic Vision"),
    "Xizmat ko'rsatish standartlari": ("Стандарты обслуживания", "Service standards"),
    "CRM va mijoz bilan ishlash": ("CRM и работа с клиентом",
                                   "CRM and working with clients"),
    "Sotuv va marketing": ("Продажи и маркетинг", "Sales and marketing"),
    "Referral tizimi": ("Реферальная система", "Referral system"),
    "HR va KPI": ("HR и KPI", "HR and KPI"),
    "Filiallarni boshqarish": ("Управление филиалами", "Branch management"),
    "Power BI va sun'iy intellekt": ("Power BI и искусственный интеллект",
                                     "Power BI and artificial intelligence"),
    "Markaziy Osiyoda rivojlanish strategiyasi": (
        "Стратегия развития в Центральной Азии",
        "Growth strategy for Central Asia"),
    "Sertifikatlar topshirish": ("Вручение сертификатов", "Presentation of certificates"),
    "Eng faol ishtirokchilarni taqdirlash": ("Награждение самых активных участников",
                                             "Awards for the most active participants"),
    "Gala Dinner": ("Gala Dinner", "Gala Dinner"),
    "Ras Mohammed Milliy bog'i yoki": ("Национальный парк Рас-Мохаммед или",
                                       "Ras Mohammed National Park, or"),
    "Qayiq sayohati": ("Морская прогулка на катере", "Boat trip"),
    "Snorkeling va dengiz dam olish": ("Снорклинг и отдых у моря",
                                       "Snorkelling and time by the sea"),
    "Networking Dinner": ("Networking Dinner", "Networking Dinner"),
    "Cho'l Safari": ("Сафари по пустыне", "Desert safari"),
    "ATV safari": ("Сафари на квадроциклах", "ATV safari"),
    "Beduin qishlog'iga tashrif": ("Посещение бедуинской деревни",
                                   "Visit to a Bedouin village"),
    "Soho Square": ("Soho Square", "Soho Square"),
    "Eski Sharm bozori": ("Старый рынок Шарма", "Old Sharm market"),
    "Yakuniy xulosalar": ("Подведение итогов", "Closing remarks"),
    "Acoustic 2027 anjumani haqida e'lon": ("Анонс конференции Acoustic 2027",
                                            "Announcement of the Acoustic 2027 conference"),
    "Esdalik sovg'alari": ("Памятные подарки", "Commemorative gifts"),
    "Jamoaviy suratga tushish": ("Общая фотография", "Group photograph"),
    "Nonushta": ("Завтрак", "Breakfast"),
    "Mehmonxonadan chiqish": ("Выезд из отеля", "Hotel check-out"),
    "Aeroportga transfer": ("Трансфер в аэропорт", "Transfer to the airport"),
    "Toshkent va boshqa davlatlarga jo'nab ketish": (
        "Вылет в Ташкент и другие страны",
        "Departure to Tashkent and other countries"),
    # ── check-in nuqtalari ──
    "Aeroport": ("Аэропорт", "Airport"),
    "Mehmonxona": ("Отель", "Hotel"),
    "Seminar": ("Семинар", "Seminar"),
    "Ovqatlanish": ("Питание", "Meals"),
    # ── mas'uliyatlar ──
    "Koordinator": ("Координатор", "Coordinator"),
    "Seminar tayyorgarligi": ("Подготовка семинара", "Seminar preparation"),
    "Transfer / logistika": ("Трансфер и логистика", "Transfers and logistics"),
    "Dengiz / qayiq sayohati": ("Море и морская прогулка", "Sea and boat trip"),
    "Shahar aylanish / madaniy": ("Городская и культурная программа",
                                  "City tour and cultural programme"),
    "Gala / kechalar": ("Гала и вечерние мероприятия", "Gala and evening events"),
    # ── tadbir ma'lumoti ──
    "ACOUSTIC 2026": ("ACOUSTIC 2026", "ACOUSTIC 2026"),
    "Xalqaro seminar-trening": ("Международный семинар-тренинг",
                                "International seminar and training"),
    "8–15 avgust 2026": ("8–15 августа 2026", "8–15 August 2026"),
    "Sharm El Sheikh, Misr": ("Шарм-эль-Шейх, Египет", "Sharm El Sheikh, Egypt"),
}

missing = set()


def localize(value):
    """Matnni uch tilli obyektga aylantiradi. Allaqachon obyekt bo'lsa — tegmaydi."""
    if isinstance(value, dict):
        return value, False               # qo'lda kiritilgan tarjima saqlanadi
    if not isinstance(value, str) or not value.strip():
        return value, False
    text = value.strip()
    pair = TR.get(text)
    if not pair:
        missing.add(text)
        return {"uz": value, "ru": value, "en": value}, True
    return {"uz": value, "ru": pair[0], "en": pair[1]}, True


def walk(node, fields):
    """`fields` dagi maydonlarni joyida uch tilli qiladi. O'zgargan soni qaytadi."""
    changed = 0
    if isinstance(node, list):
        for item in node:
            changed += walk(item, fields)
    elif isinstance(node, dict):
        for key, value in list(node.items()):
            if key in fields:
                new, did = localize(value)
                if did:
                    node[key] = new
                    changed += 1
            elif isinstance(value, (list, dict)):
                changed += walk(value, fields)
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    # Qaysi sozlamada qaysi maydonlar matn ekani.
    targets = {
        "program":     {"title", "subtitle", "dow", "date", "label", "text"},
        "checkpoints": {"label"},
        "roles":       {"label"},
        "meta":        {"title", "subtitle", "dates", "location"},
    }

    total = 0
    for key, fields in targets.items():
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            continue
        data = json.loads(row["value"])
        changed = walk(data, fields)
        total += changed
        print(f"{key:12} — {changed} ta maydon uch tilli qilindi")
        if changed and not args.dry_run:
            con.execute("UPDATE settings SET value=? WHERE key=?",
                        (json.dumps(data, ensure_ascii=False), key))

    if not args.dry_run:
        con.commit()
    con.close()

    if missing:
        print("\nTarjimasi topilmagan (uch tilda ham o'zbekcha qoldirildi — "
              "panelda qo'lda kiritishingiz mumkin):")
        for text in sorted(missing):
            print("  ·", text)
    print(f"\nJami: {total}" + (" (dry-run, yozilmadi)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
