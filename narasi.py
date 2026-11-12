#!/usr/bin/env python3
"""Peta Narasi: performa sektor (median koin per sektor) -> kartu gambar -> Telegram.

Sektor = keranjang koin yang Anda atur sendiri di SEKTOR di bawah (cukup ubah daftar
simbolnya). Koin yang tidak punya perpetual USDT di OKX, atau volumenya di bawah
NARASI_MIN_VOL_USD (default $1 juta per 24 jam), dilewati otomatis; sektor dengan
< 3 koin tersedia tidak ditampilkan.

Performa:
  7 HARI  = harga kini vs close candle harian ke-8 dari belakang (~7 hari)
  24 JAM  = harga kini vs open 24 jam OKX
  Nilai sektor = MEDIAN koin di keranjang (tahan terhadap satu koin yang pump/dump).
  Pasar 24 jam = MEDIAN semua perpetual USDT yang likuid (basis sama dengan sektor).

Blok "Panas 24 jam" = narasi (kategori CoinGecko) yang naik di atas pasar. Token pembungkus
(Wrapped ...) dan stablecoin dibuang. Jika CoinGecko gagal, blok itu disembunyikan.

Tujuan Telegram dibaca dari routes.json (kunci "group" dan topik "narasi";
jika topik "narasi" tidak ada, dipakai topik "pasar").
Token: env TELEGRAM_BOT_TOKEN atau TELEGRAM_TOKEN.

Pemakaian:
  python narasi.py          # jalankan, kirim ke Telegram
  python narasi.py --dry    # jalankan, simpan narasi.png, tidak kirim
  python narasi.py --mock   # data contoh (tanpa internet), simpan narasi.png

Kartu baru ada di narasi_image.py (+ imgkit.py). Bila file itu hilang atau gagal, otomatis
dipakai kartu lama di bawah (render_lama) supaya bot tidak berhenti.
"""
import io
import os
import re
import sys
import time
from statistics import median

import requests
from PIL import Image

from common import GREEN, MUTED, RED, TRACK, W, Pen, font, okx, pct, send_photo, stamp

try:
    from narasi_image import build_caption as _cap_baru
    from narasi_image import pick_hot, prepare_sectors
    from narasi_image import render_narasi as _render_baru
except Exception as _e:  # noqa: BLE001
    print(f"[warn] modul kartu baru tidak ada ({_e}); pakai kartu lama", file=sys.stderr)
    _render_baru = _cap_baru = pick_hot = prepare_sectors = None

THREAD = ("narasi", "pasar")  # nama topik di routes.json (urut prioritas)
MIN_ANGGOTA = 3


def _float_env(name, default):
    """Baca env angka; kosong atau salah -> default (env kosong tidak boleh bikin crash)."""
    try:
        return float((os.environ.get(name) or "").strip().replace(",", ".") or default)
    except ValueError:
        return default


MIN_VOL_USD = _float_env("NARASI_MIN_VOL_USD", 1_000_000)

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

# Kategori CoinGecko yang bukan narasi nyata (sama dengan filter di pasar.py)
SKIP_CAT = re.compile(r"stablecoin|wrapped|bridged|fiat|yield.?bearing|liquid stak|liquid restak|etf|portfolio|"
                      r"layer 1|smart contract|governance|exchange.?based|centralized exchange|alleged|"
                      r"made in|tokenized (gold|commodit|silver)|asset-backed|usd stablecoin|"
                      r"ecosystem$|coinbase|binance|ftx|dex$|nft marketplace", re.I)


def fetch():
    """-> (rows, baseline). rows = [(sektor, median 7 hari, median 24 jam, jumlah koin)];
    baseline = median perubahan 24 jam semua perpetual USDT yang likuid (None bila data kurang)."""
    tk = okx("/api/v5/market/tickers", {"instType": "SWAP"})
    if not tk:
        raise SystemExit("Ticker OKX gagal diambil, batal.")
    T = {}
    for x in tk:
        if x["instId"].endswith("-USDT-SWAP") and x.get("last") and x.get("open24h"):
            try:
                last, o24 = float(x["last"]), float(x["open24h"])
                vol_usd = float(x.get("volCcy24h") or 0) * last
            except ValueError:
                continue
            if last > 0 and o24 > 0 and vol_usd >= MIN_VOL_USD:
                T[x["instId"]] = (last, o24)

    all24 = [(last / o24 - 1) * 100 for last, o24 in T.values()]
    baseline = median(all24) if len(all24) >= 20 else None

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
    return rows, baseline


def _pretty(coin_id):
    return str(coin_id).replace("-", " ").title()


def hot_narratives():
    """Narasi (kategori CoinGecko) 24 jam: [{name, ret_24h, coins}]. None bila gagal diambil."""
    key = os.environ.get("COINGECKO_KEY") or os.environ.get("COINGECKO_API_KEY")
    hdr = {"accept": "application/json"}
    if key:
        hdr["x-cg-demo-api-key"] = key
    cats = None
    for _ in range(3):
        try:
            r = requests.get("https://api.coingecko.com/api/v3/coins/categories", headers=hdr, timeout=25)
            if r.status_code == 429:
                time.sleep(20)
                continue
            r.raise_for_status()
            cats = r.json()
            break
        except Exception as e:  # noqa: BLE001
            print(f"[warn] kategori CoinGecko gagal ({e})", file=sys.stderr)
            time.sleep(3)
    if not isinstance(cats, list):
        return None
    out = []
    for c in cats:
        try:
            mc, ch, vol = c["market_cap"], c["market_cap_change_24h"], c.get("volume_24h") or 0
            name = c["name"]
        except Exception:  # noqa: BLE001
            continue
        if not mc or ch is None or mc < 300e6 or vol < 30e6 or SKIP_CAT.search(name or ""):
            continue
        out.append({"name": name, "ret_24h": ch,
                    "coins": [_pretty(x) for x in (c.get("top_3_coins_id") or [])[:3]]})
    return out


# --------------------------------------------------------------------------
# Kartu baru (narasi_image.py)
# --------------------------------------------------------------------------

def render_baru(rows, baseline, hot):
    """-> (gambar PIL, caption) atau None bila gagal (pemanggil memakai kartu lama)."""
    if not _render_baru:
        return None
    try:
        sektor = prepare_sectors(
            [{"name": n, "ret_7d": d7, "ret_24h": d24, "n_coins": k} for n, d7, d24, k in rows],
            min_coins=MIN_ANGGOTA)
        hot_list = None if hot is None else pick_hot(hot, market_24h=baseline, limit=3)
        png = _render_baru(sektor, hot_list, market_24h=baseline,
                           market_label="Pasar 24 jam · median koin")
        if not png:
            return None
        img = Image.open(io.BytesIO(png))
        img.load()
        return img, _cap_baru(sektor, hot_list or [], baseline)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] kartu baru gagal ({type(e).__name__}: {e}); pakai kartu lama", file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# Kartu lama (cadangan)
# --------------------------------------------------------------------------

def render_lama(rows):
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
    rows = [("RWA", 2.2, 1.3, 5), ("DeFi / DEX", -1.8, 0.6, 8), ("AI", -2.9, -0.4, 7),
            ("Layer 1", -3.7, -0.1, 10), ("Privacy", -3.8, -2.4, 4), ("Layer 2", -6.1, -1.2, 6),
            ("Meme", -8.4, -2.1, 7)]
    hot = [{"name": "Lending/Borrowing Protocols", "ret_24h": 5.9, "coins": ["Aave", "Morpho", "Quantixai"]},
           {"name": "Crypto-Backed Tokens", "ret_24h": 17.5,
            "coins": ["Wrapped Steth", "Wrapped Bitcoin", "Wrapped Beacon Eth"]},
           {"name": "Chinese Meme", "ret_24h": 4.7, "coins": ["Bianrensheng", "Niu Lai", "Meme Horse"]},
           {"name": "Tokenized Stocks", "ret_24h": 2.8, "coins": ["Spacex Bstocks", "Nasdaq Xstock"]},
           {"name": "Frog-Themed", "ret_24h": -14.0, "coins": ["Pepe", "Turbo"]}]
    return rows, -2.6, hot


def main():
    args = set(sys.argv[1:])
    if "--mock" in args:
        rows, baseline, hot = mock()
    else:
        rows, baseline = fetch()
        hot = hot_narratives()

    made = render_baru(rows, baseline, hot)
    if made:
        img, cap = made
    else:
        img, srows = render_lama(rows)
        cap = caption(srows)

    if {"--dry", "--mock"} & args:
        img.save("narasi.png")
        print("Disimpan: narasi.png\n" + cap)
        return
    send_photo(img, cap, THREAD)


if __name__ == "__main__":
    main()
