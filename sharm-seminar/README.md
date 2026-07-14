# Acoustic — Sharm seminar boshqaruv paneli

Ishtirokchilar, guruhlar, sherik/xona, QR-beyjik va check-in'ni boshqarish tizimi.
Backend: **Flask + SQLite**. Frontend: bitta `index.html` (server yoki brauzer xotirasida ishlaydi).

Telegram bot integratsiyasi uchun `BOT_API_TOKEN` muhit o'zgaruvchisini belgilang.
Bot endpointlari `X-Bot-Token` headerisiz ishlamaydi.

Ma'lumot birinchi ishga tushganda `data/seed_participants.json` dan (104 kishi) yuklanadi.

---

## 1. Tez ishga tushirish (lokal / test)

Talab: **Python 3.9+**.

```bash
pip install -r requirements.txt
python server.py
```

Brauzerda oching: **http://127.0.0.1:8000**

Portni o'zgartirish: `PORT=9000 python server.py`

---

## 2. Serverga o'rnatish (VPS, doimiy ishlashi uchun)

Ubuntu/Debian misolida, `gunicorn` bilan barqaror ishlaydi:

```bash
sudo apt update && sudo apt install -y python3-venv
cd /opt && sudo mkdir sharm-seminar
# fayllarni /opt/sharm-seminar ga ko'chiring
cd /opt/sharm-seminar
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt gunicorn
gunicorn -w 2 -b 0.0.0.0:8000 server:app
```

### systemd xizmati (avtomatik qayta ishga tushish)

`/etc/systemd/system/sharm.service`:

```ini
[Unit]
Description=Sharm seminar panel
After=network.target

[Service]
WorkingDirectory=/opt/sharm-seminar
ExecStart=/opt/sharm-seminar/venv/bin/gunicorn -w 2 -b 0.0.0.0:8000 server:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sharm
```

### Nginx + domen + HTTPS (tavsiya etiladi)

`nginx` bilan `sharm.acoustic.uz` domenini 8000-portga yo'naltiring, so'ng
`certbot` bilan bepul SSL o'rnating. Telefonlarda kamera orqali QR skanlash
**faqat HTTPS** da ishlaydi — shuning uchun domen + SSL muhim.

---

## 3. Docker bilan (ixtiyoriy)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt gunicorn
CMD ["gunicorn","-w","2","-b","0.0.0.0:8000","server:app"]
```

```bash
docker build -t sharm . && docker run -d -p 8000:8000 -v $PWD/data:/app/data sharm
```

`-v $PWD/data:/app/data` — ma'lumotlar bazasi konteyner qayta ishga tushsa ham saqlanadi.

---

## 4. Tuzilma

```
sharm-seminar/
├── server.py                     Flask backend + REST API
├── requirements.txt
├── data/
│   ├── seed_participants.json    boshlang'ich 104 ishtirokchi
│   └── sharm.db                  (avtomatik yaratiladi)
└── static/
    └── index.html                admin panel (frontend)
```

## 5. API (qisqacha)

| Metod | Yo'l | Vazifa |
|---|---|---|
| GET  | `/api/bootstrap` | barcha ma'lumot (ishtirokchi, check-in, nuqtalar, dastur, rollar, meta) |
| POST | `/api/participant` | ishtirokchini yangilash (guruh, guruhboshi, xona, **rollar**) |
| POST | `/api/participants/bulk` | ko'p ishtirokchi (avto-taqsimlash) |
| POST | `/api/checkin` | check-in belgilash/bekor qilish |
| POST | `/api/checkpoints` | check-in nuqtalarini qo'shish/o'chirish |
| POST | `/api/program` · `/api/meta` | seminar dasturi va tadbir ma'lumoti |
| POST | `/api/roles` | mas'uliyatlar ro'yxati |
| POST | `/api/badge` · `/api/seminar-logo` · `/api/pagebase` | beyjik shabloni, logo, sahifa domeni |
| GET  | `/api/p/<id>` | bitta ishtirokchi ma'lumoti (sahifa uchun) |
| GET  | `/p/<id>` | **ishtirokchining shaxsiy sahifasi** (QR shu yerga olib boradi) |
| POST | `/api/bot/find` | pasport, DOB yoki ism bo'yicha qidirish |
| POST | `/api/bot/link` | Telegram ID/username bog'lash |
| POST | `/api/bot/register` | yangi ishtirokchi yaratish |
| POST | `/api/bot/update` | ruxsat etilgan maydonlarni yangilash |
| POST | `/api/bot/roommate` | xona sheriklarini bog'lash |
| GET | `/api/bot/recipients` · `/api/bot/stats` | xabar oluvchilar va statistika |

## 6. Muhim eslatmalar

- **QR → shaxsiy sahifa:** har beyjikdagi QR `<domen>/p/ACO-001` ga olib boradi. Ishtirokchi skanlaganda o'ziga xos sahifa ochiladi: ismi, guruhi, guruhboshisi, xona sherigi, **mas'uliyati** (agar bo'lsa) va to'liq **seminar dasturi**. Shuning uchun panelда "Rejalar" bo'limida **sahifa manzili (domen)** ni to'g'ri kiriting — QR o'sha domenni ishlatadi.
- **Mas'ullar:** "Mas'ullar" bo'limida rollarni (Koordinator, Dengiz sayri, Shahar aylanish va h.k.) yaratasiz, ranglaysiz va ishtirokchilarга biriktrasiz. Rol egasining sahifasida mas'uliyat alohida ajratib ko'rsatiladi.
- **Check-in nuqtalari** o'zgaruvchan: "Check-in" bo'limida "+ Nuqta" bilan qo'shasiz, ✕ bilan o'chirasiz.
- **Dastur** hozir taxminiy — aniqlashganda "Rejalar" bo'limida tahrirlaysiz; o'zgarish darhol ishtirokchi sahifalarida aks etadi.
- **Zaxira nusxa:** butun ma'lumot `data/sharm.db` da. Vaqti-vaqti bilan nusxalab qo'ying.
- **Beyjik:** "Beyjik dizayneri"да o'lchamni (7×10, 7×11, 8×12…) va yozuv/QR joyini sozlab, "Barcha beyjiklarni chop etish" (print sozlamada *Scale 100%*, *Margins: Default*).
- Frontend serversiz ham ishlaydi (ma'lumot faqat o'sha brauzerда). Ko'p admin/guruhboshi va QR-sahifa uchun **server + domen + HTTPS** kerak.
