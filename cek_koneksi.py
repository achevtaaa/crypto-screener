#!/usr/bin/env python3
"""
Tes koneksi: sumber data mana yang bisa dibaca dari server GitHub (bukan dari HP / komputer Anda)?

Menguji daftar harga (ticker) dan candle (grafik) di banyak bursa, plus DexScreener dan lainnya.
Sekaligus mengecek apakah koin contoh (MMT, PROS, BREV, ICNT, ZKP, PIPPIN, FHE) tersedia di tiap sumber.
Hasil dikirim ke Telegram (topik Intel) sebagai kartu. Tidak ada data pribadi yang dikirim ke situs mana pun.

Pakai:  python cek_koneksi.py --dry-run   |   python cek_koneksi.py
"""
import sys
import time

import requests

import inti as S

try:
    import kartu_teks as K
except Exception:
    K = None

SAMPLE = ["MMT", "PROS", "BREV", "ICNT", "ZKP", "PIPPIN", "FHE"]
TIMEOUT = 15
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url, params=None, post=None):
    t0 = time.time()
    try:
        r = requests.post(url, json=post, headers=UA, timeout=TIMEOUT) if post is not None else \
            requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        dt = time.time() - t0
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}", dt
        return r.json(), "", dt
    except Exception as e:
        return None, type(e).__name__, time.time() - t0


def _strip(sym, suffixes):
    s = sym.upper()
    for suf in suffixes:
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


# ---- parser: json -> himpunan nama koin (base)
def p_bybit(j):
    return {_strip(x["symbol"], ["USDT"]) for x in j["result"]["list"] if x["symbol"].endswith("USDT")}


def p_okx(j):
    return {x["instId"].split("-")[0] for x in j["data"]}


def p_binance(j):
    return {_strip(x["symbol"], ["USDT"]) for x in j if x["symbol"].endswith("USDT")}


def p_mexc_fut(j):
    return {x["symbol"].split("_")[0] for x in j["data"] if x["symbol"].endswith("_USDT")}


def p_gate_fut(j):
    return {x["contract"].split("_")[0] for x in j if str(x["contract"]).endswith("_USDT")}


def p_bitget(j):
    return {_strip(x["symbol"], ["USDT"]) for x in j["data"] if x["symbol"].endswith("USDT")}


def p_kucoin(j):
    return {x["symbol"].split("-")[0] for x in j["data"]["ticker"] if x["symbol"].endswith("-USDT")}


def p_kucoin_fut(j):
    out = set()
    for x in j["data"]:
        s = x["symbol"]
        if s.endswith("USDTM"):
            b = s[:-5]
            out.add("BTC" if b == "XBT" else b)
    return out


def p_htx(j):
    return {x["symbol"][:-4].upper() for x in j["data"] if x["symbol"].endswith("usdt")}


def p_hl(j):
    return {x["name"].upper().lstrip("K") if False else x["name"].upper() for x in j["universe"]}


TICKERS = [
    ("Bybit (futures)", "https://api.bybit.com/v5/market/tickers", {"category": "linear"}, None, p_bybit),
    ("OKX (futures)", "https://www.okx.com/api/v5/market/tickers", {"instType": "SWAP"}, None, p_okx),
    ("Binance (futures)", "https://fapi.binance.com/fapi/v1/ticker/24hr", None, None, p_binance),
    ("Binance (spot)", "https://api.binance.com/api/v3/ticker/24hr", None, None, p_binance),
    ("Binance (spot, data-api)", "https://data-api.binance.vision/api/v3/ticker/24hr", None, None, p_binance),
    ("MEXC (spot)", "https://api.mexc.com/api/v3/ticker/24hr", None, None, p_binance),
    ("MEXC (futures)", "https://contract.mexc.com/api/v1/contract/ticker", None, None, p_mexc_fut),
    ("Gate (futures)", "https://api.gateio.ws/api/v4/futures/usdt/tickers", None, None, p_gate_fut),
    ("Bitget (futures)", "https://api.bitget.com/api/v2/mix/market/tickers", {"productType": "USDT-FUTURES"}, None, p_bitget),
    ("KuCoin (spot)", "https://api.kucoin.com/api/v1/market/allTickers", None, None, p_kucoin),
    ("KuCoin (futures)", "https://api-futures.kucoin.com/api/v1/contracts/active", None, None, p_kucoin_fut),
    ("HTX (spot)", "https://api.huobi.pro/market/tickers", None, None, p_htx),
    ("Hyperliquid (perp)", "https://api.hyperliquid.xyz/info", None, {"type": "meta"}, p_hl),
]

# candle: (nama, url, params, fungsi_cek(json)->jumlah_candle)
CANDLES = [
    ("Bybit", "https://api.bybit.com/v5/market/kline", {"category": "linear", "symbol": "BTCUSDT", "interval": "240", "limit": "10"},
     lambda j: len(j["result"]["list"])),
    ("OKX", "https://www.okx.com/api/v5/market/candles", {"instId": "BTC-USDT-SWAP", "bar": "4H", "limit": "10"},
     lambda j: len(j["data"])),
    ("Binance (futures)", "https://fapi.binance.com/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "4h", "limit": "10"},
     lambda j: len(j)),
    ("MEXC (spot)", "https://api.mexc.com/api/v3/klines", {"symbol": "BTCUSDT", "interval": "4h", "limit": "10"},
     lambda j: len(j)),
    ("Gate (futures)", "https://api.gateio.ws/api/v4/futures/usdt/candlesticks", {"contract": "BTC_USDT", "interval": "4h", "limit": "10"},
     lambda j: len(j)),
    ("Bitget (futures)", "https://api.bitget.com/api/v2/mix/market/candles", {"symbol": "BTCUSDT", "productType": "USDT-FUTURES", "granularity": "4H", "limit": "10"},
     lambda j: len(j["data"])),
]

OTHERS = [
    ("DexScreener (data DEX)", "https://api.dexscreener.com/latest/dex/search", {"q": "PROS"}, None, lambda j: len(j.get("pairs") or [])),
    ("CoinGecko", "https://api.coingecko.com/api/v3/ping", None, None, lambda j: 1),
    ("DefiLlama", "https://stablecoins.llama.fi/stablecoins", None, None, lambda j: len(j.get("peggedAssets", []))),
]


def run_tests():
    """-> daftar (kelompok, nama, ok, teks) dan dict ringkasan."""
    rows, tick_ok, candle_ok = [], [], []
    for name, url, params, post, parse in TICKERS:
        j, err, dt = _get(url, params, post)
        if j is None:
            rows.append(("ticker", name, False, f"gagal ({err})" + (" - diblokir wilayah/server" if err in ("HTTP 403", "HTTP 451") else "")))
            continue
        try:
            names = parse(j)
        except Exception:
            rows.append(("ticker", name, False, "respon tidak terbaca (format berubah?)"))
            continue
        have = [c for c in SAMPLE if c in names]
        miss = [c for c in SAMPLE if c not in names]
        rows.append(("ticker", name, True, f"{len(names)} koin | {dt:.1f} dtk | ada: {', '.join(have) or '-'}"
                     + (f" | tidak ada: {', '.join(miss)}" if miss else "")))
        tick_ok.append(name)
        time.sleep(0.3)
    for name, url, params, check in CANDLES:
        j, err, dt = _get(url, params)
        if j is None:
            rows.append(("candle", name, False, f"gagal ({err})"))
            continue
        try:
            n = check(j)
        except Exception:
            n = 0
        ok = n > 0
        rows.append(("candle", name, ok, f"{n} candle 4H | {dt:.1f} dtk" if ok else "respon kosong"))
        if ok:
            candle_ok.append(name)
        time.sleep(0.3)
    for name, url, params, post, parse in OTHERS:
        j, err, dt = _get(url, params, post)
        if j is None:
            rows.append(("lain", name, False, f"gagal ({err})"))
            continue
        try:
            n = parse(j)
        except Exception:
            n = 0
        rows.append(("lain", name, n > 0, f"{n} data | {dt:.1f} dtk"))
        time.sleep(0.3)
    return rows, tick_ok, candle_ok


def build_html(rows, tick_ok, candle_ok):
    e = S.esc
    L = ["<b>🔌 TES KONEKSI SUMBER DATA 🔌</b>",
         e("Dites dari server GitHub, tempat bot berjalan. Koin contoh yang dicari: " + ", ".join(SAMPLE) + ".")]
    titles = {"ticker": "DAFTAR HARGA (TICKER)", "candle": "GRAFIK (CANDLE 4H)", "lain": "SUMBER LAIN"}
    for group in ("ticker", "candle", "lain"):
        L += ["", f"<b>{titles[group]}</b>"]
        for g, name, ok, text in rows:
            if g == group:
                L.append(f"{'✅' if ok else '❌'} <b>{e(name)}</b>: {e(text)}")
    usable = [n for n in tick_ok if any(n.split(" (")[0] in c for c in candle_ok)]
    L += ["", "<i>" + e("Kesimpulan: sumber yang bisa dipakai bot untuk harga dan candle: " + (", ".join(usable) if usable else "belum ada")
                      + ". Bursa yang gagal (HTTP 403 / 451) memblokir server GitHub, jadi bot tidak bisa memakainya.") + "</i>"]
    return "\n".join(L)


def main():
    dry = "--dry-run" in sys.argv
    rows, tick_ok, candle_ok = run_tests()
    html = build_html(rows, tick_ok, candle_ok)
    if dry or not (S.TG_TOKEN and S.TG_CHAT):
        print(html)
    elif K:
        K.kirim(S, html, "intel")
    else:
        for part in S.split_for_telegram(html):
            S.send_routed(part, "intel")


if __name__ == "__main__":
    main()
