#!/usr/bin/env python3
"""
Uji historis Order Block (OB) dan divergence untuk sinyal BUY, di TF 4H dan 1D.

Pertanyaan: apakah metode OB / divergence lebih baik dari entry acak, dan lebih baik dari metode lama
(Stoch 5,3,3 cross naik dari area jenuh jual)?

Definisi (sesuai yang Anda tetapkan):
  - Bullish Order Block: candle MERAH terakhir sebelum lonjakan naik impulsif yang menembus struktur (BOS).
      impulsif  = close candle lonjakan - close candle merah >= 1,5 x ATR(14)
      BOS       = close candle lonjakan > high tertinggi 10 candle sebelum candle merah
      zona      = dari harga Open sampai Low candle merah itu
      zona batal kalau ada candle yang close di bawah Low zona, atau umurnya lebih dari 30 candle.
  - Retest OB   = candle pertama yang menyentuh zona (low <= Open candle merah) sementara close masih di atas Low zona.
  - Stoch cross = %K memotong naik %D (Stoch 5,3,3), %K sebelumnya < 20.
  - Bullish divergence = harga membuat Lower Low (pivot low, kekuatan 3) sementara %K Stoch membuat Higher Low,
      dan %K pivot sebelumnya < 30. Sinyal baru dihitung setelah pivot terkonfirmasi (3 candle sesudahnya),
      jadi tidak ada "mengintip masa depan".

Varian yang diuji:
  1. Stoch cross (metode lama)
  2. OB retest
  3. OB retest + Stoch cross (cross terjadi paling lama 3 candle setelah sentuhan zona)
  4. OB + Stoch cross + EMA13 > EMA21
  5. Divergence
  6. Divergence di zona OB + EMA13 > EMA21 (versi "paling kuat")

Masuk di harga buka candle berikutnya. SL 1,5 x ATR, TP 2,25 x ATR (RR 1:1,5), batas waktu 24 candle (4H) / 10 candle (1D),
biaya 0,055% per sisi. Hasil dalam R (1R = jarak ke SL), dibandingkan dengan entry acak di koin yang sama.

BATASAN: koin yang diuji adalah top SEKARANG (survivorship bias); sampel kecil = kesimpulan lemah;
definisi OB dan BOS di sini adalah aturan mekanis yang disederhanakan, trader manual bisa menilai berbeda.

Pakai:  python backtest_ob.py --dry-run   |   python backtest_ob.py
"""
import json
import math
import sys
from datetime import datetime, timezone

import backtest as B
import inti as S

BT_COINS = 60
TF_CFG = {"4h": ("4H", 4 * 3600_000, 1200, 24), "1d": ("1D", B.DAY_MS, 900, 10)}
IMPULSE_ATR = 1.5
STRUCT_LOOKBACK = 10
ZONE_MAX_AGE = 30
TOUCH_WINDOW = 3                  # cross Stoch harus terjadi paling lama 3 candle setelah sentuhan zona
CROSS_FROM = 20
DIV_PREV_MAX = 30
PIVOT = 3
COOLDOWN = 6
STATS_FILE = "backtest_ob_stats.json"
VARIANTS = ["Stoch cross (metode lama)", "OB retest", "OB retest + Stoch cross",
            "OB + Stoch cross + EMA13>21", "Divergence", "Divergence di zona OB + EMA13>21"]


def detect(c):
    """c = list (ts, open, high, low, close, quote_vol). Mengembalikan {varian: [indeks candle sinyal]}."""
    n = len(c)
    opens, highs, lows, closes = ([x[k] for x in c] for k in (1, 2, 3, 4))
    k, d = S.stochastic(highs, lows, closes, S.STOCH_K, S.STOCH_SMOOTH, S.STOCH_D)
    ef, es = S.ema(closes, S.EMA_FAST), S.ema(closes, S.EMA_SLOW)
    sig = {v: [] for v in VARIANTS}
    last = {v: -99 for v in VARIANTS}
    zones, prev_piv = [], None

    def add(name, i):
        if i - last[name] >= COOLDOWN:
            sig[name].append(i)
            last[name] = i

    for i in range(40, n - 1):
        up = ef[i] is not None and es[i] is not None and ef[i] > es[i]
        # --- OB baru: candle hijau i adalah ujung lonjakan, cari candle merah terakhir di 4 candle sebelumnya
        if closes[i] > opens[i]:
            r = next((q for q in range(i - 1, i - 5, -1) if closes[q] < opens[q]), None)
            if r is not None and r >= STRUCT_LOOKBACK + 15:
                atr_r = B.atr(highs, lows, closes, r)
                if (atr_r > 0 and closes[i] - closes[r] >= IMPULSE_ATR * atr_r
                        and closes[i] > max(highs[r - STRUCT_LOOKBACK:r])
                        and not any(z["r"] == r for z in zones)):
                    zones.append({"r": r, "lo": lows[r], "hi": opens[r], "born": i, "touch": None})
        # --- perbarui zona: batal / sentuhan pertama
        touched_now = False
        for z in zones[:]:
            if z["born"] >= i:
                continue
            if i - z["born"] > ZONE_MAX_AGE or closes[i] < z["lo"]:
                zones.remove(z)
                continue
            if z["touch"] is None and lows[i] <= z["hi"]:
                z["touch"] = i
                touched_now = True
        if touched_now:
            add("OB retest", i)
        # --- Stoch cross naik dari area jenuh jual
        cross = (None not in (k[i - 1], d[i - 1], k[i], d[i])
                 and k[i - 1] <= d[i - 1] and k[i] > d[i] and k[i - 1] < CROSS_FROM)
        if cross:
            add("Stoch cross (metode lama)", i)
            recent = [z for z in zones if z["touch"] is not None and 0 <= i - z["touch"] <= TOUCH_WINDOW]
            if recent:
                add("OB retest + Stoch cross", i)
                if up:
                    add("OB + Stoch cross + EMA13>21", i)
        # --- divergence bullish (pivot low terkonfirmasi PIVOT candle kemudian)
        p = i - PIVOT
        if p - PIVOT >= 0 and k[p] is not None:
            if lows[p] < min(lows[p - PIVOT:p]) and lows[p] <= min(lows[p + 1:p + PIVOT + 1]):
                if prev_piv is not None and 5 <= p - prev_piv <= 40:
                    q = prev_piv
                    if (k[q] is not None and lows[p] < lows[q] and k[p] > k[q] and k[q] < DIV_PREV_MAX):
                        add("Divergence", i)
                        in_zone = any(z["born"] <= p and z["lo"] * 0.99 <= lows[p] <= z["hi"] for z in zones)
                        if in_zone and up:
                            add("Divergence di zona OB + EMA13>21", i)
                prev_piv = p
    return sig, (opens, highs, lows, closes)


def run_coin(inst, out):
    for tf, (bar, ms, want, horizon) in TF_CFG.items():
        c = B.fetch_history(inst, bar, want)
        if len(c) < 200:
            continue
        sig, (opens, highs, lows, closes) = detect(c)
        for i in range(30, len(c) - 1):
            if i % B.BASELINE_STEP == 0:
                res = B.simulate(opens, highs, lows, closes, i, horizon)
                if res:
                    out[tf]["baseline"].append(res)
        for name, idxs in sig.items():
            for i in idxs:
                res = B.simulate(opens, highs, lows, closes, i, horizon)
                if res:
                    out[tf][name].append(res)


def new_out():
    return {tf: dict({"baseline": []}, **{v: [] for v in VARIANTS}) for tf in TF_CFG}


def summarize(rs):
    """Seperti backtest.summarize, ditambah galat baku rata-rata (se) untuk menyaring hasil yang cuma kebetulan."""
    n = len(rs)
    if not n:
        return {"n": 0, "win": 0.0, "exp": 0.0, "se": 0.0}
    xs = [x for x, _ in rs]
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / max(1, n - 1))
    return {"n": n, "win": sum(1 for _, w in rs if w) / n, "exp": mean, "se": sd / math.sqrt(n) if n else 0.0}


def verdict(s, base):
    """Hijau hanya kalau selisih dari acak >= 2x galat baku (supaya tidak tertipu kebetulan, karena kita menguji banyak varian)."""
    if s["n"] < B.MIN_N:
        return "sampel kecil", "➖"
    if s["exp"] > 0 and s["exp"] - base["exp"] >= 2 * max(s["se"], 1e-9):
        return "lebih baik dari acak (selisih cukup besar)", "✅"
    if s["exp"] > base["exp"]:
        return "sedikit lebih baik, belum meyakinkan", "➖"
    return "belum lebih baik dari acak", "❌"


def build(out, used):
    rep = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "coins": used, "tf": {}}
    for tf in TF_CFG:
        base = summarize(out[tf]["baseline"])
        rows = {}
        for v in VARIANTS:
            s = summarize(out[tf][v])
            text, icon = verdict(s, base)
            rows[v] = dict(s, verdict=text, icon=icon)
        rep["tf"][tf] = {"baseline": base, "rows": rows}
    return rep


def telegram_text(rep):
    e = S.esc
    lines = [f"<b>⚡ SCREENER | UJI ORDER BLOCK DAN DIVERGENCE ({rep['coins']} koin)</b>",
             e("Dibandingkan dengan entry acak dan metode lama. SL 1,5xATR, TP 2,25xATR, biaya ikut dihitung.")]
    for tf, data in rep["tf"].items():
        lines += ["", f"<b>TF {e(tf.upper())}</b>", e(B.fmt_line("Entry acak (pembanding)", data["baseline"]))]
        for v, s in data["rows"].items():
            lines.append(e(f"{s['icon']} {B.fmt_line(v, s).strip()}"))
            if s["n"] >= B.MIN_N:
                lines.append(e(f"     → {s['verdict']}"))
    good = [f"{v} ({tf.upper()})" for tf, d in rep["tf"].items() for v, s in d["rows"].items()
            if s["icon"] == "✅" and v != VARIANTS[0]]
    lines += ["", "<i>" + e("Kesimpulan: " + ("Layak ditambahkan ke screener: " + "; ".join(good) + "."
                                            if good else
                                            "Belum ada metode OB / divergence yang terbukti lebih baik dari acak. "
                                            "Jangan jadikan dasar entry.")) + "</i>",
              e("✅ lebih baik dari acak dengan selisih >= 2x galat baku (n >= 30) | ➖ sampel kecil atau belum meyakinkan | ❌ belum lebih baik. "
                "Koin yang diuji adalah top sekarang (bias survivorship). Masa lalu tidak menjamin masa depan.")]
    return "\n".join(lines)


def main():
    dry = "--dry-run" in sys.argv
    tick, err = S.okx_tickers()
    if not tick:
        print("OKX tidak bisa diakses:", err)
        return
    ranked = sorted(((t["vol"], t["id"]) for b, t in tick.items() if not S.excluded(b)), reverse=True)[:BT_COINS]
    out, used = new_out(), 0
    for _, inst in ranked:
        try:
            run_coin(inst, out)
            used += 1
        except Exception as e:
            print("skip", inst, type(e).__name__)
    if not used:
        msg = "<b>Backtest OB error:</b> data historis tidak bisa diambil."
    else:
        rep = build(out, used)
        msg = telegram_text(rep)
        if not dry:
            with open(STATS_FILE, "w") as f:
                json.dump(rep, f, indent=1)
    if dry or not (S.TG_TOKEN and S.TG_CHAT):
        print(msg)
    else:
        for part in S.split_for_telegram(msg):
            S.send_routed(part, "screener")


if __name__ == "__main__":
    main()
