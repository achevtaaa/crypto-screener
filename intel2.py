#!/usr/bin/env python3
"""
Intel Plus: pelengkap whales.py. Semua alert masuk ke topik Intel dengan judul berbeda tiap jenis.

  💵 STABLECOIN         pencetakan / pembakaran USDT & USDC besar dalam 24 jam (DefiLlama)
  🧨 POSISI TERLALU RAMAI  funding ekstrem di koin besar (OKX), rawan squeeze
  💤 KOIN BANGUN TIDUR  volume 24 jam melonjak 4-8x di koin kripto yang lama sepi dan sideways; hanya yang CVD positif dan tanpa tanda bandar (OKX)
  🕵️ WALLET PANTAUAN    wallet pilihan di wallets.json (ETH lewat Ethplorer, BTC lewat mempool.space)

(Rotasi whale BTC -> alt ada di whales.py: 🔄 ROTASI WHALE.)

Semua alert adalah PETUNJUK untuk dicek sendiri, bukan sinyal beli/jual.
Pakai:
  python intel2.py --dry-run     # tampil di layar saja
  python intel2.py               # kirim ke Telegram + simpan intel2_state.json
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

import screener as S

try:
    import bantu
except ImportError:
    bantu = None

STATE_FILE = "intel2_state.json"
WALLET_FILE = "wallets.json"
WIB = timezone(timedelta(hours=7))
MAX_ALERTS_PER_RUN = 10

# --- stablecoin
STABLE_WATCH = ("USDT", "USDC")
STABLE_MIN_MOVE = 1_000_000_000          # perubahan 24 jam minimal $1 miliar

# --- funding
FUND_TOP = 40
FUND_HIGH = 0.06                         # % per 8 jam: long terlalu ramai
FUND_LOW = -0.04                         # % per 8 jam: short terlalu ramai
FUND_COOLDOWN_H = 12

# --- koin bangun tidur
DORM_EVERY_H = 4
DORM_TOP = 300
DORM_MIN_VOL = 500_000                   # volume 24 jam minimal (USD)
DORM_MIN_MED = 100_000                   # volume harian normal minimal (supaya bukan koin mati total)
DORM_RATIO = 4.0                         # volume sekarang >= 4x normal
DORM_MAX_RANGE = 0.70                    # rentang harga 30 hari <= 70% (sideways)
DORM_CHG_MIN, DORM_CHG_MAX = -4.0, 10.0  # perubahan harga 24 jam: belum meledak
DORM_MAX_7D = 25.0                       # naik 7 hari <= 25% (belum terlambat)
DORM_MAX_RATIO = 8.0                     # lonjakan >= 8x justru jelek di backtest, dilewati
TOKENIZED_BLOCK = {"XQQQ", "XMU"}        # aset saham/ETF yang dibuat token, bukan koin kripto
DORM_COOLDOWN_H = 72
DORM_SUMMARY_HOUR = 8                    # laporan pemindai harian dikirim sekali, setelah jam ini (WIB)
DORM_MAX_ALERTS = 3

# --- koin daftar SEC
SEC_MOVE = 10.0                          # % perubahan harga 24 jam yang dianggap kejadian
SEC_COOLDOWN_H = 12

# --- wallet pantauan
ETHPLORER = "https://api.ethplorer.io"
ETHPLORER_KEY = os.getenv("ETHPLORER_KEY", "freekey")
WALLET_MIN_USD = 500_000                 # perubahan saldo minimal segini baru di-alert


# ================================================================ util
esc = S.esc


def money(x):
    x = abs(x)
    if x >= 1e9:
        return f"${x / 1e9:.2f} miliar"
    if x >= 1e6:
        return f"${x / 1e6:.1f} juta"
    return f"${x / 1e3:.0f} ribu"


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    with open(STATE_FILE, "w") as f:
        json.dump(st, f)


def hours_since(ts):
    return (time.time() - ts) / 3600 if ts else 1e9


# ================================================================ 💵 stablecoin
def check_stablecoins(state):
    j, err = S.http_get("https://stablecoins.llama.fi/stablecoins", {"includePrices": "true"})
    if not j or "peggedAssets" not in j:
        print("stablecoin: data tidak bisa diambil", err)
        return []
    out, sent = [], state.setdefault("stable", {})
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    for a in j["peggedAssets"]:
        sym = a.get("symbol")
        if sym not in STABLE_WATCH:
            continue
        try:
            cur = a["circulating"]["peggedUSD"]
            day = a["circulatingPrevDay"]["peggedUSD"]
            week = a["circulatingPrevWeek"]["peggedUSD"]
            month = a["circulatingPrevMonth"]["peggedUSD"]
        except Exception:
            continue
        d = cur - day
        if abs(d) < STABLE_MIN_MOVE:
            continue
        kind = "mint" if d > 0 else "burn"
        key = f"{sym}:{kind}"
        if sent.get(key) == today:
            continue
        sent[key] = today
        if d > 0:
            head = f"{sym} bertambah {money(d)} dalam 24 jam"
            note = ("Pencetakan besar sering berarti dana baru siap masuk ke pasar. Tapi dana itu bisa menunggu "
                    "di bursa dan tidak selalu langsung membeli.")
        else:
            head = f"{sym} berkurang {money(d)} dalam 24 jam"
            note = "Pembakaran besar berarti dana keluar dari ekosistem kripto, biasanya tanda minat menurun."
        out.append(f"<b>💵 STABLECOIN 💵</b>\n<b>{esc(head)}</b>\n"
                   f"Total {sym}: {money(cur)} | 7 hari {money(cur - week)} {'naik' if cur >= week else 'turun'}"
                   f" | 30 hari {money(cur - month)} {'naik' if cur >= month else 'turun'}\n"
                   f"<i>{esc(note)}</i>")
    return out


# ================================================================ 🧨 funding ekstrem
def funding_norm(base):
    """Funding dinormalkan ke % per 8 jam. Mengembalikan None kalau tidak ada."""
    j, _ = S.http_get("https://www.okx.com/api/v5/public/funding-rate", {"instId": f"{base}-USDT-SWAP"})
    if not j or j.get("code") != "0" or not j["data"]:
        return None
    r = j["data"][0]
    try:
        rate = float(r["fundingRate"]) * 100
        iv = (int(r["nextFundingTime"]) - int(r["fundingTime"])) / 3.6e6
        if iv <= 0:
            iv = 8
        return rate * 8 / iv
    except Exception:
        return None


def check_funding(tick, state):
    out, cool = [], state.setdefault("funding", {})
    top = sorted(((b, t) for b, t in tick.items() if not S.excluded(b)), key=lambda x: x[1]["vol"], reverse=True)[:FUND_TOP]
    for base, t in top:
        f = funding_norm(base)
        time.sleep(0.3)
        if f is None:
            continue
        side = "long" if f >= FUND_HIGH else "short" if f <= FUND_LOW else None
        if not side:
            continue
        key = f"{base}:{side}"
        if hours_since(cool.get(key)) < FUND_COOLDOWN_H:
            continue
        cool[key] = time.time()
        if side == "long":
            note = "Posisi LONG terlalu ramai: kalau harga turun sedikit saja, long terlikuidasi beruntun (long squeeze). Hati-hati beli di sini."
        else:
            note = "Posisi SHORT terlalu ramai: kalau harga naik, short terpaksa tutup dan mendorong harga naik (short squeeze)."
        out.append(f"<b>🧨 POSISI TERLALU RAMAI 🧨</b>\n<b><u>{esc(base)}</u></b> funding {f:+.3f}% per 8 jam "
                   f"(setara {f * 3 * 365:+.0f}% per tahun) | harga {t['last']:g} | 24j {t['chg']:+.1f}%\n"
                   f"<i>{esc(note)}</i>")
    return out


# ================================================================ 💤 koin bangun tidur
def daily_candles(inst):
    """Candle 1D OKX yang sudah tutup, dengan waktu: (high, low, close, quote_vol, ts_ms), urut naik."""
    j, _ = S.http_get("https://www.okx.com/api/v5/market/candles",
                      {"instId": inst, "bar": "1D", "limit": str(S.CANDLES)})
    if not j or j.get("code") != "0":
        return None
    rows = sorted((r for r in j["data"] if r[8] == "1"), key=lambda r: int(r[0]))
    return [(float(r[2]), float(r[3]), float(r[4]), float(r[7]), int(r[0])) for r in rows]


def looks_tokenized(raw):
    """Saham/ETF yang dibuat token hampir tidak diperdagangkan di akhir pekan. Kripto asli ramai 24/7."""
    wk, we = [], []
    for h, l, c, v, ts in raw[-56:]:
        day = datetime.fromtimestamp(ts / 1000, timezone(timedelta(hours=8))).weekday()
        (we if day >= 5 else wk).append(v)
    if len(wk) < 20 or len(we) < 8:
        return False
    med = lambda a: sorted(a)[len(a) // 2]
    return med(wk) > 0 and med(we) < 0.2 * med(wk)


def dorm_summary(fn):
    """Satu pesan singkat per hari: berapa koin diperiksa dan kenapa yang lain tidak lolos."""
    order = ["diperiksa", "saham token (dibuang)", "volume naik 4x+", "bukan sideways", "harga 24j di luar -4%..+10%",
             "sudah naik >25% seminggu", "CVD belum mendukung", "ada tanda bandar", "lonjakan 8x+ (terlalu ekstrem)", "LOLOS"]
    names = {"diperiksa": "Koin diperiksa", "volume naik 4x+": "Volume naik 4x atau lebih", "LOLOS": "Lolos semua filter"}
    lines = ["<b>💤 KOIN BANGUN TIDUR 💤</b>", "<b>Laporan pemindai (pemindaian terakhir)</b>"]
    for k in order:
        if k in fn:
            lines.append(esc(f"{names.get(k, 'Gugur: ' + k)}: {fn[k]}"))
    if not fn.get("LOLOS"):
        lines.append("<i>" + esc("Tidak ada koin yang lolos hari ini. Itu normal: filternya ketat, dan di pasar yang takut "
                                 "(Extreme Fear) tekanan beli jarang kuat.") + "</i>")
    return "\n".join(lines)


def check_dormant(tick, state):
    last = state.get("dormant_scan")
    if hours_since(last) < DORM_EVERY_H:
        return []
    state["dormant_scan"] = time.time()
    cool = state.setdefault("dormant", {})
    fn = state["dorm_funnel"] = {}                      # hitungan pemindaian ini: kenapa koin tidak lolos
    today = datetime.now(WIB).strftime("%Y-%m-%d")

    def cnt(key):
        fn[key] = fn.get(key, 0) + 1

    uni = sorted(((b, t) for b, t in tick.items() if not S.excluded(b) and t["vol"] >= DORM_MIN_VOL),
                 key=lambda x: x[1]["vol"], reverse=True)[:DORM_TOP]
    hits = []
    for base, t in uni:
        if hours_since(cool.get(base)) < DORM_COOLDOWN_H:
            continue
        raw = daily_candles(t["id"])
        time.sleep(0.15)
        if not raw or len(raw) < 60:
            continue
        cnt("diperiksa")
        if base in TOKENIZED_BLOCK or looks_tokenized(raw):
            cnt("saham token (dibuang)")
            continue
        c = [x[:4] for x in raw]
        vols = sorted(x[3] for x in c[-60:-3])
        med = vols[len(vols) // 2]
        if med < DORM_MIN_MED or med <= 0:
            continue
        ratio = t["vol"] / med
        if ratio < DORM_RATIO:
            continue
        cnt("volume naik 4x+")
        hi, lo = max(x[0] for x in c[-30:]), min(x[1] for x in c[-30:])
        rng = hi / lo - 1 if lo > 0 else 9
        if rng > DORM_MAX_RANGE:
            cnt("bukan sideways")
            continue
        if not (DORM_CHG_MIN <= t["chg"] <= DORM_CHG_MAX):
            cnt("harga 24j di luar -4%..+10%")
            continue
        chg7 = (t["last"] / c[-7][2] - 1) * 100 if c[-7][2] else 0
        if chg7 > DORM_MAX_7D:
            cnt("sudah naik >25% seminggu")
            continue
        cvd = S.cvd_proxy(c, 5)[0]
        if cvd < S.CVD_POSITIVE:
            cnt("CVD belum mendukung")
            continue
        if S.risk_flags(c, 0):
            cnt("ada tanda bandar")
            continue
        if ratio >= DORM_MAX_RATIO:
            cnt("lonjakan 8x+ (terlalu ekstrem)")
            continue
        cnt("LOLOS")                                  # syarat sama dengan uji historis: CVD+, tanpa tanda bandar, bukan lonjakan ekstrem
        risk = S.risk_flags(c, t["vol"])              # untuk tampilan: peringatan likuiditas tipis tetap ditampilkan
        hits.append((ratio, base, t, med, rng, chg7, cvd, risk))
    hits.sort(reverse=True, key=lambda x: x[0])
    out = []
    print("pemindai koin bangun tidur:", fn)
    if datetime.now(WIB).hour >= DORM_SUMMARY_HOUR and state.get("dorm_summary_day") != today:
        state["dorm_summary_day"] = today
        out.append(dorm_summary(fn))
    for ratio, base, t, med, rng, chg7, cvd, risk in hits[:DORM_MAX_ALERTS]:
        cool[base] = time.time()
        support = cvd >= S.CVD_POSITIVE
        tag = bantu.narasi_label(base) if bantu else ""
        lines = [f"<b>💤 KOIN BANGUN TIDUR 💤</b>",
                 f"<b><u>{esc(base)}</u></b>" + (f"  🏷️ {esc(tag)}" if tag else "") + "\n"
                 f"📊 Volume 24 jam {money(t['vol'])} = <b>{ratio:.1f}x</b> normal ({money(med)}/hari)",
                 f"💰 Harga {t['last']:g} | 24j {t['chg']:+.1f}% | 7 hari {chg7:+.1f}% | rentang 30 hari hanya {rng * 100:.0f}%",
                 f"🟣 CVD spot (proxy, 5 hari): {cvd:+.2f} " + ("(tekanan beli mendukung)" if support else "(BELUM mendukung: bisa jadi distribusi)")]
        if risk:
            lines.append("<i>⚠️ " + esc("; ".join(risk)) + "</i>")
        lines.append("<i>Volume naik duluan bisa tanda akumulasi, tapi juga bisa pump yang disiapkan. "
                     "Cek chart, pastikan ada di Bybit, pakai ukuran posisi kecil.</i>")
        out.append("\n".join(lines))
    return out


# ================================================================ 🕵️ wallet pantauan
def load_wallets():
    try:
        with open(WALLET_FILE) as f:
            return [w for w in json.load(f).get("wallets", []) if w.get("address") and w.get("chain")]
    except Exception:
        return []


def snapshot_eth(addr):
    try:
        r = requests.get(f"{ETHPLORER}/getAddressInfo/{addr}", params={"apiKey": ETHPLORER_KEY}, timeout=30)
        if r.status_code != 200:
            return None
        j = r.json()
    except Exception:
        return None
    snap = {}
    eth = j.get("ETH", {})
    price = ((eth.get("price") or {}).get("rate")) or 0
    snap["ETH"] = {"bal": float(eth.get("balance", 0)), "px": float(price)}
    for tk in j.get("tokens", []) or []:
        info = tk.get("tokenInfo", {})
        px = (info.get("price") or {}).get("rate") if isinstance(info.get("price"), dict) else 0
        try:
            bal = float(tk.get("balance", 0)) / 10 ** int(info.get("decimals", 0) or 0)
        except Exception:
            continue
        if px and bal * float(px) >= 100_000:
            snap[info.get("symbol", "?")] = {"bal": bal, "px": float(px)}
    return snap


def snapshot_btc(addr, btc_px):
    try:
        r = requests.get(f"https://mempool.space/api/address/{addr}", timeout=30)
        if r.status_code != 200:
            return None
        cs = r.json()["chain_stats"]
        return {"BTC": {"bal": (cs["funded_txo_sum"] - cs["spent_txo_sum"]) / 1e8, "px": btc_px}}
    except Exception:
        return None


def check_wallets(tick, state):
    wallets = load_wallets()
    out, store = [], state.setdefault("wallets", {})
    btc_px = (tick.get("BTC") or {}).get("last", 0)
    for w in wallets:
        key = f"{w['chain']}:{w['address']}"
        snap = snapshot_eth(w["address"]) if w["chain"].lower() == "eth" else \
            snapshot_btc(w["address"], btc_px) if w["chain"].lower() == "btc" else None
        time.sleep(1.0)
        if snap is None:
            continue
        prev = store.get(key)
        store[key] = snap
        if prev is None:
            continue
        for sym, now in snap.items():
            old = prev.get(sym)
            if not old:
                continue
            d = now["bal"] - old["bal"]
            usd = abs(d) * (now["px"] or old["px"] or 0)
            if usd < WALLET_MIN_USD:
                continue
            arah = "MASUK" if d > 0 else "KELUAR"
            note = ("Saldo bertambah: bisa pembelian atau transfer dari wallet lain." if d > 0 else
                    "Saldo berkurang: bisa jual, atau pindah ke bursa atau wallet lain. Belum tentu dijual.")
            out.append(f"<b>🕵️ WALLET PANTAUAN 🕵️</b>\n<b>{esc(w.get('label', 'Wallet'))}</b>: {esc(sym)} {arah} "
                       f"{abs(d):,.2f} (≈ {money(usd)})\nSaldo sekarang: {now['bal']:,.2f} {esc(sym)}\n"
                       f"<i>{esc(note)}</i>")
    return out


def check_sec_watch(tick, state):
    """Koin di daftar aset digital SEC (rilis 34-106863): alert kalau bergerak tajam dalam 24 jam."""
    sec = bantu.SEC_SET if bantu else set()
    out, cool = [], state.setdefault("sec", {})
    for base in sorted(sec):
        t = tick.get(base)
        if not t or abs(t["chg"]) < SEC_MOVE:
            continue
        key = f"{base}:{'naik' if t['chg'] > 0 else 'turun'}"
        if hours_since(cool.get(key)) < SEC_COOLDOWN_H:
            continue
        cool[key] = time.time()
        out.append(f"<b>📜 DAFTAR ASET SEC 📜</b>\n<b><u>{esc(base)}</u></b> {'naik' if t['chg'] > 0 else 'turun'} "
                   f"<b>{t['chg']:+.1f}%</b> dalam 24 jam | harga {t['last']:g} | volume {money(t['vol'])}\n"
                   f"<i>{esc('Koin ini termasuk aset digital yang disebut SEC (dasar opsi ETF di Nasdaq ISE). Itu belum berarti ETF-nya sudah ada, dan gerakan harga belum tentu karena kabar ini.')}</i>")
    return out


# ================================================================ main
def main():
    dry = "--dry-run" in sys.argv
    state = load_state()
    if bantu and not dry:
        try:
            bantu.refresh_if_old()                  # perbarui peta narasi maks seminggu sekali
        except Exception as e:
            print("peta narasi gagal diperbarui:", type(e).__name__)
    tick, err = S.okx_tickers()
    msgs = []
    if state.get("started") is None and not dry:
        msgs.append("<b>✅ INTEL PLUS AKTIF ✅</b>\nDipantau: stablecoin besar, posisi terlalu ramai (funding), "
                    "koin bangun tidur, dan wallet pilihan di wallets.json. Alert hanya muncul kalau ada kejadian.")
    mods = [("stablecoin", lambda: check_stablecoins(state))]
    if tick:
        mods += [("funding", lambda: check_funding(tick, state)),
                 ("dormant", lambda: check_dormant(tick, state)),
                 ("sec", lambda: check_sec_watch(tick, state)),
                 ("wallets", lambda: check_wallets(tick, state))]
    else:
        print("OKX tidak bisa diakses:", err)
    for name, fn in mods:
        try:
            msgs += fn()
        except Exception as e:
            print("error", name, type(e).__name__, e)
    state["started"] = True
    if len(msgs) > MAX_ALERTS_PER_RUN:
        extra = len(msgs) - MAX_ALERTS_PER_RUN
        msgs = msgs[:MAX_ALERTS_PER_RUN] + [f"<i>Masih ada {extra} alert lain di run ini (dipotong supaya tidak spam).</i>"]
    for m in msgs:
        if dry or not (S.TG_TOKEN and S.TG_CHAT):
            print(m + "\n" + "-" * 40)
        else:
            S.send_routed(m, "intel")
    if not dry:
        save_state(state)
    if not msgs:
        print("Tidak ada kejadian baru.")


if __name__ == "__main__":
    main()
