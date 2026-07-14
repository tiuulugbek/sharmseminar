# Sharm Seminar Bot + Admin panel

Telegram bot va Flask admin panel bitta tizim sifatida ishlaydi. Ma'lumotning yagona
manbasi `sharm-seminar/data/sharm.db` SQLite bazasi. Bot bazaga faqat himoyalangan
HTTP API orqali murojaat qiladi. Google Drive pasport fayllari uchun qolgan;
Google Sheets production o'qish/yozish yo'lida ishlatilmaydi.

## Tuzilma

```text
bot.py                    Telegram oqimlari
api_client.py             Flask API HTTP clienti
seminar_store.py          eski row formatidan API modeliga adapter
broadcast.py              e'lon, xona xabari va takliflar
drive.py                  pasport fayllarini Google Drive'ga yuklash
sharm-seminar/
  server.py               Flask API + SQLite migratsiyalar
  static/                 admin va shaxsiy sahifa
  data/seed_participants.json
  data/seed_program.json
```

## Sozlash

```bash
cp .env.example .env
```

Muhim qiymatlar:

```dotenv
BOT_TOKEN=<@sharmseminarbot tokeni>
API_BASE=http://127.0.0.1:8000
BOT_API_TOKEN=<uzun tasodifiy maxfiy kalit>
PUBLIC_URL=https://sharm.acoustic.uz
GROUP_INVITE_CHAT_ID=<telegram guruh id>
ADMIN_IDS=<vergul bilan telegram id lar>
CREDENTIALS_FILE=/absolute/path/service-account.json
```

`BOT_API_TOKEN` Flask va bot jarayonlarida aynan bir xil bo'lishi shart.

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

Panel: `http://127.0.0.1:8000`. Ishtirokchi sahifasi:
`https://sharm.acoustic.uz/p/ACO-001`.

## Bot buyruqlari

- `/start` — pasport yoki tug'ilgan sana orqali aniqlash, Telegram ID bog'lash,
  shaxsiy sahifa va QR yuborish, guruhga taklif qilish.
- `/royxat` — yangi ishtirokchi/oila a'zosini to'liq ro'yxatga olish.
- `/yangilash` — mavjud ma'lumotni pasport orqali yangilash.
- `/bekor` — joriy jarayonni bekor qilish.
- `/admin` — e'lon, xona xabarlari, guruh takliflari va statistika.
- `/joyla` — legacy buyruq; SQLite'da xona guruhlari API orqali saqlanadi.

## Production (gunicorn + systemd)

Flask:

```ini
[Unit]
Description=Sharm Seminar Flask
After=network.target

[Service]
WorkingDirectory=/opt/sharm-bot/sharm-seminar
EnvironmentFile=/opt/sharm-bot/.env
ExecStart=/opt/sharm-bot/venv/bin/gunicorn -w 2 -b 127.0.0.1:18080 server:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Bot:

```ini
[Unit]
Description=Sharm Seminar Telegram Bot
After=network.target sharm-seminar.service

[Service]
WorkingDirectory=/opt/sharm-bot
EnvironmentFile=/opt/sharm-bot/.env
ExecStart=/opt/sharm-bot/venv/bin/python bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

SQLite migratsiyalari birinchi HTTP so'rovida idempotent bajariladi. `sharm.db`ni
muntazam zaxiralang. Nginx `PUBLIC_URL` domenini `127.0.0.1:8000`ga proxy qilishi kerak.

## Test

```bash
BOT_API_TOKEN=test-token ./venv/bin/python -m unittest discover -s sharm-seminar/tests -v
./venv/bin/python -m py_compile bot.py api_client.py seminar_store.py sharm-seminar/server.py
```

## Bir martalik legacy migratsiya

Seed allaqachon eski `Yaxlit ro'yxat`dan pasport/DOB/contact maydonlari bilan
boyitilgan. Qayta generatsiya zarur bo'lsa:

```bash
./venv/bin/python sharm-seminar/tools/enrich_seed_from_sheets.py
```

Bu skript faqat migratsiya vositasi; production bot Google Sheets'dan foydalanmaydi.
