"""
Helper bersama untuk semua bot:
  - translate_id(teks): terjemah Inggris -> Indonesia (Google gtx, cadangan MyMemory; kalau gagal, teks asli)
  - narasi_label(koin): label narasi + emoji, contoh "🤖 AI | 🐸 Meme" (dari narasi_map.json)
  - refresh_if_old(): perbarui narasi_map.json dari CoinGecko (maks seminggu sekali)
"""
import json
import os
import time

import requests

MAP_FILE = "narasi_map.json"
REFRESH_DAYS = 7
CG_KEY = os.getenv("COINGECKO_API_KEY")

# (id kategori CoinGecko, emoji, nama Indonesia). Kategori yang id-nya tidak ada akan dilewati sendiri.
NARASI = [
    ("artificial-intelligence", "🤖", "AI"),
    ("ai-agents", "🧠", "Agen AI"),
    ("meme-token", "🐸", "Meme"),
    ("real-world-assets-rwa", "🏛️", "RWA"),
    ("depin", "📡", "DePIN"),
    ("gaming", "🎮", "Gaming"),
    ("decentralized-finance-defi", "💱", "DeFi"),
    ("layer-2", "🧱", "Layer 2"),
    ("privacy-coins", "🕶️", "Privasi"),
    ("zero-knowledge-zk", "🔐", "ZK"),
    ("oracle", "🔮", "Oracle"),
    ("storage", "💾", "Penyimpanan"),
    ("metaverse", "🌐", "Metaverse"),
    ("bitcoin-ecosystem", "₿", "Ekosistem Bitcoin"),
    ("decentralized-exchange", "💠", "DEX"),
    ("perpetuals", "📊", "Perp DEX"),
]
_INFO = {cid: (emo, nama) for cid, emo, nama in NARASI}
_map = None
_cache = {}


# ================================================================ terjemah
def translate_id(text):
    text = (text or "").strip()
    if not text:
        return text
    if text in _cache:
        return _cache[text]
    out = None
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params={"client": "gtx", "sl": "en", "tl": "id", "dt": "t", "q": text}, timeout=10)
        if r.status_code == 200:
            out = "".join(seg[0] for seg in r.json()[0] if seg and seg[0]).strip()
    except Exception:
        out = None
    if not out:
        try:
            r = requests.get("https://api.mymemory.translated.net/get",
                             params={"q": text, "langpair": "en|id"}, timeout=10)
            if r.status_code == 200:
                out = (r.json().get("responseData", {}).get("translatedText") or "").strip()
                if "MYMEMORY WARNING" in out.upper():
                    out = None
        except Exception:
            out = None
    _cache[text] = out or text
    return _cache[text]


# ================================================================ narasi koin
def _load():
    global _map
    if _map is None:
        try:
            with open(MAP_FILE) as f:
                _map = json.load(f)
        except Exception:
            _map = {}
    return _map


def narasi_label(base, limit=3):
    """base = simbol koin, contoh 'RENDER'. Mengembalikan '' kalau tidak ada data."""
    ids = _load().get("map", {}).get(str(base).upper(), [])
    parts = [f"{_INFO[i][0]} {_INFO[i][1]}" for i in ids if i in _INFO][:limit]
    return " | ".join(parts)


def _cg(params):
    headers = {"accept": "application/json"}
    if CG_KEY:
        headers["x-cg-demo-api-key"] = CG_KEY
    for _ in range(3):
        try:
            r = requests.get("https://api.coingecko.com/api/v3/coins/markets", params=params,
                             headers=headers, timeout=20)
            if r.status_code == 429:
                time.sleep(30)
                continue
            return r.json() if r.status_code == 200 else None
        except Exception:
            time.sleep(3)
    return None


def refresh_if_old(force=False):
    """Perbarui peta narasi. Mengembalikan True kalau file diperbarui."""
    global _map
    cur = _load()
    if not force and cur.get("updated") and (time.time() - cur["updated"]) < REFRESH_DAYS * 86400:
        return False
    new, ok = {}, 0
    for cid, _, _ in NARASI:
        rows = _cg({"vs_currency": "usd", "category": cid, "order": "market_cap_desc", "per_page": 100, "page": 1})
        time.sleep(3)
        if not isinstance(rows, list) or not rows:
            continue
        ok += 1
        for c in rows:
            sym = (c.get("symbol") or "").upper()
            if sym:
                new.setdefault(sym, []).append(cid)
    if ok < 5:
        print("peta narasi: terlalu sedikit kategori berhasil, tidak disimpan")
        return False
    _map = {"updated": time.time(), "map": new}
    with open(MAP_FILE, "w") as f:
        json.dump(_map, f)
    return True
