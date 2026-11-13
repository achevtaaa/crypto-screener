#!/usr/bin/env python3
"""
Whale + News + Wallet Besar -> Telegram, mode "kirim kalau ada kejadian baru".

Dijalankan sering oleh GitHub Actions (lihat whales.yml). Tiap jalan, skrip membandingkan
keadaan sekarang dengan keadaan sebelumnya (whale_state.json) dan HANYA mengirim yang baru:

  🚨 WHALE ALERT 🚨        whale Hyperliquid buka / tutup / balik arah / tambah / kurangi posisi
  ❗️ NEWS DARI (...) ❗️     berita baru dari tokoh/lembaga besar (CZ, Saylor, Vitalik, SEC, Fed, ...)
  🐳 WALLET BESAR 🐳        pemegang terbesar token ERC-20 menambah / menjual banyak (cek tiap 6 jam)

Teks tebal (bold) = judul. Teks miring (italic) = hal penting.
Hari pertama: skrip hanya merekam kondisi awal (tanpa spam), lalu mengirim alert setelah itu.

Pakai:
  pip install requests
  python whales.py --dry-run     # tampil di layar, tidak kirim dan tidak simpan state
  python whales.py               # kirim ke Telegram
"""
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

try:
    import bantu
except ImportError:          # file bantu.py belum diupload: berita tetap jalan tanpa terjemahan
    bantu = None

TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
WIB = timezone(timedelta(hours=7))
STATE_FILE = "whale_state.json"

# ---------- Whale Hyperliquid ----------
HL_INFO = "https://api.hyperliquid.xyz/info"
HL_BOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
N_WHALES = 7
MIN_ACCOUNT = 1_000_000
MIN_MONTH_ROI = 0.02
MIN_POS_USD = 50_000           # posisi lebih kecil dianggap tidak ada
ALERT_MIN_USD = 500_000        # alert hanya untuk posisi sebesar ini ke atas
CHANGE_PCT = 0.30              # tambah/kurangi minimal 30% dianggap kejadian
BIG_USD = 5_000_000            # di atas ini ditandai PENTING (miring)

# ---------- Berita ----------
NEWS_MAX_AGE_H = 6
MAX_NEWS_PER_RUN = 5
FEEDS = [("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
         ("Cointelegraph", "https://cointelegraph.com/rss"),
         ("Decrypt", "https://decrypt.co/feed"),
         ("The Block", "https://www.theblock.co/rss.xml"),
         ("CryptoSlate", "https://cryptoslate.com/feed/"),
         ("Bitcoin Magazine", "https://bitcoinmagazine.com/feed"),
         ("CryptoPotato", "https://cryptopotato.com/feed/"),
         ("BeInCrypto", "https://beincrypto.com/feed/"),
         ("U.Today", "https://u.today/rss"),
         ("Daily Hodl", "https://dailyhodl.com/feed/"),
         ("Bitcoinist", "https://bitcoinist.com/feed/"),
         ("NewsBTC", "https://www.newsbtc.com/feed/"),
         ("CoinGape", "https://coingape.com/feed/")]
# Sumber yang tidak bisa dibuka dilewati otomatis. Satu berita yang sama dari banyak situs hanya dikirim sekali.
# (regex, label, bobot, case_sensitive)
PEOPLE = [
    (r"\bsaylor\b|\bstrategy\b|\bmicrostrategy\b", "Saylor/Strategy", 3, False),
    (r"\bCZ\b|[Cc]hangpeng|\b[Bb]inance\b", "CZ/Binance", 3, True),
    (r"\bvitalik\b|\bbuterin\b", "Vitalik", 3, False),
    (r"\btrump\b|\bwhite house\b|\bbessent\b", "Trump/Gedung Putih", 3, False),
    (r"\bpowell\b|\bfomc\b|\bfederal reserve\b|\bthe fed\b|\brate cut\b|\bCPI\b", "The Fed/makro", 3, False),
    (r"\bblackrock\b|\bfidelity\b|\bvanguard\b|\bETF\b", "BlackRock/ETF", 2, False),
    (r"\bSEC\b|\b[Gg]ensler\b|\b[Aa]tkins\b|\bCFTC\b", "SEC/regulator", 3, True),
    (r"\bgarlinghouse\b|\bripple\b", "Ripple", 2, False),
    (r"\bcoinbase\b|\barmstrong\b", "Coinbase", 2, False),
    (r"\btether\b|\bardoino\b|\bcircle\b", "Tether/Circle", 2, False),
    (r"\belon\b|\bmusk\b", "Elon Musk", 2, False),
    (r"\bhack(ed|s)?\b|\bexploit(ed|s)?\b|\bdrained\b|\bstolen\b", "HACK/EXPLOIT", 3, False),
    # berita ETF / opsi yang menyebut koin di daftar aset digital SEC (selain BTC dan ETH yang sudah terpantau)
    (r"(?=.*\b(?:ETFs?|ETPs?|options)\b)(?=.*\b(?:Solana|XRP|Ripple|Cardano|Dogecoin|Litecoin|Hedera|HBAR|Chainlink|"
     r"Avalanche|Polkadot|Stellar|Aptos|Tezos|Algorand|Shiba|Canton|LBRY|Bitcoin Cash)\b)", "ETF/Opsi koin SEC", 2, False),
]
MAX_ALERTS_PER_RUN = 12
NEWS_MIN_WEIGHT = 2
NEWS_IMPORTANT_WEIGHT = 3

# ---------- Wallet pemegang besar (ERC-20) ----------
ETHPLORER = "https://api.ethplorer.io"
ETHPLORER_KEY = os.getenv("ETHPLORER_KEY", "freekey")
HOLDERS_EVERY_H = 6
HOLDER_TOP = 10
HOLDER_CHANGE = 0.10           # saldo wallet berubah >= 10% dianggap kejadian
TOKENS = {
    "LINK": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
    "UNI": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
    "PEPE": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
    "ONDO": "0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3",
    "ENA": "0x57e114B691Db790C35207b2e685D4A43181e6061",
    "AAVE": "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
    "ARB": "0xB50721BCf8d664c30412Cfbc6cf7a15145234ad1",
    "RENDER": "0x6De037ef9aD2725EB40118Bb1702EBb27e4Aeb24",
}


# ================================================================ util
def esc(x):
    return html.escape(str(x), quote=False)


def money(x):
    a = abs(x)
    s = f"${a / 1e6:.2f}M" if a >= 1e6 else f"${a / 1e3:.0f}K"
    return ("-" if x < 0 else "") + s


def short(addr):
    return addr[:6] + "..." + addr[-4:]


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    with open(STATE_FILE, "w") as f:
        json.dump(st, f)


def get_json(url, **kw):
    for _ in range(3):
        try:
            r = requests.get(url, timeout=30, **kw)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (403, 404, 429):
                break
        except Exception:
            pass
        time.sleep(1.5)
    return None


def hl_post(payload):
    for _ in range(3):
        try:
            r = requests.post(HL_INFO, json=payload, timeout=20)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.5)
    return None


# ================================================================ whale Hyperliquid
def _month_perf(row):
    for name, d in row.get("windowPerformances", []):
        if name == "month":
            return float(d.get("pnl", 0)), float(d.get("roi", 0))
    return 0.0, 0.0


def pick_whales():
    j = get_json(HL_BOARD)
    if not j:
        return None
    rows = j.get("leaderboardRows", j) if isinstance(j, dict) else j
    cands = []
    for row in rows:
        try:
            acct = float(row["accountValue"])
            pnl, roi = _month_perf(row)
        except Exception:
            continue
        if acct >= MIN_ACCOUNT and roi >= MIN_MONTH_ROI and pnl > 0:
            cands.append((pnl, row["ethAddress"], acct, row.get("displayName")))
    cands.sort(reverse=True)
    return cands[:N_WHALES]


def whale_positions(addr):
    st = hl_post({"type": "clearinghouseState", "user": addr})
    if st is None:
        return None
    out = {}
    for ap in st.get("assetPositions", []):
        p = ap.get("position", {})
        try:
            size, val = float(p["szi"]), float(p.get("positionValue", 0))
            if size == 0 or val < MIN_POS_USD:
                continue
            liq = p.get("liquidationPx")
            out[p["coin"]] = {"side": "LONG" if size > 0 else "SHORT", "usd": val,
                              "entry": float(p.get("entryPx") or 0),
                              "pnl": float(p.get("unrealizedPnl") or 0),
                              "lev": (p.get("leverage") or {}).get("value"),
                              "liq": float(liq) if liq not in (None, "") else None}
        except Exception:
            continue
    return out



ROT_MIN_USD = 500_000          # perubahan BTC minimal segini dianggap rotasi


def _net(pos, coins):
    return sum((p["usd"] if p["side"] == "LONG" else -p["usd"]) for c, p in pos.items() if c in coins)


def rotation_alert(addr, name, prev, pos):
    """Whale mengurangi BTC lalu menambah alt (atau sebaliknya) pada pembaruan yang sama."""
    btc_p, btc_n = _net(prev, {"BTC"}), _net(pos, {"BTC"})
    alts = (set(prev) | set(pos)) - {"BTC"}
    alt_p, alt_n = _net(prev, alts), _net(pos, alts)
    d_btc, d_alt = btc_n - btc_p, alt_n - alt_p
    if d_btc <= -ROT_MIN_USD and d_alt >= ROT_MIN_USD * 0.5:
        arah, ket = "BTC -> ALT", "Whale ini mengurangi BTC dan menambah alt."
    elif d_btc >= ROT_MIN_USD and d_alt <= -ROT_MIN_USD * 0.5:
        arah, ket = "ALT -> BTC", "Whale ini mengurangi alt dan menambah BTC (tanda lari ke aman)."
    else:
        return None
    who = short(addr) + (f" ({esc(name)})" if name else "")
    top = sorted(((c, _net(pos, {c}) - _net(prev, {c})) for c in alts), key=lambda x: abs(x[1]), reverse=True)[:3]
    det = ", ".join(f"{esc(c)} {money(abs(v))} {'tambah' if v > 0 else 'kurang'}" for c, v in top if abs(v) >= 50_000)
    text = (f"<b>🔄 ROTASI WHALE 🔄</b>\n<b>{arah}</b>\n{who}\n"
            f"BTC: {money(d_btc)} | Alt (bersih): {money(d_alt)}\n")
    if det:
        text += f"Perubahan terbesar: {det}\n"
    return text + f"<i>{ket} Satu whale bukan tren pasar, cek dulu sebelum mengikuti.</i>"


def whale_msg(action, addr, name, coin, now, prev, big):
    who = f"{short(addr)}" + (f" ({esc(name)})" if name else "")
    lines = [f"<b>🚨 WHALE ALERT 🚨</b>", f"<b>{esc(action)}</b>"]
    d = now or prev
    detail = f"{esc(coin)} {d['side']}" + (f" {d['lev']}x" if d.get("lev") else "") + f" {money(d['usd'])}"
    if now:
        detail += f" | entry {now['entry']:g}"
        if now.get("liq"):
            detail += f" | liq {now['liq']:g}"
        detail += f" | PnL {money(now['pnl'])}"
    lines.append(detail)
    lines.append(f"Wallet: {esc(addr)}" if big else f"Wallet: {who}")
    text = "\n".join(lines)
    if big:
        text += "\n<i>PENTING: posisi besar, perhatikan koin ini.</i>"
    return text


def check_whales(state, baseline):
    alerts = []
    picked = pick_whales()
    if not picked:
        return alerts, None
    wstate = state.setdefault("whales", {})
    for pnl, addr, acct, name in picked:
        pos = whale_positions(addr)
        time.sleep(0.3)
        if pos is None:
            continue
        prev = wstate.get(addr)
        wstate[addr] = pos
        if prev is None or baseline:
            continue                                   # rekam kondisi awal tanpa alert
        rot = rotation_alert(addr, name, prev, pos)
        if rot:
            alerts.append(rot)
        for coin in set(pos) | set(prev):
            n, p = pos.get(coin), prev.get(coin)
            if n and not p and n["usd"] >= ALERT_MIN_USD:
                alerts.append(whale_msg(f"BUKA {n['side']} {coin}", addr, name, coin, n, None, n["usd"] >= BIG_USD))
            elif p and not n and p["usd"] >= ALERT_MIN_USD:
                alerts.append(whale_msg(f"TUTUP {p['side']} {coin}", addr, name, coin, None, p, p["usd"] >= BIG_USD))
            elif n and p and n["side"] != p["side"] and max(n["usd"], p["usd"]) >= ALERT_MIN_USD:
                alerts.append(whale_msg(f"BALIK ARAH {coin}: {p['side']} -> {n['side']}", addr, name, coin, n, p, True))
            elif n and p and n["usd"] >= ALERT_MIN_USD:
                ch = n["usd"] / p["usd"] - 1 if p["usd"] else 0
                if ch >= CHANGE_PCT:
                    alerts.append(whale_msg(f"TAMBAH {n['side']} {coin} (+{ch * 100:.0f}%)", addr, name, coin, n, p, n["usd"] >= BIG_USD))
                elif ch <= -CHANGE_PCT:
                    alerts.append(whale_msg(f"KURANGI {n['side']} {coin} ({ch * 100:.0f}%)", addr, name, coin, n, p, p["usd"] >= BIG_USD))
    return alerts, picked


# ================================================================ berita
def fetch_feed(name, url):
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
    except Exception:
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if title:
            items.append({"src": name, "title": title, "link": link, "ts": ts})
    return items


IMPACT = {
    "Saylor/Strategy": "Pembelian BTC oleh Strategy biasanya dibaca positif (permintaan institusi). Penjualan atau tekanan pendanaan dibaca negatif.",
    "CZ/Binance": "Berita Binance (listing, regulasi, keamanan) menggerakkan koin terkait dan sentimen bursa. Listing biasanya pump singkat lalu sering berbalik.",
    "Vitalik": "Pernyataan atau penjualan ETH oleh Vitalik memengaruhi sentimen ETH dan ekosistemnya. Usulan teknis biasanya berdampak jangka panjang.",
    "Trump/Gedung Putih": "Kebijakan atau pernyataan politik AS menggeser sentimen risk-on / risk-off dan koin terkait politik. Reaksi sering cepat dan berbalik.",
    "The Fed/makro": "Sinyal suku bunga lebih ketat menekan kripto, lebih longgar mendukung. Lihat jadwal dan skenario di topik Pasar.",
    "BlackRock/ETF": "Arus dana ETF dan institusi adalah sumber permintaan besar. Arus masuk atau persetujuan positif, arus keluar negatif.",
    "SEC/regulator": "Gugatan atau penolakan regulator biasanya negatif untuk koin yang disebut. Kejelasan aturan atau persetujuan biasanya positif.",
    "Ripple": "Perkembangan kasus atau produk Ripple terutama menggerakkan XRP dan koin sejenis.",
    "Coinbase": "Listing di Coinbase biasanya pump singkat. Masalah hukum atau keamanan menekan sentimen.",
    "Tether/Circle": "Isu cadangan stablecoin memengaruhi kepercayaan pasar. Penerbitan baru yang besar sering dibaca sebagai masuknya likuiditas.",
    "Elon Musk": "Pernyataan Musk bisa memicu lonjakan jangka pendek di koin meme, sering berbalik cepat.",
    "ETF/Opsi koin SEC": "Produk ETF atau opsi baru untuk sebuah koin membuka pintu dana institusi, tapi baru terasa setelah produknya benar-benar listing dan ada arus dana. Berita saja sering sudah masuk harga.",
    "HACK/EXPLOIT": "Biasanya negatif untuk token atau protokol terkait dan sentimen DeFi. Pantau jumlah dana, ada tidaknya perbaikan, dan bursa yang menghentikan deposit.",
}


def impact_line(labels):
    for lb in labels:
        if lb in IMPACT:
            return "\nDampak (pola umum, bukan ramalan): " + esc(IMPACT[lb])
    return ""


def check_news(state, baseline):
    seen = set(state.get("news_seen", []))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=NEWS_MAX_AGE_H)
    found, keys, tok_list = [], set(), []
    for name, url in FEEDS:
        for it in fetch_feed(name, url):
            key = re.sub(r"\W+", "", it["title"].lower())[:70]
            if key in keys:
                continue
            toks = set(re.findall(r"[a-z0-9]{3,}", it["title"].lower()))
            if any(len(toks & o) / max(1, len(toks | o)) >= 0.6 for o in tok_list):
                continue                                   # berita yang sama dari situs lain
            keys.add(key)
            tok_list.append(toks)
            labels, weight = [], 0
            for pat, label, w, cs in PEOPLE:
                if re.search(pat, it["title"], 0 if cs else re.I):
                    labels.append(label)
                    weight += w
            it["key"], it["labels"], it["w"] = key, labels, weight
            found.append(it)
    alerts = []
    fresh = [it for it in found if it["key"] not in seen and it["ts"] >= cutoff and it["w"] >= NEWS_MIN_WEIGHT]
    fresh.sort(key=lambda x: (-x["w"], -x["ts"].timestamp()))
    if not baseline:
        for it in fresh[:MAX_NEWS_PER_RUN]:
            who = ", ".join(dict.fromkeys(it["labels"]))
            title = esc(bantu.translate_id(it["title"]) if bantu else it["title"])      # judul diterjemahkan ke Indonesia
            if it["w"] >= NEWS_IMPORTANT_WEIGHT:
                title = f"<i>{title}</i>"                  # penting = miring
            t = it["ts"].astimezone(WIB).strftime("%H:%M")
            alerts.append(f"<b>❗️ NEWS DARI ({esc(who)}) ❗️</b>\n{title}\n{esc(it['src'])} {t} WIB"
                          f"{impact_line(it['labels'])}\n{esc(it['link'])}")
    state["news_seen"] = (list(seen | {it["key"] for it in found}))[-600:]
    return alerts, bool(found)


# ================================================================ wallet pemegang besar
def check_holders(state, baseline):
    last = state.get("holders_ts", 0)
    if time.time() - last < HOLDERS_EVERY_H * 3600:
        return []
    alerts, hstate, okcount = [], state.setdefault("holders", {}), 0
    for sym, addr in TOKENS.items():
        info = get_json(f"{ETHPLORER}/getTokenInfo/{addr}", params={"apiKey": ETHPLORER_KEY})
        time.sleep(0.6)
        if not info or str(info.get("symbol", "")).upper() != sym:
            continue                                      # alamat tidak cocok / API gagal: lewati
        j = get_json(f"{ETHPLORER}/getTopTokenHolders/{addr}",
                     params={"apiKey": ETHPLORER_KEY, "limit": HOLDER_TOP})
        time.sleep(0.6)
        if not j or "holders" not in j:
            continue
        okcount += 1
        now = {h["address"].lower(): {"bal": float(h["balance"]), "share": float(h.get("share", 0))}
               for h in j["holders"]}
        prev = hstate.get(sym)
        hstate[sym] = now
        if prev is None or baseline:
            continue
        for a, d in now.items():
            p = prev.get(a)
            if p is None:
                alerts.append(f"<b>🐳 WALLET BESAR 🐳</b>\n<b>{sym}: wallet baru masuk top {HOLDER_TOP}</b>\n"
                              f"{esc(a)} | {d['share']:.2f}% dari supply\n<i>PENTING: pemegang baru di jajaran terbesar.</i>")
            elif p["bal"] > 0:
                ch = d["bal"] / p["bal"] - 1
                if abs(ch) >= HOLDER_CHANGE:
                    verb = "MENAMBAH" if ch > 0 else "MENJUAL / MEMINDAHKAN"
                    alerts.append(f"<b>🐳 WALLET BESAR 🐳</b>\n<b>{sym}: pemegang besar {verb} ({ch * 100:+.0f}%)</b>\n"
                                  f"{esc(a)} | sekarang {d['share']:.2f}% dari supply\n"
                                  f"<i>PENTING. Wallet ini bisa saja bursa atau kontrak, bukan selalu whale pribadi.</i>")
    if okcount:
        state["holders_ts"] = time.time()
    return alerts


# ================================================================ kirim
def send_routed(chunk, topic):
    """Kirim ke topik grup kalau routes.json ada; kalau belum diatur atau gagal, kirim ke chat pribadi."""
    try:
        with open("routes.json") as f:
            routes = json.load(f)
    except Exception:
        routes = {}
    base = {"text": chunk[:4000], "parse_mode": "HTML", "disable_web_page_preview": "true"}
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    gid, thread = routes.get("group"), routes.get(topic)
    if gid and thread:
        r = requests.post(url, data=dict(base, chat_id=gid, message_thread_id=thread), timeout=15)
        if r.status_code == 200:
            time.sleep(0.5)
            return
        print("kirim ke topik gagal:", r.status_code, "-> kirim ke chat pribadi")
    r = requests.post(url, data=dict(base, chat_id=TG_CHAT), timeout=15)
    r.raise_for_status()
    time.sleep(0.5)


def send(text):
    send_routed(text, "intel")


def main():
    dry = "--dry-run" in sys.argv
    state = load_state()
    baseline = not state.get("started")
    msgs = []
    for name, call in (("news", lambda: check_news(state, baseline)[0]),
                       ("holders", lambda: check_holders(state, baseline)),
                       ("whales", lambda: check_whales(state, baseline)[0])):
        try:
            msgs += call()
        except Exception as e:
            print("error", name, e)

    if baseline:
        msgs = ["<b>✅ PELACAK AKTIF ✅</b>\nWhale Hyperliquid, berita tokoh besar, dan wallet pemegang besar "
                "sedang dipantau. Alert dikirim hanya kalau ada kejadian baru."]
    state["started"] = True

    if len(msgs) > MAX_ALERTS_PER_RUN:
        extra = len(msgs) - MAX_ALERTS_PER_RUN
        msgs = msgs[:MAX_ALERTS_PER_RUN] + [f"<i>Masih ada {extra} alert lain di run ini (dipotong supaya tidak spam).</i>"]
    for m in msgs:
        if dry or not (TG_TOKEN and TG_CHAT):
            print(m + "\n" + "-" * 40)
        else:
            send(m)
    if not dry:
        save_state(state)
    if not msgs:
        print("Tidak ada kejadian baru.")


if __name__ == "__main__":
    main()
