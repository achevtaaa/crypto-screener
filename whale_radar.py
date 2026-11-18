"""
Radar Beli Whale: koin apa yang sedang DIBELI banyak whale (bukan satu koin yang itu-itu saja).

Memantau 60 trader teratas Hyperliquid (bukan 7), lalu membandingkan posisi mereka dengan pemindaian sebelumnya.
  - 🐋 WHALE BELI : koin (selain BTC/ETH) yang ditambah LONG oleh banyak whale sekaligus, atau satu whale sangat besar.
                   Satu koin tidak diulang selama 12 jam.
  - 🐋 PETA POSISI WHALE (harian, 09.00 WIB): koin paling banyak di-LONG dan di-SHORT, beserta perubahan dari kemarin.

Dipanggil dari whales.py (check(state, baseline)). Datanya dari posisi perpetual Hyperliquid, bukan seluruh pasar.
"""
import time
from datetime import datetime, timedelta, timezone

import whales as W

WIB = timezone(timedelta(hours=7))
N_RADAR = 60
MIN_ACCT = 300_000
MIN_ROI = 0.02
ADD_MIN_USD = 100_000          # satu whale dihitung "menambah" kalau eksposur LONG naik minimal segini
MIN_WHALES = 3                 # alert kalau minimal segini whale menambah, dan ...
MIN_TOTAL = 1_000_000          # ... totalnya minimal segini
SOLO_TOTAL = 3_000_000         # atau satu whale saja menambah sebesar ini
COOLDOWN_H = 12
MAX_ALERTS = 4
DIGEST_HOUR = 9
DIGEST_ROWS = 8
MAJORS = {"BTC", "ETH"}        # tidak dialert per koin (terlalu umum), tetap muncul di peta harian
STABLE = {"USDC", "USDT", "USDE", "USDH", "DAI"}


def norm(coin):
    """kPEPE -> PEPE (Hyperliquid memberi awalan k untuk koin berharga sangat kecil)."""
    c = str(coin)
    return c[1:] if len(c) > 2 and c[0] == "k" and c[1:].isupper() else c


def pick():
    j = W.get_json(W.HL_BOARD)
    if not j:
        return None
    rows = j.get("leaderboardRows", j) if isinstance(j, dict) else j
    cands = []
    for row in rows:
        try:
            acct = float(row["accountValue"])
            pnl, roi = W._month_perf(row)
        except Exception:
            continue
        if acct >= MIN_ACCT and roi >= MIN_ROI and pnl > 0:
            cands.append((pnl, row["ethAddress"]))
    cands.sort(reverse=True)
    return [a for _, a in cands[:N_RADAR]]


def exposure(pos):
    ex = {}
    for c, p in pos.items():
        k = norm(c)
        ex[k] = ex.get(k, 0.0) + (p["usd"] if p["side"] == "LONG" else -p["usd"])
    return ex


def tally(book):
    """book = {addr: {koin: eksposur}} -> {koin: (jumlah_long, jumlah_short, net_usd)}"""
    out = {}
    for ex in book.values():
        for c, v in ex.items():
            if c in STABLE or abs(v) < W.MIN_POS_USD:
                continue
            l, s, n = out.get(c, (0, 0, 0.0))
            out[c] = (l + (v > 0), s + (v < 0), n + v)
    return out


def buy_alert(coin, buyers, stat):
    total = sum(d for _, d in buyers)
    l, s, net = stat.get(coin, (0, 0, 0.0))
    biggest = max(d for _, d in buyers)
    lines = [f"<b>🐋 WHALE BELI 🐋</b>", f"<b><u>{W.esc(coin)}</u></b>",
             f"📈 {len(buyers)} whale menambah LONG sejak pemindaian terakhir | total <b>+{W.money(total)}</b>",
             f"📊 Posisi whale sekarang: {l} LONG, {s} SHORT | net {'LONG' if net >= 0 else 'SHORT'} {W.money(abs(net))}",
             f"💰 Penambahan terbesar satu whale: {W.money(biggest)}",
             "<i>" + W.esc("Datanya dari 60 trader teratas Hyperliquid, bukan seluruh pasar. Whale bisa saja sedang hedging "
                           "atau menutup short. Cek chart dulu, pastikan koin ada di Bybit, pakai ukuran posisi kecil.") + "</i>"]
    return "\n".join(lines)


def digest(stat, prev_net, n_whales):
    now = datetime.now(WIB)
    rows = [f"<b>🐋 WHALE | PETA POSISI HARIAN</b> ({W.esc(now.strftime('%d %b %Y'))})",
            W.esc(f"Dari {n_whales} trader teratas Hyperliquid. Urut dari jumlah whale yang memegang.")]

    def fmt(c, l, s, n):
        ch = ""
        if c in prev_net:
            d = n - prev_net[c]
            if abs(d) >= 100_000:
                ch = f" ({'naik' if d > 0 else 'turun'} {W.money(abs(d))} dari kemarin)"
        return W.esc(f"• {c}: {l} LONG, {s} SHORT | net {'LONG' if n >= 0 else 'SHORT'} {W.money(abs(n))}{ch}")

    longs = sorted(((c, *v) for c, v in stat.items() if v[2] > 0), key=lambda x: (x[1], x[3]), reverse=True)[:DIGEST_ROWS]
    shorts = sorted(((c, *v) for c, v in stat.items() if v[2] < 0), key=lambda x: (x[2], -x[3]), reverse=True)[:DIGEST_ROWS]
    rows += ["", "<b>PALING BANYAK LONG</b>"] + [fmt(*r) for r in longs]
    if shorts:
        rows += ["", "<b>PALING BANYAK SHORT</b>"] + [fmt(*r) for r in shorts]
    rows += ["", "<i>" + W.esc("Whale bisa salah dan posisinya bisa berubah cepat. Ini bahan pertimbangan, bukan sinyal beli.") + "</i>"]
    return "\n".join(rows)


def check(state, baseline=False):
    st = state.setdefault("radar", {"pos": {}, "cool": {}, "digest": "", "yday": {}})
    addrs = pick()
    if not addrs:
        return []
    now_book = {}
    for a in addrs:
        pos = W.whale_positions(a)
        time.sleep(0.25)
        if pos is not None:
            now_book[a] = exposure(pos)
    if len(now_book) < 10:                                  # data tidak lengkap: jangan simpulkan apa pun
        return []
    prev = st.get("pos", {})
    merged = {a: now_book.get(a, prev.get(a)) for a in addrs if a in now_book or a in prev}
    msgs = []
    ts = time.time()
    if prev and not baseline:
        buyers = {}
        for a, ex in now_book.items():
            p = prev.get(a)
            if p is None:
                continue                                    # whale baru masuk daftar: jadikan acuan saja
            for c in set(ex) | set(p):
                d = ex.get(c, 0.0) - p.get(c, 0.0)
                if d >= ADD_MIN_USD and ex.get(c, 0.0) > 0:
                    buyers.setdefault(c, []).append((a, d))
        stat = tally(merged)
        hits = []
        for c, lst in buyers.items():
            if c in MAJORS or c in STABLE:
                continue
            total = sum(d for _, d in lst)
            if not ((len(lst) >= MIN_WHALES and total >= MIN_TOTAL) or total >= SOLO_TOTAL):
                continue
            if ts - st["cool"].get(c, 0) < COOLDOWN_H * 3600:
                continue
            hits.append((total, c, lst))
        for total, c, lst in sorted(hits, reverse=True)[:MAX_ALERTS]:
            msgs.append(buy_alert(c, lst, stat))
            st["cool"][c] = ts
    st["cool"] = {c: t for c, t in st["cool"].items() if ts - t < 3 * 86400}
    st["pos"] = merged
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    if datetime.now(WIB).hour >= DIGEST_HOUR and st.get("digest") != today and not baseline:
        stat = tally(merged)
        if stat:
            msgs.append(digest(stat, st.get("yday", {}), len(merged)))
            st["yday"] = {c: round(v[2]) for c, v in stat.items()}
            st["digest"] = today
    return msgs
