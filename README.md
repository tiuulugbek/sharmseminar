# Sharm Seminar Bot + Admin panel

Telegram bot va Flask admin panel bitta tizim sifatida ishlaydi. Ma'lumotning yagona
manbasi `sharm-seminar/data/sharm.db` SQLite bazasi. Bot bazaga faqat himoyalangan
HTTP API orqali murojaat qiladi. Google Drive pasport fayllari uchun qolgan;
Google Sheets production o'qish/yozish yo'lida ishlatilmaydi.

## Tuzilma

```text
bot.py                    Telegram oqimlari va rol menyulari
messaging.py              uch rolli ikki tomonlama xabar tizimi
api_client.py             Flask API HTTP clienti
seminar_store.py          eski row formatidan API modeliga adapter
broadcast.py              e'lon, xona xabari va takliflar
drive.py                  pasport fayllarini Google Drive'ga yuklash
sharm-seminar/
  server.py               Flask API + SQLite migratsiyalar + WebApp tekshiruvi
  static/index.html       admin panel
  static/participant.html /p/<token> shaxsiy sahifasi
  static/scan.html        Telegram mini-app QR skaner
  static/vendor/jsQR.min.js
  tools/import_xlsx.py    acoustic2026seminar.xlsx dan import
  tools/set_role.py       panelga kirish rolini belgilash
  data/seed_participants.json
  data/seed_groups.json   guruh nomlari (Sazanchik/Meduza/Akula/Delfin/Nemo)
  data/seed_program.json
```

## Pasport → token → QR

Har bir ishtirokchining QR kodi endi **`https://sharm.acoustic.uz/p/<token>`** ni
kodlaydi, bunda:

```text
token = hmac_sha256(SECRET, PASPORT_SERIYA + RAQAM)[:16]
```

Shu sababli beyjikdagi QR na pasport raqamini, na `ACO-xxx` ID sini oshkor qiladi.
Eski `/p/ACO-001` havolalari ham ishlashda davom etadi (orqaga moslik).

`SECRET` o'zgartirilsa **barcha tokenlar** o'zgaradi va beyjiklarni qayta chop etish
kerak bo'ladi. Pasport tuzatilsa ham o'sha ishtirokchining tokeni yangilanadi.

## Sozlash

```bash
cp .env.example .env
```

Muhim qiymatlar:

```dotenv
BOT_TOKEN=<@sharmseminarbot tokeni>
API_BASE=http://127.0.0.1:8000
BOT_API_TOKEN=<uzun tasodifiy maxfiy kalit>
SECRET=<token hash va sessiya imzosi uchun maxfiy kalit>
PUBLIC_URL=https://sharm.acoustic.uz
WEBAPP_URL=https://sharm.acoustic.uz
GROUP_INVITE_CHAT_ID=<telegram guruh id>
ADMIN_IDS=<vergul bilan telegram id lar>
PANEL_ADMIN_IDS=<panel texnik adminlari, ACO-004,ACO-012>
CREDENTIALS_FILE=/absolute/path/service-account.json
```

- `BOT_API_TOKEN` Flask va bot jarayonlarida aynan bir xil bo'lishi shart.
- `BOT_TOKEN` Flask tomonida ham kerak — mini-app `initData` imzosi shu bilan
  tekshiriladi.
- `SECRET` berilmasa server tasodifiy kalitni bazadagi `settings` jadvaliga
  saqlaydi; tokenlar baribir taxmin qilib bo'lmaydigan bo'ladi, lekin bazani
  ko'chirganda kalit ham u bilan ketadi.

## Lokal ishga tushirish

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python sharm-seminar/server.py
```

Ikkinchi terminalda:

```bash
./venv/bin/python bot.py
```

Panel: `http://127.0.0.1:8000`. Ishtirokchi sahifasi: `.../p/<token>` yoki `.../p/ACO-001`.

## Ishtirokchilar ro'yxatini import qilish

`acoustic2026seminar.xlsx` — ism-familya, jinsi, tug'ilgan sana, pasport
seriya/raqam, amal muddati va fuqarolik uchun **yagona to'g'ri manba**. Guruh,
guruh mas'uli, toifa va xona turi/bloki bazadagi mavjud qiymatlaridan saqlanadi
(bu ustunlar faylda yo'q).

```bash
cp <fayl>.xlsx sharm-seminar/data/acoustic2026seminar.xlsx
./venv/bin/python sharm-seminar/tools/import_xlsx.py --dry-run   # avval hisobot
./venv/bin/python sharm-seminar/tools/import_xlsx.py             # keyin yozish
```

Skript idempotent: ikkinchi marta ishga tushirilsa hech narsa o'zgarmaydi.
Ismi bo'yicha topilmagan qatorlar tartib bo'yicha bo'sh "o'rin"ga tushadi —
ya'ni ishtirokchi almashgan bo'lsa yangi odam eski `ACO-xxx` raqamini, guruhini
va xonasini oladi, lekin oldingi odamning Telegram bog'lanishi tozalanadi.

Guruh nomlari va mas'ullari `data/seed_groups.json` da yoki admin panel orqali
belgilanadi. Mas'ulni tez qo'yish:

```bash
curl -X POST http://127.0.0.1:18080/api/groups -H 'Content-Type: application/json' \
  -d '{"groups":[{"id":1,"name":"Sazanchik","leader":"ACO-004"}]}'
```

## Panelga kirish — maxfiy kod va rollar

`sharm.acoustic.uz` endi **"Maxfiy kodni kiriting"** oynasi bilan ochiladi. Maxfiy
kod — odamning **pasport seriya + raqami**. Sahifada pasport so'ralayotgani
yozilmaydi, faqat kod so'raladi. To'liq (`FA1177095`), faqat raqam (`1177095`)
yoki boshidagi nollarsiz yozish ham qabul qilinadi; agar faqat raqam bir nechta
odamga to'g'ri kelsa, to'liq holida so'raladi.

Kirgandan keyin sessiya imzolangan cookie'da 30 kun saqlanadi. Rol har so'rovda
bazadan qayta o'qiladi, ya'ni rolni o'zgartirsangiz darhol kuchga kiradi.

| Rol | Nimani ko'radi va qila oladi |
|---|---|
| `member` — ishtirokchi | faqat **o'zi** haqidagi ma'lumot (xonasi, guruhi, mas'uliyati) + dastur |
| `leader` — guruh mas'uli | **o'z guruhi** ro'yxati, QR kodlari va check-in (faqat o'z guruhini) |
| `manager` — rahbar | **barcha guruhlar**, ishtirokchilarni tahrirlash, check-in, pasport ma'lumoti |
| `admin` — texnik admin | hammasi: sozlamalar, dastur, mas'uliyatlar, guruh nomlari |

Pasport ma'lumoti (seriya, raqam, tug'ilgan sana, telefon, Telegram) faqat
`manager` va `admin` ga, hamda odamning **o'ziga** ko'rinadi. Guruh mas'uli o'z
guruhini ko'radi, lekin ularning pasportini ko'rmaydi.

Rol qanday aniqlanadi (yuqoridan pastga):

1. `.env` dagi `PANEL_ADMIN_IDS` ro'yxatidagi ACO raqami → `admin`;
2. Telegram hisobi `ADMIN_IDS` da bo'lsa → `admin` (bot admini panelda ham admin);
3. `participants.panel_role` ustuni (`admin`/`manager`/`leader`/`member`);
4. `participants.leader=1` → `leader`;
5. aks holda → `member`.

Rol berish:

```bash
./venv/bin/python sharm-seminar/tools/set_role.py                 # joriy rollar
./venv/bin/python sharm-seminar/tools/set_role.py ACO-004 admin   # texnik admin
./venv/bin/python sharm-seminar/tools/set_role.py "Musaev" manager
systemctl restart sharm-seminar    # faqat .env o'zgarsa kerak
```

**Ochiq qoladi:** beyjik QR i ochadigan `/p/<token>` sahifasi, uning
`/api/p/<token>` ma'lumoti va bot mini-app'i (`/scan`) — ular kod so'ramaydi,
aks holda ishtirokchi o'z sahifasini ocholmay qolardi.

## QR kodlar

Panel → **«QR kodlar»** bo'limi (guruh mas'uli va undan yuqori rollarga ko'rinadi):

- har bir ishtirokchining QR kodi ro'yxatda ko'rinadi;
- **⬇ PNG** — bittasini yuklab olish;
- **⬇ Hammasini ZIP qilib yuklash** — ko'rinib turgan hammasini bitta ZIP faylda
  (guruh yoki ism bo'yicha izlab, faqat kerakligini ham olish mumkin);
- o'lcham 512 / 1024 / 2048 px, xohlasangiz QR tagida ism-familya va ACO raqami
  chiziladi.

Fayl nomi `ACO-042_Aziz_Karimov.png` ko'rinishida. ZIP brauzerning o'zida
yig'iladi — hech qanday tashqi kutubxona yoki server yuklamasi kerak emas.

Beyjik dizayneri bo'limi olib tashlandi.

## Bot rollari

Botdagi rol `/api/bot/whoami` orqali aniqlanadi: `ADMIN_IDS` dagi Telegram ID → **admin**,
`participants.leader=1` → **guruh rahbari**, bog'langan ishtirokchi → **a'zo**.

| Rol | Menyu |
|---|---|
| Admin | 📢 Hammaga xabar · 📢 Bitta guruhga · 👤 Bitta odamga · 📊 Umumiy statistika · 📷 QR skanlash |
| Guruh rahbari | 📢 Guruhimga xabar · 👥 Guruhim ro'yxati · 📷 QR skanlash (check-in) · 📊 Kim keldi / kim yo'q |
| A'zo | ✍️ Rahbarimga savol · 📄 Mening sahifam |

## Ikki tomonlama xabar tizimi

Qoida: **javob har doim xabar kimdan kelgan bo'lsa, o'shanga qaytadi**, va hammasi
shaxsiy chat (DM) orqali — ochiq guruh chatiga hech narsa yozilmaydi.

| Xabar kimdan | Kimga | "↩️ Javob" bosilsa → kimga |
|---|---|---|
| Admin → hammaga | Telegram ID si bor barcha ishtirokchi | Admin |
| Admin → bitta guruhga | o'sha guruh | Admin |
| Guruh rahbari → o'z guruhiga | guruh a'zolari | O'sha rahbar |
| A'zo → "Rahbarimga savol" | o'z guruh rahbari | Javob bergan rahbardan → a'zoga |

Har bir yuborilgan nusxa `msg_targets` jadvaliga `telegram_msg_id` bilan
yoziladi, javob shu orqali manzilini topadi. Rahbar boshqa guruhga, a'zo esa
hammaga yoza olmaydi — buni server rad etadi (`403 forbidden`).

`settings.copy_member_questions=true` bo'lsa adminlar a'zo savollarining
nusxasini oladi. `settings.leader_scope="all"` bo'lsa rahbar istalgan
ishtirokchini check-in qila oladi (standart: faqat o'z guruhini).

## QR skanlash — ikki usul

1. **Oddiy ishtirokchi:** telefon kamerasi bilan beyjik QR ini skanlaydi →
   brauzerda `/p/<token>` sahifasi ochiladi. Bot kerak emas.
2. **Rahbar / admin:** botdagi **📷 QR skanlash** tugmasi Telegram mini-app
   (`/scan`) ni ochadi. Mini-app kameradan QR o'qiydi (Telegram'ning o'z skaneri,
   bo'lmasa `jsQR` + `getUserMedia`) va `POST /api/webapp/checkin` chaqiradi.

Mini-app har so'rovda `Telegram.WebApp.initData` yuboradi; server uni
`HMAC_SHA256("WebAppData", BOT_TOKEN)` sxemasi bo'yicha tekshiradi va 24 soatdan
eski imzoni rad etadi. Soxta `telegram_id` bilan check-in qilib bo'lmaydi.

## Bot buyruqlari

- `/start` — pasport yoki tug'ilgan sana orqali aniqlash, Telegram ID bog'lash,
  shaxsiy sahifa (`/p/<token>`) va QR yuborish, guruhga taklif. Allaqachon
  bog'langan bo'lsa — to'g'ridan-to'g'ri rol menyusi ochiladi.
- `/menyu` — rol menyusini qayta ochish.
- `/royxat` — yangi ishtirokchi/oila a'zosini to'liq ro'yxatga olish.
- `/yangilash` — mavjud ma'lumotni pasport orqali yangilash.
- `/bekor` — joriy jarayonni bekor qilish.
- `/admin` — eski admin paneli (e'lon, xona xabarlari, guruh takliflari).
- `/joyla` — legacy buyruq; SQLite'da xona guruhlari API orqali saqlanadi.

## API qisqacha

Barchasi `X-Bot-Token` sarlavhasi bilan (WebApp endpointlaridan tashqari —
ular `initData` bilan himoyalangan).

| Endpoint | Vazifasi |
|---|---|
| `GET /api/p/<id yoki token>` | ishtirokchi sahifasi ma'lumoti (pasportsiz) |
| `GET /api/bot/whoami?telegram_id=` | botdagi rol, guruh, guruh nomi |
| `POST /api/auth/login` · `/api/auth/logout` · `GET /api/auth/me` | panelga kirish |
| `GET /api/bot/groups` | guruhlar, mas'ullar, kelganlar soni |
| `GET /api/bot/group/<n>` | guruh a'zolari + check-in holati |
| `POST /api/bot/checkin` | `{token\|id, checkpoint, by_telegram_id}` |
| `GET /api/bot/recipients?scope=all\|group\|one&value=` | xabar manzillari |
| `POST /api/bot/message` | xabarni yozib, manzillar ro'yxatini qaytaradi |
| `POST /api/bot/message/sent` | yuborilgan `telegram_msg_id` larni saqlash |
| `POST /api/bot/reply` | javob manzilini (asl yuboruvchi) qaytaradi |
| `POST /api/webapp/whoami` | mini-app: rol + nuqtalar ro'yxati |
| `POST /api/webapp/checkin` | mini-app: imzolangan check-in |

## Production (gunicorn + systemd + nginx)

Serverda loyiha `/root/sharm_bot` da, ikkita servis bilan ishlaydi.

Flask (`/etc/systemd/system/sharm-seminar.service`):

```ini
[Unit]
Description=Sharm Seminar Flask API and admin panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/sharm_bot/sharm-seminar
EnvironmentFile=/root/sharm_bot/.env
ExecStart=/root/sharm_bot/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:18080 \
  --access-logfile /root/sharm_bot/sharm-seminar-access.log \
  --error-logfile /root/sharm_bot/sharm-seminar-error.log server:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Bot (`/etc/systemd/system/sharm-bot.service`) `EnvironmentFile=/root/sharm_bot/.env`
bilan `bot.py` ni ishga tushiradi va `sharm-seminar.service` dan keyin startlaydi.

Nginx `sharm.acoustic.uz` ni `127.0.0.1:18080` ga proxy qiladi, HTTPS sertifikati
Certbot bilan. Mini-app **faqat HTTPS** da ishlaydi — kamera boshqacha ochilmaydi.

Deploy:

```bash
./venv/bin/python -m unittest discover -s sharm-seminar/tests
systemctl restart sharm-seminar && systemctl restart sharm-bot
systemctl status sharm-seminar sharm-bot --no-pager
curl -s -o /dev/null -w '%{http_code}\n' https://sharm.acoustic.uz/scan
```

SQLite migratsiyalari birinchi HTTP so'rovida idempotent bajariladi va mavjud
ma'lumotni o'chirmaydi. `sharm.db`ni muntazam zaxiralang.

## Test

```bash
./venv/bin/python -m unittest discover -s sharm-seminar/tests -v
./venv/bin/python -m py_compile bot.py messaging.py api_client.py seminar_store.py \
  sharm-seminar/server.py
```

Testlar tokenlar, rol tekshiruvi, check-in ruxsatlari, uch rolli xabarlashuv,
javob yo'naltirish va WebApp `initData` imzosini qamrab oladi.

## Bir martalik legacy migratsiya

`tools/enrich_seed_from_sheets.py` — eski Google Sheets'dan seedni boyitgan bir
martalik skript. Endi ro'yxat `tools/import_xlsx.py` orqali yangilanadi;
production bot Google Sheets'dan foydalanmaydi.
