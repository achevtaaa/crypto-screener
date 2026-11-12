"""screener_image.py - gambar hasil screener (5-7 koin) + caption Telegram.

Pemakaian paling singkat:

    from screener_image import render_screener, build_caption

    coins = [
        {"symbol": "NIGHT", "name": "Midnight", "score": 7,
         "stoch_1d": 59, "stoch_4h": 41, "funding_pct": -0.006, "oi_change_pct": 10.4,
         "cvd": "naik", "sl_pct": 4.2, "sl_basis": "ATR 4H"},
        ...
    ]
    market = {"mcap_usd": 2.47e12, "btc_dom": 60.3, "usdt_dom": 7.2, "verdict": "Netral"}

    png = render_screener(coins, market, scanned=56, risk_usd=1.0)   # bytes PNG, atau None bila gagal
    caption = build_caption(coins, market)
    if png:
        bot.send_photo(chat_id, png, caption=caption)
    else:
        bot.send_message(chat_id, caption)        # cadangan: teks saja, bot tidak berhenti

Satuan data (PENTING):
    funding_pct, oi_change_pct, sl_pct, btc_dom, usdt_dom = PERSEN (0.010 berarti 0,010%).
    Jika bot kamu menyimpan funding sebagai pecahan (0.0001), kalikan 100 dulu.
    mcap_usd = dolar penuh (2.47e12 berarti $2,47T).
    Semua field boleh kosong (None); gambar tetap jadi, bagian itu ditampilkan "-" atau dilewati.

Koin dengan skor >= HOT_SCORE (7) diberi ikon 🔥 di samping namanya.
Maksimal MAX_COINS (7) koin tampil; sisanya hanya disebut jumlahnya.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple

try:  # dipakai sebagai bagian paket
    from . import imgkit as K
except ImportError:  # dipakai sebagai file biasa di folder yang sama
    import imgkit as K

log = logging.getLogger("bot_images")

HOT_SCORE = 7          # skor minimal untuk ikon 🔥
MAX_COINS = 7          # jumlah koin maksimal di gambar
STOCH_OVERSOLD = 20    # batas oversold untuk alasan otomatis
OI_DROP_PCT = -3.0     # OI turun sebesar ini dianggap melemah
FUNDING_HIGH_PCT = 0.05

DEFAULT_NOTES = {
    "netral": "Belum ada arah tegas. Ukuran posisi kecil, tunggu konfirmasi.",
    "bullish": "Pasar condong naik. Tetap disiplin di SL.",
    "bearish": "Pasar condong turun. Perkecil ukuran posisi dan hati-hati.",
}


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@dataclass
class Coin:
    symbol: str
    name: str = ""
    score: float = 0.0
    stoch_1d: Optional[float] = None
    stoch_4h: Optional[float] = None
    funding_pct: Optional[float] = None
    oi_change_pct: Optional[float] = None
    cvd: Optional[str] = None                  # "naik" | "turun" | "datar"
    reasons_pos: List[str] = field(default_factory=list)
    reasons_neg: List[str] = field(default_factory=list)
    sl_pct: Optional[float] = None
    sl_basis: str = "ATR 4H"


@dataclass
class Market:
    mcap_usd: Optional[float] = None
    btc_dom: Optional[float] = None
    usdt_dom: Optional[float] = None
    verdict: str = ""                          # contoh: "Netral", "Bullish", "Bearish"
    note: Optional[str] = None


def _first(d, keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _clean_list(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    out: List[str] = []
    try:
        for x in v:
            s = str(x).strip()
            if s[:2] in ("+ ", "- ", "− "):  # buang penanda "+ " / "- " di awal saja
                s = s[2:].strip()
            if s:
                out.append(s)
    except TypeError:
        return []
    return out


def _norm_cvd(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("naik", "up", "rising", "bull", "bullish", "+"):
        return "naik"
    if s in ("turun", "down", "falling", "bear", "bearish", "-"):
        return "turun"
    if s in ("datar", "flat", "netral", "neutral", "sideways", "0"):
        return "datar"
    return None


def normalize_coin(obj: Any) -> Coin:
    """Ubah dict / Coin menjadi Coin yang sudah dibersihkan. Tidak pernah memberi nilai aneh."""
    if isinstance(obj, Coin):
        d = asdict(obj)
    elif hasattr(obj, "keys"):
        d = dict(obj)
    else:
        raise TypeError("tipe data koin tidak didukung: %s" % type(obj).__name__)

    symbol = str(_first(d, ("symbol", "sym", "ticker", "coin", "base")) or "?").strip().upper() or "?"
    name = str(_first(d, ("name", "nama")) or "").strip()
    score = K.num(_first(d, ("score", "skor")))
    score = 0.0 if score is None else max(0.0, min(10.0, score))

    s1, s4 = K.num(d.get("stoch_1d", d.get("stoch1d"))), K.num(d.get("stoch_4h", d.get("stoch4h")))
    pair = d.get("stoch")
    if (s1 is None and s4 is None) and isinstance(pair, (list, tuple)) and len(pair) == 2:
        s1, s4 = K.num(pair[0]), K.num(pair[1])

    sl = K.num(d.get("sl_pct"))
    if sl is not None and sl <= 0:
        sl = None
    basis = d.get("sl_basis")
    basis = "ATR 4H" if basis is None else str(basis).strip()

    return Coin(
        symbol=symbol, name=name, score=score, stoch_1d=s1, stoch_4h=s4,
        funding_pct=K.num(d.get("funding_pct")), oi_change_pct=K.num(d.get("oi_change_pct")),
        cvd=_norm_cvd(d.get("cvd")),
        reasons_pos=_clean_list(d.get("reasons_pos")), reasons_neg=_clean_list(d.get("reasons_neg")),
        sl_pct=sl, sl_basis=basis,
    )


def _norm_verdict(v: str) -> str:
    s = (v or "").strip().lower()
    if s in ("netral", "neutral", "campur", "mixed", "campur / netral"):
        return "netral"
    if "bull" in s or s in ("naik", "positif"):
        return "bullish"
    if "bear" in s or s in ("turun", "negatif"):
        return "bearish"
    return s


def normalize_market(obj: Any) -> Optional[Market]:
    if obj is None:
        return None
    if isinstance(obj, Market):
        d = asdict(obj)
    elif hasattr(obj, "keys"):
        d = dict(obj)
    else:
        return None
    note = _first(d, ("note", "catatan"))
    return Market(
        mcap_usd=K.num(_first(d, ("mcap_usd", "mcap"))),
        btc_dom=K.num(_first(d, ("btc_dom", "btc_d"))),
        usdt_dom=K.num(_first(d, ("usdt_dom", "usdt_d"))),
        verdict=str(_first(d, ("verdict", "kesimpulan")) or "").strip(),
        note=str(note).strip() if note else None,
    )


def auto_reasons(c: Coin) -> Tuple[List[str], List[str]]:
    """Alasan otomatis dari angka indikator (dipakai bila bot tidak mengirim alasan sendiri).
    Ini penjelasan sederhana, bukan skor: skor tetap dari bot kamu."""
    pos: List[str] = []
    neg: List[str] = []
    f, oi = c.funding_pct, c.oi_change_pct
    if f is not None and oi is not None and f < 0 and oi > 0:
        pos.append("OI naik, funding negatif: short ramai, potensi squeeze")
    os1 = c.stoch_1d is not None and c.stoch_1d <= STOCH_OVERSOLD
    os4 = c.stoch_4h is not None and c.stoch_4h <= STOCH_OVERSOLD
    if os1 and os4:
        pos.append("Stoch oversold di 1D dan 4H")
    elif os4:
        pos.append("Stoch oversold di 4H")
    elif os1:
        pos.append("Stoch oversold di 1D")
    elif c.stoch_1d is not None and c.stoch_4h is not None:
        neg.append("Stoch belum oversold, entry bukan di dasar")
    if c.cvd == "naik":
        pos.append("CVD spot naik: ada pembeli nyata")
    if oi is not None and oi <= OI_DROP_PCT:
        neg.append("OI turun: posisi dilepas, tren belum berbalik")
    if c.cvd == "turun":
        neg.append("CVD spot turun: tekanan jual masih ada")
    if f is not None and f >= FUNDING_HIGH_PCT:
        neg.append("Funding positif tinggi: long ramai")
    return pos, neg


def _pick_reasons(c: Coin, max_lines: int) -> List[Tuple[str, str]]:
    pos, neg = (c.reasons_pos, c.reasons_neg) if (c.reasons_pos or c.reasons_neg) else auto_reasons(c)
    neg_sel = neg[:1] if (neg and max_lines >= 2) else []
    pos_sel = pos[: max(0, max_lines - len(neg_sel))]
    return [("+", t) for t in pos_sel] + [("-", t) for t in neg_sel]


# --------------------------------------------------------------------------- #
# Gambar
# --------------------------------------------------------------------------- #
def _chips(c: Coin):
    chips = []
    if c.stoch_1d is not None or c.stoch_4h is not None:
        chips.append([("Stoch %s · %s" % (K.fmt_int(c.stoch_1d), K.fmt_int(c.stoch_4h)), K.TEXT)])
    if c.funding_pct is not None:
        chips.append([("Funding " + K.fmt_pct(c.funding_pct, 3), K.TEXT)])
    if c.oi_change_pct is not None:
        chips.append([("OI " + K.fmt_pct(c.oi_change_pct, 1), K.TEXT)])
    if c.cvd:
        col = {"naik": K.GREEN, "turun": K.RED}.get(c.cvd, K.MUTED)
        chips.append([("CVD spot ", K.TEXT), (c.cvd, col)])
    return chips


def _flow(chips, font, max_w, pad_x=12, gap=8):
    rows: List[list] = [[]]
    cur = 0.0
    for ch in chips:
        w = sum(K.text_w(t, font) for t, _ in ch) + 2 * pad_x
        if rows[-1] and cur + gap + w > max_w:
            rows.append([])
            cur = 0.0
        if rows[-1]:
            cur += gap
        rows[-1].append((ch, w))
        cur += w
    return [r for r in rows if r]


def _sl_texts(c: Coin, risk_usd: float) -> Tuple[str, str]:
    if c.sl_pct is None:
        return "SL ref: belum tersedia", ""
    basis = " (%s)" % c.sl_basis if c.sl_basis else ""
    left = "SL ref ±%s%s" % (K.fmt_pct(c.sl_pct, 1, signed=False), basis)
    size = risk_usd / (c.sl_pct / 100.0)
    right = "Posisi untuk risk %s: ±%s" % (K.fmt_usd(risk_usd), K.fmt_usd(size))
    return left, right


def _draw_card(cv, x, y, w, c: Coin, hot: bool, risk_usd: float, max_reasons: int, h=None) -> float:
    if h is None and not cv.measure:
        h = _draw_card(K.MEASURE, x, y, w, c, hot, risk_usd, max_reasons)
    if h is not None and not cv.measure:
        cv.rrect(x, y, x + w, y + h, r=16, fill=K.CARD, outline=K.CARD_BORDER, width=1)

    padx, padt = 20, 18
    x0, x1 = x + padx, x + w - padx
    iw = x1 - x0
    f_t, f_n = K.get_font(28, True), K.get_font(16)
    f_s, f_s2 = K.get_font(34, True), K.get_font(16)

    top = y + padt
    base = top + K.asc(f_s)
    sc = K.fmt_score(c.score)
    cv.text(x1, base, "/10", f_s2, K.MUTED, "rs")
    sc_block = K.text_w("/10", f_s2) + 3 + K.text_w(sc, f_s)
    cv.text(x1 - K.text_w("/10", f_s2) - 3, base, sc, f_s, K.AMBER, "rs")

    sym = K.fit_text(c.symbol, f_t, iw - sc_block - 24)
    cv.text(x0, base, sym, f_t, K.TEXT)
    cx = x0 + K.text_w(sym, f_t) + 10
    if hot:
        K.draw_fire(cv, cx, base - 25, 26)
        cx += 26 + 8
    if c.name and c.name.upper() != c.symbol.upper():
        avail = x1 - sc_block - 16 - cx
        if avail > 36:
            cv.text(cx, base, K.fit_text(c.name, f_n, avail), f_n, K.MUTED)

    cur = top + K.line_h(f_s) + 8
    bh = 8
    cv.rrect(x0, cur, x1, cur + bh, r=4, fill=K.TRACK)
    ratio = max(0.0, min(1.0, c.score / 10.0))
    if ratio > 0:
        cv.rrect(x0, cur, x0 + max(bh, iw * ratio), cur + bh, r=4, fill=K.AMBER)
    cur += bh + 14

    f_c = K.get_font(17)
    chip_h = K.line_h(f_c) + 10
    rows = _flow(_chips(c), f_c, iw)
    for row in rows:
        cx = x0
        for ch, wd in row:
            cv.rrect(cx, cur, cx + wd, cur + chip_h, r=8, fill=K.CHIP)
            tx = cx + 12
            for t, col in ch:
                cv.text(tx, cur + 5 + K.asc(f_c), t, f_c, col)
                tx += K.text_w(t, f_c)
            cx += wd + 8
        cur += chip_h + 8
    if rows:
        cur += 4

    f_r = K.get_font(17)
    pitch = 26.0
    for sign, text in _pick_reasons(c, max_reasons):
        mark = "+" if sign == "+" else K.minus()
        col = K.GREEN if sign == "+" else K.RED
        line = K.fit_text("%s %s" % (mark, text), f_r, iw)
        cv.text(x0, cur + (pitch - K.line_h(f_r)) / 2 + K.asc(f_r), line, f_r, col)
        cur += pitch

    cur += 6
    cv.hrule(x0, x1, cur, fill=K.CARD_BORDER)
    cur += 1 + 12

    f_l = K.get_font(16)
    left, right = _sl_texts(c, risk_usd)
    base_l = cur + K.asc(f_l)
    rw = K.text_w(right, f_l) if right else 0.0
    cv.text(x0, base_l, K.fit_text(left, f_l, iw - rw - 16), f_l, K.SUB)
    if right:
        cv.text(x1, base_l, right, f_l, K.SUB, "rs")
    cur += K.line_h(f_l) + padt
    return cur - y


def _draw_market(cv, x, y, w, m: Market, h=None) -> float:
    """Strip kondisi pasar. Mengembalikan tinggi (0 bila tidak ada data)."""
    if h is None and not cv.measure:
        h = _draw_market(K.MEASURE, x, y, w, m)
    f_l, f_v = K.get_font(17), K.get_font(19)
    f_b, f_n = K.get_font(15, True), K.get_font(16)
    padx, pady = 18, 14
    x0, x1 = x + padx, x + w - padx
    iw = x1 - x0

    items = []
    if m.mcap_usd is not None:
        items.append("Mcap " + K.fmt_mcap(m.mcap_usd))
    if m.btc_dom is not None:
        items.append("BTC.D " + K.fmt_pct(m.btc_dom, 1, signed=False))
    if m.usdt_dom is not None:
        items.append("USDT.D " + K.fmt_pct(m.usdt_dom, 1, signed=False))
    vkey = _norm_verdict(m.verdict)
    note = m.note or DEFAULT_NOTES.get(vkey)
    if not (items or m.verdict or note):
        return 0.0

    if h is not None and not cv.measure:
        cv.rrect(x, y, x + w, y + h, r=12, fill=K.PANEL)

    cur = y + pady
    bh = K.line_h(f_b) + 10
    row_h = max(K.line_h(f_l), bh)
    cv.text(x0, cur + (row_h - K.line_h(f_l)) / 2 + K.asc(f_l), "Kondisi pasar", f_l, K.MUTED)
    if m.verdict:
        bg, fg = {"bullish": (K.GREEN_BG, K.GREEN), "bearish": (K.RED_BG, K.RED)}.get(vkey, (K.AMBER_BG, K.AMBER))
        label = K.fit_text(m.verdict, f_b, iw * 0.4)
        bw = K.text_w(label, f_b) + 28
        by = cur + (row_h - bh) / 2
        cv.rrect(x1 - bw, by, x1, by + bh, r=8, fill=bg)
        cv.text(x1 - bw / 2, by + (bh - K.line_h(f_b)) / 2 + K.asc(f_b), label, f_b, fg, "ms")
    cur += row_h

    if items:
        cur += 8
        base = cur + K.asc(f_v)
        if len(items) == 1:
            cv.text(x0, base, items[0], f_v, K.TEXT)
        elif len(items) == 2:
            cv.text(x0, base, items[0], f_v, K.TEXT)
            cv.text(x1, base, items[1], f_v, K.TEXT, "rs")
        else:
            cv.text(x0, base, items[0], f_v, K.TEXT)
            cv.text((x0 + x1) / 2, base, items[1], f_v, K.TEXT, "ms")
            cv.text(x1, base, items[2], f_v, K.TEXT, "rs")
        cur += K.line_h(f_v)

    if note:
        cur += 6
        for line in K.wrap_text(note, f_n, iw, max_lines=3):
            cv.text(x0, cur + K.asc(f_n), line, f_n, K.MUTED)
            cur += K.line_h(f_n) + 2
    cur += pady - 2
    return cur - y


def _draw_empty(cv, x, y, w) -> float:
    h = 96.0
    cv.rrect(x, y, x + w, y + h, r=16, fill=K.CARD, outline=K.CARD_BORDER, width=1)
    f = K.get_font(20)
    cv.text(x + w / 2, y + h / 2 + K.asc(f) / 2 - 2, "Tidak ada kandidat saat ini", f, K.MUTED, "ms")
    return h


def _render(cv, coins: List[Coin], hidden: int, market: Optional[Market], scanned: Optional[int],
            risk_usd: float, ts: Optional[datetime], hot_score: float, tf_label: str) -> float:
    W, P, GAP = cv.width, 28, 12
    f_title, f_sub = K.get_font(26, True), K.get_font(17)

    y = P
    y += K.watermark(cv, P, y)
    base = y + K.asc(f_title)
    cv.text(P, base, "Screener buy · oversold", f_title, K.TEXT)
    cv.text(W - P, base, K.fmt_stamp(ts), f_sub, K.MUTED, "rs")
    y += K.line_h(f_title) + 2

    sub = "TF %s" % tf_label
    if scanned is not None:
        sub += " · %d koin dipindai" % scanned
    sub += " · %d kandidat" % (len(coins) + hidden)
    cv.text(P, y + K.asc(f_sub), sub, f_sub, K.MUTED)
    y += K.line_h(f_sub) + 16

    if market is not None:
        mh = _draw_market(cv, P, y, W - 2 * P, market)
        if mh > 0:
            y += mh + 14

    max_reasons = 3 if len(coins) <= 5 else 2
    if not coins:
        y += _draw_empty(cv, P, y, W - 2 * P)
    for i, c in enumerate(coins):
        hot = c.score >= hot_score
        y += _draw_card(cv, P, y, W - 2 * P, c, hot, risk_usd, max_reasons)
        if i < len(coins) - 1:
            y += GAP

    f_note, f_foot = K.get_font(16), K.get_font(15)
    if hidden > 0:
        y += 16
        cv.text(W / 2, y + K.asc(f_note), "+%d kandidat lain tidak ditampilkan" % hidden, f_note, K.MUTED, "ms")
        y += K.line_h(f_note)

    y += 18
    cv.text(W / 2, y + K.asc(f_foot),
            "Skor %s+ = kandidat kuat · Pasang SL sebelum entry · Bukan nasihat finansial"
            % K.fmt_score(hot_score), f_foot, K.MUTED, "ms")
    y += K.line_h(f_foot) + 4

    legend = "Stoch = 1D · 4H"
    any_hot = any(c.score >= hot_score for c in coins)
    base2 = y + K.asc(f_foot)
    if any_hot:
        legend = "= skor %s+ · Stoch = 1D · 4H" % K.fmt_score(hot_score)
        total = 18 + 6 + K.text_w(legend, f_foot)
        sx = (W - total) / 2
        K.draw_fire(cv, sx, base2 - 15, 18)
        cv.text(sx + 24, base2, legend, f_foot, K.MUTED)
    else:
        cv.text(W / 2, base2, legend, f_foot, K.MUTED, "ms")
    y += K.line_h(f_foot)
    return y + P


# --------------------------------------------------------------------------- #
# API publik
# --------------------------------------------------------------------------- #
def _prepare(coins: Sequence[Any], max_coins: int) -> Tuple[List[Coin], int]:
    out: List[Coin] = []
    for obj in (coins or []):
        try:
            out.append(normalize_coin(obj))
        except Exception as exc:
            log.warning("Koin dilewati (%r): %s", obj, exc)
    out.sort(key=lambda c: -c.score)  # urut stabil: skor tertinggi di atas
    try:
        mc = max(1, int(max_coins))
    except (TypeError, ValueError):
        mc = MAX_COINS
    return out[:mc], max(0, len(out) - mc)


def render_screener(coins: Sequence[Any], market: Any = None, scanned: Optional[int] = None,
                    risk_usd: float = 1.0, timestamp: Optional[datetime] = None,
                    hot_score: float = HOT_SCORE, max_coins: int = MAX_COINS,
                    tf_label: str = "1D + 4H", raise_errors: bool = False) -> Optional[bytes]:
    """Buat gambar screener. Mengembalikan bytes PNG, atau None bila gagal
    (kecuali raise_errors=True, untuk debugging)."""
    try:
        shown, hidden = _prepare(coins, max_coins)
        m = normalize_market(market)
        risk = K.num(risk_usd)
        risk = 1.0 if (risk is None or risk <= 0) else risk
        sc = K.num(scanned)
        scanned_i = int(sc) if (sc is not None and sc >= 0) else None
        hs = K.num(hot_score)
        hs = HOT_SCORE if hs is None else hs

        def draw(cv):
            return _render(cv, shown, hidden, m, scanned_i, risk, timestamp, hs, str(tf_label))

        return K.render(draw)
    except Exception:
        if raise_errors:
            raise
        log.exception("Gagal membuat gambar screener")
        return None


def build_caption(coins: Sequence[Any], market: Any = None, hot_score: float = HOT_SCORE,
                  max_coins: int = MAX_COINS) -> str:
    """Caption Telegram (maks 1024 karakter). Koin kuat diberi 🔥."""
    try:
        shown, hidden = _prepare(coins, max_coins)
    except Exception:  # pragma: no cover
        shown, hidden = [], 0
    m = normalize_market(market)
    hs = K.num(hot_score)
    hs = HOT_SCORE if hs is None else hs
    total = len(shown) + hidden
    if total == 0:
        return "Screener BUY · belum ada kandidat. Bukan nasihat finansial."
    head = "Screener BUY · %d kandidat" % total
    if m is not None and m.verdict:
        head += ", pasar %s" % m.verdict.lower()
    items = ["%s%s %s/10" % ("\U0001F525" if c.score >= hs else "", c.symbol, K.fmt_score(c.score)) for c in shown]
    line2 = ", ".join(items) + (" (+%d lainnya)" % hidden if hidden else "")
    tail = "Skor %s+ = kandidat kuat. Pasang SL sebelum entry. Bukan nasihat finansial." % K.fmt_score(hs)
    text = "%s.\n%s.\n%s" % (head, line2, tail)
    return text if len(text) <= 1024 else text[:1020] + "..."


def screener_message(coins: Sequence[Any], market: Any = None, **kwargs) -> Tuple[Optional[bytes], str]:
    """Praktis: (png_bytes_atau_None, caption). kwargs diteruskan ke render_screener."""
    cap_kwargs = {k: kwargs[k] for k in ("hot_score", "max_coins") if k in kwargs}
    return render_screener(coins, market, **kwargs), build_caption(coins, market, **cap_kwargs)
