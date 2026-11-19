"""
Pemeriksaan tambahan untuk alert volume: apakah ada tanda dana nyata atau rawan manipulasi?

Sumber (gratis, tanpa kunci):
  - DexScreener : likuiditas, volume, jumlah transaksi beli vs jual, umur pasangan, FDV di DEX
  - OKX         : perubahan open interest dan funding (apakah kenaikan didorong leverage)

PENTING: ini INDIKASI dari data publik, bukan bukti. Data per dompet (siapa pemegang besar, apakah satu pihak
mengontrol banyak dompet) butuh alat berbayar atau pengecekan manual (Bubblemaps, explorer chain).

  lines(base, price, vol24_cex) -> daftar baris HTML siap ditempel ke kartu alert (aman: tidak pernah melempar error)
"""
import time

import requests

import inti as S

DEX_SEARCH = "https://api.dexscreener.com/latest/dex/search"
LIQ_THIN = 300_000
LIQ_OK = 1_000_000
NEW_PAIR_DAYS = 14
FDV_LIQ_RATIO = 200
BUY_SELL_GOOD = 1.2
BUY_SELL_BAD = 0.8


def usd(x):
    x = float(x or 0)
    if x >= 1e9:
        return f"${x / 1e9:.2f} miliar"
    if x >= 1e6:
        return f"${x / 1e6:.1f} juta"
    if x >= 1e3:
        return f"${x / 1e3:.0f} ribu"
    return f"${x:.0f}"


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def dex_info(sym, price):
    """Pasangan DEX dengan likuiditas terbesar yang simbol DAN harganya cocok dengan koin di bursa (menyaring token tiruan)."""
    try:
        r = requests.get(DEX_SEARCH, params={"q": sym}, timeout=15)
        if r.status_code != 200:
            return None
        pairs = r.json().get("pairs") or []
    except Exception:
        return None
    best = None
    for p in pairs:
        try:
            if str(p["baseToken"]["symbol"]).upper() != sym.upper():
                continue
            px = _f(p.get("priceUsd"))
            if price and px and abs(px / price - 1) > 0.2:
                continue                                    # harga beda jauh: kemungkinan token lain bernama sama
            liq = _f((p.get("liquidity") or {}).get("usd"))
            if best is None or liq > best["liq"]:
                tx24, tx1 = (p.get("txns") or {}).get("h24") or {}, (p.get("txns") or {}).get("h1") or {}
                created = _f(p.get("pairCreatedAt"))
                best = {"chain": p.get("chainId", "?"), "dex": p.get("dexId", "?"), "liq": liq,
                        "vol24": _f((p.get("volume") or {}).get("h24")), "vol1": _f((p.get("volume") or {}).get("h1")),
                        "b24": _f(tx24.get("buys")), "s24": _f(tx24.get("sells")),
                        "b1": _f(tx1.get("buys")), "s1": _f(tx1.get("sells")),
                        "fdv": _f(p.get("fdv")), "age": (time.time() - created / 1000) / 86400 if created else None}
        except Exception:
            continue
    return best


def nilai(info, deriv):
    """-> (penilaian, daftar_alasan). Heuristik sederhana, bukan bukti."""
    risk, ok, why = 0, 0, []
    if info:
        if info["liq"] < LIQ_THIN:
            risk += 2
            why.append(f"likuiditas DEX tipis ({usd(info['liq'])}), mudah digerakkan segelintir pihak")
        elif info["liq"] < LIQ_OK:
            risk += 1
            why.append(f"likuiditas DEX sedang ({usd(info['liq'])})")
        else:
            ok += 1
        if info["age"] is not None and info["age"] < NEW_PAIR_DAYS:
            risk += 1
            why.append(f"pasangan DEX baru ({info['age']:.0f} hari)")
        if info["fdv"] and info["liq"] and info["fdv"] / info["liq"] > FDV_LIQ_RATIO:
            risk += 1
            why.append("nilai total token (FDV) jauh lebih besar dari likuiditasnya")
        ratio = info["b24"] / max(1.0, info["s24"])
        if info["b24"] + info["s24"] >= 100:
            if ratio >= BUY_SELL_GOOD:
                ok += 1
            elif ratio <= BUY_SELL_BAD:
                risk += 1
                why.append("transaksi jual lebih banyak dari beli di DEX")
    if deriv:
        oi, fr = deriv.get("oi_chg"), deriv.get("funding")
        if oi is not None and fr is not None and oi > 30 and fr > 0.05:
            risk += 1
            why.append("derivatif panas: funding tinggi dan open interest naik tajam (didorong leverage)")
    if info is None and not (deriv and (deriv.get("oi_chg") is not None or deriv.get("funding") is not None)):
        return "⚪ Belum bisa dinilai (data tidak ada)", why
    if risk >= 2:
        return "🔴 Rawan manipulasi", why
    if risk == 1 or ok < 2:
        return "🟡 Campuran, hati-hati", why
    return "🟢 Terlihat lebih sehat", why


def lines(base, price, vol24_cex):
    try:
        info = dex_info(base, price)
        try:
            deriv = S.okx_deriv(base)
        except Exception:
            deriv = None
        out = []
        if info:
            out.append(f"🧾 DEX ({S.esc(info['chain'])}): likuiditas {usd(info['liq'])} | volume 24 jam {usd(info['vol24'])} "
                       f"| beli:jual {info['b24']:.0f}:{info['s24']:.0f}")
        else:
            out.append("🧾 " + S.esc("Pasangan DEX yang cocok tidak ditemukan (koin mungkin hanya di bursa terpusat), data on-chain tidak tersedia."))
        if deriv and (deriv.get("oi_chg") is not None or deriv.get("funding") is not None):
            parts = []
            if deriv.get("oi_chg") is not None:
                parts.append(f"open interest {deriv['oi_chg']:+.0f}% (24 jam)")
            if deriv.get("funding") is not None:
                parts.append(f"funding {deriv['funding']:+.3f}%")
            out.append("📉 Derivatif: " + " | ".join(parts))
        verdict, why = nilai(info, deriv)
        emo, _, txt = verdict.partition(" ")
        out.append(f"{emo} Indikasi: <b>{S.esc(txt)}</b>")           # lingkaran warna di depan = hijau / kuning / merah
        for w in why[:3]:
            out.append(f"⚠️ {S.esc(w)}")
        out.append("<i>" + S.esc("Ini indikasi dari data publik, bukan bukti. Cek pemegang besar manual di Bubblemaps atau explorer chain.") + "</i>")
        return out
    except Exception as e:
        print("onchain gagal:", type(e).__name__)
        return []
