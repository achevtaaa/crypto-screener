"""narasi_image.py - gambar "Peta narasi" + caption Telegram.

Pemakaian paling singkat:

    from narasi_image import render_narasi, build_caption, pick_hot, prepare_sectors

    sectors = prepare_sectors([
        {"name": "Layer 2", "ret_7d": -7.5, "ret_24h": -0.1, "n_coins": 12},
        {"name": "Meme",    "ret_7d": -9.2, "ret_24h": 0.8,  "n_coins": 30},
        ...
    ], min_coins=3)                                  # sektor dengan < 3 koin dibuang

    hot = pick_hot([
        {"name": "Boy's Club", "ret_24h": 5.3, "coins": ["Pepe", "Ape And Pepe", "Based Brett"]},
        {"name": "Crypto-Backed Tokens", "ret_24h": 17.5, "coins": ["Wrapped Steth", "Wrapped Bitcoin"]},
        ...
    ], market_24h=-4.3, limit=3)                     # token pembungkus dibuang, hanya yang di atas pasar

    png = render_narasi(sectors, hot, market_24h=-4.3)      # bytes PNG, atau None bila gagal
    caption = build_caption(sectors, hot, market_24h=-4.3)
    if png:
        bot.send_photo(chat_id, png, caption=caption)
    else:
        bot.send_message(chat_id, caption)               # cadangan: teks saja

Satuan data: semua angka dalam PERSEN (-7.5 berarti -7,5%). Semua field boleh None.
Catatan basis hitung: market_24h sebaiknya memakai basis yang sama dengan sektor
(mis. median semua koin), bukan total market cap, bila ingin dibandingkan langsung.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, List, Optional, Sequence, Tuple

try:  # dipakai sebagai bagian paket
    from . import imgkit as K
except ImportError:  # dipakai sebagai file biasa di folder yang sama
    import imgkit as K

log = logging.getLogger("bot_images")

# Token pembungkus / staking / stablecoin yang tidak boleh ikut dihitung sebagai "narasi".
# Tambahkan sendiri jika ada yang lolos.
WRAPPED_SYMBOLS = {
    "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "CBETH", "RETH", "SWETH", "METH",
    "EZETH", "RSETH", "WBETH", "WBNB", "WSOL", "JITOSOL", "MSOL", "BNSOL",
    "USDT", "USDC", "DAI", "USDE", "FDUSD", "TUSD", "PYUSD", "USDS", "USD1",
}
WRAPPED_NAME_WORDS = ("wrapped", "bridged", "staked", "restaked")


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@dataclass
class Sector:
    name: str
    ret_7d: Optional[float] = None
    ret_24h: Optional[float] = None
    n_coins: Optional[int] = None


@dataclass
class Narrative:
    name: str
    ret_24h: Optional[float] = None
    coins: List[str] = field(default_factory=list)


def _first(d, keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def normalize_sector(obj: Any) -> Sector:
    if isinstance(obj, Sector):
        d = asdict(obj)
    elif hasattr(obj, "keys"):
        d = dict(obj)
    else:
        raise TypeError("tipe data sektor tidak didukung: %s" % type(obj).__name__)
    n = K.num(d.get("n_coins"))
    return Sector(
        name=str(_first(d, ("name", "sektor", "sector")) or "?").strip() or "?",
        ret_7d=K.num(_first(d, ("ret_7d", "r7d", "change_7d"))),
        ret_24h=K.num(_first(d, ("ret_24h", "r24h", "change_24h"))),
        n_coins=None if n is None else int(n),
    )


def _coin_names(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    out: List[str] = []
    try:
        for x in v:
            s = str(x).strip()
            if s:
                out.append(s)
    except TypeError:
        return []
    return out


def normalize_narrative(obj: Any) -> Narrative:
    if isinstance(obj, Narrative):
        d = asdict(obj)
    elif hasattr(obj, "keys"):
        d = dict(obj)
    else:
        raise TypeError("tipe data narasi tidak didukung: %s" % type(obj).__name__)
    return Narrative(
        name=str(_first(d, ("name", "narasi", "narrative")) or "?").strip() or "?",
        ret_24h=K.num(_first(d, ("ret_24h", "r24h", "change_24h"))),
        coins=_coin_names(_first(d, ("coins", "koin", "top_coins"))),
    )


# --------------------------------------------------------------------------- #
# Pembersihan data (opsional tapi disarankan)
# --------------------------------------------------------------------------- #
def is_wrapper_token(symbol: str = "", name: str = "") -> bool:
    """True untuk token pembungkus/staking/stablecoin (Wrapped Bitcoin, stETH, USDT, dst)."""
    sym = (symbol or "").strip().upper()
    nm = (name or "").strip().lower()
    if sym in WRAPPED_SYMBOLS:
        return True
    return any(w in nm for w in WRAPPED_NAME_WORDS)


def prepare_sectors(sectors: Iterable[Any], min_coins: int = 3) -> List[Sector]:
    """Normalisasi sektor dan buang yang jumlah koinnya (jika diketahui) kurang dari min_coins."""
    out: List[Sector] = []
    for obj in (sectors or []):
        try:
            s = normalize_sector(obj)
        except Exception as exc:
            log.warning("Sektor dilewati (%r): %s", obj, exc)
            continue
        if s.n_coins is not None and s.n_coins < min_coins:
            continue
        out.append(s)
    return out


def pick_hot(narratives: Iterable[Any], market_24h: Optional[float] = None, limit: int = 3) -> List[Narrative]:
    """Pilih narasi 'panas': naik > 0 dan di atas pasar, tanpa token pembungkus. Urut dari terpanas."""
    base = K.num(market_24h)
    threshold = 0.0 if base is None else max(base, 0.0)
    out: List[Narrative] = []
    for obj in (narratives or []):
        try:
            n = normalize_narrative(obj)
        except Exception as exc:
            log.warning("Narasi dilewati (%r): %s", obj, exc)
            continue
        if n.ret_24h is None or n.ret_24h <= threshold or is_wrapper_token("", n.name):
            continue
        if n.coins:
            kept = [c for c in n.coins if not is_wrapper_token(c, c)]
            if not kept:  # semua koinnya token pembungkus -> bukan narasi nyata
                continue
            n.coins = kept
        out.append(n)
    out.sort(key=lambda n: -(n.ret_24h or 0.0))
    try:
        lim = max(0, int(limit))
    except (TypeError, ValueError):
        lim = 3
    return out[:lim]


# --------------------------------------------------------------------------- #
# Gambar
# --------------------------------------------------------------------------- #
def _sign_color(v: Optional[float]):
    if v is None or round(abs(v), 1) == 0:
        return K.MUTED
    return K.GREEN if v > 0 else K.RED


def _sorted_sectors(sectors: List[Sector]) -> List[Sector]:
    return sorted(sectors, key=lambda s: (s.ret_7d is None, -(s.ret_7d if s.ret_7d is not None else 0.0)))


def _draw_market(cv, x, y, w, market_24h: float, label: str = "Pasar 24 jam") -> float:
    h = 52.0
    cv.rrect(x, y, x + w, y + h, r=12, fill=K.PANEL)
    f_l, f_v = K.get_font(20), K.get_font(20, True)
    base = y + (h - K.line_h(f_l)) / 2 + K.asc(f_l)
    cv.text(x + 18, base, K.fit_text(label, f_l, w - 36 - 120), f_l, K.MUTED)
    cv.text(x + w - 18, base, K.fmt_pct(market_24h, 1), f_v, _sign_color(market_24h), "rs")
    return h


def _draw_sector_card(cv, x, y, w, sectors: List[Sector], h=None) -> float:
    if h is None and not cv.measure:
        h = _draw_sector_card(K.MEASURE, x, y, w, sectors)
    if h is not None and not cv.measure:
        cv.rrect(x, y, x + w, y + h, r=16, fill=K.CARD, outline=K.CARD_BORDER, width=1)

    x0, x1 = x + 20, x + w - 20
    name_w, w7, w24, gap = 150, 100, 92, 14
    r24 = x1
    r7 = x1 - w24 - gap
    bar0 = x0 + name_w + gap
    bar1 = r7 - w7 - gap
    f_h, f_r = K.get_font(16), K.get_font(21)

    cur = y + 8
    head_h = 38.0
    base = cur + (head_h - K.line_h(f_h)) / 2 + K.asc(f_h)
    cv.text(x0, base, "Sektor", f_h, K.MUTED)
    cv.text(r7, base, "7 hari", f_h, K.MUTED, "rs")
    cv.text(r24, base, "24 jam", f_h, K.MUTED, "rs")
    cur += head_h

    values = [abs(s.ret_7d) for s in sectors if s.ret_7d is not None]
    max_abs = max(values) if values else 0.0
    row_h = 50.0
    for i, s in enumerate(sectors):
        cv.hrule(x0, x1, cur, fill=K.RULE)
        base = cur + (row_h - K.line_h(f_r)) / 2 + K.asc(f_r)
        cv.text(x0, base, K.fit_text(s.name, f_r, name_w), f_r, K.TEXT)
        by = cur + (row_h - 10) / 2
        cv.rrect(bar0, by, bar1, by + 10, r=5, fill=K.TRACK)
        if s.ret_7d is not None and max_abs > 0 and s.ret_7d != 0:
            frac = min(1.0, abs(s.ret_7d) / max_abs) * 0.98
            col = K.BAR_GREEN if s.ret_7d > 0 else K.BAR_RED
            cv.rrect(bar0, by, bar0 + max(10.0, (bar1 - bar0) * frac), by + 10, r=5, fill=col)
        cv.text(r7, base, K.fmt_pct(s.ret_7d, 1), f_r, _sign_color(s.ret_7d), "rs")
        cv.text(r24, base, K.fmt_pct(s.ret_24h, 1), f_r, _sign_color(s.ret_24h), "rs")
        cur += row_h
    cur += 8
    return cur - y


def _draw_hot_card(cv, x, y, w, hot: List[Narrative], h=None) -> float:
    if h is None and not cv.measure:
        h = _draw_hot_card(K.MEASURE, x, y, w, hot)
    if h is not None and not cv.measure:
        cv.rrect(x, y, x + w, y + h, r=16, fill=K.CARD, outline=K.CARD_BORDER, width=1)

    x0, x1 = x + 20, x + w - 20
    f_n, f_p, f_c, f_e = K.get_font(21, True), K.get_font(21, True), K.get_font(16), K.get_font(18)
    if not hot:
        hh = 64.0
        cv.text(x0, y + (hh - K.line_h(f_e)) / 2 + K.asc(f_e), "Tidak ada narasi di atas pasar saat ini", f_e, K.MUTED)
        return hh

    cur = y + 4
    for i, n in enumerate(hot):
        if i > 0:
            cv.hrule(x0, x1, cur, fill=K.RULE)
        cur += 14
        pct = K.fmt_pct(n.ret_24h, 1)
        pw = K.text_w(pct, f_p)
        base = cur + K.asc(f_n)
        cv.text(x0, base, K.fit_text(n.name, f_n, (x1 - x0) - pw - 16), f_n, K.TEXT)
        cv.text(x1, base, pct, f_p, _sign_color(n.ret_24h), "rs")
        cur += K.line_h(f_n)
        if n.coins:
            cur += 4
            cv.text(x0, cur + K.asc(f_c), K.fit_text(", ".join(n.coins[:3]), f_c, x1 - x0), f_c, K.MUTED)
            cur += K.line_h(f_c)
        cur += 14
    cur += 4
    return cur - y


def _best_worst(sectors: List[Sector]) -> Tuple[Optional[Sector], Optional[Sector]]:
    ranked = [s for s in _sorted_sectors(sectors) if s.ret_7d is not None]
    if not ranked:
        return None, None
    return ranked[0], (ranked[-1] if len(ranked) > 1 else None)


def _best_label(s: Sector) -> str:
    return "Terkuat" if (s.ret_7d or 0) > 0 else "Paling tahan"


def _draw_summary(cv, x, y, w, best: Sector, worst: Optional[Sector], h=None) -> float:
    if h is None and not cv.measure:
        h = _draw_summary(K.MEASURE, x, y, w, best, worst)
    if h is not None and not cv.measure:
        cv.rrect(x, y, x + w, y + h, r=16, fill=K.CARD, outline=K.CARD_BORDER, width=1)

    f = K.get_font(19)
    f_b = K.get_font(19, True)
    pitch, pad = 33.0, 14.0
    x0 = x + 20
    cur = y + pad
    lines = [(_best_label(best), K.GREEN, best)]
    if worst is not None:
        lines.append(("Paling lemah", K.RED, worst))
    for label, col, s in lines:
        base = cur + (pitch - K.line_h(f)) / 2 + K.asc(f)
        cv.text(x0, base, label, f_b, col)
        tx = x0 + K.text_w(label, f_b) + 10
        nm = K.fit_text(s.name, f, w - 40 - (tx - x0) - 100)
        cv.text(tx, base, nm, f, K.TEXT)
        tx += K.text_w(nm, f) + 10
        cv.text(tx, base, K.fmt_pct(s.ret_7d, 1), f, K.TEXT)
        cur += pitch
    return cur - y + pad - 2


def _render(cv, sectors: List[Sector], hot: Optional[List[Narrative]], market_24h: Optional[float],
            ts: Optional[datetime], subtitle: str, market_label: str = "Pasar 24 jam") -> float:
    W, P = cv.width, 28
    f_title, f_sub, f_lbl, f_foot = K.get_font(26, True), K.get_font(17), K.get_font(17), K.get_font(15)

    y = P
    y += K.watermark(cv, P, y)
    base = y + K.asc(f_title)
    cv.text(P, base, "Peta narasi", f_title, K.TEXT)
    cv.text(W - P, base, K.fmt_stamp(ts), f_sub, K.MUTED, "rs")
    y += K.line_h(f_title) + 2
    cv.text(P, y + K.asc(f_sub), subtitle, f_sub, K.MUTED)
    y += K.line_h(f_sub) + 16

    if market_24h is not None:
        y += _draw_market(cv, P, y, W - 2 * P, market_24h, market_label) + 18

    ordered = _sorted_sectors(sectors)
    if ordered:
        y += _draw_sector_card(cv, P, y, W - 2 * P, ordered)
    else:
        h = 80.0
        cv.rrect(P, y, W - P, y + h, r=16, fill=K.CARD, outline=K.CARD_BORDER, width=1)
        f = K.get_font(19)
        cv.text(W / 2, y + h / 2 + K.asc(f) / 2 - 2, "Data sektor belum tersedia", f, K.MUTED, "ms")
        y += h

    if hot is not None:  # None = data narasi gagal diambil -> blok disembunyikan (bukan "kosong")
        y += 22
        cv.text(P, y + K.asc(f_lbl), "Panas 24 jam · di atas pasar", f_lbl, K.MUTED)
        y += K.line_h(f_lbl) + 8
        y += _draw_hot_card(cv, P, y, W - 2 * P, hot)

    best, worst = _best_worst(sectors)
    if best is not None:
        y += 18
        y += _draw_summary(cv, P, y, W - 2 * P, best, worst)

    y += 22
    cv.text(W / 2, y + K.asc(f_foot),
            "Hijau = uang masuk · merah = uang keluar · Bukan nasihat finansial", f_foot, K.MUTED, "ms")
    y += K.line_h(f_foot)
    return y + P


# --------------------------------------------------------------------------- #
# API publik
# --------------------------------------------------------------------------- #
def _norm_sectors(sectors: Iterable[Any]) -> List[Sector]:
    out: List[Sector] = []
    for obj in (sectors or []):
        try:
            out.append(normalize_sector(obj))
        except Exception as exc:
            log.warning("Sektor dilewati (%r): %s", obj, exc)
    return out


def _norm_hot(hot: Iterable[Any], limit: int) -> List[Narrative]:
    out: List[Narrative] = []
    for obj in (hot or []):
        try:
            out.append(normalize_narrative(obj))
        except Exception as exc:
            log.warning("Narasi dilewati (%r): %s", obj, exc)
    try:
        return out[: max(0, int(limit))]
    except (TypeError, ValueError):
        return out[:3]


def render_narasi(sectors: Iterable[Any], hot: Iterable[Any] = (), market_24h: Optional[float] = None,
                  timestamp: Optional[datetime] = None, subtitle: str = "Median performa koin per sektor",
                  hot_limit: int = 3, raise_errors: bool = False,
                  market_label: str = "Pasar 24 jam") -> Optional[bytes]:
    """Buat gambar peta narasi. Mengembalikan bytes PNG, atau None bila gagal
    (kecuali raise_errors=True). `hot` sebaiknya hasil pick_hot(); hot=None menyembunyikan
    blok 'Panas 24 jam' (dipakai bila data narasi gagal diambil)."""
    try:
        secs = _norm_sectors(sectors)
        hots = None if hot is None else _norm_hot(hot, hot_limit)
        mk = K.num(market_24h)

        def draw(cv):
            return _render(cv, secs, hots, mk, timestamp, str(subtitle), str(market_label))

        return K.render(draw)
    except Exception:
        if raise_errors:
            raise
        log.exception("Gagal membuat gambar narasi")
        return None


def build_caption(sectors: Iterable[Any], hot: Iterable[Any] = (), market_24h: Optional[float] = None,
                  hot_limit: int = 3) -> str:
    """Caption Telegram ringkas (maks 1024 karakter)."""
    secs = _norm_sectors(sectors)
    hots = _norm_hot(hot, hot_limit)
    mk = K.num(market_24h)
    best, worst = _best_worst(secs)

    parts: List[str] = []
    if mk is not None:
        parts.append("Pasar %s (24 jam)" % K.fmt_pct(mk, 1))
    if best is not None:
        parts.append("%s: %s (%s / 7 hari)" % (_best_label(best), best.name, K.fmt_pct(best.ret_7d, 1)))
    if hots:
        parts.append("Panas 24 jam: " + ", ".join("%s %s" % (n.name, K.fmt_pct(n.ret_24h, 1)) for n in hots))
    if worst is not None:
        parts.append("Paling lemah: %s (%s / 7 hari)" % (worst.name, K.fmt_pct(worst.ret_7d, 1)))
    text = (". ".join(parts) + "." if parts else "Peta narasi belum tersedia.") + " Bukan nasihat finansial."
    return text if len(text) <= 1024 else text[:1020] + "..."


def narasi_message(sectors: Iterable[Any], hot: Iterable[Any] = (), market_24h: Optional[float] = None,
                   **kwargs) -> Tuple[Optional[bytes], str]:
    """Praktis: (png_bytes_atau_None, caption). kwargs diteruskan ke render_narasi."""
    cap = {k: kwargs[k] for k in ("hot_limit",) if k in kwargs}
    return render_narasi(sectors, hot, market_24h, **kwargs), build_caption(sectors, hot, market_24h, **cap)
