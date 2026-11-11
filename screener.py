#!/usr/bin/env python3
"""Screener BUY (oversold) -> kartu gambar -> topik Screener di Telegram.

Cara kerja (2 tahap, supaya hemat panggilan API):
  Tahap 1  Semua koin top 100 (CoinGecko) yang punya perpetual USDT di OKX
           dihitung secara teknikal pada TF 1D + 4H  -> skor teknikal 0-6
  Tahap 2  12 koin teratas dicek derivatif (funding, OI, CVD spot) -> skor 0-4
           Skor akhir = teknikal + derivatif, dibatasi 1-10.
           Yang ditampilkan: skor >= SCREENER_MIN_SKOR (default 5), maks. 5 koin.

Aturan skor (ubah di fungsi score_* bila mau):
  Teknikal (maks 6)
    Stoch 5-3-3 1D : K < 20 -> +2 ; K 20-30 -> +1
    Stoch 5-3-3 4H : K < 20 -> +1
    4H K menyilang naik di atas D (3 candle terakhir, dari bawah 40) -> +1
    4H close di atas EMA13           -> +1  (harga merebut kembali EMA cepat)
    1D close di atas EMA21           -> +1  (koreksi, belum breakdown)
  Derivatif (maks 4)
    Funding <= 0                     -> +1  (short ramai, bahan squeeze)
    Open interest 24 jam naik        -> +1
    CVD spot 24 jam: naik +2, datar +1, turun 0
      (CVD dihitung dari taker buy - taker sell spot OKX, bar 1 jam)

Tujuan Telegram dibaca dari routes.json (kunci "group" dan topik "screener").
Token: env TELEGRAM_BOT_TOKEN atau TELEGRAM_TOKEN.
Env opsional:
  SCREENER_MIN_SKOR (default 5; kosong = default), COINGECKO_KEY

Pemakaian:
  python screener.py          # jalankan, kirim ke Telegram
  python screener.py --dry    # jalankan, simpan screener.png, tidak kirim
  python screener.py --mock   # data contoh (tanpa internet), simpan screener.png
"""
import os
import sys

import requests

from common import (AMBER, GREEN, MUTED, RED, TRACK, TXT, W, Pen, font, now_wib, okx, pct,
                    send_photo, send_text, stamp)

TOP_N = 100
STAGE2_N = 12
SHOW_N = 5
MIN_TECH = 3
THREAD = ("screener",)  # nama topik di routes.json


def _int_env(name, default):
    """Baca env bilangan bulat; kosong atau salah -> default (env kosong tidak boleh bikin crash)."""
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


MIN_SKOR = _int_env("SCREENER_MIN_SKOR", 5)

EXCLUDE = {"USDT", "USDC", "DAI", "FDUSD", "USDE", "USDS", "TUSD", "PYUSD", "USD1", "USDD", "BUIDL",
           "STETH", "WSTETH", "WEETH", "WBTC", "WETH", "WBETH", "CBBTC", "CBETH", "SUSDE", "USDG",
           "XAUT", "PAXG"}


# --------------------------------------------------------------------------
# Indikator
# --------------------------------------------------------------------------

def sma(v, n):
    return [sum(v[i - n + 1:i + 1]) / n for i in range(n - 1, len(v))]


def ema(v, n):
    if len(v) < n:
        return []
    k = 2 / (n + 1)
    e = sum(v[:n]) / n
    out = [e]
    for x in v[n:]:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def stoch(h, l, c, k=5, sm=3, d=3):
    """Stochastic 5-3-3. Mengembalikan (K, D) selaras, atau None bila data kurang."""
    raw = []
    for i in range(k - 1, len(c)):
        hh, ll = max(h[i - k + 1:i + 1]), min(l[i - k + 1:i + 1])
        raw.append(50.0 if hh == ll else (c[i] - ll) / (hh - ll) * 100)
    kk = sma(raw, sm)
    dd = sma(kk, d)
    if len(dd) < 5:
        return None
    return kk[-len(dd):], dd


def bull_cross(K, D, lookback=3, zone=40):
    for i in range(lookback):
        if K[-1 - i] > D[-1 - i] and K[-2 - i] <= D[-2 - i] and K[-2 - i] < zone:
            return True
    return False


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def candles(inst, bar, limit):
    rows = okx("/api/v5/market/candles", {"instId": inst, "bar": bar, "limit": str(limit)})
    if not rows or len(rows) < 30:
        return None
    rows = list(reversed(rows))  # lama -> baru
    return ([float(r[2]) for r in rows], [float(r[3]) for r in rows], [float(r[4]) for r in rows])


def universe():
    """[(simbol, nama, instId)] top 100 CoinGecko yang ada perpetual USDT di OKX."""
    ins = okx("/api/v5/public/instruments", {"instType": "SWAP"}) or []
    live = {x["instId"] for x in ins if x["instId"].endswith("-USDT-SWAP") and x.get("state") == "live"}
    if not live:
        raise SystemExit("Daftar instrumen OKX kosong, batal.")

    coins, seen = [], set()
    hdr = {"x-cg-demo-api-key": os.environ["COINGECKO_KEY"]} if os.environ.get("COINGECKO_KEY") else None
    try:
        r = requests.get("https://api.coingecko.com/api/v3/coins/markets",
                         params={"vs_currency": "usd", "order": "market_cap_desc",
                                 "per_page": TOP_N, "page": 1},
                         headers=hdr, timeout=20)
        r.raise_for_status()
        for row in r.json():
            sym = row["symbol"].upper()
            inst = f"{sym}-USDT-SWAP"
            if sym in EXCLUDE or sym in seen or inst not in live:
                continue
            seen.add(sym)
            coins.append((sym, row["name"], inst))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] CoinGecko gagal ({e}); pakai urutan volume OKX", file=sys.stderr)

    if not coins:  # cadangan: urut volume 24 jam OKX
        tk = okx("/api/v5/market/tickers", {"instType": "SWAP"}) or []
        vol = []
        for x in tk:
            if x["instId"] in live and x.get("last") and x.get("volCcy24h"):
                sym = x["instId"].split("-")[0]
                if sym not in EXCLUDE:
                    vol.append((float(x["volCcy24h"]) * float(x["last"]), sym, x["instId"]))
        for _, sym, inst in sorted(vol, reverse=True)[:TOP_N]:
            coins.append((sym, sym, inst))
    return coins


# --------------------------------------------------------------------------
# Tahap 1 - teknikal
# --------------------------------------------------------------------------

def analyze(sym, name, inst):
    d1 = candles(inst, "1D", 60)
    h4 = candles(inst, "4H", 100)
    if not d1 or not h4:
        return None
    s1, s4 = stoch(*d1), stoch(*h4)
    e13, e21 = ema(h4[2], 13), ema(d1[2], 21)
    if not s1 or not s4 or not e13 or not e21:
        return None
    k1, k4 = s1[0][-1], s4[0][-1]
    cross = bull_cross(*s4)
    pts = (2 if k1 < 20 else 1 if k1 < 30 else 0) + (1 if k4 < 20 else 0) + (1 if cross else 0)
    pts += (1 if h4[2][-1] > e13[-1] else 0) + (1 if d1[2][-1] > e21[-1] else 0)
    return dict(sym=sym, name=name, inst=inst, k1d=k1, k4h=k4, tech=pts,
                fund=None, oi=None, cvd=None, skor=pts)


# --------------------------------------------------------------------------
# Tahap 2 - derivatif
# --------------------------------------------------------------------------

def derivs(x):
    fr = okx("/api/v5/public/funding-rate", {"instId": x["inst"]}, gap=0.12)
    if fr and fr[0].get("fundingRate") not in (None, ""):
        x["fund"] = float(fr[0]["fundingRate"]) * 100

    oi = okx("/api/v5/rubik/stat/contracts/open-interest-volume",
             {"ccy": x["sym"], "period": "1H"}, gap=0.45)
    if oi and len(oi) >= 25:  # terbaru dulu: [ts, oi, vol]
        now_, old = float(oi[0][1]), float(oi[24][1])
        if old > 0:
            x["oi"] = (now_ / old - 1) * 100

    tv = okx("/api/v5/rubik/stat/taker-volume",
             {"ccy": x["sym"], "instType": "SPOT", "period": "1H"}, gap=0.45)
    if tv and len(tv) >= 12:  # [ts, sellVol, buyVol]
        rows = tv[:24]
        buy = sum(float(r[2]) for r in rows)
        sell = sum(float(r[1]) for r in rows)
        tot = buy + sell
        if tot > 0:
            ratio = (buy - sell) / tot
            x["cvd"] = "naik" if ratio > 0.03 else "turun" if ratio < -0.03 else "datar"

    d = (1 if x["fund"] is not None and x["fund"] <= 0 else 0)
    d += (1 if x["oi"] is not None and x["oi"] > 0 else 0)
    d += {"naik": 2, "datar": 1}.get(x["cvd"], 0)
    x["skor"] = max(1, min(10, x["tech"] + d))


def run():
    coins = universe()
    found = []
    for i, (sym, name, inst) in enumerate(coins, 1):
        r = analyze(sym, name, inst)
        if r:
            found.append(r)
        if i % 20 == 0:
            print(f"  tahap 1: {i}/{len(coins)}")
    scanned = len(found)
    print(f"{len(coins)} koin di universe, {scanned} berhasil dianalisis")
    if scanned < 20:
        raise SystemExit("Terlalu sedikit koin yang datanya berhasil diambil; kemungkinan OKX bermasalah. Batal.")

    top = sorted((x for x in found if x["tech"] >= MIN_TECH), key=lambda x: (-x["tech"], x["k1d"]))[:STAGE2_N]
    for x in top:
        derivs(x)
    if top:
        print(f"tahap 2: {len(top)} koin dicek; data kosong -> funding {sum(x['fund'] is None for x in top)}, "
              f"OI {sum(x['oi'] is None for x in top)}, CVD {sum(x['cvd'] is None for x in top)}")
        for x in top:
            print(f"  {x['sym']}: teknikal {x['tech']} -> skor {x['skor']}")
    final = sorted((x for x in top if x["skor"] >= MIN_SKOR), key=lambda x: (-x["skor"], x["k1d"]))
    return final[:SHOW_N], scanned


# --------------------------------------------------------------------------
# Kartu (desain v1)
# --------------------------------------------------------------------------

def render(cands, scanned):
    n = len(cands)
    H = 180 + n * 186 + 230
    p = Pen(H)
    p.watermark()
    p.t(48, 92, "SCREENER BUY", font("Inter-SemiBold.otf", 22), fill=MUTED)
    p.t(W - 48, 92, stamp(), font("Inter-Medium.otf", 22), fill=MUTED, anchor="ra")
    p.t(48, 128, f"Oversold · TF 1D + 4H · {scanned} koin dipindai", font("Inter-Medium.otf", 22), fill=MUTED)

    def chip(x, y, label, value, col=TXT):
        fl, fv = font("Inter-Medium.otf", 19), font("Inter-SemiBold.otf", 19)
        wl = p.width(label + " ", fl)
        wv = p.width(value, fv)
        w = int(wl + wv + 28)
        p.box(x, y, x + w, y + 38, 19, fill=TRACK)
        p.t(x + 14, y + 8, label, fl, fill=MUTED)
        p.t(x + 14 + wl, y + 8, value, fv, fill=col)
        return w + 10

    y0 = 180
    for i, x in enumerate(cands):
        y = y0 + i * 186
        sc = x["skor"]
        p.rr(40, y, W - 80, 170)
        p.t(80, y + 24, x["sym"], font("Inter-Bold.otf", 40))
        p.t(80, y + 76, x["name"][:24], font("Inter-Medium.otf", 20), fill=MUTED)
        scol = GREEN if sc >= 7 else AMBER
        p.t(W - 80, y + 20, f"{sc}", font("Inter-Bold.otf", 56), fill=scol, anchor="ra")
        p.t(W - 80, y + 82, "/10", font("Inter-Medium.otf", 20), fill=MUTED, anchor="ra")
        bx, bw = 400, 420
        p.box(bx, y + 40, bx + bw, y + 52, 6, fill=TRACK)
        p.box(bx, y + 40, bx + int(bw * sc / 10), y + 52, 6, fill=scol)
        p.t(bx, y + 62, "Skor kekuatan setup", font("Inter-Medium.otf", 18), fill=MUTED)

        cx, cy = 80, y + 118
        cx += chip(cx, cy, "Stoch 1D·4H", f"{x['k1d']:.0f} · {x['k4h']:.0f}",
                   GREEN if min(x["k1d"], x["k4h"]) < 20 else TXT)
        f = x["fund"]
        cx += chip(cx, cy, "Funding", pct(f, 3) if f is not None else "—",
                   GREEN if f is not None and f < 0 else TXT)
        o = x["oi"]
        cx += chip(cx, cy, "OI", pct(o, 1) if o is not None else "—",
                   MUTED if o is None else GREEN if o > 0 else RED)
        cv = x["cvd"]
        cx += chip(cx, cy, "CVD spot", cv or "—",
                   {"naik": GREEN, "turun": RED}.get(cv, MUTED))

    y = y0 + n * 186 + 4
    p.rr(40, y, W - 80, 96, 24, fill=(40, 34, 24), outline=(88, 72, 40))
    p.t(80, y + 22, "Skor 7+ = kandidat kuat", font("Inter-SemiBold.otf", 26), fill=AMBER)
    p.t(80, y + 60, "Tetap pasang SL sebelum entry", font("Inter-Medium.otf", 20), fill=MUTED)
    p.footer("Data: OKX · CoinGecko  |  Bukan nasihat finansial")
    return p.done()


def caption(cands):
    ring = ", ".join(f"{x['sym']} {x['skor']}/10" for x in cands)
    return (f"Screener BUY · {len(cands)} kandidat: {ring}.\n"
            "Skor 7+ = kandidat kuat. Pasang SL sebelum entry. Bukan nasihat finansial.")


def mock():
    rows = [("NEAR", "NEAR Protocol", 11, 17, -0.004, 3.2, "naik", 8),
            ("HYPE", "Hyperliquid", 17, 22, 0.002, 1.8, "naik", 7),
            ("TIA", "Celestia", 9, 14, -0.011, -0.6, "datar", 7),
            ("ARB", "Arbitrum", 14, 25, 0.006, 0.9, "turun", 6),
            ("RENDER", "Render", 19, 31, 0.001, 2.4, "naik", 6)]
    return [dict(sym=a, name=b, k1d=c, k4h=d, fund=e, oi=f, cvd=g, skor=h) for a, b, c, d, e, f, g, h in rows], 78


def main():
    args = set(sys.argv[1:])
    if "--mock" in args:
        cands, scanned = mock()
    else:
        cands, scanned = run()

    if not cands:
        msg = (f"Screener BUY {stamp(now_wib())}: tidak ada kandidat dengan skor ≥ {MIN_SKOR} "
               f"dari {scanned} koin. Tidak memaksa entry — cek lagi di jadwal berikutnya.")
        print(msg)
        if not ({"--dry", "--mock"} & args):
            send_text(msg, THREAD)
        return

    img = render(cands, scanned)
    cap = caption(cands)
    if {"--dry", "--mock"} & args:
        img.save("screener.png")
        print("Disimpan: screener.png\n" + cap)
        return
    send_photo(img, cap, THREAD)


if __name__ == "__main__":
    main()
