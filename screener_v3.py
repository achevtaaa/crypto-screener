#!/usr/bin/env python3
"""
Screener v3: cari koin OVERSOLD pakai Stochastic (5,3,3) + EMA 13/21 -> Telegram.

Data: candle spot dari exchange publik (tanpa login, tanpa API key).
Script mencoba OKX dulu, lalu Bitget, lalu Gate. Yang pertama menjawab dipakai
(penting karena server GitHub di Amerika, dan sebagian exchange memblokirnya).

Indikator:
  - Stochastic 5,3,3  : %K mentah 5 candle, dihaluskan SMA 3 (%K), %D = SMA 3 dari %K
  - EMA 13 dan EMA 21
Hanya candle yang SUDAH CLOSE yang dipakai (tidak ada sinyal "repaint").

Sinyal:
  TRIGGER : %K memotong ke atas %D dalam 2 candle terakhir, dan %K sebelum
            cross masih di bawah 25 (keluar dari zona oversold)
  WATCH   : %K sekarang di bawah 20 (oversold) tapi belum cross
Tambahan skor : harga di atas EMA13, dan EMA13 di atas EMA21.
Peringatan "lawan tren" muncul kalau EMA13 < EMA21 dan harga < EMA21.

Pakai:
  pip install requests
  python screener_v3.py --dry-run     # tampil di layar saja
  python screener_v3.py               # kirim ke Telegram
Opsional: TIMEFRAME=1h / 4h / 1d (default 4h)
"""
import csv
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

# ---------- Parameter (ubah sesuai selera) ----------
TIMEFRAME = os.getenv("TIMEFRAME", "4h").lower()   # 1h, 4h, atau 1d
TOP_N = 80                  # periksa N koin dengan volume spot terbesar
MIN_VOLUME_USD = 3_000_000  # abaikan koin dengan volume 24 jam di bawah ini
CANDLES = 100
STOCH_K, STOCH_SMOOTH, STOCH_D = 5, 3, 3
EMA_FAST, EMA_SLOW = 13, 21
OVERSOLD = 20
TRIGGER_FROM = 25
SHOW = 6
LOG_FILE = "screener_log.csv"

STABLE = {"USDC", "FDUSD", "TUSD", "DAI", "USDE", "USD1", "PYUSD", "USDD", "USDP",
          "EURC", "EUR", "AEUR", "XUSD", "USDG", "RLUSD", "BUIDL", "USDS", "FRAX",
          "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "BTCB"}


def excluded(base):
    return (base in STABLE or base.startswith("USD") or
            re.search(r"\d[LS]$", base) is not None)   # token leverage 3L/3S/5L/5S


# ---------------------------------------------------------------- HTTP
def http_get(url, params=None):
    last = ""
    for _ in range(2):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json(), ""
            last = f"HTTP {r.status_code}"
            if r.status_code in (403, 451):   # diblokir, tidak perlu ulang
                break
        except Exception as e:
            last = type(e).__name__
        time.sleep(1.5)
    return None, last


# ---------------------------------------------------------------- OKX
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


def okx_candles(inst):
    bar = {"1h": "1H", "4h": "4H", "1d": "1D"}[TIMEFRAME]
    j, _ = http_get("https://www.okx.com/api/v5/market/candles",
                    {"instId": inst, "bar": bar, "limit": str(CANDLES)})
    if j is None or j.get("code") != "0":
        return None
    rows = [r for r in j["data"] if r[8] == "1"]    # hanya candle yang sudah close
    rows.sort(key=lambda r: int(r[0]))               # terlama -> terbaru
    return [(float(r[2]), float(r[3]), float(r[4])) for r in rows]   # high, low, close


# ---------------------------------------------------------------- Bitget
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


def bitget_candles(sym):
    gran = {"1h": "1h", "4h": "4h", "1d": "1day"}[TIMEFRAME]
    j, _ = http_get("https://api.bitget.com/api/v2/spot/market/candles",
                    {"symbol": sym, "granularity": gran, "limit": str(CANDLES)})
    if j is None or j.get("code") != "00000":
        return None
    rows = sorted(j["data"], key=lambda r: int(r[0]))
    rows = rows[:-1]                                  # buang candle yang masih berjalan
    return [(float(r[2]), float(r[3]), float(r[4])) for r in rows]


# ---------------------------------------------------------------- Gate
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


def gate_candles(pair):
    j, _ = http_get("https://api.gateio.ws/api/v4/spot/candlesticks",
                    {"currency_pair": pair, "interval": TIMEFRAME, "limit": CANDLES})
    if not isinstance(j, list) or not j:
        return None
    rows = sorted(j, key=lambda r: int(r[0]))
    if len(rows[-1]) >= 8:
        rows = [r for r in rows if str(r[7]).lower() == "true"]
    else:
        rows = rows[:-1]
    # urutan Gate: [ts, quote_vol, close, high, low, open, base_vol, closed]
    return [(float(r[3]), float(r[4]), float(r[2])) for r in rows]


PROVIDERS = [("OKX", okx_tickers, okx_candles),
             ("Bitget", bitget_tickers, bitget_candles),
             ("Gate", gate_tickers, gate_candles)]


def pick_provider():
    errors = []
    for name, tick, cand in PROVIDERS:
        data, err = tick()
        if data:
            return name, data, cand
        errors.append(f"{name}: {err}")
    raise RuntimeError("Semua sumber data menolak koneksi -> " + "; ".join(errors))


# ---------------------------------------------------------------- Indikator
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
    d = sma(k, d_len)
    return k, d


def analyze(candles):
    if not candles or len(candles) < 40:
        return None
    highs = [c[0] for c in candles]
    lows = [c[1] for c in candles]
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
    oversold = k[i] < OVERSOLD
    if trigger_ago is None and not oversold:
        return None

    score = 0
    if trigger_ago is not None:
        score += 3 + (1 if trigger_ago == 0 else 0)
    if oversold:
        score += 2
    if closes[i] > e_fast[i]:
        score += 1
    if e_fast[i] > e_slow[i]:
        score += 1
    return {
        "signal": "TRIGGER" if trigger_ago is not None else "WATCH",
        "trigger_ago": trigger_ago, "k": k[i], "d": d[i],
        "close": closes[i], "ema_fast": e_fast[i], "ema_slow": e_slow[i],
        "counter_trend": e_fast[i] < e_slow[i] and closes[i] < e_slow[i],
        "score": score,
    }


# ---------------------------------------------------------------- Alur utama
def scan():
    name, tickers, get_candles = pick_provider()
    coins = [(b, t) for b, t in tickers.items()
             if not excluded(b) and t["vol"] >= MIN_VOLUME_USD and t["last"] > 0]
    coins.sort(key=lambda x: x[1]["vol"], reverse=True)
    coins = coins[:TOP_N]

    results, failed = [], 0
    for base, t in coins:
        candles = get_candles(t["id"])
        if candles is None:
            failed += 1
            time.sleep(0.2)
            continue
        a = analyze(candles)
        if a:
            a.update({"symbol": base + "USDT", "chg24h": t["chg"], "vol": t["vol"]})
            results.append(a)
        time.sleep(0.12)

    if coins and failed == len(coins):
        raise RuntimeError(f"{name} menjawab daftar koin tapi menolak data candle semua koin.")
    results.sort(key=lambda r: (r["score"], r["vol"]), reverse=True)
    return name, len(coins), failed, results


def format_message(name, checked, failed, results):
    now = datetime.now().strftime("%d %b %Y %H:%M")
    head = (f"Screener Oversold ({now})\n"
            f"Stoch 5,3,3 + EMA 13/21 | TF {TIMEFRAME.upper()} | sumber {name}\n"
            f"Diperiksa {checked} koin" + (f" ({failed} gagal)" if failed else "") + "\n")
    if not results:
        return head + "\nTidak ada koin oversold atau trigger kali ini."
    lines = [head]
    for r in results[:SHOW]:
        if r["signal"] == "TRIGGER":
            sig = "TRIGGER cross naik" + (" (candle terakhir)" if r["trigger_ago"] == 0 else " (1 candle lalu)")
        else:
            sig = "WATCH oversold, belum cross"
        trend = "EMA13>EMA21" if r["ema_fast"] > r["ema_slow"] else "EMA13<EMA21"
        pos = "di atas" if r["close"] > r["ema_fast"] else "di bawah"
        warn = "\n  Peringatan: lawan tren (harga di bawah EMA21)" if r["counter_trend"] else ""
        lines.append(
            f"{r['symbol']}  (skor {r['score']})\n"
            f"  {sig}\n"
            f"  Harga: {r['close']:g}  |  24j: {r['chg24h']:+.1f}%\n"
            f"  Stoch K/D: {r['k']:.1f} / {r['d']:.1f}\n"
            f"  {trend}  |  harga {pos} EMA13{warn}\n"
        )
    lines.append("Ini hasil filter otomatis, bukan sinyal beli. Cek chart, tentukan entry, SL, dan ukuran posisi sendiri.")
    return "\n".join(lines)


def log_results(name, results):
    new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["waktu_utc", "sumber", "tf", "symbol", "sinyal", "harga", "k", "d",
                        "ema13", "ema21", "chg24h", "skor"])
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        for r in results[:SHOW]:
            w.writerow([ts, name, TIMEFRAME, r["symbol"], r["signal"], r["close"],
                        round(r["k"], 1), round(r["d"], 1), round(r["ema_fast"], 6),
                        round(r["ema_slow"], 6), round(r["chg24h"], 2), r["score"]])


def send_telegram(text):
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": text}, timeout=15)
    r.raise_for_status()


def main():
    dry = "--dry-run" in sys.argv
    try:
        name, checked, failed, results = scan()
        msg = format_message(name, checked, failed, results)
        log_results(name, results)
    except Exception as e:
        msg = f"Screener error: {e}"
    if dry or not (TG_TOKEN and TG_CHAT):
        print(msg)
        if not dry:
            print("\n(TELEGRAM_TOKEN / TELEGRAM_CHAT_ID belum diisi, jadi tidak dikirim.)")
    else:
        send_telegram(msg)


if __name__ == "__main__":
    main()
