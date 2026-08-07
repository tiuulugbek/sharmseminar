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
  tools/translate_content.py  dastur va matnlarni uch tilga o'tkazish
  tools/unlink_telegram.py    Telegram bog'lanishlarini tozalash
  tools/seed_checkpoints.py   check-in nuqtalarini dasturga moslash
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
| `leader` — guruh mas'uli | **o'z guruhi**: ro'yxat, QR kodlari, check-in, xona raqami va tilini o'zgartirish, noto'g'ri Telegram bog'lanishini uzish |
| `manager` — rahbar | **barcha guruhlar**, xona va til tahriri, check-in, pasport ma'lumoti |
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

⚠️ `ADMIN_IDS` va `PANEL_ADMIN_IDS` **eng ustun** turadi: u yerdagi odam
`set_role.py` bilan qanday rol berilganidan qat'i nazar to'liq admin bo'lib
qolaveradi. Shuning uchun `.env` da faqat haqiqiy texnik adminlar tursin,
qolgan hamma rol bazadan (`tools/set_role.py`) beriladi.

**Panel roli botga ham o'tadi:** `admin` va `manager` botda ham hamma guruh
bilan ishlaydi, ya'ni rollar bitta joyda boshqariladi va `.env` bilan bo'linib
ketmaydi.

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

## Tillar (uz / ru / en)

Ikkala sahifa ham uch tilli:

Sahifa **uchinchi shaxsda** yozilgan: beyjik QR ini kim skanlasa ham, u odam
haqidagi umumiy ma'lumotni ko'radi (kimligi, qaysi guruhda, mas'uli kim,
seminardagi vazifalari), «siz» deb murojaat qilinmaydi.

- **Ishtirokchi sahifasi** (`/p/<token>`) — yuqori o'ng burchakda UZ/RU/EN tugmasi.
  Ochilish tili shu tartibda tanlanadi: havoladagi `?lang=ru` → mehmon oldin
  tanlagan til → **admin panelda o'sha odamga belgilangan til** → brauzer tili → uz.
- **Admin panel** — sarlavhadagi UZ/RU/EN tugmasi, tanlov brauzerda saqlanadi.

Har bir ishtirokchining asosiy tili panelning «Ishtirokchilar» bo'limidagi
**«Til»** ustunidan qo'yiladi (`Avto` = brauzer tili bo'yicha). Chet eldan
kelgan mehmonga `EN` yoki `RU` qo'ysangiz, u beyjik QR ini skanlaganda sahifa
o'sha tilda ochiladi.

### Dastur tartibini o'zgartirish

Kun, bo'lim va bandlarning har birida **↑ ↓** tugmalari bor — istalgan joyga
suriladi. Qo'shish ham ikki tomondan:

- **«↑ Kun»** / **«+ Kun»** — dasturning boshiga yoki oxiriga;
- har bo'lim va band ostida **«↑ Boshiga»** va **«+ Band»**.

Ya'ni «GN ReSound kuni» ni ro'yxatning boshiga qo'yish uchun uni qo'shib, ↑
bilan surasiz — yoki darrov boshiga qo'shasiz.

### Dastur va matnlarni uch tilda kiritish

Dastur bandlari, check-in nuqtalari, mas'uliyat nomlari va tadbir ma'lumoti
bazada uch tilli saqlanadi:

```json
{"uz": "Kelish kuni", "ru": "День приезда", "en": "Arrival day"}
```

«Rejalar» bo'limidagi **«Uchala tilni birdan»** belgisi har bir maydonni UZ/RU/EN
uchtasi bilan ochadi — yangi band qo'shganda uchalasini bir joyda yozasiz (yangi
kun/bo'lim/band qo'shilganda bu rejim o'zi yoqiladi). Belgi olib qo'yilsa, faqat
**yuqoridagi UZ/RU/EN tugmasi bilan tanlangan til** tahrirlanadi, boshqa tillar
tegilmaydi. «Rejalar» va
«Mas'ullar» bo'limlarida qaysi tilda yozayotganingiz alohida yozib turiladi.
Vaqt (`10:30`) hamma tilda bitta.

Eski, bir tilli matn ham ishlaydi — sahifa uni o'zbekcha deb oladi, birinchi
tahrirda esa avtomatik uch tilli bo'ladi.

Mavjud matnlarni bir marta tarjima qilib qo'yish:

```bash
./venv/bin/python sharm-seminar/tools/translate_content.py --dry-run
./venv/bin/python sharm-seminar/tools/translate_content.py
```

Skript idempotent: qo'lda kiritilgan tarjima ustidan yozmaydi, ikkinchi marta
ishga tushirilsa hech narsa o'zgarmaydi.

## Check-in — kamera bilan


Ikki joyda ham bir xil ishlaydi:

- **Panelda:** «Check-in» bo'limi → **«📷 Kamera bilan skanlash»**. Kamera QR ni
  o'qishi bilan odam belgilanadi, natija ro'yxatda darhol ko'rinadi. Bitta QR
  ikki soniya ichida ikki marta hisoblanmaydi.
- **Botda:** «📷 QR skanlash» → inline tugma → Telegram mini-app.

`POST /api/checkin` endi `id` ni ham, QR dan o'qilgan `token` ni ham qabul
qiladi, shuning uchun bitta skaner ikkala joyda ishlaydi.

### Check-in nuqtalari va ularning tartibi

Nuqtalar seminar dasturiga mos: aeroport → mehmonxona → yahta → **Signia kuni**
→ **GN ReSound kuni** → **Acoustic kuni** → sayohat → madaniy dastur → qaytish.
Har birida uch tilli nom va o'sha kunning vaqt oynasi bor.

Boshlang'ich holatga keltirish yoki dasturga moslash:

```bash
./venv/bin/python sharm-seminar/tools/seed_checkpoints.py --dry-run
./venv/bin/python sharm-seminar/tools/seed_checkpoints.py
```

Skript idempotent: mavjud nuqta o'chirilmaydi, uning check-inlariga tegilmaydi,
qo'lda qo'yilgan nom yoki vaqt oynasi ustidan yozilmaydi — faqat yetishmayotgani
qo'shiladi va tartib dasturga qarab tuziladi.

**Tartibni panelda o'zgartirasiz:** «Check-in» bo'limida har bir nuqta ustidagi
`‹ ›` tugmalari bilan suriladi (faqat texnik adminlarga ko'rinadi). Tartib
ahamiyatli — skaner o'tkazib yuborilgan **oldingi** nuqtalarni shu tartib
bo'yicha ogohlantiradi. Nuqta chipida tartib raqami va vaqt oynasi ham ko'rinadi.

### Check-in qoidalari

Skanerlangan zahoti katta rangli javob chiqadi (ovoz + vibratsiya bilan):

| Holat | Ko'rinish | Ma'nosi |
|---|---|---|
| `ok` | 🟢 **Ro'yxatga olindi** | belgilandi, vaqti yozildi |
| `already` | 🟠 **Allaqachon ro'yxatda** | ilgari belgilangan, vaqti **qayta yozilmaydi** |
| `not_open` | 🔵 **Vaqti hali kelmagan** | nuqta oynasi hali ochilmagan |
| `closed` | 🔵 **Vaqti tugagan** | nuqta oynasi yopilgan |
| `not_found` / `forbidden` | 🔴 | QR ro'yxatda yo'q yoki begona guruh |

**Bir odam — bir marta.** Ikkinchi skan hech narsa yozmaydi, faqat qachon
belgilangani ko'rsatiladi. Adminlar `force` bilan qayta yoza oladi, panelda
belgini olib tashlab qayta belgilash ham mumkin.

**Vaqt oynasi.** Har bir nuqtaga «Check-in» bo'limida boshlanish va tugash
vaqti qo'yiladi (bo'sh qoldirilsa nuqta har doim ochiq). Oynadan tashqarida
skaner ishlamaydi — yahtaga chiqish kuni kelmasdan turib uni belgilab bo'lmaydi.

**Ketma-ketlik — ogohlantirish, to'siq emas.** Kimdir aeroportda belgilanmay
qolsa, yahtada bemalol belgilanadi; skanerda «⚠️ O'tkazib yuborilgan: Aeroport»
deb yozilib turadi. O'tkazib yuborilgani o'tkazib yuborilganicha qoladi —
haqiqatan bormagan odam keyingi nuqtada belgilanaveradi.

**Vaqt mintaqasi.** «Check-in» bo'limining tepasidagi ro'yxatdan tanlanadi va
yonida hozirgi vaqt ko'rinib turadi. Toshkentda tarqatma berilayotganda `UTC+5`,
Misrga yetgach `UTC+3` qilib qo'yiladi — check-in vaqtlari ham, nuqta oynalari
ham shu mintaqada hisoblanadi.

**Nuqta nomi va belgisi** chipdagi **✎** tugmasi bilan o'zgartiriladi (nomi
tanlangan tilda saqlanadi). Yangi nuqta qo'shilsa darrov ishlaydi — vaqt oynasi
bo'sh bo'lgani uchun har doim ochiq bo'ladi.

**Skanerda ro'yxat.** Mini-appda kamera ostida guruh ro'yxati turadi:
belgilanmaganlar tepada, belgilanganlar vaqti bilan pastda. Har skandan keyin
o'zi yangilanadi. Guruh mas'uli o'z guruhini, admin va rahbar hamma guruhni
(yig'indisi bilan) ko'radi.

## Bot rollari

Botdagi rol `/api/bot/whoami` orqali aniqlanadi: `ADMIN_IDS` dagi Telegram ID → **admin**,
`participants.leader=1` → **guruh rahbari**, bog'langan ishtirokchi → **a'zo**.

| Rol | Menyu |
|---|---|
| Admin | 📢 Hammaga xabar · 📢 Bitta guruhga · 👤 Bitta odamga · 📊 Umumiy statistika · 📷 QR skanlash |
| Guruh rahbari | 📢 Guruhimga xabar · 👥 Guruhim ro'yxati · 📷 QR skanlash (check-in) · 📊 Kim keldi / kim yo'q |
| A'zo | ✍️ Rahbarimga savol · 📄 Mening sahifam |

Admin va guruh mas'ulida `⏳ Tasdiqlanmaganlar` tugmasi ham bor.

## Ikki tomonlama xabar tizimi

Qoida: **javob har doim xabar kimdan kelgan bo'lsa, o'shanga qaytadi**, va hammasi
shaxsiy chat (DM) orqali — ochiq guruh chatiga hech narsa yozilmaydi.

| Xabar kimdan | Kimga | "↩️ Javob" bosilsa → kimga |
|---|---|---|
| Admin → hammaga | Telegram ID si bor barcha ishtirokchi | Admin |
| Admin → bitta guruhga | o'sha guruh | Admin |
| Guruh rahbari → o'z guruhiga | guruh a'zolari | O'sha rahbar |
| A'zo → "Rahbarimga savol" | o'z guruh rahbari | Javob bergan rahbardan → a'zoga |

Guruh o'zgarsa, panel o'sha odamga botdan xabar yuboradi (uning tilida) va
QR o'zgarmasligini alohida eslatadi — token pasportdan olinadi, guruhga bog'liq
emas, ya'ni chop etilgan beyjik hech qachon yaroqsiz bo'lmaydi.

Har bir yuborilgan nusxa `msg_targets` jadvaliga `telegram_msg_id` bilan
yoziladi, javob shu orqali manzilini topadi. Rahbar boshqa guruhga, a'zo esa
hammaga yoza olmaydi — buni server rad etadi (`403 forbidden`).


Xabar turlari: **matn, rasm, video, ovozli xabar, audio, hujjat, GIF, doiraviy
video va stiker**. Izoh qabul qiladigan turlarga («kimdan» sarlavhasi) fayl
izohiga qo'shiladi; doiraviy video va stikerga izoh qo'yib bo'lmagani uchun
sarlavha alohida xabar bo'lib oldin ketadi.

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

## Tasdiqlash jarayoni (ish tartibi)

Guruhdagi odamlarni tekshirib, faqat ro'yxatdagilarni qoldirish tartibi:

1. **Bog'lanishlarni tozalash** (bir marta) — `tools/unlink_telegram.py`, shunda
   hamma noldan pasport bilan tasdiqlaydi.
2. **`/guruh_chaqiruv`** — bot guruhga tugmali chaqiruv yuboradi.
3. **Odamlar `/start` bosib pasport seriya va raqamini kiritadi.** Bot darrov
   guruhini, mas'ulini, xonasini va shaxsiy QR sahifasini qaytaradi. Shu daqiqada
   `telegram_id` bog'lanadi va odam «tasdiqlangan» hisoblanadi.
4. **`📊 Umumiy statistika`** — necha foiz tasdiqlagani, guruh bo'yicha
   `tasdiqlagan/jami`.
5. **`⏳ Tasdiqlanmaganlar`** — kim qolgani, guruh bo'yicha, telefon raqami bilan.
   Guruh mas'uli o'z guruhinikini ko'radi va qo'ng'iroq qilib chaqiradi.
6. **`/guruh_holat`** — guruhda jami nechta, bot nechtasini taniydi, ro'yxatda
   bor/yo'q.
7. **`/guruh_qulf`** — guruhni yopish: tasdiqlamaganlar yoza olmaydi.
   Odam tasdiqlashi bilan ruxsat o'zi ochiladi.
8. **`/guruh_tozala`** — ro'yxatda yo'qlarni chiqarish (oxirida, tasdiqlash
   muddati tugagach).

Guruhlar allaqachon bazada taqsimlangan, shuning uchun «guruhlarga ajratish»
alohida qadam emas: odam tasdiqlanishi bilan o'z guruhini biladi, guruh mas'uli
uni ro'yxatida ko'radi, mas'ul unga xabar yoza oladi va QR bilan check-in qiladi.
Guruhni panelda o'zgartirsangiz, odamga botdan xabar boradi.

## Yosh, saralash va Excel eksport

**Yosh** tug'ilgan sanadan hisoblanadi va «Ishtirokchilar» jadvalida alohida
ustunda turadi. Yoshni hamma ko'radi, lekin **tug'ilgan sananing o'zi** pasport
ma'lumoti sifatida faqat rahbar va adminlarga qoladi.

**Saralash** — ustun sarlavhasini bosasiz (ID · F.I.O. · Yosh · Xona turi · Xona
bloki · Guruh), ikkinchi bosishda teskarisiga. Bo'sh qiymat doim oxirida turadi.

**Excel** — «Ishtirokchilar» va «Guruhlar» bo'limlarida `⤓ Excel` tugmasi:

| Fayl | Nima chiqadi |
|---|---|
| `acoustic2026-ishtirokchilar-<sana>.xlsx` | Bitta varaq: ID, ism, yosh, jinsi, fuqarolik, guruh va nomi, mas'ul, xona turi/bloki/raqami, xonadoshlar, mas'uliyati, til, botda bormi, voucher/chipta bormi |
| `acoustic2026-guruhlar-<sana>.xlsx` | Har bir guruh **alohida varaqda**, mas'uli tepada |

Eksport **rol qamroviga bo'ysunadi**: admin va rahbar hammasini, guruh mas'uli
o'z guruhini, oddiy ishtirokchi faqat o'zini oladi. Pasport, tug'ilgan sana,
amal muddati va telefon ustunlari faqat rahbar va adminlarga qo'shiladi. Guruhlar
eksporti guruh mas'ulidan boshlab ochiq.

Sarlavhalar panel tilida (`?lang=uz|ru|en`), birinchi qator qotirilgan va
filtrli — Excelda darrov saralab ko'rish mumkin.

## Hujjatlar — voucher va chiptalar

Har bir ishtirokchining o'z hujjatlari saqlanadi va bot ularni **faqat egasiga**
yuboradi.

### Yuklash — ikki yo'l

**1. Botga tashlab.** Faylni shunchaki `@sharmseminarbot` ga yuborasiz (admin
sifatida). Bot uni saqlaydi va egasini fayl nomidan yoki **izohdan** topadi:

> *fayl:* `IMG_2841.pdf` · *izoh:* `Musaev Sardorjon voucher`
> → ✅ ACO-004 — Musaev Sardorjon · 1-guruh

Egasi topilmasa bot **«Kimga tegishli?»** deb so'raydi — ism, ACO raqami yoki
pasportni yozasiz, bir nechta bo'lsa vergul bilan (`ACO-004, ACO-104`). Telefonda
turib, agentlikdan kelgan faylni to'g'ridan-to'g'ri qayta yuborsangiz bo'ladi.

**2. Paneldan.** «Hujjatlar» bo'limida fayllarni maydonga tashlaysiz (bir vaqtda
yuzlab bo'lsa ham) — egasi **fayl nomidan** o'zi topiladi:

| Fayl nomi | Topilgan egasi |
|---|---|
| `ACO-042 voucher.pdf` | ACO-042, turi — voucher |
| `Bilet_Niyazov_Bobir.pdf` | Niyazov Bobir, turi — chipta |
| `voucher_FB1177095_hotel.pdf` | pasport bo'yicha |
| `Sardorjon Musaev voucher.pdf` | ism-familya bo'yicha |

Turi ham shunday: avval fayl nomidan (`voucher/hotel/mehmonxona` → 🏨,
`ticket/bilet/avia/passenger` → ✈️), nomda yozilmagan bo'lsa — **hujjatning o'z
matnidan**. Baribir aniqlanmasa 📎 bo'lib qoladi va panelda chip sariq rangda
ajralib turadi; uning yonidagi ochiladigan ro'yxatdan bir bosishda to'g'rilanadi.

Egasi **aniq topilmasa fayl biriktirilmaydi** va ro'yxatda ko'rsatiladi —
noto'g'ri odamga voucher ketgandan ko'ra qo'lda biriktirgan yaxshiroq. Uni
odamning qatoridagi **«+ Fayl»** tugmasi bilan qo'shasiz.

### PDF ichidan o'qish va sahifalarga bo'lish

Egasi shu tartibda topiladi:

1. **Fayl nomi va izoh** — odam ataylab yozgan nom eng ishonchli manba;
2. **PDF ning matni** — nom hech kimni ko'rsatmasa, hujjat ichidagi ismlardan.

Ikkinchi holatda uch vaziyat o'z-o'zidan hal bo'ladi:

| Fayl | Natija |
|---|---|
| Bir sahifada bitta odam | o'shanga biriktiriladi |
| Bir sahifada 2–3 xonadosh | **hammasiga** biriktiriladi, fayl bo'linmaydi |
| Hammasi bitta katta PDF da | **sahifalarga ajratiladi**, har kimga faqat o'ziniki |

Ajratishda ketma-ket sahifalar bir xil odamga tegishli bo'lsa birga qoladi, ya'ni
ikki sahifali chipta bo'linib ketmaydi. Ismi yozilmagan sahifa (masalan «Baggage:
20 kg» degan davomi) oldingi odam bilan qoladi.

Bu **maxfiylik uchun ham muhim**: 105 sahifali umumiy faylni hammaga yuborish
o'rniga, har kim faqat o'z sahifasini oladi.

**Skanerlangan PDF** (matn qatlami yo'q, faqat rasm) da ism topilmaydi — u holda
fayl nomi va izohga qaytiladi, ya'ni `ACO-042 voucher.pdf` deb nomlang yoki botga
yuborayotganda izohga ismni yozing.

### Bitta faylda bir necha kishi

Uch kishilik xona voucherida uchalasining ismi bo'ladi — bunday fayl **hammasiga
biriktiriladi**:

> `Voucher_Musaev_Sardorjon_Niyazov_Bobir.pdf` → ikkalasiga ham

Fayl diskda **bir marta** saqlanadi, har bir egasiga alohida yozuv ochiladi:
bot har biriga o'z nusxasini yuboradi va kim olganini alohida belgilaydi.
Keyin yana birov qo'shilsa — fayl qayta yuklanmaydi. Bittasidan olib tashlansa
fayl qolganlar uchun saqlanib qoladi.

Fayllar `data/docs/_files/` da yotadi (git ga tushmaydi), bittasi 20 MB gacha.

### Kim ko'radi

Odamning o'zi va guruh mas'uli — o'z guruhinikini; rahbar va admin — hammasini.
Boshqa birov havolani bilsa ham ocholmaydi.

### Tashkilotchilar — ro'yxatdan tashqarida

Safarda qatnashmaydigan, lekin panelga kiradigan odam (tashkilotchi, texnik
admin) `staff` deb belgilanadi:

```bash
./venv/bin/python sharm-seminar/tools/set_role.py ACO-105 --staff        # chiqarish
./venv/bin/python sharm-seminar/tools/set_role.py ACO-105 --participant  # qaytarish
```

Shundan keyin u **hech qaysi ro'yxat va hisobotga kirmaydi**: statistikadagi umumiy
son, guruh ro'yxatlari, «hammaga xabar» manzillari, hujjat qamrovi, check-in
kutilayotganlar — hech qayerda. Uni QR bilan check-in qilib ham bo'lmaydi
(«forbidden»). Panelga kirishi va admin huquqlari o'zgarmaydi; ishtirokchilar
jadvalida «tashkilotchi» yorlig'i bilan ko'rinadi.

### Ism yozilishidagi farqlar

Fayl nomini kim yozganiga qarab ism har xil yozilishi mumkin. Moslashtirish
shularni bir xil deb qabul qiladi:

| | |
|---|---|
| `Jumayev` = `Jumaev` | `yev/ayev/oyev` → `ev/aev/oev` |
| `Toshkhodjaev` = `Toshhojaev` | `kh` → `h`, `iy` → `i`, `yo` → `o` |
| `Azizbek` = `Aziz` | `bek, jon, xon, boy, zoda…` qo'shimchalari |

Lekin `Karimov` va `Karimova` ikki xil odam bo'lib qoladi — faqat ma'lum
qo'shimchalar hisobga olinadi, ixtiyoriy harf emas.

### Hujjat bilan yuboriladigan xabar

Chipta yoki voucherga **qo'shimcha matn** biriktirish mumkin — masalan reys
vaqti o'zgargani. Bot uni o'sha turdagi birinchi fayldan **oldin**, odamning
tilida, bir marta yuboradi.

Panel → «Hujjatlar» bo'limining tepasidagi maydonlar. Har til alohida: yuqoridagi
**UZ/RU/EN** tugmasi bilan tilni almashtirib yozasiz. Bo'sh qoldirilsa hech nima
yuborilmaydi.

Matn HTML qabul qiladi: `<b>qalin</b>`, `<i>qiya</i>`, `<s>chizilgan</s>` —
eski vaqtni chizib tashlash uchun qulay.

Xabar faqat **tarqatilayotgan** turga chiqadi: chiptalar hali ushlab turilgan
bo'lsa, chipta izohi ham yuborilmaydi.

### Yuklab bo'lgunicha ushlab turish

Fayllarni bo'lak-bo'lak yuklayotganda yarim tayyor holat odamlarga ketib
qolmasligi kerak. Buning uchun **«Tarqatishni ushlab turish»** bor:

- Panel → «Hujjatlar» bo'limining tepasidagi belgi, yoki botda `/hujjat_ushla`;
- yoqilganda **hech kimga hech narsa yubormaydi** — ro'yxatdan o'tganlarga ham,
  «📎 Hujjatlarim» bosganlarga ham; odam so'rasa «hali tayyor emas» deb javob
  beradi;
- fayllarni bemalol yuklayverasiz, takrorlari o'zi aniqlanadi;
- tayyor bo'lgach `/hujjat_yuborish` — bot «ochib, hammaga yuboraymi?» deb
  so'raydi va tasdiqlansa qulfni ochib, birdan tarqatadi.

`/hujjat_holat` istalgan paytda holatni ko'rsatadi: ushlab turilganmi, nechta
fayl bor, kimda voucher/chipta yetishmayapti.

### Qachon yuboriladi

«Hujjatlar» bo'limida har bir tur uchun **chiqarish vaqti** qo'yiladi (bo'sh
bo'lsa darrov). Vaqti kelmagan hujjat botga umuman ko'rinmaydi.

Uch yo'l bilan yetadi:

1. **Odam so'raganda** — botdagi **«📎 Hujjatlarim»** tugmasi yoki `/hujjatlarim`;
2. **Ro'yxatdan o'tgan zahoti** — hali tasdiqlamaganlar `/start` bosib pasportini
   kiritishi bilan hujjatlari o'zi yuboriladi;
3. **Ommaviy** — admin `/hujjat_yuborish` bilan hali olmaganlarning hammasiga
   birdan yuboradi. Hisobotda hali tasdiqlamaganlar soni ham ko'rsatiladi.

Yuborilgan hujjat belgilanadi va ommaviy yuborishda ikkinchi marta ketmaydi;
odamning o'zi so'rasa baribir qayta oladi.

## Telegram guruhini nazorat qilish

Bot guruhda **hech qachon javob bermaydi**. Buni `GroupSilence` middleware
kafolatlaydi: u barcha handlerlardan oldin ishlaydi, shuning uchun guruhda
buyruq yozilsa ham, kimdir yarim qolgan ro'yxatdan o'tish oqimida bo'lsa ham,
bot indamaydi. Bot qaysi guruh yoki kanalga qo'shilgan bo'lsa ham shunday —
faqat shaxsiy chatda gapiradi. Seminar guruhida esa jim turib, kim kirgani,
chiqqani va yozganini yozib boradi.

**Muhim cheklov:** Telegram Bot API guruh a'zolarini ro'yxatlab bermaydi —
bot faqat umumiy sonni, adminlarni va bitta odamni tekshira oladi. Shuning
uchun bot guruhda **ko'rgan** hamma narsani yozib boradi:

- kimdir guruhga kirsa yoki chiqsa (`chat_member` yangilanishi);
- kimdir guruhda yozsa.

Jim turgan eski a'zolar tasdiqlash chaqiruvidan keyin ko'rinadi.

Admin buyruqlari (botga shaxsiy chatda):

| Buyruq | Vazifasi |
|---|---|
| `/guruh_chaqiruv` | Guruhga tasdiqlash chaqiruvini yuboradi — tugma bosilsa bot ochiladi |
| `/guruh_taklif` | Tasdiqlagan, lekin guruhda yo'q odamlarga taklif havolasi |
| `/guruh_adminlar` | Guruh mas'ullarini Telegram guruhida ham administrator qilish |
| `/guruh_holat` | Hisobot: guruhda jami nechta, bot nechtasini taniydi, uch toifaga bo'lib |
| `/guruh_tozala` | Ro'yxatda yo'qlarni guruhdan chiqaradi (tasdiqlashdan keyin) |

### Guruhga taklif

Botda tasdiqlagan, lekin Telegram guruhida bo'lmagan odamlarga shaxsiy taklif
havolasi yuboriladi: admin menyusida **«➕ Guruhga taklif»** yoki `/guruh_taklif`.

1. Bot tasdiqlaganlarning har birini `getChatMember` bilan tekshiradi;
2. kim guruhda, kim yo'qligini ko'rsatib, ismlari bilan tasdiq so'raydi;
3. tasdiqlansa — har biriga **faqat o'zi uchun, bir marta** ishlaydigan havola
   yuboradi (`member_limit=1`).

Botni hech qachon ochmagan yoki bloklagan odamga yozib bo'lmaydi — ular
hisobotda alohida ko'rsatiladi, ularni qo'ng'iroq bilan chaqirish kerak.

Bu tekshiruv yo'l-yo'lakay `group_members` jadvalini ham to'ldiradi: bot
boshqa yo'l bilan ko'ra olmaydigan **jim a'zolar** shu orqali hisobga tushadi
va `/guruh_holat` aniqroq bo'ladi.

Guruh qulflangan bo'lsa, ruxsat berilgan odamning Telegramdagi statusi
`restricted` bo'ladi — u guruhda hisoblanadi, unga taklif yuborilmaydi.

Botda **«Foydalanuvchilarni taklif qilish»** huquqi bo'lishi kerak; bo'lmasa
buyruq shuni aytadi va hech narsa yubormaydi.

### Mas'ullarni Telegram guruhida administrator qilish

`/guruh_adminlar` beshala guruh mas'ulini Telegram guruhida administrator qiladi
va har biriga «N-guruh mas'uli» degan sarlavha qo'yadi.

Beriladigan huquqlar: xabarlarni o'chirish, xabarlarni qadash, foydalanuvchilarni
taklif qilish, video chatlarni boshqarish. **A'zolarni chiqarish va yangi admin
tayinlash berilmaydi** — ular adminda qoladi.

⚠️ **Telegram qoidasi: bot faqat o'zida bor huquqni ulasha oladi.** Shuning uchun
botga ham shu huquqlar berilishi kerak:

> Guruh → Administratorlar → `@sharmseminarbot` →
> **Administratorlarni tayinlash**, **Xabarlarni o'chirish**,
> **Xabarlarni qadash**, **Video chatlarni boshqarish**

Huquq bo'lmasa buyruq qaysi biri yetishmayotganini aniq aytadi va hech narsa
qilmaydi. Mas'ul o'zgarsa buyruqni qayta yuborasiz.

### Faqat tasdiqlaganlar yozsin

Telegramda **shaxsiy cheklov guruhning umumiy sozlamasidan ustun turadi**. Shu
qoidadan foydalanamiz: guruh butunlay yozish-taqiqli qilinadi, tasdiqlagan har
bir odamga esa alohida ruxsat beriladi. Bu botning a'zolarni ro'yxatlab
ololmasligini ham chetlab o'tadi — kimni tanimasa, o'sha jim qoladi.

| Buyruq | Vazifasi |
|---|---|
| `/guruh_qulf` | Guruhni yopadi va tasdiqlaganlarga ruxsatni qaytaradi |
| `/guruh_ochiq` | Qulfni bekor qiladi, guruh avvalgi holatiga qaytadi |

Qulflashdan **oldin guruhning hozirgi sozlamasi nusxalanadi** va `settings` ga
saqlanadi; ruxsat qaytarilganda aynan o'sha holat tiklanadi. Ya'ni guruhda
media yoki taklif qilish yopiq bo'lsa, ochilgandan keyin ham yopiq qoladi —
bot o'zining sozlamasini majburlamaydi.

Odam `/start` bosib pasportini kiritishi bilan ruxsat **avtomatik** ochiladi va
unga «Endi safar guruhida yozishingiz mumkin» deb yoziladi. Guruh qulflanmagan
bo'lsa bot hech kimga cheklov qo'ymaydi.

Adminlarga cheklov tegmaydi — Telegram adminlarni umumiy sozlamadan ozod qiladi.


`/guruh_holat` a'zolarni **uch toifaga** ajratadi:

| | |
|---|---|
| ✅ **Ro'yxatda va tasdiqlagan** | Telegram hisobi ishtirokchiga bog'langan |
| 🟡 **Ro'yxatda bor** | Telegramdagi ismi ishtirokchiga to'g'ri keladi, lekin hisobi bog'lanmagan — botda hali tasdiqlamagan **yoki guruhda ikkinchi akkaunti turibdi** |
| ❌ **Ro'yxatda yo'q** | Ismi ham hech kimga to'g'ri kelmadi |

Ism solishtirishda transliteratsiya farqlari hisobga olinadi: `Farrux` = `Farrukh`,
`Toshxo'jayev` = `Toshkhodjaev`, `Zhuraev` = `Juraev`, `Jumayev` = `Jumaev`.
Ism tartibi ham muhim emas (`Farrux Abdulazizov` = `Abdulazizov Farrukh`).
Lekin `Karimov` va `Karimova` ikki xil odam bo'lib qoladi.

**`/guruh_tozala` faqat ❌ toifasini chiqaradi.** 🟡 dagilar ro'yxatdagi haqiqiy
odamlar, ularga tegilmaydi — hisobotda alohida ko'rsatiladi va nima uchun
tanilmagani yoziladi.

Chiqarish uchun botda **«Foydalanuvchilarni bloklash»** huquqi bo'lishi shart.
Buni faqat guruh admini Telegram ilovasidan beradi — bot o'ziga huquq qo'sha
olmaydi (Telegram `can't promote self` deb rad etadi):

> Guruh → nomini bosing → **Administratorlar** → `@sharmseminarbot` →
> **Foydalanuvchilarni bloklash** (*Ban users*) ni yoqing.

Huquq bo'lmasa `/guruh_tozala` shuni aytadi va hech narsa qilmaydi.

Chiqarish `ban` + darhol `unban` orqali bajariladi — maqsad guruhdan chiqarish,
umrbod bloklash emas, shuning uchun ro'yxatda ekani aniqlansa odam qaytadan
kira oladi.

Yangi kelgan odam ro'yxatda bo'lmasa, bot unga shaxsiy xabar yozib pasporti
bilan tasdiqlashni so'raydi.

### Guruhlarni saqlash tugmasi

Guruh taqsimoti bir necha harakatdan iborat, shuning uchun har bir o'zgarish
darrov yozilmaydi — ular **qoralama** sifatida yig'iladi:

- guruh yoki mas'ulni o'zgartirsangiz pastda tasma chiqadi:
  «**3 ta saqlanmagan o'zgarish** · Bekor qilish · 💾 Saqlash»;
- **Saqlash** hammasini bitta so'rovda yozadi va guruhi almashganlarga botdan
  xabar shunda boradi (yarim tayyor holatda ortiqcha xabar ketmaydi);
- **Bekor qilish** hammasini avvalgi holiga qaytaradi;
- o'zgartirib, keyin asl qiymatga qaytarsangiz — u hisobga olinmaydi;
- saqlanmagan o'zgarish bilan sahifani yopmoqchi bo'lsangiz brauzer ogohlantiradi.

«Avtomatik taqsimlash» va «Tozalash» ham shu qoralamaga tushadi: natijani ko'rib,
keyin saqlaysiz yoki bekor qilasiz. Saqlash — `admin` roli uchun.

### Guruh mas'uli o'z guruhida nima qila oladi

Panelda: guruh ro'yxati, QR kodlari, check-in, **xona raqami** (`room`) va
**tilini** o'zgartirish, noto'g'ri **Telegram bog'lanishini uzish** — faqat o'z
guruhi ichida. Guruh taqsimoti, xona bloki va mas'ullik unga yopiq, ular admin
qo'lida.

Botda: guruhiga xabar, guruh ro'yxati, check-in skaneri, kim kelgani,
tasdiqlanmaganlar ro'yxati.

### Guruh taqsimotini himoyalash

Guruh (`group`) va guruh mas'uli (`leader`) maydonlarini **faqat `admin`**
o'zgartira oladi — panelda ham, API da ham. Rahbar (`manager`) ishtirokchining
xonasi, tili va check-inini boshqaradi, lekin guruhiga tegolmaydi.

«Guruhlar» bo'limidagi **«Avtomatik taqsimlash»** va **«Tozalash»** ham faqat
adminga ko'rinadi va endi aniq tasdiq so'raydi — bitta bosishda butun
taqsimotni almashtirib yuboradigan tugmalar edi. Avtomatik taqsimlash endi
guruh mas'ullarini ham o'chirmaydi: mas'ul o'z guruhida qoladi.

### Xonadoshni o'zgartirish

Xonadosh degani — bir xil **xona bloki** (`xona_guruhi`, masalan `D07`) dagi
odamlar. Uni almashtirish «Ishtirokchilar» bo'limidagi **«Sherik»** ustunidan
qilinadi:

- ro'yxatda barcha bloklar `D07 (2/2) · Alliyar` ko'rinishida chiqadi — nechta
  odam bor, sig'imi qancha va kim turibdi;
- boshqa blokni tanlasangiz odam o'sha xonaga ko'chadi, eski xonadoshi ro'yxati
  darrov yangilanadi;
- **`+ yangi xona`** yangi blok ochadi;
- **`— yakka`** blokdan chiqaradi;
- to'la xonaga ko'chirmoqchi bo'lsangiz (`2 kishilik` da 2 kishi bor) — tasdiq
  so'raladi, `⚠` belgisi bilan ko'rsatiladi.

Ko'chirilgan odamga botdan xabar boradi (uning tilida): yangi xonasi, kimlar
bilan turishi va QR kodi o'zgarmagani. Ishtirokchi sahifasidagi «Xona sherigi»
qatori ham darrov yangilanadi.

Sig'im xona turidan olinadi: `1 kishilik` → 1, `2 kishilik` → 2, `3 kishilik`
va `Posh club` → 3. Ustun `manager` va `admin` rollariga ko'rinadi.

### Ishtirokchini almashtirish

Kimdir bormay qolib, o'rniga boshqasi ketsa — «Ishtirokchilar» jadvalida ismning
yonidagi **⇄** tugmasi (faqat admin).

O'rin **saqlanadi**: `ACO` raqami, guruh, xona turi, xona bloki va raqami. Shu
sababli guruh taqsimoti va xona joylashuvi buzilmaydi.

Ketgan odamning hamma izi **o'chadi**: Telegram bog'lanishi, hujjatlari
(voucher/chipta), check-inlari, mas'ulligi, paneldagi roli va tili. Fayl boshqa
odamga ham tegishli bo'lsa diskda qoladi.

⚠️ **QR kod yangilanadi** — token pasportdan olinadi, pasport esa boshqa odamniki.
Eski beyjik yaroqsiz bo'ladi, «QR kodlar» bo'limidan yangisini chop eting.

Pasport allaqachon boshqa ishtirokchida bo'lsa almashtirish rad etiladi va kim
ekani aytiladi — seriyasi alohida saqlangan (masalan `77` + `3408359`) pasportlar
ham topiladi.

Kerak bo'ladigan ma'lumot: ism-familya va pasport majburiy; tug'ilgan sana,
jinsi, fuqarolik va amal muddati ixtiyoriy (keyin xlsx importidan ham keladi).

### Noto'g'ri bog'lanishni tuzatish

Kimdir boshqa odamning pasporti bilan ro'yxatdan o'tib qo'ysa (masalan ikkita
Telegram akkaunti bo'lgan odam o'zinikidan tashqari yana bittasini kiritsa),
panelda **«Ishtirokchilar»** bo'limidagi **Telegram** ustunidan tuzatiladi:

- ustunda kim bog'langani ko'rinadi (`@username` yoki `id 123…`);
- yonidagi **✕** bog'lanishni uzadi;
- shundan keyin haqiqiy egasi botda `/start` bosib o'z pasporti bilan tasdiqlaydi.

Guruh, xona, rol va **QR token tegilmaydi** — beyjik ishlashda davom etadi.
Ustun va tugma `manager` va `admin` rollariga ko'rinadi.

Hammasini birdan tozalash kerak bo'lsa — `tools/unlink_telegram.py`.

## Telegram bog'lanishlarini tozalash

Hamma qaytadan pasport bilan tasdiqlashi kerak bo'lsa:

```bash
./venv/bin/python sharm-seminar/tools/unlink_telegram.py --dry-run
./venv/bin/python sharm-seminar/tools/unlink_telegram.py
```

`telegram_id` va `telegram_username` bo'shatiladi, eski qiymatlar
`data/telegram_links_backup.json` ga saqlanadi (`--restore` bilan qaytariladi).
Guruh, xona, rol va **QR token tegilmaydi** — token pasportdan olinadi, ya'ni
chop etilgan beyjiklar ishlashda davom etadi.

## Bot buyruqlari

- `/start` — **pasport seriya va raqami** orqali aniqlash (tug'ilgan sana ham
  qabul qilinadi), Telegram ID bog'lash, guruh kartasi (guruh nomi, mas'uli,
  xonasi, sheriklari), shaxsiy sahifa (`/p/<token>`) va QR. Allaqachon
  bog'langan bo'lsa ham guruh kartasi qayta ko'rsatiladi — guruhlar keyin
  taqsimlangani uchun eski foydalanuvchilar uni ko'rmagan.
- `/menyu` — rol menyusini qayta ochish.
- `/skaner` — check-in skanerini (mini-app) ochish.
- `/guruh_chaqiruv` · `/guruh_taklif` · `/guruh_holat` · `/guruh_qulf` · `/guruh_ochiq` · `/guruh_tozala` — guruh nazorati (admin).
- `/tasdiqlanmaganlar` — kim hali tasdiqlamagan (admin va guruh mas'uli).
- `/hujjatlarim` — o'z voucher va chiptalarini olish.
- `/hujjat_yuborish` — hujjatlarni hali olmaganlarga ommaviy yuborish (admin).
- `/hujjat_ushla` — tarqatishni to'xtatib turish (admin).
- `/hujjat_holat` — nechta fayl bor, kimda nima yetishmayapti (admin).
- `/royxat` — yangi ishtirokchi/oila a'zosini to'liq ro'yxatga olish.
- `/yangilash` — mavjud ma'lumotni pasport orqali yangilash.
- `/bekor` — joriy jarayonni bekor qilib, o'z menyusiga qaytish.
  Xabar yozish va javob berish oynalarida **«❌ Bekor qilish»** tugmasi ham bor —
  bosilsa menyu darrov qaytadi, klaviaturasiz qolib ketilmaydi.
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
