"""ringkasan_image.py - gambar "Narasi | ringkasan harian" + caption Telegram.

Pemakaian paling singkat:

    from ringkasan_image import render_ringkasan, build_caption

    hot = [
        {"name": "Lending/Borrowing Protocols", "ret_24h": 5.9, "flow_pct": 5,
         "coins": ["Aave", "Morpho", "Quantixai"]},
        {"name": "Tokenized Stocks", "ret_24h": 2.8, "flow_pct": 39, "streak_days": 4,
         "coins": ["Spacex Bstocks Tokenized Stock", "Circle Internet Group Bstock"]},
    ]
    cold = [{"name": "The Boy's Club", "ret_24h": -14.3}, {"name": "Frog-Themed", "ret_24h": -14.0}]
    trending = ["ZIG Finance (ZIG)", "Pudgy Penguins (PENGU)", "Zcash (ZEC)"]

    png = render_ringkasan(hot, cold, trending, market_24h=-2.6)     # bytes PNG, atau None bila gagal
    caption = build_caption(hot, cold, market_24h=-2.6)
    if png:
        bot.send_photo(chat_id, png, caption=caption)
    else:
        bot.send_message(chat_id, caption)

Satuan data: semua angka dalam PERSEN (5.9 berarti +5,9%). Field boleh kosong.
  ret_24h     perubahan narasi 24 jam (%)
  vs_market   selisih terhadap pasar dalam poin; jika kosong dihitung ret_24h - market_24h
  flow_pct    angka "uang masuk % dari market cap" dari botmu
  streak_days isi HANYA jika botmu menandai narasi naik berhari-hari berturut-turut (label
              "lebih meyakinkan" ditampilkan untuk narasi ini)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, List, Optional, Tuple

try:  # dipakai sebagai bagian paket
    from . import imgkit as K
except ImportError:  # dipakai sebagai file biasa di folder yang sama
    import imgkit as K

log = logging.getLogger("bot_images")

MAX_HOT = 8
MAX_COLD = 5
MAX_TREND = 12
FLOW_LABEL = "uang masuk"   # samakan dengan label di botmu (mis. "perputaran volume")
DEFAULT_HOW_TO_READ = (
    "Narasi yang naik berhari-hari dengan uang masuk besar lebih layak dipantau daripada "
    "yang naik satu hari saja (sering cuma pump). Cek koinnya di screener dan hindari "
    "koin bervolume tipis."
)


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@dataclass
class Hot:
    name: str
    ret_24h: Optional[float] = None
    vs_market: Optional[float] = None
    flow_pct: Optional[float] = None
    coins: List[str] = field(default_factory=list)
    streak_days: Optional[int] = None


@dataclass
class Cold:
    name: str
    ret_24h: Optional[float] = None


def _first(d, keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _names(v) -> List[str]:
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


def _as_dict(obj):
    if hasattr(obj, "keys"):
        return dict(obj)
    raise TypeError("tipe data tidak didukung: %s" % type(obj).__name__)


def normalize_hot(obj: Any, market: Optional[float]) -> Hot:
    d = _as_dict(obj)
    ret = K.num(_first(d, ("ret_24h", "r24h", "change_24h")))
    vs = K.num(_first(d, ("vs_market", "vs_pasar")))
    if vs is None and ret is not None and market is not None:
        vs = ret - market
    streak = K.num(_first(d, ("streak_days", "streak", "hari_beruntun")))
    return Hot(
        name=str(_first(d, ("name", "narasi", "narrative")) or "?").strip() or "?",
        ret_24h=ret, vs_market=vs,
        flow_pct=K.num(_first(d, ("flow_pct", "uang_masuk_pct", "inflow_pct"))),
        coins=_names(_first(d, ("coins", "koin", "top_coins"))),
        streak_days=int(streak) if (streak is not None and streak >= 2) else None,
    )


def normalize_cold(obj: Any) -> Cold:
    d = _as_dict(obj)
    return Cold(
        name=str(_first(d, ("name", "narasi", "narrative")) or "?").strip() or "?",
        ret_24h=K.num(_first(d, ("ret_24h", "r24h", "change_24h"))),
    )


def _prep(items: Iterable[Any], fn, key, reverse: bool, limit: int) -> list:
    out = []
    for obj in (items or []):
        try:
            out.append(fn(obj))
        except Exception as exc:
            log.warning("Item dilewati (%r): %s", obj, exc)
    # None selalu di akhir; sort stabil
    out.sort(key=lambda n: (key(n) is None, -(key(n) or 0.0) if reverse else (key(n) or 0.0)))
    return out[: max(0, limit)]


# --------------------------------------------------------------------------- #
# Format
# --------------------------------------------------------------------------- #
def _sign_color(v: Optional[float]):
    if v is None or round(abs(v), 1) == 0:
        return K.MUTED
    return K.GREEN if v > 0 else K.RED


def _poin(v: Optional[float]) -> str:
    if v is None:
        return "-"
    r = round(abs(v), 1)
    body = ("%.1f" % r).replace(".", ",")
    if r == 0:
        return body + " poin"
    return ("+" if v > 0 else K.minus()) + body + " poin"


def _flow(v: float) -> str:
    return K.fmt_pct(v, 0 if abs(v) >= 1 else 1, signed=False)


def _flow_rows(labels: List[str], font, max_w: float, pad_x=12, gap=8):
    rows: List[list] = [[]]
    cur = 0.0
    for lb in labels:
        lb = K.fit_text(lb, font, max_w - 2 * pad_x)
        w = K.text_w(lb, font) + 2 * pad_x
        if rows[-1] and cur + gap + w > max_w:
            rows.append([])
            cur = 0.0
        if rows[-1]:
            cur += gap
        rows[-1].append((lb, w))
        cur += w
    return [r for r in rows if r]


# --------------------------------------------------------------------------- #
# Gambar
# --------------------------------------------------------------------------- #
def _draw_market_strip(cv, x, y, w, market: float) -> float:
    f_l, f_v, f_s = K.get_font(20), K.get_font(22, True), K.get_font(16)
    h = 14 + K.line_h(f_v) + 4 + K.line_h(f_s) + 14
    cv.rrect(x, y, x + w, y + h, r=12, fill=K.PANEL)
    base = y + 14 + K.asc(f_v)
    cv.text(x + 18, base, "Pasar kripto 24 jam", f_l, K.MUTED)
    cv.text(x + w - 18, base, K.fmt_pct(market, 1), f_v, _sign_color(market), "rs")
    cv.text(x + 18, y + 14 + K.line_h(f_v) + 4 + K.asc(f_s), "Narasi dinilai terhadap angka ini", f_s, K.MUTED)
    return h


def _draw_hot_card(cv, x, y, w, hot: List[Hot], h=None) -> float:
    if h is None and not cv.measure:
        h = _draw_hot_card(K.MEASURE, x, y, w, hot)
    if h is not None and not cv.measure:
        cv.rrect(x, y, x + w, y + h, r=16, fill=K.CARD, outline=K.CARD_BORDER, width=1)

    x0, x1 = x + 20, x + w - 20
    iw = x1 - x0
    f_n, f_m, f_c, f_p = K.get_font(21, True), K.get_font(17), K.get_font(16), K.get_font(15, True)
    if not hot:
        hh = 64.0
        f = K.get_font(18)
        cv.text(x0, y + (hh - K.line_h(f)) / 2 + K.asc(f), "Tidak ada narasi panas hari ini", f, K.MUTED)
        return hh

    cur = y + 4
    for i, n in enumerate(hot):
        if i > 0:
            cv.hrule(x0, x1, cur, fill=K.RULE)
        cur += 14
        pct = K.fmt_pct(n.ret_24h, 1)
        pw = K.text_w(pct, f_n)
        base = cur + K.asc(f_n)
        cv.text(x0, base, K.fit_text(n.name, f_n, iw - pw - 16), f_n, K.TEXT)
        cv.text(x1, base, pct, f_n, _sign_color(n.ret_24h), "rs")
        cur += K.line_h(f_n)

        if n.streak_days:
            cur += 6
            label = K.fit_text("Naik %d hari berturut-turut · lebih meyakinkan" % n.streak_days, f_p, iw - 24)
            ph = K.line_h(f_p) + 8
            cv.rrect(x0, cur, x0 + K.text_w(label, f_p) + 24, cur + ph, r=8, fill=K.GREEN_BG)
            cv.text(x0 + 12, cur + 4 + K.asc(f_p), label, f_p, K.GREEN)
            cur += ph

        pieces: List[Tuple[str, tuple]] = []
        if n.vs_market is not None:
            pieces += [("vs pasar ", K.MUTED), (_poin(n.vs_market), _sign_color(n.vs_market))]
        if n.flow_pct is not None:
            if pieces:
                pieces.append((" · ", K.MUTED))
            pieces += [(FLOW_LABEL + " ", K.MUTED), (_flow(n.flow_pct), K.TEXT), (" dari market cap", K.MUTED)]
        if pieces:
            cur += 6
            base = cur + K.asc(f_m)
            if sum(K.text_w(t, f_m) for t, _ in pieces) <= iw:
                tx = x0
                for t, c in pieces:
                    cv.text(tx, base, t, f_m, c)
                    tx += K.text_w(t, f_m)
            else:
                cv.text(x0, base, K.fit_text("".join(t for t, _ in pieces), f_m, iw), f_m, K.MUTED)
            cur += K.line_h(f_m)

        if n.coins:
            cur += 4
            for line in K.wrap_text("Koin besar: " + ", ".join(n.coins), f_c, iw, max_lines=2):
                cv.text(x0, cur + K.asc(f_c), line, f_c, K.MUTED)
                cur += K.line_h(f_c) + 2
        cur += 14
    cur += 4
    return cur - y


def _draw_cold_card(cv, x, y, w, cold: List[Cold], h=None) -> float:
    if h is None and not cv.measure:
        h = _draw_cold_card(K.MEASURE, x, y, w, cold)
    if h is not None and not cv.measure:
        cv.rrect(x, y, x + w, y + h, r=16, fill=K.CARD, outline=K.CARD_BORDER, width=1)

    x0, x1 = x + 20, x + w - 20
    f, fb = K.get_font(20), K.get_font(20, True)
    if not cold:
        hh = 64.0
        cv.text(x0, y + (hh - K.line_h(f)) / 2 + K.asc(f), "Tidak ada narasi dingin hari ini", f, K.MUTED)
        return hh
    row_h = 46.0
    cur = y + 6
    for i, n in enumerate(cold):
        if i > 0:
            cv.hrule(x0, x1, cur, fill=K.RULE)
        pct = K.fmt_pct(n.ret_24h, 1)
        base = cur + (row_h - K.line_h(f)) / 2 + K.asc(f)
        cv.text(x0, base, K.fit_text(n.name, f, (x1 - x0) - K.text_w(pct, fb) - 16), f, K.TEXT)
        cv.text(x1, base, pct, fb, _sign_color(n.ret_24h), "rs")
        cur += row_h
    cur += 6
    return cur - y


def _draw_trend_card(cv, x, y, w, items: List[str], h=None) -> float:
    if h is None and not cv.measure:
        h = _draw_trend_card(K.MEASURE, x, y, w, items)
    if h is not None and not cv.measure:
        cv.rrect(x, y, x + w, y + h, r=16, fill=K.CARD, outline=K.CARD_BORDER, width=1)

    x0, x1 = x + 16, x + w - 16
    f = K.get_font(17)
    chip_h = K.line_h(f) + 12
    cur = y + 16
    for row in _flow_rows(items, f, x1 - x0):
        cx = x0
        for lb, wd in row:
            cv.rrect(cx, cur, cx + wd, cur + chip_h, r=9, fill=K.CHIP)
            cv.text(cx + 12, cur + 6 + K.asc(f), lb, f, K.TEXT)
            cx += wd + 8
        cur += chip_h + 8
    cur += 8
    return cur - y


def _draw_note_card(cv, x, y, w, text: str, h=None) -> float:
    if h is None and not cv.measure:
        h = _draw_note_card(K.MEASURE, x, y, w, text)
    if h is not None and not cv.measure:
        cv.rrect(x, y, x + w, y + h, r=16, fill=K.CARD, outline=K.CARD_BORDER, width=1)

    x0, x1 = x + 20, x + w - 20
    f_t, f = K.get_font(17, True), K.get_font(17)
    cur = y + 18
    cv.text(x0, cur + K.asc(f_t), "Cara baca", f_t, K.AMBER)
    cur += K.line_h(f_t) + 8
    for line in K.wrap_text(text, f, x1 - x0, max_lines=8):
        cv.text(x0, cur + (25 - K.line_h(f)) / 2 + K.asc(f), line, f, K.SUB)
        cur += 25
    cur += 12
    return cur - y


def _render(cv, hot: List[Hot], cold: List[Cold], trending: List[str], market: Optional[float],
            ts: Optional[datetime], how_to_read: str) -> float:
    W, P = cv.width, 28
    f_title, f_sub, f_lbl, f_foot = K.get_font(26, True), K.get_font(17), K.get_font(17), K.get_font(15)

    y = P
    y += K.watermark(cv, P, y)
    base = y + K.asc(f_title)
    cv.text(P, base, "Narasi · ringkasan harian", f_title, K.TEXT)
    cv.text(W - P, base, K.fmt_stamp(ts), f_sub, K.MUTED, "rs")
    y += K.line_h(f_title) + 16

    if market is not None:
        y += _draw_market_strip(cv, P, y, W - 2 * P, market) + 18

    def label(text):
        nonlocal y
        cv.text(P, y + K.asc(f_lbl), text, f_lbl, K.MUTED)
        y += K.line_h(f_lbl) + 8

    label("Narasi panas · 24 jam")
    y += _draw_hot_card(cv, P, y, W - 2 * P, hot) + 22

    label("Narasi dingin · 24 jam")
    y += _draw_cold_card(cv, P, y, W - 2 * P, cold) + 22

    if trending:
        label("Koin trending di CoinGecko")
        y += _draw_trend_card(cv, P, y, W - 2 * P, trending) + 22

    if how_to_read:
        y += _draw_note_card(cv, P, y, W - 2 * P, how_to_read) + 22

    cv.text(W / 2, y + K.asc(f_foot), "Hijau = naik · merah = turun · Bukan nasihat finansial",
            f_foot, K.MUTED, "ms")
    y += K.line_h(f_foot)
    return y + P


# --------------------------------------------------------------------------- #
# API publik
# --------------------------------------------------------------------------- #
def _prepare(hot, cold, trending, market):
    mk = K.num(market)
    hots = _prep(hot, lambda o: normalize_hot(o, mk), lambda n: n.ret_24h, True, MAX_HOT)
    colds = _prep(cold, normalize_cold, lambda n: n.ret_24h, False, MAX_COLD)
    trend = _names(trending)[:MAX_TREND]
    return hots, colds, trend, mk


def render_ringkasan(hot: Iterable[Any], cold: Iterable[Any] = (), trending: Iterable[Any] = (),
                     market_24h: Optional[float] = None, timestamp: Optional[datetime] = None,
                     how_to_read: Optional[str] = DEFAULT_HOW_TO_READ,
                     raise_errors: bool = False) -> Optional[bytes]:
    """Buat gambar ringkasan harian. Mengembalikan bytes PNG, atau None bila gagal
    (kecuali raise_errors=True). how_to_read=None/"" menyembunyikan kotak 'Cara baca'."""
    try:
        hots, colds, trend, mk = _prepare(hot, cold, trending, market_24h)
        note = "" if not how_to_read else str(how_to_read)

        def draw(cv):
            return _render(cv, hots, colds, trend, mk, timestamp, note)

        return K.render(draw)
    except Exception:
        if raise_errors:
            raise
        log.exception("Gagal membuat gambar ringkasan")
        return None


def build_caption(hot: Iterable[Any], cold: Iterable[Any] = (), market_24h: Optional[float] = None,
                  timestamp: Optional[datetime] = None) -> str:
    """Caption Telegram ringkas (maks 1024 karakter)."""
    hots, colds, _trend, mk = _prepare(hot, cold, (), market_24h)
    date = K.fmt_stamp(timestamp).split(" · ")[0]
    parts = ["Ringkasan narasi %s" % date]
    if mk is not None:
        parts.append("Pasar %s (24 jam)" % K.fmt_pct(mk, 1))
    if hots:
        parts.append("Panas: " + ", ".join("%s %s" % (n.name, K.fmt_pct(n.ret_24h, 1)) for n in hots[:3]))
    if colds:
        parts.append("Dingin: " + ", ".join("%s %s" % (n.name, K.fmt_pct(n.ret_24h, 1)) for n in colds[:2]))
    text = ". ".join(parts) + ". Bukan nasihat finansial."
    return text if len(text) <= 1024 else text[:1020] + "..."


def ringkasan_message(hot: Iterable[Any], cold: Iterable[Any] = (), trending: Iterable[Any] = (),
                      market_24h: Optional[float] = None, **kwargs) -> Tuple[Optional[bytes], str]:
    """Praktis: (png_bytes_atau_None, caption). kwargs diteruskan ke render_ringkasan."""
    cap = {k: kwargs[k] for k in ("timestamp",) if k in kwargs}
    return render_ringkasan(hot, cold, trending, market_24h, **kwargs), build_caption(hot, cold, market_24h, **cap)
