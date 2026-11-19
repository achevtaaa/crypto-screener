#!/usr/bin/env python3
"""
VOLUME DULUAN: volume melonjak, harga belum naik (pola sebelum banyak pump, seperti contoh PROS di 4H).

Dipakai dua tempat:
  - intel2.py  : memindai koin tiap 30 menit (candle 4H yang baru tutup) dan mengirim kartu 📊
  - file ini   : python volume_duluan.py  -> uji historis (backtest) untuk memeriksa apakah pola ini benar-benar
                 lebih baik dari entry acak. Dijalankan lewat workflow "Volume Duluan".

Aturan (candle 4H yang BARU TUTUP):
  1. volume >= 5x median 30 candle sebelumnya
  2. candle itu hijau dan badannya paling besar +8% (harga belum lari)
  3. 2 hari sebelumnya sideways: rentang high-low <= 20%
  4. 24 jam sebelumnya harga hampir tidak bergerak (maks +-6%)
  5. tekanan beli (CVD proxy 12 candle) positif
Batasan: koin yang diuji adalah top sekarang (bias survivorship); uji historis bukan jaminan.
"""
import json
import sys
from datetime import datetime, timezone

import backtest as B
import backtest_ob as OB
import inti as S

try:
    import kartu_teks as K
except Exception:
    K = None

RATIO = 5.0
MAX_BODY = 0.08
BASE_CANDLES = 12
MAX_RANGE = 0.20
MAX_DRIFT = 0.06
CVD_N = 12
BT_COINS = 60
HORIZON = 24                  # 24 candle 4H = 4 hari
COOLDOWN = 6
STATS_FILE = "backtest_vol4h_stats.json"
VARIANTS = ["Volume duluan (tanpa filter CVD)", "Volume duluan + CVD positif"]


def cek(c, cvd_min=None):
    """c = daftar (ts, open, high, low, close, quote_vol) yang sudah tutup, candle terakhir yang dinilai.
    Mengembalikan dict (ratio, body, rng, drift, cvd) kalau pola cocok, selain itu None."""
    if len(c) < 45:
        return None
    vols = sorted(x[5] for x in c[-31:-1])
    med = vols[len(vols) // 2]
    ts, o, h, l, cl, v = c[-1]
    if med <= 0 or o <= 0 or v < RATIO * med or cl < o:
        return None
    body = cl / o - 1
    if body > MAX_BODY:
        return None
    base = c[-1 - BASE_CANDLES:-1]
    lo = min(x[3] for x in base)
    if lo <= 0:
        return None
    rng = max(x[2] for x in base) / lo - 1
    if rng > MAX_RANGE:
        return None
    ref = c[-8][4]
    drift = c[-2][4] / ref - 1 if ref > 0 else 0
    if abs(drift) > MAX_DRIFT:
        return None
    cvd, _ = S.cvd_proxy([(x[2], x[3], x[4], x[5]) for x in c[-2 * CVD_N:]], CVD_N)
    if cvd_min is not None and cvd < cvd_min:
        return None
    return {"ratio": v / med, "body": body * 100, "rng": rng * 100, "drift": drift * 100, "cvd": cvd}


# ================================================================ uji historis
def new_out():
    return {"baseline": [], **{v: [] for v in VARIANTS}}


def run_coin(inst, out):
    c = B.fetch_history(inst, "4H", 1200)
    if len(c) < 200:
        return
    opens, highs, lows, closes = ([x[k] for x in c] for k in (1, 2, 3, 4))
    last = {v: -99 for v in VARIANTS}
    for i in range(45, len(c) - 1):
        if i % B.BASELINE_STEP == 0:
            res = B.simulate(opens, highs, lows, closes, i, HORIZON)
            if res:
                out["baseline"].append(res)
        win = c[max(0, i - 60):i + 1]
        s = cek(win, None)
        if not s:
            continue
        for name, ok in ((VARIANTS[0], True), (VARIANTS[1], s["cvd"] >= S.CVD_POSITIVE)):
            if ok and i - last[name] >= COOLDOWN:
                res = B.simulate(opens, highs, lows, closes, i, HORIZON)
                if res:
                    out[name].append(res)
                    last[name] = i


def build(out, used):
    base = OB.summarize(out["baseline"])
    rows = {}
    for v in VARIANTS:
        s = OB.summarize(out[v])
        text, icon = OB.verdict(s, base)
        rows[v] = dict(s, verdict=text, icon=icon)
    return {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "coins": used, "baseline": base, "rows": rows}


def telegram_text(rep):
    e = S.esc
    lines = [f"<b>⚡ SCREENER | UJI VOLUME DULUAN 4H ({rep['coins']} koin)</b>",
             e("Pola: volume 4H >= 5x normal, harga belum naik (badan candle <= 8%), sebelumnya sideways. "
               "Dibandingkan entry acak. SL 1,5xATR, TP 2,25xATR, biaya dihitung."),
             "", e(B.fmt_line("Entry acak (pembanding)", rep["baseline"]))]
    for v, s in rep["rows"].items():
        lines.append(e(f"{s['icon']} {B.fmt_line(v, s).strip()}"))
        if s["n"] >= B.MIN_N:
            lines.append(e(f"     → {s['verdict']}"))
    good = [v for v, s in rep["rows"].items() if s["icon"] == "✅"]
    lines += ["", "<i>" + e("Kesimpulan: " + ("Pola ini lebih baik dari acak: " + "; ".join(good) + ". Tetap kandidat, bukan jaminan."
                                            if good else
                                            "Belum terbukti lebih baik dari acak. Anggap alert Volume Duluan hanya bahan pantauan.")) + "</i>",
              "<i>" + e("Titik hijau = lebih baik dari acak dengan selisih >= 2x galat baku (n >= 30). Abu-abu = sampel kecil atau belum "
                        "meyakinkan. Merah = belum lebih baik. Koin yang diuji adalah top sekarang (bias survivorship).") + "</i>"]
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
        msg = "<b>Uji Volume Duluan error:</b> data historis tidak bisa diambil."
    else:
        rep = build(out, used)
        msg = telegram_text(rep)
        if not dry:
            with open(STATS_FILE, "w") as f:
                json.dump(rep, f, indent=1)
    if dry or not (S.TG_TOKEN and S.TG_CHAT):
        print(msg)
    elif K:
        K.kirim(S, msg, "screener")
    else:
        for part in S.split_for_telegram(msg):
            S.send_routed(part, "screener")


if __name__ == "__main__":
    main()
