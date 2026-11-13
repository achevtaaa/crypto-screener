#!/usr/bin/env python3
"""
Screener v5 -> Telegram
  1. Kondisi pasar: TOTAL3, BTC.D, USDT.D (dari CoinGecko, perkiraan) + kesimpulan risk-on / risk-off
  2. Top 100 koin (market cap) dianalisa di 2 timeframe, 1D (arah) dan 1H (timing):
       Stochastic 5,3,3 + EMA 13/21, plus OI 24 jam, funding rate, CVD spot (proxy)
  3. Koin yang punya setup oversold diberi SKOR 1-10 lalu dikirim ke Telegram (WIB)

Sumber data candle: OKX -> Bitget -> Gate (yang pertama menjawab dari server GitHub).
CVD spot adalah PROXY (posisi close di dalam range candle x volume), bukan CVD asli.
TOTAL3, BTC.D, USDT.D dihitung dari CoinGecko dan angkanya bisa sedikit beda dari TradingView.

Pakai:
  pip install requests
  python screener.py --dry-run     # tampil di layar saja
  python screener.py               # kirim ke Telegram
"""
import csv
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

try:
    import bantu
except ImportError:
    bantu = None

TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
CG_KEY = os.getenv("COINGECKO_API_KEY")
WIB = timezone(timedelta(hours=7))

# ---------- Parameter ----------
TOP_COINS = 150
SHOW = 8                     # jumlah koin yang dikirim
CANDLES = 100
STOCH_K, STOCH_SMOOTH, STOCH_D = 5, 3, 3
EMA_FAST, EMA_SLOW = 13, 21
OVERSOLD = 20
TRIGGER_FROM = 25
# >>> UBAH DI SINI: timeframe pendek untuk timing. Pilihan: "1h" atau "4h" (1D tetap untuk arah) <<<
TF_LOW = "4h"
TF_LABEL = TF_LOW.upper()
CVD_N = {"1h": 12, "4h": 6, "1d": 5}   # jumlah candle untuk hitung CVD proxy (kira-kira 12 jam / 24 jam / 5 hari)
CVD_POSITIVE = 0.05
LOG_FILE = "screener_log.csv"
STATS_FILE = "backtest_stats.json"   # dibuat oleh backtest.py
LIQ_SKIP = 500_000        # volume 24j (USD) di bawah ini: koin dilewati (terlalu tipis, mudah digerakkan bandar)
LIQ_WARN = 3_000_000      # di bawah ini: diberi peringatan dan skor dikurangi

STABLE = {"USDC", "FDUSD", "TUSD", "DAI", "USDE", "USD1", "PYUSD", "USDD", "USDP",
          "EURC", "EUR", "AEUR", "XUSD", "USDG", "RLUSD", "BUIDL", "USDS", "FRAX",
          "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "BTCB", "USDT"}


def excluded(base):
    return (base in STABLE or base.startswith("USD") or
            re.search(r"\d[LS]$", base) is not None)


# ================================================================ HTTP
def http_get(url, params=None):
    last = ""
    for _ in range(2):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json(), ""
            last = f"HTTP {r.status_code}"
            if r.status_code in (403, 451):
                break
        except Exception as e:
            last = type(e).__name__
        time.sleep(1.5)
    return None, last


# ================================================================ OKX
def okx_tickers():
    j, err = http_get("https://www.okx.com/api/v5/market/tickers", {"instType": "SPOT"})
    if j is None or j.get("code") != "0":
        return None, err or "respon tidak valid"
    out = {}
    for t in j["data"]:
        inst = t["instId"]
        if not inst.endswith("-USDT"):
            continue
        last = float(t["last"] or 0)
        open24 = float(t["open24h"] or 0)
        out[inst[:-5]] = {"id": inst, "last": last,
                          "chg": (last / open24 - 1) * 100 if open24 else 0.0,
                          "vol": float(t["volCcy24h"] or 0)}
    return out, ""


def okx_candles(inst, tf):
    bar = {"1h": "1H", "4h": "4H", "1d": "1D"}[tf]
    j, _ = http_get("https://www.okx.com/api/v5/market/candles",
                    {"instId": inst, "bar": bar, "limit": str(CANDLES)})
    if j is None or j.get("code") != "0":
        return None
    rows = sorted([r for r in j["data"] if r[8] == "1"], key=lambda r: int(r[0]))
    return [(float(r[2]), float(r[3]), float(r[4]), float(r[7])) for r in rows]


def okx_deriv(base):
    out = {"oi_chg": None, "funding": None}
    try:
        j, _ = http_get("https://www.okx.com/api/v5/public/funding-rate",
                        {"instId": f"{base}-USDT-SWAP"})
        if j and j.get("code") == "0" and j["data"]:
            out["funding"] = float(j["data"][0]["fundingRate"]) * 100
        time.sleep(0.4)
        j, _ = http_get("https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume",
                        {"ccy": base, "period": "1H"})
        if j and j.get("code") == "0" and len(j["data"]) >= 2:
            rows = sorted(j["data"], key=lambda r: int(r[0]))
            newest = float(rows[-1][1])
            old = float(rows[max(0, len(rows) - 1 - 24)][1])
            if old:
                out["oi_chg"] = (newest / old - 1) * 100
    except Exception:
        pass
    return out


# ================================================================ Bitget
def bitget_tickers():
    j, err = http_get("https://api.bitget.com/api/v2/spot/market/tickers")
    if j is None or j.get("code") != "00000":
        return None, err or "respon tidak valid"
    out = {}
    for t in j["data"]:
        sym = t["symbol"]
        if not sym.endswith("USDT"):
            continue
        out[sym[:-4]] = {"id": sym, "last": float(t["lastPr"] or 0),
                         "chg": float(t.get("change24h") or 0) * 100,
                         "vol": float(t.get("usdtVolume") or t.get("quoteVolume") or 0)}
    return out, ""


def bitget_candles(sym, tf):
    gran = {"1h": "1h", "4h": "4h", "1d": "1day"}[tf]
    j, _ = http_get("https://api.bitget.com/api/v2/spot/market/candles",
                    {"symbol": sym, "granularity": gran, "limit": str(CANDLES)})
    if j is None or j.get("code") != "00000":
        return None
    rows = sorted(j["data"], key=lambda r: int(r[0]))[:-1]   # buang candle berjalan
    return [(float(r[2]), float(r[3]), float(r[4]), float(r[6])) for r in rows]


def bitget_deriv(base):
    out = {"oi_chg": None, "funding": None}      # Bitget tidak punya riwayat OI
    try:
        j, _ = http_get("https://api.bitget.com/api/v2/mix/market/current-fund-rate",
                        {"symbol": base + "USDT", "productType": "USDT-FUTURES"})
        if j and j.get("code") == "00000" and j["data"]:
            out["funding"] = float(j["data"][0]["fundingRate"]) * 100
    except Exception:
        pass
    return out


# ================================================================ Gate
def gate_tickers():
    j, err = http_get("https://api.gateio.ws/api/v4/spot/tickers")
    if not isinstance(j, list):
        return None, err or "respon tidak valid"
    out = {}
    for t in j:
        pair = t["currency_pair"]
        if not pair.endswith("_USDT"):
            continue
        out[pair[:-5]] = {"id": pair, "last": float(t["last"] or 0),
                          "chg": float(t.get("change_percentage") or 0),
                          "vol": float(t.get("quote_volume") or 0)}
    return out, ""


def gate_candles(pair, tf):
    j, _ = http_get("https://api.gateio.ws/api/v4/spot/candlesticks",
                    {"currency_pair": pair, "interval": tf, "limit": CANDLES})
    if not isinstance(j, list) or not j:
        return None
    rows = sorted(j, key=lambda r: int(r[0]))
    if len(rows[-1]) >= 8:
        rows = [r for r in rows if str(r[7]).lower() == "true"]
    else:
        rows = rows[:-1]
    return [(float(r[3]), float(r[4]), float(r[2]), float(r[1])) for r in rows]


def gate_deriv(base):
    out = {"oi_chg": None, "funding": None}
    contract = base + "_USDT"
    try:
        j, _ = http_get("https://api.gateio.ws/api/v4/futures/usdt/contract_stats",
                        {"contract": contract, "interval": "1h", "limit": 25})
        if isinstance(j, list) and len(j) >= 2:
            rows = sorted(j, key=lambda r: int(r["time"]))
            key = "open_interest_usd" if rows[-1].get("open_interest_usd") else "open_interest"
            newest, old = float(rows[-1][key]), float(rows[0][key])
            if old:
                out["oi_chg"] = (newest / old - 1) * 100
        j, _ = http_get("https://api.gateio.ws/api/v4/futures/usdt/contracts/" + contract)
        if isinstance(j, dict) and j.get("funding_rate") not in (None, ""):
            out["funding"] = float(j["funding_rate"]) * 100
    except Exception:
        pass
    return out


PROVIDERS = [("OKX", okx_tickers, okx_candles, okx_deriv),
             ("Bitget", bitget_tickers, bitget_candles, bitget_deriv),
             ("Gate", gate_tickers, gate_candles, gate_deriv)]


def pick_provider():
    errors = []
    for name, tick, cand, deriv in PROVIDERS:
        data, err = tick()
        if data:
            return name, data, cand, deriv
        errors.append(f"{name}: {err}")
    raise RuntimeError("Semua sumber data menolak koneksi -> " + "; ".join(errors))


# ================================================================ CoinGecko + kondisi pasar
def cg_get(path, params=None):
    headers = {"accept": "application/json"}
    if CG_KEY:
        headers["x-cg-demo-api-key"] = CG_KEY
    for _ in range(4):
        try:
            r = requests.get("https://api.coingecko.com/api/v3" + path, params=params,
                             headers=headers, timeout=20)
            if r.status_code == 429:
                time.sleep(25)
                continue
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            time.sleep(3)
    return None


def load_markets():
    coins = cg_get("/coins/markets", {"vs_currency": "usd", "order": "market_cap_desc",
                                      "per_page": TOP_COINS, "page": 1, "sparkline": "false",
                                      "price_change_percentage": "24h,7d"})
    time.sleep(2)
    glob = cg_get("/global")
    return coins, (glob or {}).get("data")


def macro_analysis(coins, g):
    """TOTAL3, BTC.D, USDT.D beserta perubahannya (perkiraan dari top 100 CoinGecko)."""
    if not coins or not g:
        return None
    total = g["total_market_cap"]["usd"]
    dom = g["market_cap_percentage"]
    btc_d, usdt_d = dom.get("btc", 0.0), dom.get("usdt", 0.0)
    btc_cap, eth_cap = total * btc_d / 100, total * dom.get("eth", 0.0) / 100
    usdt_cap = total * usdt_d / 100
    m = {"total": total, "btc_d": btc_d, "usdt_d": usdt_d, "total3": total - btc_cap - eth_cap}
    for label, key in (("24h", "price_change_percentage_24h_in_currency"),
                       ("7d", "price_change_percentage_7d_in_currency")):
        shift = alt_now = alt_prev = 0.0
        btc_chg = None
        for c in coins:
            chg, mcap = c.get(key), c.get("market_cap") or 0
            if chg is None or mcap <= 0:
                continue
            prev = mcap / (1 + chg / 100)
            shift += mcap - prev
            sym = (c.get("symbol") or "").lower()
            if sym == "btc":
                btc_chg = chg
            if sym not in ("btc", "eth"):
                alt_now += mcap       # TOTAL3 termasuk stablecoin, sama seperti di TradingView
                alt_prev += prev
        total_prev = total - shift
        m[f"total3_{label}"] = (alt_now / alt_prev - 1) * 100 if alt_prev else None
        m[f"btc_{label}"] = btc_chg
        if total_prev > 0:
            if btc_chg is not None:
                m[f"btcd_{label}"] = btc_d - (btc_cap / (1 + btc_chg / 100)) / total_prev * 100
            m[f"usdtd_{label}"] = usdt_d - usdt_cap / total_prev * 100   # supply USDT dianggap tetap
    return m


def _f(x, fmt="{:+.1f}"):
    return "n/a" if x is None else fmt.format(x)


def macro_text(m):
    if not m:
        return "KONDISI PASAR\n  Data CoinGecko tidak tersedia kali ini (batas request atau koneksi)."
    t3, bd, ud, bp = m.get("total3_24h"), m.get("btcd_24h"), m.get("usdtd_24h"), m.get("btc_24h")

    if ud is not None and t3 is not None and ud > 0.05 and t3 < -1:
        regime = "RISK-OFF: uang lari ke stablecoin dan alt turun. Sinyal bounce kurang bisa diandalkan."
    elif ud is not None and t3 is not None and ud < -0.05 and t3 > 1:
        regime = "RISK-ON: uang keluar dari stablecoin dan alt menguat. Pasar mendukung posisi long."
    else:
        regime = "CAMPUR / NETRAL: belum ada arah yang tegas, utamakan konfirmasi dan ukuran posisi kecil."

    if bd is None or bp is None:
        btc_read = "n/a"
    elif bd > 0.15 and bp >= 0:
        btc_read = "BTC memimpin, alt tertinggal"
    elif bd > 0.15 and bp < 0:
        btc_read = "BTC turun dan alt jatuh lebih dalam"
    elif bd < -0.15 and bp >= 0:
        btc_read = "ada rotasi ke alt"
    elif bd < -0.15 and bp < 0:
        btc_read = "alt turun lebih ringan dari BTC (cek USDT.D)"
    else:
        btc_read = "dominasi BTC relatif datar"

    return (
        "KONDISI PASAR (perkiraan dari top 100 CoinGecko)\n"
        f"  Total market cap: ${m['total'] / 1e12:.2f}T | BTC 24j {_f(bp)}% | 7h {_f(m.get('btc_7d'))}%\n"
        f"  TOTAL3: ${m['total3'] / 1e12:.2f}T | 24j {_f(t3)}% | 7h {_f(m.get('total3_7d'))}%\n"
        f"  BTC.D: {m['btc_d']:.1f}% | 24j {_f(bd, '{:+.2f}')} poin | 7h {_f(m.get('btcd_7d'), '{:+.2f}')} poin -> {btc_read}\n"
        f"  USDT.D: {m['usdt_d']:.1f}% | 24j {_f(ud, '{:+.2f}')} poin | 7h {_f(m.get('usdtd_7d'), '{:+.2f}')} poin\n"
        f"  Kesimpulan: {regime}")


# ================================================================ Indikator
def sma(values, n):
    out = [None] * len(values)
    for i in range(n - 1, len(values)):
        w = values[i - n + 1:i + 1]
        if any(v is None for v in w):
            continue
        out[i] = sum(w) / n
    return out


def ema(values, n):
    out = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def stochastic(highs, lows, closes, k_len, smooth, d_len):
    raw = [None] * len(closes)
    for i in range(k_len - 1, len(closes)):
        hh = max(highs[i - k_len + 1:i + 1])
        ll = min(lows[i - k_len + 1:i + 1])
        raw[i] = 50.0 if hh == ll else 100.0 * (closes[i] - ll) / (hh - ll)
    k = sma(raw, smooth)
    return k, sma(k, d_len)


def cvd_proxy(candles, n):
    """Proxy tekanan beli spot -1..1. Mengembalikan (n candle terakhir, n sebelumnya)."""
    def score(rows):
        num = den = 0.0
        for h, l, c, v in rows:
            if h == l or v <= 0:
                continue
            num += (((c - l) - (h - c)) / (h - l)) * v
            den += v
        return num / den if den else 0.0
    return score(candles[-n:]), score(candles[-2 * n:-n])


def analyze_tf(candles, tf):
    if not candles or len(candles) < 40:
        return None
    highs, lows = [c[0] for c in candles], [c[1] for c in candles]
    closes = [c[2] for c in candles]
    k, d = stochastic(highs, lows, closes, STOCH_K, STOCH_SMOOTH, STOCH_D)
    e_fast, e_slow = ema(closes, EMA_FAST), ema(closes, EMA_SLOW)
    i = len(closes) - 1
    if None in (k[i], d[i], e_fast[i], e_slow[i]):
        return None
    trigger_ago = None
    for ago in (0, 1):
        j = i - ago
        if None in (k[j - 1], d[j - 1], k[j], d[j]):
            continue
        if k[j - 1] <= d[j - 1] and k[j] > d[j] and k[j - 1] < TRIGGER_FROM:
            trigger_ago = ago
            break
    cvd_now, cvd_prev = cvd_proxy(candles, CVD_N[tf])
    return {"k": k[i], "d": d[i], "close": closes[i], "ema_fast": e_fast[i], "ema_slow": e_slow[i],
            "trigger_ago": trigger_ago, "cvd": cvd_now, "cvd_prev": cvd_prev,
            "counter": e_fast[i] < e_slow[i] and closes[i] < e_slow[i]}


# ================================================================ Penyaring risiko "koin bandar"
def risk_flags(candles, liq_usd):
    """Tanda bahaya sederhana (heuristik): likuiditas tipis, pump mendadak, penolakan harga berulang."""
    flags = []
    if liq_usd and liq_usd < LIQ_WARN:
        flags.append(f"likuiditas tipis (volume 24j ${liq_usd / 1e6:.1f} juta)")
    if len(candles) >= 40:
        vols = sorted(c[3] for c in candles[-33:-3])
        med = vols[len(vols) // 2] if vols else 0
        c_now, c_ref = candles[-1][2], candles[-4][2]
        if med > 0 and max(c[3] for c in candles[-3:]) > 5 * med and c_ref > 0 and c_now / c_ref - 1 > 0.08:
            flags.append("lonjakan volume + harga naik cepat (rawan pump and dump)")
        ranges = [c[0] - c[1] for c in candles[-23:-3]]
        avg_rng = sum(ranges) / len(ranges) if ranges else 0
        rej = 0
        for h, l, c, v in candles[-3:]:
            if avg_rng > 0 and h > l and (h - l) > 1.5 * avg_rng and (c - l) / (h - l) < 0.35:
                rej += 1
        if rej >= 2:
            flags.append("sumbu atas panjang berulang (tanda distribusi / jual ke pembeli)")
    return flags


# ================================================================ Skor 1-10
def evaluate(d1, h1, der, risk=None):
    """1D (maks 4) + 1H (maks 3) + aliran dana (maks 3) = 10."""
    pts = 0
    pts += d1["k"] < 30                       # 1D masih di area bawah
    pts += d1["k"] > d1["d"]                  # 1D mulai berbalik naik
    pts += d1["close"] > d1["ema_fast"]
    pts += d1["ema_fast"] > d1["ema_slow"]
    if h1["trigger_ago"] is not None:
        pts += 2                              # 1H: cross naik dari oversold
    elif h1["k"] < OVERSOLD:
        pts += 1                              # 1H: oversold, belum cross
    pts += h1["close"] > h1["ema_fast"]
    pts += h1["cvd"] >= CVD_POSITIVE          # tekanan beli spot
    if der["funding"] is not None and der["funding"] < 0:
        pts += 1                              # funding negatif: short ramai
    if der["oi_chg"] is not None and der["oi_chg"] >= 5:
        pts += 1                              # OI naik
    if risk:
        pts -= 2                              # ada tanda bahaya bandar: skor dikurangi
    return max(1, min(10, int(pts)))


def rating(s):
    return "KUAT" if s >= 8 else "MENARIK" if s >= 6 else "LEMAH" if s >= 4 else "SANGAT LEMAH"


# ================================================================ Alur utama
def build_universe(cg_coins, tickers):
    out, seen = [], set()
    if cg_coins:
        for rank, c in enumerate(cg_coins, 1):
            base = (c.get("symbol") or "").upper()
            if not base or base in seen:
                continue
            seen.add(base)
            price = c.get("current_price") or 0
            chg = c.get("price_change_percentage_24h_in_currency") or 0
            if excluded(base) or (abs(price - 1) <= 0.02 and abs(chg) < 0.5):
                continue
            if base in tickers:
                out.append({"base": base, "t": tickers[base], "rank": rank})
        return out, "market cap"
    ranked = sorted(((b, t) for b, t in tickers.items() if not excluded(b)),
                    key=lambda x: x[1]["vol"], reverse=True)[:TOP_COINS]
    return [{"base": b, "t": t, "rank": None} for b, t in ranked], "volume (CoinGecko gagal)"


def scan(cg_coins):
    name, tickers, get_candles, get_deriv = pick_provider()
    universe, basis = build_universe(cg_coins, tickers)
    results, failed = [], 0
    for u in universe:
        try:
            c1d = get_candles(u["t"]["id"], "1d")
            time.sleep(0.15)
            c1h = get_candles(u["t"]["id"], TF_LOW)
            time.sleep(0.15)
            d1, h1 = analyze_tf(c1d, "1d"), analyze_tf(c1h, TF_LOW)
        except Exception:
            d1 = h1 = None
        if d1 is None or h1 is None:
            failed += 1
            continue
        if u["t"].get("vol", 0) and u["t"]["vol"] < LIQ_SKIP:
            continue                                  # terlalu tipis
        long_ok = h1["trigger_ago"] is not None or h1["k"] < OVERSOLD or d1["k"] < OVERSOLD
        if not long_ok:
            continue                                  # tidak ada setup
        der = get_deriv(u["base"])
        risk = risk_flags(c1h, u["t"].get("vol", 0))
        base_row = {"symbol": u["base"] + "USDT", "rank": u["rank"], "price": h1["close"],
                    "chg24h": u["t"]["chg"], "d1": d1, "h1": h1, "oi_chg": der["oi_chg"],
                    "funding": der["funding"], "risk": risk}
        results.append(dict(base_row, side="BUY", score=evaluate(d1, h1, der, risk)))
    if universe and failed == len(universe):
        raise RuntimeError(f"{name} menjawab daftar koin tapi menolak data candle semua koin.")
    results.sort(key=lambda r: (-r["score"], r["rank"] or 999))
    return {"provider": name, "basis": basis, "checked": len(universe), "failed": failed,
            "results": results}


def now_wib():
    return datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")


def esc(x):
    return html.escape(str(x), quote=False)


def coin_text(r):
    """Format HTML Telegram: nama koin tebal + garis bawah, hal penting miring."""
    d1, h1 = r["d1"], r["h1"]
    t1 = "EMA13>EMA21" if d1["ema_fast"] > d1["ema_slow"] else "EMA13<EMA21"
    p1 = "di atas" if d1["close"] > d1["ema_fast"] else "di bawah"
    warn1 = "  [LAWAN TREN 1D]" if d1["counter"] else ""
    fresh = h1["trigger_ago"] == 0
    if h1["trigger_ago"] is not None:
        sig = "TRIGGER cross naik" + (" (candle terakhir)" if fresh else " (1 candle lalu)")
    elif h1["k"] < OVERSOLD:
        sig = "oversold, belum cross"
    else:
        sig = "tidak oversold"
    t2 = "EMA13>EMA21" if h1["ema_fast"] > h1["ema_slow"] else "EMA13<EMA21"
    p2 = "di atas" if h1["close"] > h1["ema_fast"] else "di bawah"
    dir1 = "naik" if h1["cvd"] > h1["cvd_prev"] else "turun"
    rk = f" | rank #{r['rank']}" if r["rank"] else ""

    score_txt = esc(f"SKOR {r['score']}/10 ({rating(r['score'])})")
    if r["score"] >= 8:
        score_txt = f"<i>{score_txt}</i>"                      # penting: skor kuat
    line_tf = esc(f"  {TF_LABEL}: {sig} | Stoch {h1['k']:.0f}/{h1['d']:.0f} | {t2} | harga {p2} EMA13")
    if fresh and not d1["counter"]:
        line_tf = f"<i>{line_tf}</i>"                          # penting: trigger baru dan searah tren 1D
    tag = bantu.narasi_label(r["symbol"][:-4] if r["symbol"].endswith("USDT") else r["symbol"]) if bantu else ""
    lines = [f"<b><u>{esc(r['symbol'])}</u></b>  {score_txt}{esc(rk)}",
             esc(f"  Harga {r['price']:g} | 24j {r['chg24h']:+.1f}%"),
             *([f"  🏷️ Narasi: {esc(tag)}"] if tag else []),
             esc(f"  1D: Stoch {d1['k']:.0f}/{d1['d']:.0f} | {t1} | harga {p1} EMA13{warn1}"),
             line_tf,
             esc(f"  OI 24j {_f(r['oi_chg'])}% | Funding {_f(r['funding'], '{:+.4f}')}%"),
             esc(f"  CVD spot (proxy): {TF_LABEL} {h1['cvd']:+.2f} ({dir1}) | 1D {d1['cvd']:+.2f}")]
    if r["oi_chg"] is not None and r["funding"] is not None:
        if r["oi_chg"] >= 5 and r["funding"] < 0:
            lines.append("<i>  PENTING: OI naik + funding negatif = short ramai (potensi squeeze)</i>")
        elif r["oi_chg"] <= -5:
            lines.append(esc("  Catatan: OI turun = posisi ditutup / likuidasi"))
    if r.get("risk"):
        lines.append("<i>  ⚠️ RISIKO BANDAR: " + esc("; ".join(r["risk"])) + "</i>")
    return "\n".join(lines)


def stats_line():
    """Ringkasan hasil uji historis dari backtest.py (kalau sudah pernah dijalankan)."""
    try:
        with open(STATS_FILE) as f:
            st = json.load(f)
        hi, base = st["buckets"]["tinggi"], st["baseline"]
        txt = (f"Uji historis ({st['days']} hari, {st['coins']} koin, {st['updated']}): sinyal skor tinggi "
               f"n={hi['n']}, win {hi['win'] * 100:.0f}%, rata-rata {hi['exp']:+.2f}R; "
               f"entry acak {base['exp']:+.2f}R. {st['verdict']}")
        return f"<i>{esc(txt)}</i>"
    except Exception:
        return None


def format_messages(macro, scan_res):
    head = (f"<b>⚡ SCREENER | RINGKASAN ({esc(now_wib())})</b>\n"
            + esc(f"Top {TOP_COINS} koin | Stoch 5,3,3 + EMA 13/21 | TF 1D + {TF_LABEL} | sumber {scan_res['provider']}\n"
                  f"Diperiksa {scan_res['checked']} koin"
                  + (f" ({scan_res['failed']} gagal)" if scan_res["failed"] else "")) + "\n\n")
    macro_plain = macro_text(macro)
    title, _, body = macro_plain.partition("\n")
    msg1 = head + f"<b>{esc(title)}</b>\n" + esc(body)
    longs = scan_res["results"]
    legend = esc("Skor: 8-10 kuat | 6-7 menarik | 4-5 lemah | 1-3 sangat lemah | tanda miring = penting")
    foot = esc("Hasil filter otomatis, bukan sinyal beli. Skor adalah hitungan heuristik. Cek chart, "
               "tentukan entry, SL, dan ukuran posisi sendiri.")
    if longs:
        parts = ["<b>⚡ SCREENER | KOIN DENGAN SETUP OVERSOLD (skor 1-10)</b>\n" + legend]
        sl = stats_line()
        if sl:
            parts[0] += "\n" + sl
        parts += [coin_text(r) for r in longs[:SHOW]] + [foot]
        msg2 = "\n\n".join(parts)
    else:
        msg2 = "<b>⚡ SCREENER | KOIN OVERSOLD</b>\n" + esc(f"  Tidak ada koin top {TOP_COINS} yang oversold atau trigger kali ini.")
    return [msg1, msg2]


def log_results(scan_res):
    new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["waktu_utc", "sumber", "symbol", "rank", "harga", "skor", "k1d", "d1d", "k1h", "d1h",
                        "trigger1h", "cvd1h", "cvd1d", "oi24h", "funding", "chg24h"])
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        for r in scan_res["results"][:SHOW]:
            d1, h1 = r["d1"], r["h1"]
            w.writerow([ts, scan_res["provider"], r["symbol"], r["rank"], r["price"], r["score"],
                        round(d1["k"], 1), round(d1["d"], 1), round(h1["k"], 1), round(h1["d"], 1),
                        "" if h1["trigger_ago"] is None else h1["trigger_ago"],
                        round(h1["cvd"], 3), round(d1["cvd"], 3),
                        "" if r["oi_chg"] is None else round(r["oi_chg"], 2),
                        "" if r["funding"] is None else round(r["funding"], 4), round(r["chg24h"], 2)])


def split_for_telegram(text, limit=3800):
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > limit:
            chunks.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        chunks.append(cur)
    return chunks


def send_routed(chunk, topic):
    """Kirim ke topik grup kalau routes.json ada; kalau belum diatur atau gagal, kirim ke chat pribadi."""
    try:
        with open("routes.json") as f:
            routes = json.load(f)
    except Exception:
        routes = {}
    base = {"text": chunk[:4000], "parse_mode": "HTML", "disable_web_page_preview": "true"}
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    gid, thread = routes.get("group"), routes.get(topic)
    if gid and thread:
        r = requests.post(url, data=dict(base, chat_id=gid, message_thread_id=thread), timeout=15)
        if r.status_code == 200:
            time.sleep(0.5)
            return
        print("kirim ke topik gagal:", r.status_code, "-> kirim ke chat pribadi")
    r = requests.post(url, data=dict(base, chat_id=TG_CHAT), timeout=15)
    r.raise_for_status()
    time.sleep(0.5)


def send_telegram(text):
    for chunk in split_for_telegram(text):
        send_routed(chunk, "screener")


def main():
    dry = "--dry-run" in sys.argv
    messages = []
    try:
        cg_coins, glob = load_markets()
        macro = None
        try:
            macro = macro_analysis(cg_coins, glob)
        except Exception:
            pass
        scan_res = scan(cg_coins)
        messages = format_messages(macro, scan_res)
        log_results(scan_res)
    except Exception as e:
        messages = [f"<b>Screener error:</b> {esc(e)}"]
    for m in messages:
        if dry or not (TG_TOKEN and TG_CHAT):
            print(m + "\n" + "-" * 40)
        else:
            send_telegram(m)
    if not dry and not (TG_TOKEN and TG_CHAT):
        print("(TELEGRAM_TOKEN / TELEGRAM_CHAT_ID belum diisi, jadi tidak dikirim.)")


if __name__ == "__main__":
    main()
