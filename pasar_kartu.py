#!/usr/bin/env python3
"""Bot Pasar: ambil data pasar BTC, gambar kartu briefing, kirim ke topik Telegram.

Sumber data (semua publik, tanpa API key):
  - OKX      : harga, candle harian, funding, open interest, likuidasi, long/short
  - CoinGecko: market cap total & dominasi
  - alternative.me: Fear & Greed

Tujuan Telegram dibaca dari routes.json (kunci "group" dan topik "pasar").
Token: env TELEGRAM_BOT_TOKEN atau TELEGRAM_TOKEN.
Env opsional:
  PASAR_EVENT   teks peringatan manual, mis. "CPI AS malam ini · 20.30 WIB"
  COINGECKO_KEY demo key CoinGecko (jika kena rate limit)

Pemakaian:
  python pasar_kartu.py            # ambil data, kirim ke Telegram
  python pasar_kartu.py --dry      # ambil data, simpan kartu.png, tidak kirim
  python pasar_kartu.py --mock     # data contoh (tanpa internet), simpan kartu.png
"""
import io
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OKX = "https://www.okx.com"
TIMEOUT = 20

# --------------------------------------------------------------------------
# Util
# --------------------------------------------------------------------------

def get(url, params=None, headers=None):
    """GET JSON dengan 2x retry. Mengembalikan None jika gagal."""
    for i in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {url} gagal ({e})", file=sys.stderr)
            time.sleep(1.5 * (i + 1))
    return None


def idn(s):
    """Format angka gaya Indonesia untuk desimal: 1.234,5 -> tukar , dan ."""
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def pct(v, digits=2, sign=True):
    if v is None:
        return "—"
    s = f"{v:+.{digits}f}%" if sign else f"{v:.{digits}f}%"
    return s.replace(".", ",").replace("-", "−")


def usd_big(v, digits=2):
    if v is None:
        return "—"
    a = abs(v)
    sign = "−" if v < 0 else ""
    if a >= 1e12:
        return f"{sign}${a / 1e12:.{digits}f}T".replace(".", ",")
    if a >= 1e9:
        return f"{sign}${a / 1e9:.{0 if a >= 1e11 else digits}f}B".replace(".", ",")
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M".replace(".", ",")
    return f"{sign}${a:,.0f}"


def fng_label(v):
    if v is None:
        return "—"
    return ("Sangat takut" if v < 25 else "Takut" if v < 45 else "Netral" if v < 55
            else "Serakah" if v < 75 else "Sangat serakah")


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def rsi14(closes):
    """RSI Wilder dari list close (lama -> baru)."""
    if len(closes) < 15:
        return None
    gains = losses = 0.0
    for i in range(1, 15):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    ag, al = gains / 14, losses / 14
    for i in range(15, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * 13 + max(ch, 0)) / 14
        al = (al * 13 + max(-ch, 0)) / 14
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def swing_levels(candles, price):
    """Resistance/support terdekat dari swing high/low harian (2 candle kiri-kanan)."""
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    res, sup = [], []
    for i in range(2, len(candles) - 2):
        if highs[i] == max(highs[i - 2:i + 3]) and highs[i] > price:
            res.append(highs[i])
        if lows[i] == min(lows[i - 2:i + 3]) and lows[i] < price:
            sup.append(lows[i])
    r = min(res) if res else max(highs[-30:])
    s = max(sup) if sup else min(lows[-30:])
    return r, s


def fetch_data():
    d = {}

    # candle harian OKX (terbaru dulu) -> lama ke baru
    j = get(f"{OKX}/api/v5/market/candles", {"instId": "BTC-USDT", "bar": "1D", "limit": "100"})
    candles = []
    if j and j.get("data"):
        for row in reversed(j["data"]):
            candles.append({"c": float(row[4]), "h": float(row[2]), "l": float(row[3])})
    if len(candles) < 55:
        raise SystemExit("Candle OKX tidak cukup, batal.")
    closes = [c["c"] for c in candles]

    # harga & perubahan 24 jam
    j = get(f"{OKX}/api/v5/market/ticker", {"instId": "BTC-USDT"})
    t = j["data"][0] if j and j.get("data") else None
    price = float(t["last"]) if t else closes[-1]
    open24 = float(t["open24h"]) if t else closes[-2]
    d["price"] = price
    d["chg24"] = (price / open24 - 1) * 100

    d["rsi"] = rsi14(closes)
    d["ma20"] = sum(closes[-20:]) / 20
    d["ma50"] = sum(closes[-50:]) / 50
    d["res"], d["sup"] = swing_levels(candles[-60:], price)
    d["low30"] = min(c["l"] for c in candles[-30:])

    # funding
    j = get(f"{OKX}/api/v5/public/funding-rate", {"instId": "BTC-USDT-SWAP"})
    d["funding"] = float(j["data"][0]["fundingRate"]) * 100 if j and j.get("data") else None

    # open interest (USD) + perubahan 7 hari
    j = get(f"{OKX}/api/v5/rubik/stat/contracts/open-interest-volume", {"ccy": "BTC", "period": "1D"})
    d["oi"], d["oi7"] = None, None
    if j and j.get("data") and len(j["data"]) >= 8:
        rows = j["data"]  # terbaru dulu: [ts, oi, vol]
        now_oi, old_oi = float(rows[0][1]), float(rows[7][1])
        d["oi"], d["oi7"] = now_oi, (now_oi / old_oi - 1) * 100

    # long/short account ratio
    j = get(f"{OKX}/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": "BTC", "period": "1H"})
    d["ls"] = float(j["data"][0][1]) if j and j.get("data") else None

    # likuidasi 24 jam (OKX saja)
    d["liq_long"], d["liq_short"] = None, None
    j = get(f"{OKX}/api/v5/public/liquidation-orders",
            {"instType": "SWAP", "uly": "BTC-USDT", "state": "filled", "limit": "100"})
    if j and j.get("data"):
        cutoff = (time.time() - 86400) * 1000
        lng = sht = 0.0
        for grp in j["data"]:
            for x in grp.get("details", []):
                if float(x["ts"]) < cutoff:
                    continue
                usd = float(x["sz"]) * 0.01 * float(x["bkPx"])  # 1 kontrak = 0,01 BTC
                if x["posSide"] == "long":
                    lng += usd
                else:
                    sht += usd
        d["liq_long"], d["liq_short"] = lng, sht

    # market cap global
    hdr = {"x-cg-demo-api-key": os.environ["COINGECKO_KEY"]} if os.environ.get("COINGECKO_KEY") else None
    j = get("https://api.coingecko.com/api/v3/global", headers=hdr)
    g = j["data"] if j and j.get("data") else {}
    d["mcap"] = g.get("total_market_cap", {}).get("usd")
    d["mcap_chg"] = g.get("market_cap_change_percentage_24h_usd")
    d["btc_dom"] = g.get("market_cap_percentage", {}).get("btc")
    d["eth_dom"] = g.get("market_cap_percentage", {}).get("eth")

    # fear & greed
    j = get("https://api.alternative.me/fng/")
    d["fng"] = int(j["data"][0]["value"]) if j and j.get("data") else None

    d["event"] = os.environ.get("PASAR_EVENT", "").strip()
    return d


def mock_data():
    return dict(price=74404, chg24=0.21, rsi=38, ma20=77800, ma50=79500, res=79296, sup=74451,
                low30=73665, funding=0.0073, oi=20.4e9, oi7=-7.4, ls=0.92,
                liq_long=14.01e6, liq_short=20.97e6, mcap=2.49e12, mcap_chg=1.09,
                btc_dom=60.3, eth_dom=9.8, fng=28, event="CPI AS malam ini · 20.30 WIB")


# --------------------------------------------------------------------------
# Analisis singkat
# --------------------------------------------------------------------------

def regime(d):
    below = (d["price"] < d["ma20"]) + (d["price"] < d["ma50"])
    if below == 2 and (d["rsi"] or 50) < 45:
        return "DEFENSIF", "amber"
    if below == 0 and (d["rsi"] or 50) > 55:
        return "BULLISH", "green"
    return "NETRAL", "blue"


def note_text(d, reg):
    if d.get("event"):
        return d["event"], "Hindari entry baru sebelum rilis", "amber"
    if reg == "DEFENSIF":
        return "Tren turun — tunggu konfirmasi", "Close harian di atas resistance dulu sebelum entry", "amber"
    if reg == "BULLISH":
        return "Tren naik — ikuti pullback", "Waspadai harga yang terlalu jauh dari MA20", "green"
    return "Pasar sideways", "Tunggu breakout dari level kunci", "blue"


# --------------------------------------------------------------------------
# Gambar
# --------------------------------------------------------------------------

S = 2
W, H = 1080, 1380
BG = (13, 17, 27)
CARD = (24, 30, 44)
BORDER = (40, 48, 66)
TXT = (236, 240, 247)
MUTED = (139, 149, 170)
GREEN = (74, 200, 150)
RED = (240, 110, 110)
AMBER = (232, 184, 94)
BLUE = (110, 150, 240)

FONT_DIRS = ["/usr/share/fonts/opentype/inter/", "/usr/share/fonts/truetype/inter/",
             "/usr/share/fonts/truetype/dejavu/"]
FALLBACK = {"Inter-Italic.otf": "DejaVuSans-Oblique.ttf", "Inter-Bold.otf": "DejaVuSans-Bold.ttf",
            "Inter-SemiBold.otf": "DejaVuSans-Bold.ttf", "Inter-Medium.otf": "DejaVuSans.ttf",
            "Inter-Regular.otf": "DejaVuSans.ttf"}


def font(name, size):
    for base in FONT_DIRS:
        for n in (name, FALLBACK.get(name, "")):
            p = base + n
            if n and os.path.exists(p):
                return ImageFont.truetype(p, size * S)
    return ImageFont.load_default()


PILL = {"amber": ((52, 42, 24), (98, 78, 38), AMBER),
        "green": ((20, 52, 42), (34, 98, 78), GREEN),
        "blue": ((26, 36, 62), (50, 70, 120), BLUE)}


def render(d):
    glow = Image.new("RGB", (W * S, H * S), BG)
    ImageDraw.Draw(glow).ellipse([-200 * S, -420 * S, 900 * S, 300 * S], fill=(22, 32, 58))
    img = glow.filter(ImageFilter.GaussianBlur(140 * S))
    dr = ImageDraw.Draw(img)

    def rr(x, y, w, h, r=28, fill=CARD, outline=BORDER):
        dr.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], r * S, fill=fill, outline=outline, width=S)

    def t(x, y, s, f, fill=TXT, anchor="la"):
        dr.text((x * S, y * S), s, font=f, fill=fill, anchor=anchor)

    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    bulan = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"][now.month - 1]
    reg, reg_col = regime(d)

    # watermark + header
    t(48, 38, "achevtaaa", font("Inter-Italic.otf", 20), fill=(150, 160, 182))
    t(48, 92, "BRIEFING PASAR", font("Inter-SemiBold.otf", 22), fill=MUTED)
    t(W - 48, 92, f"{now.day} {bulan} {now.year} · {now:%H.%M} WIB", font("Inter-Medium.otf", 22),
      fill=MUTED, anchor="ra")

    # hero
    rr(40, 140, W - 80, 250)
    t(80, 178, "Bitcoin", font("Inter-Medium.otf", 26), fill=MUTED)
    t(80, 218, f"${d['price']:,.0f}", font("Inter-Bold.otf", 92))
    t(80, 330, f"{pct(d['chg24'])} 24 jam", font("Inter-SemiBold.otf", 26),
      fill=GREEN if d["chg24"] >= 0 else RED)
    bgc, bdc, fgc = PILL[reg_col]
    px, py, pw, ph = W - 80 - 250, 186, 250, 64
    dr.rounded_rectangle([px * S, py * S, (px + pw) * S, (py + ph) * S], 32 * S, fill=bgc, outline=bdc, width=S)
    t(px + pw / 2, py + ph / 2, f"● {reg}", font("Inter-SemiBold.otf", 24), fill=fgc, anchor="mm")
    rsi_txt = f"RSI {d['rsi']:.0f}" if d["rsi"] else "RSI —"
    ma_txt = ("bawah MA20 & MA50" if d["price"] < min(d["ma20"], d["ma50"])
              else "atas MA20 & MA50" if d["price"] > max(d["ma20"], d["ma50"]) else "di antara MA20 & MA50")
    t(W - 80, 330, f"{rsi_txt} · {ma_txt}", font("Inter-Medium.otf", 22), fill=MUTED, anchor="ra")

    # tiga kartu kecil
    cw = (W - 80 - 40) // 3

    def stat(i, label, value, sub, col=TXT):
        x = 40 + i * (cw + 20)
        rr(x, 414, cw, 170, 24)
        t(x + 28, 440, label, font("Inter-Medium.otf", 20), fill=MUTED)
        t(x + 28, 480, value, font("Inter-Bold.otf", 46), fill=col)
        t(x + 28, 546, sub, font("Inter-Medium.otf", 20), fill=MUTED)

    fng = d["fng"]
    stat(0, "Fear & Greed", str(fng) if fng is not None else "—", fng_label(fng),
         AMBER if fng is not None and 25 <= fng < 55 else RED if fng is not None and fng < 25 else GREEN)
    stat(1, "Dominasi BTC", pct(d["btc_dom"], 1, sign=False) if d["btc_dom"] else "—",
         f"ETH {pct(d['eth_dom'], 1, sign=False)}" if d["eth_dom"] else "")
    stat(2, "Total Market Cap", usd_big(d["mcap"]), f"{pct(d['mcap_chg'])} 24 jam" if d["mcap_chg"] is not None else "")

    # derivatif
    rr(40, 608, W - 80, 330)
    t(80, 640, "Derivatif", font("Inter-SemiBold.otf", 24), fill=MUTED)
    f = d["funding"]
    ls = d["ls"]
    cols = [
        ("Open Interest", usd_big(d["oi"], 1) if d["oi"] else "—",
         f"{pct(d['oi7'], 1)} / 7 hari" if d["oi7"] is not None else "", RED if (d["oi7"] or 0) < 0 else GREEN),
        ("Funding", pct(f, 4) if f is not None else "—",
         "Netral, tidak ramai" if f is not None and abs(f) < 0.02 else
         "Long ramai" if f is not None and f > 0 else "Short ramai" if f is not None else "", TXT),
        ("Rasio Long/Short", f"{ls:.2f}".replace(".", ",") if ls else "—",
         "Lebih banyak long" if ls and ls > 1 else "Lebih banyak short" if ls else "", TXT),
    ]
    colw = (W - 160) // 3
    for i, (a, b, c, col) in enumerate(cols):
        x = 80 + i * colw
        t(x, 690, a, font("Inter-Medium.otf", 20), fill=MUTED)
        t(x, 724, b, font("Inter-Bold.otf", 40), fill=col)
        t(x, 782, c, font("Inter-Medium.otf", 20), fill=MUTED)

    ll, ls_ = d["liq_long"], d["liq_short"]
    t(80, 840, "Likuidasi OKX 24 jam", font("Inter-Medium.otf", 20), fill=MUTED)
    if ll is not None and (ll + ls_) > 0:
        t(W - 80, 840, usd_big(ll + ls_, 1), font("Inter-SemiBold.otf", 22), anchor="ra")
        bx, by, bw, bh = 80, 880, W - 160, 16
        lw = int(bw * ll / (ll + ls_))
        dr.rounded_rectangle([bx * S, by * S, (bx + bw) * S, (by + bh) * S], 8 * S, fill=BORDER)
        if lw > 6:
            dr.rounded_rectangle([bx * S, by * S, (bx + lw - 3) * S, (by + bh) * S], 8 * S, fill=RED)
        dr.rounded_rectangle([(bx + lw) * S, by * S, (bx + bw) * S, (by + bh) * S], 8 * S, fill=GREEN)
        t(bx, 906, f"Long {usd_big(ll, 1)}", font("Inter-Medium.otf", 19), fill=RED)
        t(bx + bw, 906, f"Short {usd_big(ls_, 1)}", font("Inter-Medium.otf", 19), fill=GREEN, anchor="ra")
    else:
        t(W - 80, 840, "—", font("Inter-SemiBold.otf", 22), anchor="ra")

    # level kunci
    rr(40, 962, W - 80, 230)
    t(80, 994, "Level Kunci BTC", font("Inter-SemiBold.otf", 24), fill=MUTED)
    sup_label = "Support"
    sup_val = d["sup"]
    if abs(d["low30"] - d["sup"]) / d["sup"] < 0.002:
        sup_label = "Support (low 30 hari)"
    rows = [("Resistance", f"${d['res']:,.0f}", RED),
            ("Harga kini", f"${d['price']:,.0f}", TXT),
            (sup_label, f"${sup_val:,.0f}", GREEN)]
    for i, (a, b, col) in enumerate(rows):
        y = 1040 + i * 48
        dr.ellipse([80 * S, (y + 8) * S, 96 * S, (y + 24) * S], fill=col)
        t(116, y, a, font("Inter-Medium.otf", 24), fill=MUTED)
        t(W - 80, y, b, font("Inter-SemiBold.otf", 26), fill=col, anchor="ra")

    # catatan
    title, sub, ncol = note_text(d, reg)
    nbg = {"amber": ((40, 34, 24), (88, 72, 40), AMBER), "green": ((20, 40, 34), (34, 80, 64), GREEN),
           "blue": ((22, 30, 52), (44, 62, 108), BLUE)}[ncol]
    rr(40, 1216, W - 80, 104, 24, fill=nbg[0], outline=nbg[1])
    t(80, 1244, title, font("Inter-SemiBold.otf", 28), fill=nbg[2])
    t(80, 1282, sub, font("Inter-Medium.otf", 22), fill=MUTED)

    t(W / 2, 1350, "Data: OKX · CoinGecko · alternative.me  |  Bukan nasihat finansial",
      font("Inter-Regular.otf", 17), fill=(96, 106, 128), anchor="mm")

    return img.resize((W, H), Image.LANCZOS), reg


def caption(d, reg):
    chg = pct(d["chg24"])
    return (f"Pasar {reg.lower()}: BTC ${d['price']:,.0f} ({chg} 24 jam). "
            f"Resistance ${d['res']:,.0f}, support ${d['sup']:,.0f}.")


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def send(img, cap):
    from common import send_photo
    send_photo(img, cap, ("pasar",))


def main():
    args = set(sys.argv[1:])
    d = mock_data() if "--mock" in args else fetch_data()
    img, reg = render(d)
    cap = caption(d, reg)
    if "--mock" in args or "--dry" in args:
        img.save("kartu.png")
        print("Disimpan: kartu.png\n" + cap)
        return
    send(img, cap)


if __name__ == "__main__":
    main()
