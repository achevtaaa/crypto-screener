"""
Kartu gambar umum untuk pesan Intel dan hasil backtest (gaya gelap sama seperti kartu lampu lalu lintas).

Pesan teks HTML yang sudah ada (<b>, <i>, <u>) diubah jadi gambar, jadi isi pesan tidak perlu ditulis ulang.
Kalau Pillow / font tidak ada atau gambar gagal dibuat, pesan dikirim sebagai teks biasa seperti sebelumnya.

  render(html, topic)        -> bytes PNG atau None
  kirim(S, html, topic)      -> mengirim gambar ke topik (routes.json); kalau gagal, kirim teks
"""
import html as htmlmod
import io
import json
import re
import time
from datetime import datetime

try:
    import lampu_kartu as L
    from PIL import Image, ImageDraw
except Exception:
    L = None

# warna aksen berdasarkan emoji di judul
ACCENT = {"🧨": "red", "🚨": "red", "❗": "red", "💤": "violet", "💵": "green", "🔄": "green", "✅": "green",
          "🕵": "amber", "⚡": "amber", "🐳": "blue", "🐋": "blue", "🚀": "green", "📊": "violet", "📜": "blue", "🧱": "violet", "🏷": "blue"}
MARK = {"✅": "green", "❌": "red", "➖": "mute", "⚠": "amber", "🟢": "green", "🔴": "red", "🟡": "amber",
        "🟣": "violet", "🔵": "blue", "⚪": "mute"}
EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF☀-➿⬀-⯿️‍⃣]")
TAG_RE = re.compile(r"(<[^>]+>)")
MAX_LOGICAL_H = 3800


def _colors():
    C = L.C
    return {"red": C["red"], "green": C["green"], "amber": C["amber"], "violet": (163, 113, 247),
            "blue": (88, 166, 255), "mute": C["mute"]}


def parse(html):
    """-> daftar baris; tiap baris = (penanda_emoji_awal, [(teks, flag)]) dengan flag subset 'biu'."""
    lines, cur, state = [], [], set()
    for tok in TAG_RE.split(html):
        if not tok:
            continue
        m = re.match(r"</?(b|i|u|a|code|pre|strong|em)\b", tok)
        if m:
            tag = {"strong": "b", "em": "i"}.get(m.group(1), m.group(1))
            if tag in ("b", "i", "u"):
                if tok.startswith("</"):
                    state.discard(tag)
                else:
                    state.add(tag)
            continue
        if tok.startswith("<"):
            continue
        text = htmlmod.unescape(tok)
        parts = text.split("\n")
        for k, part in enumerate(parts):
            if k > 0:
                lines.append(cur)
                cur = []
            if part:
                cur.append((part, "".join(sorted(state))))
    lines.append(cur)
    out = []
    for segs in lines:
        full = "".join(t for t, _ in segs).strip()
        if not full:
            continue
        lead = None
        first = full[0]
        if EMOJI_RE.match(first):
            lead = first
        cleaned = []
        for t, fl in segs:
            t = EMOJI_RE.sub("", t)
            if t:
                cleaned.append((t, fl))
        if cleaned:
            cleaned[0] = (cleaned[0][0].lstrip(), cleaned[0][1])
            cleaned[-1] = (cleaned[-1][0].rstrip(), cleaned[-1][1])
            cleaned = [(t, f) for t, f in cleaned if t]
        if cleaned or lead:
            out.append((lead, cleaned))
    return out


def _kind(fl):
    return "bold" if "b" in fl else ("ital" if "i" in fl else "reg")


def _flow(d, segs, size, maxw):
    toks = []
    for t, fl in segs:
        for part in re.split(r"(\s+)", t):
            if part:
                toks.append((part, fl))
    rows, x = [[]], 0
    limit = L.p(maxw)
    for w, fl in toks:
        f = L.font(_kind(fl), size)
        wl = d.textlength(w, font=f)
        if w.isspace():
            if x == 0:
                continue
            rows[-1].append((" ", fl, wl))
            x += wl
            continue
        if x + wl > limit and x > 0:
            rows.append([])
            x = 0
        rows[-1].append((w, fl, wl))
        x += wl
    return rows


def _all(segs, flag):
    return bool(segs) and all(flag in fl for _, fl in segs)


def _draw(d, parsed, acc, label, title_lines, y0, topic):
    C, col = L.C, _colors()
    cx, cr = 54, L.W - 54
    inner = cr - cx
    y = y0
    L.spaced(d, (L.p(cx), L.p(y)), label, L.font("bold", 13), C["mute"], 2.0)
    y += 24
    tf = L.font("bold", 30)
    for ln in title_lines:
        d.text((L.p(cx), L.p(y)), ln, font=tf, fill=acc)
        y += 40
    now = datetime.now(L.WIB)
    d.text((L.p(cx), L.p(y + 2)), f"{L.HARI[now.weekday()]}, {now.day} {L.BULAN[now.month - 1]} {now.year} · "
                                 f"{now.strftime('%H:%M')} WIB", font=L.font("reg", 14), fill=C["mute"])
    y += 34
    for lead, segs in parsed:
        if not segs:
            continue
        # catatan (seluruh baris miring) -> kotak
        if _all(segs, "i"):
            rows = _flow(d, segs, 15, inner - 40)
            bh = 26 + len(rows) * 22
            d.rounded_rectangle((L.p(cx), L.p(y), L.p(cr), L.p(y + bh)), radius=L.p(12),
                                fill=L.mix(C["card"], acc, 0.10), outline=L.mix(C["card"], acc, 0.45), width=L.p(1))
            for i, row in enumerate(rows):
                x = L.p(cx + 20)
                for w, fl, wl in row:
                    d.text((x, L.p(y + 13 + i * 22)), w, font=L.font(_kind(fl), 15), fill=(201, 209, 217))
                    x += wl
            y += bh + 14
            continue
        # sub-judul (seluruh baris tebal)
        if _all(segs, "b"):
            d.line((L.p(cx), L.p(y + 2), L.p(cr), L.p(y + 2)), fill=C["border"], width=L.p(1))
            rows = _flow(d, segs, 18, inner)
            y += 12
            for row in rows:
                x = L.p(cx)
                for ti, (w, fl, wl) in enumerate(row):
                    d.text((x, L.p(y)), w, font=L.font("bold", 18), fill=C["text"])
                    if "u" in fl and (w.strip() or ti < len(row) - 1):
                        d.line((x, L.p(y + 25), x + wl, L.p(y + 25)), fill=C["text"], width=L.p(1))
                    x += wl
                y += 26
            y += 6
            continue
        indent = 0
        if lead:
            indent = 22
            mc = col.get(MARK.get(lead, ""), acc) if lead in MARK else acc
            d.ellipse((L.p(cx), L.p(y + 8), L.p(cx + 10), L.p(y + 18)), fill=mc)
        rows = _flow(d, segs, 16, inner - indent)
        for row in rows:
            x = L.p(cx + indent)
            for ti, (w, fl, wl) in enumerate(row):
                fc = C["text"] if "b" in fl else C["mute"] if "i" in fl else (201, 209, 217)
                d.text((x, L.p(y)), w, font=L.font(_kind(fl), 16), fill=fc)
                if "u" in fl and (w.strip() or ti < len(row) - 1):
                    d.line((x, L.p(y + 22), x + wl, L.p(y + 22)), fill=fc, width=L.p(1))
                x += wl
            y += 25
        y += 6
    y += 8
    foot = "Bukan nasihat finansial · selalu pasang SL"
    ff = L.font("reg", 11)
    d.text(((L.p(L.W) - d.textlength(foot, font=ff)) / 2, L.p(y)), foot, font=ff, fill=C["faint"])
    return y + 16


def _page(rest, head, lead, label, topic, tag=""):
    """Satu gambar PNG, atau None kalau terlalu tinggi / gagal."""
    col = _colors()
    acc = col.get(ACCENT.get(lead, ""), col["amber"]) if lead else col["amber"]
    title = head + (f" {tag}" if tag else "")
    scratch = Image.new("RGB", (L.p(L.W), L.p(MAX_LOGICAL_H + 400)), L.C["bg"])
    d0 = ImageDraw.Draw(scratch)
    tl = L.wrap(d0, title, L.font("bold", 30), L.W - 108)
    y_end = _draw(d0, rest, acc, label, tl, 74, topic)
    if y_end > MAX_LOGICAL_H - 60:
        return None
    bottom = y_end + 8
    img = Image.new("RGB", (L.p(L.W), L.p(bottom + 30)), L.C["bg"])
    d = ImageDraw.Draw(img)
    d.text((L.p(28), L.p(20)), "achevtaaa", font=L.font("ital", 13), fill=L.C["faint"])
    d.rounded_rectangle((L.p(28), L.p(48), L.p(L.W - 28), L.p(bottom)), radius=L.p(18), fill=L.C["card"],
                        outline=L.C["border"], width=L.p(1))
    _draw(d, rest, acc, label, tl, 74, topic)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _split(rest, head, lead, label, topic):
    """Daftar PNG. Kalau satu gambar terlalu tinggi, isinya dibagi dua (bukan dipotong) secara rekursif."""
    png = _page(rest, head, lead, label, topic)
    if png:
        return [png]
    if len(rest) < 2:
        return None
    mid = len(rest) // 2
    while mid > 1 and _all(rest[mid - 1][1], "b"):      # jangan pisahkan sub-judul dari isinya
        mid -= 1
    a = _split(rest[:mid], head, lead, label, topic)
    b = _split(rest[mid:], head, lead, label, topic)
    if a is None or b is None:
        return None
    return a + b


def render_pages(html, topic="intel"):
    """Semua isi pesan jadi satu atau beberapa gambar PNG (tidak ada yang dikurangi). None kalau gagal."""
    if L is None or L.Image is None:
        return None
    try:
        parsed = parse(html)
        if not parsed:
            return None
        head_lead, head_segs = parsed[0]
        head = "".join(t for t, _ in head_segs).strip()
        label = {"intel": "INTEL", "screener": "SCREENER", "pasar": "PASAR", "narasi": "NARASI"}.get(topic, topic.upper())
        if "|" in head:                                  # "SCREENER | UJI ..." -> label + judul
            a, _, b = head.partition("|")
            label, head = a.strip().upper(), b.strip()
        pages = _split(parsed[1:], head, head_lead, label, topic)
        if not pages:
            return None
        if len(pages) > 1:                               # beri nomor halaman
            pages = _split_numbered(parsed[1:], head, head_lead, label, topic, len(pages))
        return pages
    except Exception as e:
        print("kartu gagal dibuat:", type(e).__name__, e)
        return None


def _split_numbered(rest, head, lead, label, topic, n):
    """Sama seperti _split, tetapi judul tiap halaman diberi (1/n)."""
    chunks = []

    def go(items):
        if _page(items, head, lead, label, topic):
            chunks.append(items)
            return
        mid = len(items) // 2
        while mid > 1 and _all(items[mid - 1][1], "b"):
            mid -= 1
        go(items[:mid])
        go(items[mid:])

    go(rest)
    return [_page(c, head, lead, label, topic, tag=f"({i + 1}/{len(chunks)})") for i, c in enumerate(chunks)]


def render(html, topic="intel"):
    """Halaman pertama saja (kompatibel dengan pemanggil lama)."""
    pages = render_pages(html, topic)
    return pages[0] if pages else None


def _send_photo(S, png, caption, topic):
    import requests
    try:
        with open("routes.json") as f:
            routes = json.load(f)
    except Exception:
        routes = {}
    url = f"https://api.telegram.org/bot{S.TG_TOKEN}/sendPhoto"
    gid, thread = routes.get("group"), routes.get(topic) or (routes.get("intel") if topic == "narasi" else None)
    for data in ([dict(chat_id=gid, message_thread_id=thread)] if gid and thread else []) + [dict(chat_id=S.TG_CHAT)]:
        r = requests.post(url, data=dict(data, caption=caption[:200]), files={"photo": ("kartu.png", png, "image/png")},
                          timeout=30)
        if r.status_code == 200:
            time.sleep(0.5)
            return True
        print("kirim gambar gagal:", r.status_code)
    return False


def kirim(S, html, topic="intel", fallback=None):
    """Kirim sebagai gambar (satu atau beberapa halaman). Kalau tidak bisa, kirim teks seperti biasa."""
    pages = render_pages(html, topic)
    if pages:
        head = "".join(t for t, _ in (parse(html)[0][1] or [("", "")])).strip()
        sent = 0
        for i, png in enumerate(pages):
            cap = head + (f" ({i + 1}/{len(pages)})" if len(pages) > 1 else "")
            try:
                if _send_photo(S, png, cap, topic):
                    sent += 1
            except Exception as e:
                print("kartu gagal dikirim:", type(e).__name__)
        if sent == len(pages):
            return
        if sent:                                          # sebagian terkirim: jangan kirim ulang semuanya
            print("sebagian halaman kartu gagal terkirim")
            return
    if fallback:
        fallback(html)
        return
    for part in S.split_for_telegram(html):
        S.send_routed(part, topic)


def simpan_contoh(html, path, topic="intel"):
    pages = render_pages(html, topic)
    if not pages:
        return False
    for i, png in enumerate(pages):
        with open(path if i == 0 else path.replace(".png", f"_{i + 1}.png"), "wb") as f:
            f.write(png)
    return True
