#!/usr/bin/env python3
"""
Screener v2: CoinGecko (cari kandidat) + Bybit (cek funding, OI, proxy CVD) -> Telegram.

Alur:
  1. CoinGecko (gratis): ambil ~500 koin teratas + daftar trending.
     Kandidat = volume 24j besar dibanding market cap, atau sedang trending.
  2. Bybit (data publik, tanpa login): untuk tiap kandidat yang punya
     perpetual USDT, cek funding rate, perubahan open interest 24j,
     dan proxy CVD spot.
  3. Beri skor, kirim top kandidat ke Telegram, simpan log ke CSV.

CATATAN: "CVD spot" di sini adalah PROXY (perkiraan dari posisi close tiap
candle x turnover), bukan CVD asli dari data tick.

Setup:
  pip install requests
  Windows (PowerShell):
    $env:TELEGRAM_TOKEN="TOKEN_KAMU"
    $env:TELEGRAM_CHAT_ID="CHAT_ID_KAMU"
  Opsional (kalau punya demo API key CoinGecko, gratis):
    $env:COINGECKO_API_KEY="CG-xxxx"

  python screener_v2.py --dry-run   # tampil di layar saja
  python screener_v2.py             # kirim ke Telegram
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone

import requests

CG_BASE = "https://api.coingecko.com/api/v3"
BYBIT_HOSTS = ["https://api.bybit.com", "https://api.bytick.com"]  # domain resmi Bybit
LAST_ERR = {"msg": ""}
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
CG_KEY = os.getenv("COINGECKO_API_KEY")

# ---------- Parameter (ubah sesuai selera) ----------
CG_PAGES = 2                    # 2 halaman x 250 = 500 koin teratas
MCAP_MIN = 30_000_000           # abaikan koin dengan market cap di bawah ini
MCAP_MAX = 30_000_000_000
CG_MIN_VOLUME = 20_000_000      # volume 24 jam minimal (USD)
VOL_MCAP_MIN = 0.10             # volume/market cap minimal agar dianggap "ramai"
TOP_N = 50                      # maksimal kandidat yang dicek ke Bybit
MIN_OI_CHANGE_PCT = 5.0         # OI naik minimal sekian % dalam 24 jam
FUNDING_MIN = -0.0003           # -0.03% per 8 jam
FUNDING_MAX = 0.0005            # +0.05% per 8 jam (lebih tinggi = terlalu ramai long)
PRICE_MIN_PCT = -5.0            # harga 24 jam: jangan sudah anjlok terlalu dalam
PRICE_MAX_PCT = 15.0            # ... dan jangan sudah terbang (hindari kejar pucuk)
CVD_POSITIVE = 0.05             # proxy CVD (-1..1) di atas ini = tekanan beli
SHOW = 5
LOG_FILE = "screener_log.csv"


def cg_get(path, params=None):
    headers = {"accept": "application/json"}
    if CG_KEY:
        headers["x-cg-demo-api-key"] = CG_KEY
    for attempt in range(4):
        try:
            r = requests.get(CG_BASE + path, params=params, headers=headers, timeout=20)
            if r.status_code == 429:           # kena rate limit, tunggu lalu coba lagi
                time.sleep(25)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(3)
    return None


def bybit_get(path, params):
    """Coba tiap domain Bybit; domain yang berhasil dipakai duluan di panggilan berikutnya."""
    for host in list(BYBIT_HOSTS):
        for _ in range(2):
            try:
                r = requests.get(host + path, params=params, timeout=15)
                if r.status_code != 200:
                    LAST_ERR["msg"] = f"{host} -> HTTP {r.status_code}"
                    break  # domain ini ditolak, coba domain lain
                data = r.json()
                if data.get("retCode") == 0:
                    BYBIT_HOSTS.sort(key=lambda h: h != host)
                    return data["result"]
                LAST_ERR["msg"] = f"{host} -> retCode {data.get('retCode')}"
            except Exception as e:
                LAST_ERR["msg"] = f"{host} -> {type(e).__name__}"
            time.sleep(1.5)
    return None


def coingecko_candidates():
    """Kembalikan list kandidat dari CoinGecko, urut dari yang paling ramai."""
    coins = []
    for page in range(1, CG_PAGES + 1):
        data = cg_get("/coins/markets", {
            "vs_currency": "usd", "order": "market_cap_desc",
            "per_page": 250, "page": page, "sparkline": "false",
        })
        if data is None:
            raise RuntimeError("Gagal ambil data CoinGecko (rate limit atau koneksi).")
        coins.extend(data)
        time.sleep(2)

    trending_ids = set()
    tr = cg_get("/search/trending")
    if tr:
        trending_ids = {c["item"]["id"] for c in tr.get("coins", [])}

    seen, out = set(), []
    for c in coins:
        sym = (c.get("symbol") or "").upper()
        mcap, vol = c.get("market_cap") or 0, c.get("total_volume") or 0
        if sym in seen or not (MCAP_MIN <= mcap <= MCAP_MAX) or vol < CG_MIN_VOLUME:
            continue
        seen.add(sym)  # ticker kembar: yang market cap terbesar menang (daftar sudah terurut)
        ratio = vol / mcap if mcap else 0
        trending = c["id"] in trending_ids
        if ratio >= VOL_MCAP_MIN or trending:
            out.append({"id": c["id"], "sym": sym, "ratio": ratio, "trending": trending,
                        "mcap": mcap, "vol": vol})
    out.sort(key=lambda x: (x["trending"], x["ratio"]), reverse=True)
    return out[:TOP_N]


def bybit_tickers():
    res = bybit_get("/v5/market/tickers", {"category": "linear"})
    if not res:
        raise RuntimeError(f"Gagal ambil data Bybit [{LAST_ERR['msg']}]. Kemungkinan IP server diblokir Bybit.")
    return {t["symbol"]: t for t in res["list"] if t["symbol"].endswith("USDT")}


def oi_change_pct(symbol):
    res = bybit_get("/v5/market/open-interest",
                    {"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 25})
    if not res or len(res.get("list", [])) < 2:
        return None
    rows = res["list"]  # terbaru -> terlama
    now, prev = float(rows[0]["openInterest"]), float(rows[-1]["openInterest"])
    return (now / prev - 1) * 100 if prev else None


def cvd_proxy(base):
    """Proxy tekanan beli spot -1..1. Pakai pair spot BASEUSDT kalau ada."""
    res = bybit_get("/v5/market/kline",
                    {"category": "spot", "symbol": base + "USDT", "interval": "60", "limit": 24})
    if not res or not res.get("list"):
        return None
    num = den = 0.0
    for k in res["list"]:  # [start, open, high, low, close, volume, turnover]
        high, low, close, turnover = float(k[2]), float(k[3]), float(k[4]), float(k[6])
        if high == low or turnover == 0:
            continue
        clv = ((close - low) - (high - close)) / (high - low)
        num += clv * turnover
        den += turnover
    return num / den if den else None


def scan():
    cg = coingecko_candidates()
    bb = bybit_tickers()

    results = []
    for c in cg:
        # cocokkan ke simbol perpetual Bybit (termasuk format 1000XXXUSDT)
        sym = None
        for cand in (c["sym"] + "USDT", "1000" + c["sym"] + "USDT"):
            if cand in bb:
                sym = cand
                break
        if not sym:
            continue
        t = bb[sym]
        if t.get("fundingRate") in ("", None):
            continue
        funding = float(t["fundingRate"])
        price_chg = float(t["price24hPcnt"]) * 100
        if not (FUNDING_MIN <= funding <= FUNDING_MAX):
            continue
        if not (PRICE_MIN_PCT <= price_chg <= PRICE_MAX_PCT):
            continue

        oi = oi_change_pct(sym)
        if oi is None or oi < MIN_OI_CHANGE_PCT:
            continue
        cvd = cvd_proxy(c["sym"])

        score = 2
        if oi >= 10:
            score += 1
        if funding < 0:
            score += 1                       # funding negatif + OI naik: potensi squeeze
        if cvd is not None and cvd >= CVD_POSITIVE:
            score += 2
        if c["trending"]:
            score += 2                       # sedang trending di CoinGecko
        if c["ratio"] >= 0.30:
            score += 1                       # volume sangat ramai dibanding market cap

        results.append({
            "symbol": sym, "price": float(t["lastPrice"]), "price_chg": price_chg,
            "funding": funding * 100, "oi": oi, "cvd": cvd, "ratio": c["ratio"],
            "trending": c["trending"], "score": score,
        })
        time.sleep(0.15)

    results.sort(key=lambda r: (r["score"], r["ratio"]), reverse=True)
    return results


def format_message(cands):
    now = datetime.now().strftime("%d %b %Y %H:%M")
    if not cands:
        return f"Screener ({now})\nTidak ada koin yang lolos filter kali ini."
    lines = [f"Screener CoinGecko + Bybit ({now})",
             f"Top {min(SHOW, len(cands))} kandidat untuk DIPANTAU:\n"]
    for c in cands[:SHOW]:
        cvd = "n/a" if c["cvd"] is None else f"{c['cvd']:+.2f}"
        trend = "  | TRENDING" if c["trending"] else ""
        lines.append(
            f"{c['symbol']}  (skor {c['score']}){trend}\n"
            f"  Harga: {c['price']:g}  |  24j: {c['price_chg']:+.1f}%\n"
            f"  OI 24j: {c['oi']:+.1f}%  |  Funding: {c['funding']:+.4f}%\n"
            f"  Proxy CVD spot: {cvd}  |  Vol/MCap: {c['ratio']:.2f}\n"
        )
    lines.append("Ini hasil filter otomatis, bukan sinyal beli. Cek chart, tentukan entry, SL, dan ukuran posisi sendiri.")
    return "\n".join(lines)


def log_results(cands):
    new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["waktu_utc", "symbol", "harga", "chg24h", "oi24h", "funding",
                        "cvd_proxy", "vol_mcap", "trending", "skor"])
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        for c in cands[:SHOW]:
            w.writerow([ts, c["symbol"], c["price"], round(c["price_chg"], 2), round(c["oi"], 2),
                        round(c["funding"], 4), "" if c["cvd"] is None else round(c["cvd"], 3),
                        round(c["ratio"], 3), int(c["trending"]), c["score"]])


def send_telegram(text):
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": text}, timeout=15)
    r.raise_for_status()


def main():
    dry = "--dry-run" in sys.argv
    try:
        cands = scan()
        msg = format_message(cands)
        log_results(cands)
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
