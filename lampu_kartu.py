"""
Kartu gambar "Pasar | Lampu Lalu Lintas" (gaya gelap, watermark achevtaaa di kiri atas).

render(level, notes, snap, events, brief) -> bytes PNG, atau None kalau font/Pillow tidak tersedia
(pasar.py lalu otomatis memakai pesan teks biasa).

  level  : "HIJAU" | "KUNING" | "MERAH"
  notes  : daftar alasan (teks) dari pasar.assess()
  snap   : dict kondisi pasar dari pasar.market_snapshot()
  events : [{"title","forecast","previous","when","eta"}] (sudah diformat, boleh kosong)
  brief  : True = ringkasan pagi, False = status berubah
"""
import io
import os
from datetime import datetime, timedelta, timezone

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except Exception:                       # Pillow belum terpasang
    Image = None

WIB = timezone(timedelta(hours=7))
S = 2                                   # skala (gambar 1800 px lebar, tajam di layar HP)
W = 900
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

C = dict(bg=(13, 17, 23), card=(22, 27, 34), border=(38, 44, 54), box=(13, 17, 23), text=(230, 237, 243),
         mute=(139, 148, 158), faint=(110, 118, 129), dim=(33, 38, 45),
         red=(248, 81, 73), redt=(255, 123, 114), amber=(210, 153, 34), ambert=(227, 179, 65),
         green=(63, 185, 80), greent=(86, 211, 100))
LEVEL = {
    "MERAH": dict(col=C["red"], txt=C["redt"], title="MERAH · Risiko Tinggi", head="Kurangi leverage",
                  body="Pertimbangkan tidak membuka posisi baru.", on=0),
    "KUNING": dict(col=C["amber"], txt=C["ambert"], title="KUNING · Waspada", head="Kecilkan ukuran posisi",
                   body="Perketat SL dan jangan mengejar harga.", on=1),
    "HIJAU": dict(col=C["green"], txt=C["greent"], title="HIJAU · Kondisi Normal", head="Trading sesuai rencana",
                  body="Pakai ukuran risiko biasa dan tetap pasang SL.", on=2),
}
FONT_FILES = {
    "reg": ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "NotoSans-Regular.ttf", "Arial.ttf"],
    "bold": ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "NotoSans-Bold.ttf", "Arial Bold.ttf"],
    "ital": ["DejaVuSans-Oblique.ttf", "LiberationSans-Italic.ttf", "NotoSans-Italic.ttf", "Arial Italic.ttf"],
}
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation",
             "/usr/share/fonts/truetype/noto", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF",
             "/Library/Fonts", "/System/Library/Fonts/Supplemental", "C:/Windows/Fonts"]
_fc = {}


def font(kind, size):
    key = (kind, size)
    if key not in _fc:
        for name in FONT_FILES[kind]:
            for dd in FONT_DIRS:
                path = os.path.join(dd, name)
                if os.path.exists(path):
                    _fc[key] = ImageFont.truetype(path, int(size * S))
                    return _fc[key]
        raise RuntimeError("font tidak ditemukan")
    return _fc[key]


def p(v):
    return int(round(v * S))


def mix(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def wrap(d, text, f, maxw):
    lines, cur = [], ""
    for word in text.split():
        t = f"{cur} {word}".strip()
        if d.textlength(t, font=f) <= p(maxw):
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def spaced(d, xy, text, f, fill, gap=1.6):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + p(gap)


def fnum(v, fmt, default="-"):
    return default if v is None else fmt.format(v)


def content(d, img, lv, notes, snap, events, brief, y0, draw_glow):
    cx, cr = 54, W - 54
    inner = cr - cx
    # ---- judul
    y = y0
    spaced(d, (p(cx), p(y)), "PASAR · LAMPU LALU LINTAS", font("bold", 13), C["mute"], 2.0)
    d.text((p(cx), p(y + 22)), lv["title"], font=font("bold", 34), fill=C["text"])
    now = datetime.now(WIB)
    tag = "pagi" if brief else "status berubah"
    d.text((p(cx), p(y + 68)), f"{HARI[now.weekday()]}, {now.day} {BULAN[now.month - 1]} {now.year} · "
                              f"{now.strftime('%H:%M')} WIB ({tag})", font=font("reg", 14), fill=C["mute"])
    # ---- lampu lalu lintas
    hx, hy, hw, hh = cr - 54, y - 4, 54, 146
    d.rounded_rectangle((p(hx), p(hy), p(hx + hw), p(hy + hh)), radius=p(27), fill=C["box"], outline=C["border"], width=p(1))
    for i in range(3):
        dx, dy = hx + 10, hy + 12 + i * 44
        lit = i == lv["on"]
        if lit and draw_glow:
            pad = 40
            gl = Image.new("RGBA", (p(34 + pad * 2), p(34 + pad * 2)), (0, 0, 0, 0))
            ImageDraw.Draw(gl).ellipse((p(pad - 4), p(pad - 4), p(pad + 38), p(pad + 38)), fill=lv["col"] + (150,))
            gl = gl.filter(ImageFilter.GaussianBlur(p(11)))
            img.paste(gl, (p(dx - pad), p(dy - pad)), gl)
        d.ellipse((p(dx), p(dy), p(dx + 34), p(dy + 34)), fill=lv["col"] if lit else C["dim"])
    y = max(y + 96, hy + hh) + 20
    # ---- kotak status
    bf, tf = font("bold", 22), font("reg", 16)
    n = len(notes)
    body = lv["body"] + (f" {n} pemicu aktif." if n else " Tidak ada pemicu aktif.")
    lines = wrap(d, body, tf, inner - 44)
    bh = 16 + 28 + len(lines) * 23 + 14
    d.rounded_rectangle((p(cx), p(y), p(cr), p(y + bh)), radius=p(14), fill=mix(C["card"], lv["col"], 0.12),
                        outline=mix(C["card"], lv["col"], 0.5), width=p(1))
    d.text((p(cx + 22), p(y + 14)), lv["head"], font=bf, fill=lv["txt"])
    for i, ln in enumerate(lines):
        d.text((p(cx + 22), p(y + 48 + i * 23)), ln, font=tf, fill=C["text"])
    y += bh + 18
    # ---- kotak angka
    gap, cw, ch = 12, (inner - 24) / 3, 98
    btc24 = snap.get("btc24")
    fng = snap.get("fng")
    boxes = [
        ("BTC", fnum(snap.get("btc"), "${:,.0f}"), fnum(btc24, "{:+.1f}% dalam 24 jam"),
         C["redt"] if btc24 is not None and btc24 <= -5 else C["greent"] if btc24 is not None and btc24 >= 5 else C["mute"], None),
        ("FEAR & GREED", fnum(fng, "{}"), snap.get("fng_label") or "-", C["mute"], fng),
        ("FUNDING BTC", fnum(snap.get("fund_btc"), "{:+.4f}%"),
         "ekstrem" if snap.get("fund_btc") is not None and (snap["fund_btc"] > 0.05 or snap["fund_btc"] < -0.03) else "normal",
         C["ambert"] if snap.get("fund_btc") is not None and (snap["fund_btc"] > 0.05 or snap["fund_btc"] < -0.03) else C["mute"], None),
        ("USDT.D", fnum(snap.get("usdtd"), "{:.1f}%"), fnum(snap.get("usdtd_chg"), "{:+.2f} poin"),
         C["ambert"] if (snap.get("usdtd_chg") or 0) >= 0.3 else C["mute"], None),
        ("BTC.D", fnum(snap.get("btcd"), "{:.1f}%"), fnum(snap.get("btcd_chg"), "{:+.2f} poin"), C["mute"], None),
        ("EVENT BESAR 24 JAM", str(len(events)) if brief or events else "-", "lihat jadwal di bawah" if events else "tidak ada",
         C["ambert"] if events else C["mute"], None),
    ]
    for i, (lab, val, sub, subcol, bar) in enumerate(boxes):
        bx, by = cx + (i % 3) * (cw + gap), y + (i // 3) * (ch + gap)
        d.rounded_rectangle((p(bx), p(by), p(bx + cw), p(by + ch)), radius=p(12), fill=C["box"], outline=C["border"], width=p(1))
        spaced(d, (p(bx + 16), p(by + 13)), lab, font("reg", 12), C["mute"], 0.9)
        vcol = C["text"]
        if lab == "FEAR & GREED" and fng is not None and (fng <= 25 or fng >= 75):
            vcol = C["redt"]
        d.text((p(bx + 16), p(by + 31)), val, font=font("bold", 24), fill=vcol)
        d.text((p(bx + 16), p(by + 66)), sub, font=font("reg", 13), fill=subcol)
        if bar is not None:
            x0, x1, yy = bx + 16, bx + cw - 16, by + 90
            for k in range(int(x1 - x0)):
                t = k / max(1, x1 - x0)
                col = mix(C["red"], C["amber"], t * 2) if t < .5 else mix(C["amber"], C["green"], (t - .5) * 2)
                d.rectangle((p(x0 + k), p(yy), p(x0 + k + 1), p(yy + 4)), fill=col)
            mx = x0 + (x1 - x0) * max(0, min(100, bar)) / 100
            d.rectangle((p(mx - 1.5), p(yy - 3), p(mx + 1.5), p(yy + 7)), fill=(255, 255, 255))
    y += 2 * ch + gap + 22
    # ---- alasan
    sf = font("bold", 13)
    spaced(d, (p(cx), p(y)), f"KENAPA {lv['title'].split(' ')[0]}" if notes else "STATUS", sf, C["mute"], 1.8)
    y += 24
    if not notes:
        d.text((p(cx), p(y + 6)), "Tidak ada pemicu risiko aktif saat ini.", font=font("reg", 15), fill=C["mute"])
        y += 38
    for i, note in enumerate(notes[:5]):
        title, _, desc = note.partition(": ")
        if not desc:
            title, desc = note, ""
        d.line((p(cx), p(y), p(cr), p(y)), fill=C["border"], width=p(1))
        d.ellipse((p(cx), p(y + 17), p(cx + 10), p(y + 27)), fill=lv["col"])
        tl = wrap(d, title, font("bold", 15), inner - 28)
        for j, ln in enumerate(tl):
            d.text((p(cx + 24), p(y + 11 + j * 21)), ln, font=font("bold", 15), fill=C["text"])
        yy = y + 11 + len(tl) * 21
        if desc:
            for ln in wrap(d, desc[0].upper() + desc[1:] + ".", font("reg", 14), inner - 28):
                d.text((p(cx + 24), p(yy)), ln, font=font("reg", 14), fill=C["mute"])
                yy += 20
        y = yy + 10
    y += 8
    # ---- jadwal event
    if events:
        spaced(d, (p(cx), p(y)), "JADWAL 24 JAM KE DEPAN", sf, C["mute"], 1.8)
        y += 24
        for ev in events[:4]:
            d.line((p(cx), p(y), p(cr), p(y)), fill=C["border"], width=p(1))
            d.text((p(cx), p(y + 11)), ev["title"], font=font("bold", 15), fill=C["text"])
            d.text((p(cx), p(y + 34)), f"Forecast {ev['forecast'] or '-'} · Sebelumnya {ev['previous'] or '-'}",
                   font=font("reg", 13), fill=C["mute"])
            wf = font("reg", 15)
            d.text((p(cr) - d.textlength(ev["when"], font=wf), p(y + 11)), ev["when"], font=wf, fill=C["mute"])
            bfnt = font("bold", 12)
            bw = d.textlength(ev["eta"], font=bfnt) / S + 22
            bx = cr - bw
            d.rounded_rectangle((p(bx), p(y + 34), p(cr), p(y + 56)), radius=p(11), fill=mix(C["card"], C["amber"], 0.15),
                                outline=mix(C["card"], C["amber"], 0.5), width=p(1))
            d.text((p(bx + 11), p(y + 38)), ev["eta"], font=bfnt, fill=C["ambert"])
            y += 70
        y += 8
    # ---- saran aman
    if lv["on"] != 2:
        tip = ("Hindari buka posisi baru 30 menit sebelum dan sesudah rilis. Pasang SL, kecilkan leverage, tunggu candle "
               "15 menit setelah rilis ditutup." if events else
               "Pasang SL di setiap posisi, kecilkan leverage, dan tunggu konfirmasi sebelum masuk.")
        tl = wrap(d, tip, font("reg", 14), inner - 36)
        th = 14 + 22 + len(tl) * 21 + 12
        d.rounded_rectangle((p(cx), p(y), p(cr), p(y + th)), radius=p(12), fill=mix(C["card"], C["amber"], 0.10),
                            outline=mix(C["card"], C["amber"], 0.45), width=p(1))
        d.text((p(cx + 18), p(y + 12)), "Saran aman", font=font("bold", 15), fill=C["ambert"])
        for i, ln in enumerate(tl):
            d.text((p(cx + 18), p(y + 38 + i * 21)), ln, font=font("reg", 14), fill=(201, 209, 217))
        y += th + 18
    foot = "Data: OKX · CoinGecko · alternative.me · ForexFactory  |  Bukan nasihat finansial"
    ff = font("reg", 11)
    d.text(((p(W) - d.textlength(foot, font=ff)) / 2, p(y)), foot, font=ff, fill=C["faint"])
    return y + 14


def render(level, notes, snap, events, brief=True):
    if Image is None:
        return None
    try:
        lv = LEVEL[level]
        scratch = Image.new("RGB", (p(W), p(3600)), C["bg"])
        y_end = content(ImageDraw.Draw(scratch), scratch, lv, notes, snap, events, brief, 74, False)
        bottom = y_end + 8
        img = Image.new("RGB", (p(W), p(bottom + 30)), C["bg"])
        d = ImageDraw.Draw(img)
        d.text((p(28), p(20)), "achevtaaa", font=font("ital", 13), fill=C["faint"])
        d.rounded_rectangle((p(28), p(48), p(W - 28), p(bottom)), radius=p(18), fill=C["card"], outline=C["border"], width=p(1))
        content(d, img, lv, notes, snap, events, brief, 74, True)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:                 # font hilang, dsb: pasar.py memakai pesan teks
        print("kartu lampu gagal dibuat:", type(e).__name__, e)
        return None
