#!/usr/bin/env python3
"""
Backtest (uji historis) untuk screener.py -> hasil dikirim ke Telegram dan disimpan ke backtest_stats.json.

Pertanyaan yang dijawab: "Kalau dulu saya masuk tiap kali screener memberi sinyal, apakah hasilnya
lebih baik daripada masuk acak?"

Cara kerja:
  - Ambil data historis OKX untuk koin-koin dengan volume terbesar (BT_COINS koin).
  - Di setiap candle (TF_LOW dari screener.py, default 4H), hitung sinyal yang SAMA dengan screener
    (Stochastic 5,3,3 + EMA 13/21 + CVD proxy + konteks 1D). Sinyal = TRIGGER cross naik di candle itu.
  - Masuk di harga buka candle berikutnya. SL = 1,5 x ATR(14) di bawah entry, TP = 2,25 x ATR (RR 1:1,5).
    Dipantau sampai batas waktu (24 candle 4H = 4 hari). Kalau SL dan TP kena di candle yang sama,
    dianggap SL dulu (konservatif). Biaya trading ikut dihitung (0,055% per sisi).
  - Hasil dihitung dalam satuan R (1R = jarak ke SL). Dikelompokkan menurut skor, dan dibandingkan
    dengan entry acak (tiap 6 candle) di koin yang sama.

BATASAN yang harus kamu tahu:
  - Skor di backtest hanya dari komponen HARGA (1D, 4H, CVD proxy, tanda bahaya bandar), maksimal 8.
    Data historis funding dan open interest tidak ikut diuji.
  - Koin yang diuji adalah koin top SEKARANG (survivorship bias: koin yang sudah mati tidak ada).
  - Hasil masa lalu tidak menjamin masa depan. Sampel kecil = kesimpulan lemah.

Pakai:
  python backtest.py --dry-run     # tampil di layar saja
  python backtest.py               # kirim ke Telegram + simpan backtest_stats.json
"""
import bisect
import json
import sys
import time
from datetime import datetime, timezone

import requests

import screener as S

BT_COINS = 60
FEE = 0.00055                      # per sisi (taker)
ATR_N = 14
SL_ATR, TP_ATR = 1.5, 2.25         # RR 1 : 1,5
BASELINE_STEP = 6                  # entry acak tiap 6 candle
MIN_N = 30                         # sampel minimal supaya kesimpulan layak dipercaya

TF_CFG = {  # bar OKX, milidetik per candle, jumlah candle diambil, batas waktu trade (candle)
    "4h": ("4H", 4 * 3600_000, 1200, 24),
    "1h": ("1H", 3600_000, 3000, 48),
}
DAY_MS = 86_400_000
BTC_REG = ([], [])                 # (waktu buka candle 1D BTC, apakah close di atas EMA50)


# ------------------------------------------------------------------ data
def fetch_history(inst, bar, want):
    """Candle OKX terbaru -> terlama, dipaginasi. Mengembalikan list urut waktu naik:
    (ts, open, high, low, close, quote_volume)."""
    rows, after = [], None
    while len(rows) < want:
        params = {"instId": inst, "bar": bar, "limit": "100"}
        if after:
            params["after"] = after
        j, _ = S.http_get("https://www.okx.com/api/v5/market/history-candles", params)
        if not j or j.get("code") != "0" or not j["data"]:
            break
        batch = j["data"]
        rows += [r for r in batch if r[8] == "1"]
        after = batch[-1][0]
        time.sleep(0.12)
    rows = sorted({int(r[0]): r for r in rows}.values(), key=lambda r: int(r[0]))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])) for r in rows][-want:]


# ------------------------------------------------------------------ simulasi
def atr(hs, ls, cs, i, n=ATR_N):
    trs = []
    for k in range(i - n + 1, i + 1):
        trs.append(max(hs[k] - ls[k], abs(hs[k] - cs[k - 1]), abs(ls[k] - cs[k - 1])))
    return sum(trs) / n


def simulate(opens, highs, lows, closes, i, horizon):
    """Masuk di open candle i+1. Mengembalikan hasil dalam R (setelah biaya) atau None."""
    n = len(closes)
    if i + 1 + horizon > n or i < ATR_N + 1:
        return None
    a = atr(highs, lows, closes, i)
    entry = opens[i + 1]
    if a <= 0 or entry <= 0 or entry - SL_ATR * a <= 0:
        return None
    sl, tp = entry - SL_ATR * a, entry + TP_ATR * a
    risk = SL_ATR * a
    r = None
    for k in range(i + 1, i + 1 + horizon):
        if lows[k] <= sl:
            r = -1.0
            break
        if highs[k] >= tp:
            r = TP_ATR / SL_ATR
            break
    hit = r is not None
    if r is None:
        r = (closes[i + horizon] - entry) / risk
    cost = 2 * FEE * entry / risk
    return r - cost, hit and r > 0


def summarize(rs):
    n = len(rs)
    if not n:
        return {"n": 0, "win": 0.0, "exp": 0.0}
    return {"n": n, "win": sum(1 for _, w in rs if w) / n, "exp": sum(x for x, _ in rs) / n}


def load_btc_regime():
    global BTC_REG
    day = fetch_history("BTC-USDT", "1D", 1400)
    closes = [c[4] for c in day]
    e50 = S.ema(closes, 50)
    BTC_REG = ([c[0] for c in day], [e is not None and cl > e for cl, e in zip(closes, e50)])


def btc_up_at(close_time):
    dts, up = BTC_REG
    j = bisect.bisect_right(dts, close_time - DAY_MS) - 1
    return up[j] if 0 <= j < len(up) else None


def run_coin(inst, tf, out):
    bar, ms, want, horizon = TF_CFG[tf]
    low = fetch_history(inst, bar, want)
    day = fetch_history(inst, "1D", want * ms // DAY_MS + 120)
    if len(low) < 300 or len(day) < 140:
        return False
    ts = [c[0] for c in low]
    opens, highs, lows, closes = ([c[k] for c in low] for k in (1, 2, 3, 4))
    cand = [(c[2], c[3], c[4], c[5]) for c in low]                 # format screener: (high, low, close, quote_vol)
    dcand = [(c[2], c[3], c[4], c[5]) for c in day]
    dts = [c[0] for c in day]
    for i in range(100, len(low) - 1):
        win = cand[i - 99:i + 1]
        h1 = S.analyze_tf(win, tf)
        if h1 is None:
            continue
        if i % BASELINE_STEP == 0:
            res = simulate(opens, highs, lows, closes, i, horizon)
            if res:
                out["baseline"].append(res)
        if h1["trigger_ago"] != 0:
            continue
        close_time = ts[i] + ms
        j = bisect.bisect_right(dts, close_time - DAY_MS) - 1     # candle 1D terakhir yang sudah tutup
        if j < 99:
            continue
        d1 = S.analyze_tf(dcand[j - 99:j + 1], "1d")
        if d1 is None:
            continue
        res = simulate(opens, highs, lows, closes, i, horizon)
        if not res:
            continue
        risk = S.risk_flags(win, 0)                                # tanpa likuiditas (tidak ada data historis)
        score = S.evaluate(d1, h1, {"funding": None, "oi_chg": None}, risk)
        out["signals"].append({"score": score, "res": res, "counter": d1["counter"],
                               "cvd_pos": h1["cvd"] >= S.CVD_POSITIVE, "risk": bool(risk),
                               "btc_up": btc_up_at(close_time)})
    return True


# ------------------------------------------------------------------ laporan
def build_report(out, tf, coins_used):
    sig = out["signals"]
    bucket = {"rendah": [], "sedang": [], "tinggi": []}
    for s in sig:
        k = "rendah" if s["score"] <= 3 else "sedang" if s["score"] <= 5 else "tinggi"
        bucket[k].append(s["res"])
    b = {k: summarize(v) for k, v in bucket.items()}
    base = summarize(out["baseline"])
    detail = {
        "searah tren 1D": summarize([s["res"] for s in sig if not s["counter"]]),
        "lawan tren 1D": summarize([s["res"] for s in sig if s["counter"]]),
        "CVD positif": summarize([s["res"] for s in sig if s["cvd_pos"]]),
        "CVD negatif": summarize([s["res"] for s in sig if not s["cvd_pos"]]),
        "ada tanda bandar": summarize([s["res"] for s in sig if s["risk"]]),
        "tanpa tanda bandar": summarize([s["res"] for s in sig if not s["risk"]]),
        "BTC di atas EMA50 1D": summarize([s["res"] for s in sig if s["btc_up"]]),
        "BTC di bawah EMA50 1D": summarize([s["res"] for s in sig if s["btc_up"] is False]),
        "KOMBINASI: searah tren + CVD+ + tanpa bandar + BTC naik": summarize(
            [s["res"] for s in sig if not s["counter"] and s["cvd_pos"] and not s["risk"] and s["btc_up"]]),
    }
    hi = b["tinggi"]
    if hi["n"] < MIN_N:
        verdict = "Sampel skor tinggi masih kecil, belum bisa disimpulkan."
    elif hi["exp"] > base["exp"] + 0.1 and hi["exp"] > 0:
        verdict = "Skor tinggi terbukti lebih baik dari entry acak (bukan jaminan)."
    elif hi["exp"] > base["exp"] and hi["exp"] > 0:
        verdict = "Skor tinggi sedikit lebih baik dari acak (selisih kecil), belum meyakinkan."
    elif hi["exp"] > base["exp"]:
        verdict = "Skor tinggi sedikit lebih baik dari acak tapi belum menguntungkan setelah biaya."
    else:
        verdict = "Skor tinggi BELUM lebih baik dari entry acak. Jangan percaya skor mentah-mentah."
    days = TF_CFG[tf][2] * TF_CFG[tf][1] // DAY_MS
    return {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "days": int(days),
            "coins": coins_used, "tf": tf, "total_signals": len(sig), "buckets": b, "baseline": base,
            "detail": detail, "verdict": verdict}


def fmt_line(name, s):
    if not s["n"]:
        return f"  {name}: tidak ada sinyal"
    return f"  {name}: n={s['n']} | menang {s['win'] * 100:.0f}% | rata-rata {s['exp']:+.2f}R"


def telegram_text(rep):
    e = S.esc
    lines = [f"<b>⚡ SCREENER | HASIL UJI HISTORIS ({e(rep['tf'].upper())}, +-{rep['days']} hari, {rep['coins']} koin)</b>",
             e(f"Total sinyal diuji: {rep['total_signals']}. Skor di sini maks 8 (tanpa funding/OI)."),
             "", "<b>Per kelompok skor</b>",
             e(fmt_line("Skor tinggi (6-8)", rep["buckets"]["tinggi"])),
             e(fmt_line("Skor sedang (4-5)", rep["buckets"]["sedang"])),
             e(fmt_line("Skor rendah (1-3)", rep["buckets"]["rendah"])),
             e(fmt_line("Entry acak (pembanding)", rep["baseline"])),
             "", "<b>Per kondisi</b>"]
    lines += [e(fmt_line(k, v)) for k, v in rep["detail"].items()]
    lines += ["", f"<i>{e('Kesimpulan: ' + rep['verdict'])}</i>",
              e(f"1R = jarak ke SL. SL 1,5xATR, TP 2,25xATR, batas {TF_CFG[rep['tf']][3]} candle, biaya ikut dihitung. "
                "Koin yang diuji adalah top sekarang (bias survivorship). Masa lalu tidak menjamin masa depan.")]
    return "\n".join(lines)


def main():
    dry = "--dry-run" in sys.argv
    tf = S.TF_LOW if S.TF_LOW in TF_CFG else "4h"
    tick, err = S.okx_tickers()
    if not tick:
        msg = f"<b>Backtest error:</b> OKX tidak bisa diakses ({S.esc(err)})"
        print(msg) if dry or not (S.TG_TOKEN and S.TG_CHAT) else S.send_telegram(msg)
        return
    ranked = sorted(((b, t) for b, t in tick.items() if not S.excluded(b)),
                    key=lambda x: x[1]["vol"], reverse=True)[:BT_COINS]
    out, used = {"signals": [], "baseline": []}, 0
    try:
        load_btc_regime()
    except Exception as e:
        print("regime BTC gagal:", type(e).__name__)
    for base, t in ranked:
        try:
            used += bool(run_coin(t["id"], tf, out))
        except Exception as e:
            print("skip", base, type(e).__name__)
    if not used:
        msg = "<b>Backtest error:</b> data historis tidak bisa diambil."
    else:
        rep = build_report(out, tf, used)
        msg = telegram_text(rep)
        if not dry:
            with open(S.STATS_FILE, "w") as f:
                json.dump(rep, f, indent=1)
    if dry or not (S.TG_TOKEN and S.TG_CHAT):
        print(msg)
    else:
        S.send_telegram(msg)


if __name__ == "__main__":
    main()
