# Codex CLI uchun vazifa: `sharm-bot` ni `sharm-seminar` (Flask + SQLite) tizimiga integratsiya qilish

## Kontekst
Serverda ikki loyiha bor:

1. **`sharm-bot`** — mavjud Telegram bot (`@sharmseminarbot`). Hozir ma'lumotni **Google Sheets**da saqlaydi, pasport fayllarini **Google Drive**ga yuklaydi. Vazifalari: ishtirokchini pasport seriyasi + tug'ilgan sana orqali aniqlash, Telegram ID saqlash, seminar guruhiga taklif, ro'yxatga olish (ism, toifa, tug'ilgan sana/yosh, telefon, pasport seriya/muddat/rasm, xona turi, xona sherigi), pasport tekshiruvi va dublikat cheklovi, 21 yoshdan kichiklar uchun ota-ona sharti, xona sheriklarini pasport seriyasi orqali ketma-ket bog'lash, oila a'zosini asosiy ishtirokchiga biriktirish (100% to'lov belgisi), ma'lumot yangilash, `/admin` panel (ommaviy xabar, test xabar, xona sheriklarini xabardor qilish, guruhga qo'shilmaganlarga taklif, statistika, sheriklarni qayta tartiblash), web dashboard.

2. **`sharm-seminar`** — yangi **Flask + SQLite** admin panel (men bergan kod). Fayllar: `server.py`, `static/index.html` (admin panel), `static/participant.html` (QR orqali ochiladigan shaxsiy sahifa), `data/seed_participants.json` (104 ishtirokchi), `data/seed_program.json` (8 kunlik dastur). SQLite jadvallar: `participants(id, fio, jinsi, fuqarolik, xona_turi, xona_guruhi, kelish, grp, leader, room, branch, telegram, roles)`, `checkins(pid, checkpoint, ts)`, `settings(key, value)`. Mavjud API: `/api/bootstrap`, `/api/participant`, `/api/participants/bulk`, `/api/checkin`, `/api/checkpoints`, `/api/program`, `/api/roles`, `/api/meta`, `/api/badge`, `/api/seminar-logo`, `/api/pagebase`, `/api/p/<id>` va `/p/<id>` (shaxsiy sahifa). Har beyjikdagi QR `<PUBLIC_URL>/p/<id>` ga olib boradi.

## Maqsad
`sharm-bot` ni shunday moslashtir: **ma'lumotning yagona manbasi `sharm-seminar` ning SQLite bazasi bo'lsin** (Google Sheets asosiy baza sifatida ishlatilmasin). Bot `sharm-seminar` ning Flask HTTP API'si orqali o'qiydi/yozadi. `@sharmseminarbot` shu loyihaning boti bo'ladi. Bot ishtirokchini aniqlagach yoki ro'yxatga olgach, unga **shaxsiy sahifa havolasi `<PUBLIC_URL>/p/<id>` va o'sha havola QR-kodini** yuboradi (skanlaganда seminar tartibi chiroyli ko'rinadi).

## Arxitektura qarori
- **Yagona manba:** `sharm-seminar/data/sharm.db` (SQLite). Ikkala loyiha bitta serverda ishlaydi.
- **Ulanish:** `sharm-bot` → `sharm-seminar` Flask API (`http://127.0.0.1:8000`) orqali. To'g'ridan-to'g'ri SQLite fayliga yozma. Bot uchun API `X-Bot-Token` sarlavhasi bilan himoyalanadi.
- **Google Drive** — saqlanadi (pasport rasm/hujjat fayllari uchun). Bot faylni Drive'ga yuklab, havolasini API orqali ishtirokchiga saqlaydi.
- **Google Sheets** — asosiy baza sifatida OLIB TASHLANADI. (Ixtiyoriy: kunlik "backup export" sifatida qoldirish mumkin, lekin o'qish/yozishning asosiy manbasi SQLite bo'ladi.)

## Ish tartibi (majburiy)
1. **Avval o'qi, keyin yoz.** Har ikki loyiha kodini to'liq ko'rib chiq (`sharm-bot` va `sharm-seminar`). `sharm-bot` dagi Google Sheets bilan ishlaydigan barcha funksiyalarni ro'yxatga ol (find, append, update, read-all, reorder, stats).
2. Ishni yangi git branch'da bajar: `feat/sqlite-integration`. Har bosqichda commit qil.
3. Hech qanday tokenni kodga yozma — hammasini `.env` / muhit o'zgaruvchilaridan ol.
4. Migratsiyalar **idempotent** bo'lsin (qayta ishga tushsa buzmasin). Mavjud admin panel (`static/index.html`) ishlashdan to'xtamasligi kerak.
5. Yakunida ikkala loyihani ishga tushirib, oqimlarni sinab ko'r (pastdagi "Qabul mezoni").

## 1-qism — `sharm-seminar` (Flask) ni kengaytir

### 1.1 DB sxemasini kengaytir (idempotent migratsiya bilan)
`participants` jadvaliga quyidagi ustunlarni qo'sh (bo'lmasa `ALTER TABLE`):
- `telegram_id TEXT`, `telegram_username TEXT`
- `category TEXT`  — toifa: `xodim` | `shifokor_diller` | `oila`
- `passport_series TEXT`, `passport_number TEXT`, `passport_expiry TEXT`
- `dob TEXT` (tug'ilgan sana), `phone TEXT`
- `passport_file_url TEXT` (Google Drive havolasi)
- `main_series TEXT` (oila a'zosi uchun asosiy ishtirokchining pasport seriyasi)
- `payment_full INTEGER DEFAULT 0` (oila a'zosi = 1)
- `registered_at TEXT`, `roommate_series TEXT` (xona sherigining pasport seriyasi)

**Muhim:** `data/seed_participants.json` da hozir pasport seriya/muddat/tug'ilgan sana YO'Q (faqat id, fio, jinsi, fuqarolik, xona_turi, xona_guruhi, kelish bor). Shu sabab pasport orqali qidiruv ishlashi uchun seedni to'ldirish kerak. Agar manba Excel (`Sharm_master.xlsx` yoki asl `Sharm_ro'yxat.xlsx`) mavjud bo'lsa, undan `passport_series`, `passport_number`, `passport_expiry`, `dob` ni import qilib seedni yangila. Agar manba yo'q bo'lsa — foydalanuvchidan so'ra (pasport ma'lumotli seed fayl kerak) va bu bosqichni belgilab qo'y.

### 1.2 Bot uchun API endpointlar qo'sh (barchasi `X-Bot-Token` bilan)
- `POST /api/bot/find` — `{series?, dob?, name?}` → mos ishtirokchilar ro'yxati `[{id, fio, dob, group, telegram_id, category}]`. Bir DOB bo'yicha bir nechta bo'lsa hammasini qaytar (bot ism tanlatadi).
- `POST /api/bot/link` — `{id, telegram_id, telegram_username}` → Telegram ID/username ni saqlaydi, `registered_at` ni belgilaydi. Agar shu telegram_id boshqa id'ga biriktirilgan bo'lsa — xatolik qaytar.
- `POST /api/bot/register` — yangi ishtirokchi (seedда yo'q, masalan oila a'zosi) yaratadi. Payload: barcha shaxsiy/pasport/xona maydonlari + `category`, `main_series`, `payment_full`. Yangi `id` (masalan `ACO-1xx`) generatsiya qilib qaytaradi. Pasport seriya dublikatini rad et.
- `POST /api/bot/update` — `{series | id, patch}` → ruxsat etilgan maydonlarni yangilaydi (ism, familya, dob, telefon, pasport seriya/muddat, xona turi, pasport fayl havolasi). O'zgargach `settings`ga "admin xabar" navbatiga yozib qo'y yoki `changed=true` qaytar (bot adminlarga xabar beradi).
- `POST /api/bot/roommate` — `{series, roommate_series}` → ikki ishtirokchini xona sherigi sifatida bog'laydi, `xona_guruhi` ni moslaydi (ketma-ketlik saqlanadi), sherik hali ro'yxatda bo'lmasa "kutish" holatini qaytaradi.
- `GET /api/bot/recipients` — `[{id, fio, telegram_id, group, room, xona_guruhi, roommates:[fio], category}]` — ommaviy xabar va xona-sherik bildirishnomalari uchun.
- `GET /api/bot/stats` — jami; telegram_id bor; telegram_id yo'q; DBL; TRPL; to'liq shakllangan xonalar; sherigi belgilanmaganlar.
- (Mavjud) `/api/p/<id>` va `/api/checkin` ni qayta ishlat.

Token: `BOT_API_TOKEN` muhit o'zgaruvchisidan olinadi; mos kelmasa `401`.

### 1.3 Web dashboard
`sharm-bot` dagi eski web dashboard funksiyalari allaqachon `sharm-seminar/static/index.html` panelida bor (jami, toifa/xona taqsimoti, sheriksizlar, umumiy jadval). Eski dashboardni takrorlash shart emas — kerak bo'lsa panelга yetishmayotgan ko'rsatkichni (masalan "sherigi topilmaganlar", "toifa bo'yicha") qo'sh.

## 2-qism — `sharm-bot` ni moslashtir

1. **Data qatlamini almashtir:** Google Sheets o'qish/yozish funksiyalarini `sharm-seminar` API'siga chaqiruvlar bilan almashtir (kichik `api_client.py` yoz: `find/link/register/update/roommate/recipients/stats/checkin`). Sheets kod yo'llarini olib tashla yoki `LEGACY_SHEETS=false` bilan o'chir.
2. **Telegram oqimlarini saqlab qol** (o'zgarmaydi, faqat manba SQLite bo'ladi):
   - pasport seriya + DOB orqali aniqlash; bir DOB'da ko'p bo'lsa ism tanlash;
   - Telegram ID ni saqlash (`/api/bot/link`), qayta taklif yubormaslik;
   - to'liq ro'yxatga olish oqimi (`/api/bot/register`), toifalar: xodim / shifokor-diller / oila;
   - pasport tekshiruvi: dublikat rad, amal muddati va sana formati tekshiruvi;
   - pasport rasm/fayl → Google Drive → havola `/api/bot/update` orqali saqlanadi;
   - 21 yoshdan kichik: Misr talablari haqida ogohlantirish + ota/ona birga borishi sharti; bo'lmasa ro'yxatni yakunlamaslik;
   - oila a'zosi asosiy ishtirokchiga biriktiriladi, `payment_full=1`;
   - xona sheriklarini pasport seriyasi orqali bog'lash, ketma-ketlik (`/api/bot/roommate`); sherik hali kelmagan bo'lsa kutish;
   - ma'lumotni yangilash (`/api/bot/update`), so'ng adminlarga xabar;
   - `/admin`: ommaviy xabar (matn/rasm/hujjat), avval o'ziga test xabar, xona sheriklarini xabardor qilish, guruhga qo'shilmaganlarга taklif, statistika (`/api/bot/stats`), sheriklarni qayta tartiblash;
   - xona sherigi bildirishnomasi (xona turi + sheriklar ism-familyasi).
3. **YANGI xatti-harakat:** ishtirokchi aniqlangач yoki ro'yxatdan o'tgач, bot unga yuboradi:
   - **shaxsiy sahifa havolasi:** `<PUBLIC_URL>/p/<id>` (skanlaganda seminar tartibi + shaxsiy ma'lumot chiqadi);
   - **QR-kod rasmi:** o'sha havolaning QR PNG'si (masalan `qrcode` kutubxonasi bilan generatsiya qilinadi) — beyjikdagi QR bilan bir xil manzil.
   - "Guruhingiz", "guruhboshingiz", "mas'uliyatingiz" (agar bor bo'lsa) haqida qisqa xabar (ma'lumot `/api/p/<id>` dan).
4. **Bot tokeni:** `@sharmseminarbot` tokenini `.env` dagi `BOT_TOKEN` dan ol. Guruh taklif havolasi va `PUBLIC_URL` ham `.env` da.

## 3-qism — Konfiguratsiya (`.env`)
```
BOT_TOKEN=<@sharmseminarbot tokeni>
API_BASE=http://127.0.0.1:8000
BOT_API_TOKEN=<Flask va bot bir xil maxfiy kalit>
PUBLIC_URL=https://sharm.acoustic.uz
GROUP_INVITE_CHAT_ID=<seminar guruhi id/username>
DRIVE_CREDENTIALS=<Google Drive service account json yo'li>
ADMIN_IDS=<vergul bilan admin telegram id lar>
```
`sharm-seminar` uchun ham `BOT_API_TOKEN` va `PUBLIC_URL`/`pageBase` mos bo'lsin.

## Cheklovlar
- Mavjud admin panel (`static/index.html`, `static/participant.html`) va uning API'lari ishlashda davom etsin — buzma.
- Maxfiy kalitlar faqat `.env` dan. Repozitoriyga yozma.
- Migratsiyalar idempotent; mavjud `sharm.db` dagi ma'lumot yo'qolmasin.
- Kod o'qilishi oson, izohli va xatoliklarga chidamli (try/except, HTTP xatolarini log qil).

## Qabul mezoni (test)
1. Botда `/start` → pasport seriya + DOB kiritilganda ishtirokchi SQLite'dan topiladi; Telegram ID saqlanadi; bot shaxsiy havola + QR yuboradi.
2. Yangi oila a'zosi ro'yxatdan o'tadi → SQLite'да yangi qator, asosiy ishtirokchiga bog'langan, `payment_full=1`.
3. Xona sherigi pasport orqali bog'lanadi va admin panelда `xona_guruhi` to'g'ri ko'rinadi.
4. `/admin` ommaviy xabar `/api/bot/recipients` dagi telegram_id larга yuboriladi; yuborilgan/xato soni hisoblanadi.
5. Admin panelда o'zgargan ma'lumot (guruh, mas'uliyat, dastur) darhol `/api/p/<id>` va ishtirokchi sahifasida aks etadi.
6. Eski admin panel va `/p/<id>` sahifasi buzilmagan.
7. `README.md` ga ishga tushirish tartibi qo'shilgan (Flask + bot jarayoni, `.env`, systemd/gunicorn).

## Yakuniy natija
- Ikkala loyiha bitta SQLite bazasiga ulangan holda ishlaydi.
- `@sharmseminarbot` shu loyiha uchun ishlaydi, ishtirokchilarga shaxsiy sahifa + QR yuboradi.
- Google Sheets asosiy baza sifatida olib tashlangan; Drive faqat fayl saqlash uchun qolgan.
- O'zgarishlar branch'da, commit'lar bilan; qanday ishga tushirish `README.md` da.
