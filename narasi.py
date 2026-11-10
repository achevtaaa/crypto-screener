#!/usr/bin/env python3
"""Peta Narasi: performa sektor (median koin per sektor) -> kartu gambar -> Telegram.

Sektor = keranjang koin yang Anda atur sendiri di SEKTOR di bawah (cukup ubah daftar
simbolnya). Koin yang tidak punya perpetual USDT di OKX dilewati otomatis; sektor
dengan < 3 koin tersedia tidak ditampilkan.

Performa:
  7 HARI  = harga kini vs close candle harian ke-8 dari belakang (~7 hari)
  24 JAM  = harga kini vs open 24 jam OKX
  Nilai sektor = MEDIAN koin di keranjang (tahan terhadap satu koin yang pump/dump).

Env untuk kirim:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  TELEGRAM_THREAD_ID_NARASI (jika kosong, pakai TELEGRAM_THREAD_ID_PASAR)

Pemakaian:
  python narasi.py          # jalankan, kirim ke Telegram
  python narasi.py --dry    # jalankan, simpan narasi.png, tidak kirim
  python narasi.py --mock   # data contoh (tanpa internet), simpan narasi.png
"""
import sys
from statistics import median

from common import GREEN, MUTED, RED, TRACK, W, Pen, font, okx, pct, send_photo, stamp

THREAD = ("TELEGRAM_THREAD_ID_NARASI", "TELEGRAM_THREAD_ID_PASAR")
MIN_ANGGOTA = 3

# ---- ATUR SEKTOR DI SINI (simbol koin, tanpa -USDT) ------------------------
SEKTOR = {
    "Layer 1":    ["ETH", "SOL", "BNB", "ADA", "AVAX", "NEAR", "SUI", "APT", "TON", "TRX"],
    "Layer 2":    ["ARB", "OP", "POL", "STRK", "MNT", "IMX"],
    "DeFi / DEX": ["UNI", "AAVE", "HYPE", "JUP", "CRV", "LDO", "PENDLE", "ENA"],
    "AI":         ["RENDER", "FET", "TAO", "WLD", "VIRTUAL", "ARKM", "GRT"],
    "RWA":        ["ONDO", "LINK", "POLYX", "CFG", "PLUME"],
    "Privacy":    ["ZEC", "XMR", "DASH", "ROSE", "SCRT"],
    "Meme":       ["DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI", "TRUMP"],
}
# -----------------------------------------------------------------------------


def fetch():
    tk = okx("/api/v5/market/tickers", {"instType": "SWAP"})
    if not tk:
        raise SystemExit("Ticker OKX gagal diambil, batal.")
    T = {}
    for x in tk:
        if x["instId"].endswith("-USDT-SWAP") and x.get("last") and x.get("open24h"):
            last, o24 = float(x["last"]), float(x["open24h"])
            if last > 0 and o24 > 0:
                T[x["instId"]] = (last, o24)

    rows = []
    for nama, syms in SEKTOR.items():
        c7, c24 = [], []
        for s in syms:
            inst = f"{s}-USDT-SWAP"
            if inst not in T:
                continue
            d = okx("/api/v5/market/candles", {"instId": inst, "bar": "1D", "limit": "10"})
            if not d or len(d) < 8:  # terbaru dulu
                continue
            base = float(d[7][4])
            if base <= 0:
                continue
            last, o24 = T[inst]
            c7.append((last / base - 1) * 100)
            c24.append((last / o24 - 1) * 100)
        if len(c7) >= MIN_ANGGOTA:
            rows.append((nama, median(c7), median(c24), len(c7)))
        else:
            print(f"[info] sektor {nama} dilewati ({len(c7)} koin tersedia)")
    if len(rows) < 3:
        raise SystemExit("Terlalu sedikit sektor yang datanya lengkap, batal.")
    return rows


def render(rows):
    rows = sorted(rows, key=lambda r: -r[1])
    n, RH = len(rows), 92
    top, head = 168, 70
    ch = head + n * RH + 8
    note_y = top + ch + 24
    H = note_y + 150 + 70
    p = Pen(H)
    p.watermark()
    p.t(W - 48, 84, stamp(), font("Inter-Medium.otf", 22), fill=MUTED, anchor="ra")
    p.t(48, 84, "PETA NARASI", font("Inter-SemiBold.otf", 22), fill=MUTED)
    p.t(48, 120, "Median performa koin per sektor", font("Inter-Medium.otf", 20), fill=(104, 114, 138))

    p.rr(40, top, W - 80, ch)
    p.t(80, top + 36, "SEKTOR", font("Inter-Medium.otf", 18), fill=MUTED, anchor="lm")
    p.t(780, top + 36, "7 HARI", font("Inter-Medium.otf", 18), fill=MUTED, anchor="rm")
    p.t(1000, top + 36, "24 JAM", font("Inter-Medium.otf", 18), fill=MUTED, anchor="rm")
    p.line(80, top + head - 6, W - 80, top + head - 6)

    cx, half, scale = 520, 150, 10.0
    for i, (name, d7, d24, _) in enumerate(rows):
        ym = top + head + i * RH + RH / 2
        if i:
            p.line(80, ym - RH / 2, W - 80, ym - RH / 2)
        col = GREEN if d7 >= 0 else RED
        p.t(80, ym, name, font("Inter-SemiBold.otf", 28), anchor="lm")
        bh = 14
        p.box(cx - half, ym - bh / 2, cx + half, ym + bh / 2, 7, fill=TRACK)
        L = max(int(min(abs(d7), scale) / scale * half), 8)
        x0, x1 = (cx, cx + L) if d7 >= 0 else (cx - L, cx)
        p.box(x0, ym - bh / 2, x1, ym + bh / 2, 7, fill=col)
        p.line(cx, ym - 14, cx, ym + 14, fill=(96, 106, 130))
        p.t(780, ym, pct(d7, 1), font("Inter-Bold.otf", 28), fill=col, anchor="rm")
        p.t(1000, ym, pct(d24, 1), font("Inter-SemiBold.otf", 26),
            fill=GREEN if d24 >= 0 else RED, anchor="rm")

    best, worst = rows[0], rows[-1]
    p.rr(40, note_y, W - 80, 150, 24, fill=(22, 30, 52), outline=(44, 62, 108))
    p.t(80, note_y + 40, f"Terkuat   {best[0]}  {pct(best[1], 1)}", font("Inter-SemiBold.otf", 28),
        fill=GREEN if best[1] >= 0 else RED, anchor="lm")
    p.t(80, note_y + 84, f"Terlemah   {worst[0]}  {pct(worst[1], 1)}", font("Inter-SemiBold.otf", 28),
        fill=RED if worst[1] < 0 else GREEN, anchor="lm")
    p.t(80, note_y + 122, "Hijau = uang masuk sektor · merah = uang keluar", font("Inter-Medium.otf", 19),
        fill=MUTED, anchor="lm")
    p.footer("Data: OKX perpetual  |  Bukan nasihat finansial")
    return p.done(), rows


def caption(rows):
    b, w = rows[0], rows[-1]
    return (f"Peta narasi 7 hari: terkuat {b[0]} {pct(b[1], 1)}, terlemah {w[0]} {pct(w[1], 1)}. "
            "Bukan nasihat finansial.")


def mock():
    return [("RWA", 2.2, 1.3, 5), ("DeFi / DEX", -1.8, 0.6, 8), ("AI", -2.9, -0.4, 7),
            ("Layer 1", -3.7, -0.1, 10), ("Privacy", -3.8, -2.4, 4), ("Layer 2", -6.1, -1.2, 6),
            ("Meme", -8.4, -2.1, 7)]


def main():
    args = set(sys.argv[1:])
    rows = mock() if "--mock" in args else fetch()
    img, rows = render(rows)
    cap = caption(rows)
    if {"--dry", "--mock"} & args:
        img.save("narasi.png")
        print("Disimpan: narasi.png\n" + cap)
        return
    send_photo(img, cap, THREAD)


if __name__ == "__main__":
    main()
