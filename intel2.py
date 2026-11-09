#!/usr/bin/env python3
"""
Intel Plus: pelengkap whales.py. Semua alert masuk ke topik Intel dengan judul berbeda tiap jenis.

  💵 STABLECOIN         pencetakan / pembakaran USDT & USDC besar dalam 24 jam (DefiLlama)
  🧨 POSISI TERLALU RAMAI  funding ekstrem di koin besar (OKX), rawan squeeze
  💤 KOIN BANGUN TIDUR  volume 24 jam melonjak di koin yang lama sepi dan harganya sideways (OKX)
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
DORM_COOLDOWN_H = 72
DORM_MAX_ALERTS = 3

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
def check_dormant(tick, state):
    last = state.get("dormant_scan")
    if hours_since(last) < DORM_EVERY_H:
        return []
    state["dormant_scan"] = time.time()
    cool = state.setdefault("dormant", {})
    uni = sorted(((b, t) for b, t in tick.items() if not S.excluded(b) and t["vol"] >= DORM_MIN_VOL),
                 key=lambda x: x[1]["vol"], reverse=True)[:DORM_TOP]
    hits = []
    for base, t in uni:
        if hours_since(cool.get(base)) < DORM_COOLDOWN_H:
            continue
        c = S.okx_candles(t["id"], "1d")
        time.sleep(0.15)
        if not c or len(c) < 60:
            continue
        vols = sorted(x[3] for x in c[-60:-3])
        med = vols[len(vols) // 2]
        if med < DORM_MIN_MED or med <= 0:
            continue
        ratio = t["vol"] / med
        if ratio < DORM_RATIO:
            continue
        hi, lo = max(x[0] for x in c[-30:]), min(x[1] for x in c[-30:])
        rng = hi / lo - 1 if lo > 0 else 9
        if rng > DORM_MAX_RANGE:
            continue
        if not (DORM_CHG_MIN <= t["chg"] <= DORM_CHG_MAX):
            continue
        chg7 = (t["last"] / c[-7][2] - 1) * 100 if c[-7][2] else 0
        if chg7 > DORM_MAX_7D:
            continue
        cvd = S.cvd_proxy(c, 5)[0]
        risk = S.risk_flags(c, t["vol"])
        hits.append((ratio, base, t, med, rng, chg7, cvd, risk))
    hits.sort(reverse=True, key=lambda x: x[0])
    out = []
    for ratio, base, t, med, rng, chg7, cvd, risk in hits[:DORM_MAX_ALERTS]:
        cool[base] = time.time()
        support = cvd >= S.CVD_POSITIVE
        lines = [f"<b>💤 KOIN BANGUN TIDUR 💤</b>",
                 f"<b><u>{esc(base)}</u></b> volume 24 jam {money(t['vol'])} = <b>{ratio:.1f}x</b> normal ({money(med)}/hari)",
                 f"Harga {t['last']:g} | 24j {t['chg']:+.1f}% | 7 hari {chg7:+.1f}% | rentang 30 hari hanya {rng * 100:.0f}%",
                 f"CVD spot (proxy, 5 hari): {cvd:+.2f} " + ("(tekanan beli mendukung)" if support else "(BELUM mendukung: bisa jadi distribusi)")]
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


# ================================================================ main
def main():
    dry = "--dry-run" in sys.argv
    state = load_state()
    tick, err = S.okx_tickers()
    msgs = []
    if state.get("started") is None and not dry:
        msgs.append("<b>✅ INTEL PLUS AKTIF ✅</b>\nDipantau: stablecoin besar, posisi terlalu ramai (funding), "
                    "koin bangun tidur, dan wallet pilihan di wallets.json. Alert hanya muncul kalau ada kejadian.")
    mods = [("stablecoin", lambda: check_stablecoins(state))]
    if tick:
        mods += [("funding", lambda: check_funding(tick, state)),
                 ("dormant", lambda: check_dormant(tick, state)),
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
