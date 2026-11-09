#!/usr/bin/env python3
"""
Uji historis sinyal "koin bangun tidur" (volume harian melonjak di koin yang sideways).

Pertanyaan: kalau dulu masuk setiap kali sinyal itu muncul, apakah hasilnya lebih baik dari entry acak?
Aturan sinyal sama dengan intel2.py (versi harian): volume hari itu >= 4x median 57 hari sebelumnya,
rentang harga 30 hari <= 70%, harga hari itu -4%..+10%, naik 7 hari <= 25%.
Masuk di harga buka hari berikutnya, SL 1,5xATR, TP 2,25xATR, batas 7 hari, biaya ikut dihitung.

BATASAN: koin yang diuji adalah top SEKARANG (survivorship bias: koin yang mati tidak ada), sampel kecil
berarti kesimpulan lemah, masa lalu tidak menjamin masa depan.

Pakai:  python backtest_vol.py --dry-run   |   python backtest_vol.py
"""
import json
import sys
from datetime import datetime, timezone

import backtest as B
import screener as S

BT_COINS = 80
WANT = 900                         # candle 1D per koin (sekitar 2,5 tahun)
HORIZON = 7
RATIO, MAX_RANGE = 4.0, 0.70
CHG_MIN, CHG_MAX, MAX_7D = -4.0, 10.0, 25.0
MIN_MED = 100_000
COOLDOWN = 3
STATS_FILE = "backtest_vol_stats.json"


def run_coin(inst, out):
    c = B.fetch_history(inst, "1D", WANT)       # (ts, o, h, l, c, quote_vol)
    if len(c) < 200:
        return False
    opens, highs, lows, closes = ([x[k] for x in c] for k in (1, 2, 3, 4))
    vols = [x[5] for x in c]
    cand = [(x[2], x[3], x[4], x[5]) for x in c]
    last_sig = -99
    for i in range(70, len(c) - 1):
        if i % B.BASELINE_STEP == 0:
            res = B.simulate(opens, highs, lows, closes, i, HORIZON)
            if res:
                out["baseline"].append(res)
        win = sorted(vols[i - 59:i - 2])
        med = win[len(win) // 2]
        if med < MIN_MED or vols[i] < RATIO * med or i - last_sig < COOLDOWN:
            continue
        hi, lo = max(highs[i - 29:i + 1]), min(lows[i - 29:i + 1])
        if lo <= 0 or hi / lo - 1 > MAX_RANGE:
            continue
        chg = (closes[i] / closes[i - 1] - 1) * 100
        chg7 = (closes[i] / closes[i - 6] - 1) * 100
        if not (CHG_MIN <= chg <= CHG_MAX) or chg7 > MAX_7D:
            continue
        res = B.simulate(opens, highs, lows, closes, i, HORIZON)
        if not res:
            continue
        last_sig = i
        cvd = S.cvd_proxy(cand[:i + 1], 5)[0]
        out["signals"].append({"res": res, "cvd_pos": cvd >= S.CVD_POSITIVE, "ratio": vols[i] / med,
                               "risk": bool(S.risk_flags(cand[:i + 1], 0))})
    return True


def build(out, used):
    sig = out["signals"]
    sub = lambda f: B.summarize([s["res"] for s in sig if f(s)])
    rep = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "coins": used, "total": len(sig),
           "all": B.summarize([s["res"] for s in sig]), "baseline": B.summarize(out["baseline"]),
           "detail": {"CVD positif": sub(lambda s: s["cvd_pos"]), "CVD negatif": sub(lambda s: not s["cvd_pos"]),
                      "volume >= 8x normal": sub(lambda s: s["ratio"] >= 8),
                      "tanpa tanda bandar": sub(lambda s: not s["risk"])}}
    a, base = rep["all"], rep["baseline"]
    if a["n"] < B.MIN_N:
        rep["verdict"] = "Sampel masih kecil, belum bisa disimpulkan."
    elif a["exp"] > base["exp"] + 0.1 and a["exp"] > 0:
        rep["verdict"] = "Sinyal ini lebih baik dari entry acak (bukan jaminan)."
    elif a["exp"] > base["exp"]:
        rep["verdict"] = "Sedikit lebih baik dari acak, belum meyakinkan."
    else:
        rep["verdict"] = "Sinyal ini BELUM lebih baik dari entry acak. Jangan jadikan dasar entry."
    return rep


def text(rep):
    e = S.esc
    lines = [f"<b>⚡ SCREENER | UJI HISTORIS KOIN BANGUN TIDUR ({rep['coins']} koin)</b>",
             e(f"Total sinyal diuji: {rep['total']}. Batas waktu {HORIZON} hari, SL 1,5xATR, TP 2,25xATR."), "",
             e(B.fmt_line("Semua sinyal", rep["all"])), e(B.fmt_line("Entry acak (pembanding)", rep["baseline"])),
             "", "<b>Per kondisi</b>"]
    lines += [e(B.fmt_line(k, v)) for k, v in rep["detail"].items()]
    lines += ["", f"<i>{e('Kesimpulan: ' + rep['verdict'])}</i>",
              e("Koin yang diuji adalah top sekarang (bias survivorship). Masa lalu tidak menjamin masa depan.")]
    return "\n".join(lines)


def main():
    dry = "--dry-run" in sys.argv
    tick, err = S.okx_tickers()
    if not tick:
        print("OKX tidak bisa diakses:", err)
        return
    ranked = sorted(((t["vol"], t["id"]) for b, t in tick.items() if not S.excluded(b)), reverse=True)[:BT_COINS]
    out, used = {"signals": [], "baseline": []}, 0
    for _, inst in ranked:
        try:
            used += bool(run_coin(inst, out))
        except Exception as e:
            print("skip", inst, type(e).__name__)
    if not used:
        msg = "<b>Backtest koin bangun tidur error:</b> data historis tidak bisa diambil."
    else:
        rep = build(out, used)
        msg = text(rep)
        if not dry:
            with open(STATS_FILE, "w") as f:
                json.dump(rep, f, indent=1)
    if dry or not (S.TG_TOKEN and S.TG_CHAT):
        print(msg)
    else:
        S.send_routed(msg, "screener")


if __name__ == "__main__":
    main()
