"""Sharm al-Shayx safar ro'yxati — oddiy web dashboard.

Ishga tushirish:
    cd /root/sharm_bot
    ./venv/bin/python dashboard.py

So'ng brauzerda http://SERVER_IP:5005 ni oching.
"""

from flask import Flask, render_template_string

import sheets

app = Flask(__name__)

# Sheets ustun indekslari (sheets.HEADER bo'yicha)
H = sheets.HEADER
C_SANA = H.index("Sana")
C_TOIFA = H.index("Toifa")
C_ISM = H.index("Ism")
C_FAMILYA = H.index("Familya")
C_XONA = H.index("Xona")
C_SHERIK = H.index("Sherik (seriya)")

# Xona turlari (statistika tartibi)
ROOM_TYPES = ["1 kishilik", "2 kishilik", "3 kishilik"]

# Xona sig'imi (har bir turdagi xona necha kishilik)
ROOM_CAPACITY = {"1 kishilik": 1, "2 kishilik": 2, "3 kishilik": 3}

# Rollar — (ko'rsatiladigan nom, mos keladigan kalit so'z)
ROLE_TYPES = [
    ("Xodim", "Xodim"),
    ("Shifokor/Diller", "Shifokor"),
    ("Oila a'zosi", "Oila"),
]


def _cell(row, idx):
    """Qatordan ustun qiymatini xavfsiz oladi."""
    return row[idx].strip() if idx < len(row) and row[idx] else ""


def _clusters(entries):
    """Xonadoshlarni «Sherik (seriya)» bog'lanishi bo'yicha klasterlarga (juftlik / uchlik)
    ajratadi. Bog'lanmagan (sherigi yo'q) odam — yakka klaster bo'ladi.
    Har bir klaster — bir xonada turadigan odamlar ro'yxati."""
    n = len(entries)

    # pasport seriya -> shu seriyali entry indekslari
    by_pass = {}
    for i, e in enumerate(entries):
        p = e["pasport"].strip().upper()
        if p:
            by_pass.setdefault(p, []).append(i)

    # qo'shnilik grafi — har kim o'z sherigi (seriya) bilan bog'lanadi
    adj = {i: set() for i in range(n)}
    for i, e in enumerate(entries):
        s = (e["sherik"] or "").strip().upper()
        if s and s != "—":
            for j in by_pass.get(s, []):
                if j != i:
                    adj[i].add(j)
                    adj[j].add(i)

    # bog'langan komponentalar (klasterlar)
    visited = set()
    clusters = []
    for i in range(n):
        if i in visited:
            continue
        stack = [i]
        comp = []
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            comp.append(entries[cur])
            for nb in adj[cur]:
                if nb not in visited:
                    stack.append(nb)
        clusters.append(comp)
    return clusters


def load_data():
    """Sheets'dan ma'lumotni o'qib, statistika va qatorlarni qaytaradi."""
    ws = sheets._get_worksheet()
    values = ws.get_all_values()
    rows = values[1:] if values else []  # sarlavhani tashlab yuboramiz

    # Bo'sh qatorlarni filtrlaymiz (Ism yoki Familya bo'lsa hisoblaymiz)
    people = [r for r in rows if _cell(r, C_ISM) or _cell(r, C_FAMILYA)]

    total = len(people)

    # Xona bo'yicha taqsimot
    by_room = {rt: 0 for rt in ROOM_TYPES}
    for r in people:
        room = _cell(r, C_XONA)
        for rt in ROOM_TYPES:
            if rt in room:
                by_room[rt] += 1
                break

    # Toifa (rol) bo'yicha taqsimot
    by_role = {name: 0 for name, _ in ROLE_TYPES}
    for r in people:
        toifa = _cell(r, C_TOIFA)
        for name, kw in ROLE_TYPES:
            if kw.lower() in toifa.lower():
                by_role[name] += 1
                break

    # Xona turi bo'yicha guruhlangan jadval
    grouped = {rt: [] for rt in ROOM_TYPES}
    other = []  # xona turi aniqlanmaganlar
    for r in people:
        room = _cell(r, C_XONA)
        entry = {
            "ism": _cell(r, C_ISM),
            "familya": _cell(r, C_FAMILYA),
            "toifa": _cell(r, C_TOIFA),
            "rol": _cell(r, H.index("Asosiy / Sherik")),
            "telefon": _cell(r, H.index("Telefon")),
            "pasport": _cell(r, H.index("Pasport seriya")),
            "amal": _cell(r, H.index("Amal muddati")),
            "xona": room or "—",
            "pay100": _cell(r, H.index("100% to'lov")),
            "sherik": _cell(r, H.index("Sherik (seriya)")) or "—",
            "drive": _cell(r, H.index("Pasport (Drive havola)")),
            "sana": _cell(r, C_SANA),
        }
        placed = False
        for rt in ROOM_TYPES:
            if rt in room:
                grouped[rt].append(entry)
                placed = True
                break
        if not placed:
            other.append(entry)

    clusters = {rt: _clusters(grouped[rt]) for rt in ROOM_TYPES}

    # Xona (klaster) statistikasi: jami xona, to'lgan, sherik kerak
    room_stats = {}
    for rt in ROOM_TYPES:
        cap = ROOM_CAPACITY[rt]
        cl = clusters[rt]
        full = sum(1 for c in cl if len(c) >= cap)
        need = sum(1 for c in cl if len(c) < cap)
        room_stats[rt] = {"rooms": len(cl), "full": full, "need": need}

    return {
        "total": total,
        "by_room": by_room,
        "by_role": by_role,
        "grouped": grouped,
        "clusters": clusters,
        "room_stats": room_stats,
        "other": other,
    }


TEMPLATE = """
<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="30">
    <title>Sharm al-Shayx — Ro'yxat paneli</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
            background: #f0f2f5; color: #1a2332; font-size: 14px;
        }
        header {
            background: #1e3a5f; color: #fff; padding: 18px 28px;
            display: flex; align-items: center; justify-content: space-between;
        }
        header h1 { font-size: 18px; font-weight: 600; }
        header .refresh { font-size: 12px; color: #8fb3d4; }
        .page { padding: 24px 28px; }

        /* ── Cards ── */
        .cards { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 24px; }
        .card {
            background: #fff; border-radius: 10px; padding: 16px 22px;
            box-shadow: 0 1px 4px rgba(0,0,0,.08); min-width: 140px; flex: 1;
        }
        .card .num { font-size: 32px; font-weight: 700; line-height: 1; }
        .card .lbl { font-size: 12px; color: #6b7a90; margin-top: 6px; }
        .card .sub {
            font-size: 12px; color: #475569; margin-top: 8px;
            padding-top: 8px; border-top: 1px solid #eef2f6;
        }
        .card .sub b { color: #1a2332; }
        .card .sub .ok { color: #16a34a; font-weight: 700; }
        .card .sub .warn { color: #ea580c; font-weight: 700; }
        .card.total .num { color: #1e3a5f; }
        .card.r1 .num { color: #64748b; }
        .card.r2 .num { color: #0ea5e9; }
        .card.r3 .num { color: #7c3aed; }

        /* ── Role pills ── */
        .roles { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 28px; }
        .role-pill {
            background: #fff; border: 1.5px solid #e2e8f0; border-radius: 8px;
            padding: 8px 16px; font-size: 13px; font-weight: 500;
        }
        .role-pill span { font-weight: 700; color: #1e3a5f; margin-left: 4px; }

        /* ── Section ── */
        .section-head {
            display: flex; align-items: center; gap: 10px;
            margin: 28px 0 12px; font-size: 15px; font-weight: 600; color: #1a2332;
        }
        .badge {
            background: #1e3a5f; color: #fff; font-size: 11px; font-weight: 700;
            border-radius: 20px; padding: 2px 10px;
        }
        .badge.r2 { background: #0ea5e9; }
        .badge.r3 { background: #7c3aed; }

        /* ── Table ── */
        .tbl-wrap {
            background: #fff; border-radius: 10px;
            box-shadow: 0 1px 4px rgba(0,0,0,.07); overflow: hidden;
        }
        table { width: 100%; border-collapse: collapse; }
        thead th {
            background: #f8fafc; color: #475569; font-size: 12px;
            font-weight: 600; text-transform: uppercase; letter-spacing: .4px;
            padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0;
            white-space: nowrap;
        }
        tbody td { padding: 9px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }
        tbody tr:last-child td { border-bottom: none; }
        tbody tr:hover td { background: #f8fafc; }

        /* Juftlik / guruh separator — har bir xona (juftlik/uchlik) orasini ajratadi */
        tbody tr.pair-start td { border-top: 3px solid #cbd5e1; }
        tbody tr.pair-start:first-child td { border-top: none; }

        /* Tartib raqami */
        td.num-cell { color: #94a3b8; font-size: 12px; font-weight: 600; width: 32px; }

        /* Xona badge inline */
        .xona-tag {
            display: inline-block; font-size: 11px; font-weight: 600;
            border-radius: 4px; padding: 2px 7px;
        }
        .xona-tag.r2 { background: #e0f2fe; color: #0369a1; }
        .xona-tag.r3 { background: #ede9fe; color: #6d28d9; }

        .drive-link { color: #0ea5e9; text-decoration: none; font-size: 12px; }
        .drive-link:hover { text-decoration: underline; }
        .muted { color: #94a3b8; }
        .empty { padding: 16px 20px; color: #94a3b8; font-style: italic; }
    </style>
</head>
<body>
<header>
    <h1>🏖️ Sharm al-Shayx safari — Ro'yxat paneli</h1>
    <div class="refresh">Har 30 soniyada yangilanadi</div>
</header>
<div class="page">

    <!-- Cards -->
    <div class="cards">
        <div class="card total">
            <div class="num">{{ data.total }}</div>
            <div class="lbl">Jami ro'yxatdan o'tganlar</div>
        </div>
        <div class="card r1">
            <div class="num">{{ data.room_stats['1 kishilik'].rooms }}</div>
            <div class="lbl">1 kishilik xona ({{ data.by_room['1 kishilik'] }} kishi)</div>
            <div class="sub"><span class="ok">{{ data.room_stats['1 kishilik'].full }}</span> to'lgan</div>
        </div>
        <div class="card r2">
            <div class="num">{{ data.room_stats['2 kishilik'].rooms }}</div>
            <div class="lbl">2 kishilik xona ({{ data.by_room['2 kishilik'] }} kishi)</div>
            <div class="sub"><span class="ok">{{ data.room_stats['2 kishilik'].full }}</span> to'lgan ·
                <span class="warn">{{ data.room_stats['2 kishilik'].need }}</span> sherik kerak</div>
        </div>
        <div class="card r3">
            <div class="num">{{ data.room_stats['3 kishilik'].rooms }}</div>
            <div class="lbl">3 kishilik xona ({{ data.by_room['3 kishilik'] }} kishi)</div>
            <div class="sub"><span class="ok">{{ data.room_stats['3 kishilik'].full }}</span> to'lgan ·
                <span class="warn">{{ data.room_stats['3 kishilik'].need }}</span> sherik kerak</div>
        </div>
    </div>

    <!-- Roles -->
    <div class="roles">
        {% for name, _ in role_types %}
        <div class="role-pill">{{ name }}: <span>{{ data.by_role[name] }}</span></div>
        {% endfor %}
    </div>

    <!-- 2 kishilik -->
    <div class="section-head">🏨 2 kishilik xona <span class="badge r2">{{ data.by_room['2 kishilik'] }}</span></div>
    {% if data.clusters['2 kishilik'] %}
    <div class="tbl-wrap">
    <table>
        <thead>
            <tr>
                <th>#</th><th>Ism</th><th>Familya</th><th>Toifa</th><th>Rol</th>
                <th>Telefon</th><th>Pasport</th><th>Amal muddati</th>
                <th>Xona</th><th>100%</th><th>Sherik</th><th>Pasport foto</th><th>Sana</th>
            </tr>
        </thead>
        <tbody>
        {% set ns = namespace(counter=0) %}
        {% for group in data.clusters['2 kishilik'] %}
            {% set group_loop = loop %}
            {% for p in group %}
            {% set ns.counter = ns.counter + 1 %}
            <tr class="{{ 'pair-start' if loop.first and not group_loop.first else '' }}">
                <td class="num-cell">{{ ns.counter }}</td>
                <td>{{ p.ism }}</td>
                <td>{{ p.familya }}</td>
                <td>{{ p.toifa }}</td>
                <td><span class="muted">{{ p.rol }}</span></td>
                <td>{% if p.telefon %}{{ p.telefon }}{% else %}<span class="muted">—</span>{% endif %}</td>
                <td><code>{{ p.pasport }}</code></td>
                <td>{{ p.amal }}</td>
                <td><span class="xona-tag r2">2 kishilik</span></td>
                <td>{{ p.pay100 }}</td>
                <td><code>{{ p.sherik }}</code></td>
                <td>{% if p.drive %}<a class="drive-link" href="{{ p.drive }}" target="_blank">📎 Rasm</a>{% else %}<span class="muted">—</span>{% endif %}</td>
                <td class="muted">{{ p.sana }}</td>
            </tr>
            {% endfor %}
        {% endfor %}
        </tbody>
    </table>
    </div>
    {% else %}<div class="empty">Hozircha yo'q.</div>{% endif %}

    <!-- 3 kishilik -->
    <div class="section-head">🏨 3 kishilik xona <span class="badge r3">{{ data.by_room['3 kishilik'] }}</span></div>
    {% if data.clusters['3 kishilik'] %}
    <div class="tbl-wrap">
    <table>
        <thead>
            <tr>
                <th>#</th><th>Ism</th><th>Familya</th><th>Toifa</th><th>Rol</th>
                <th>Telefon</th><th>Pasport</th><th>Amal muddati</th>
                <th>Xona</th><th>100%</th><th>Sherik</th><th>Pasport foto</th><th>Sana</th>
            </tr>
        </thead>
        <tbody>
        {% set ns2 = namespace(counter=0) %}
        {% for group in data.clusters['3 kishilik'] %}
            {% set group_loop = loop %}
            {% for p in group %}
            {% set ns2.counter = ns2.counter + 1 %}
            <tr class="{{ 'pair-start' if loop.first and not group_loop.first else '' }}">
                <td class="num-cell">{{ ns2.counter }}</td>
                <td>{{ p.ism }}</td>
                <td>{{ p.familya }}</td>
                <td>{{ p.toifa }}</td>
                <td><span class="muted">{{ p.rol }}</span></td>
                <td>{% if p.telefon %}{{ p.telefon }}{% else %}<span class="muted">—</span>{% endif %}</td>
                <td><code>{{ p.pasport }}</code></td>
                <td>{{ p.amal }}</td>
                <td><span class="xona-tag r3">3 kishilik</span></td>
                <td>{{ p.pay100 }}</td>
                <td><code>{{ p.sherik }}</code></td>
                <td>{% if p.drive %}<a class="drive-link" href="{{ p.drive }}" target="_blank">📎 Rasm</a>{% else %}<span class="muted">—</span>{% endif %}</td>
                <td class="muted">{{ p.sana }}</td>
            </tr>
            {% endfor %}
        {% endfor %}
        </tbody>
    </table>
    </div>
    {% else %}<div class="empty">Hozircha yo'q.</div>{% endif %}

    <!-- 1 kishilik -->
    {% if data.grouped['1 kishilik'] %}
    <div class="section-head">🏨 1 kishilik xona <span class="badge">{{ data.grouped['1 kishilik']|length }}</span></div>
    <div class="tbl-wrap">
    <table>
        <thead>
            <tr>
                <th>#</th><th>Ism</th><th>Familya</th><th>Toifa</th><th>Rol</th>
                <th>Telefon</th><th>Pasport</th><th>Amal muddati</th>
                <th>Xona</th><th>100%</th><th>Sherik</th><th>Pasport foto</th><th>Sana</th>
            </tr>
        </thead>
        <tbody>
        {% for p in data.grouped['1 kishilik'] %}
            <tr>
                <td class="num-cell">{{ loop.index }}</td>
                <td>{{ p.ism }}</td><td>{{ p.familya }}</td><td>{{ p.toifa }}</td>
                <td><span class="muted">{{ p.rol }}</span></td>
                <td>{% if p.telefon %}{{ p.telefon }}{% else %}<span class="muted">—</span>{% endif %}</td>
                <td><code>{{ p.pasport }}</code></td><td>{{ p.amal }}</td>
                <td><span class="xona-tag">1 kishilik</span></td>
                <td>{{ p.pay100 }}</td>
                <td><code>{{ p.sherik }}</code></td>
                <td>{% if p.drive %}<a class="drive-link" href="{{ p.drive }}" target="_blank">📎 Rasm</a>{% else %}<span class="muted">—</span>{% endif %}</td>
                <td class="muted">{{ p.sana }}</td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
    </div>
    {% endif %}

    {% if data.other %}
    <div class="section-head">❓ Xona turi aniqlanmagan <span class="badge">{{ data.other|length }}</span></div>
    <div class="tbl-wrap"><div class="empty">{{ data.other|length }} ta yozuv — xona maydoni bo'sh.</div></div>
    {% endif %}

</div>
</body>
</html>
"""


@app.route("/")
def index():
    try:
        data = load_data()
    except Exception as e:
        return f"<h1>Xatolik</h1><p>Ma'lumotni o'qishda muammo: {e}</p>", 500
    return render_template_string(
        TEMPLATE, data=data, room_types=ROOM_TYPES, role_types=ROLE_TYPES
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5005, debug=False)
