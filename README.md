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

**Vaqt mintaqasi.** Barcha vaqtlar tadbir vaqtida (Misr, UTC+3) hisoblanadi va
yoziladi, server qayerda turganidan qat'i nazar. Boshqa mintaqa kerak bo'lsa
`settings.tz_offset` ni o'zgartiring.

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
| `/guruh_holat` | Hisobot: guruhda jami nechta, bot nechtasini taniydi, ro'yxatda borlar va yo'qlar |
| `/guruh_tozala` | Ro'yxatda yo'qlarni guruhdan chiqaradi (tasdiqlashdan keyin) |

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
- `/guruh_chaqiruv` · `/guruh_holat` · `/guruh_qulf` · `/guruh_ochiq` · `/guruh_tozala` — guruh nazorati (admin).
- `/tasdiqlanmaganlar` — kim hali tasdiqlamagan (admin va guruh mas'uli).
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
