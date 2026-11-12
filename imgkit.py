"""imgkit.py - alat render gambar bersama untuk bot screener dan narasi.

Hanya butuh Pillow >= 9.0 (pip install pillow). Tidak butuh internet, Chrome, atau library lain.

Cara kerja singkat:
  * Layout dihitung dalam "satuan" (lebar kanvas = 720 satuan).
  * Gambar digambar 3x lebih besar (supersampling) lalu diperkecil, supaya tepi halus.
  * Hasil akhir lebar 1440 px (2x), format PNG.

Cek font dan emoji di server kamu:
    python imgkit.py
"""
from __future__ import annotations

import io
import logging
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("bot_images")

# --------------------------------------------------------------------------- #
# Konstanta dasar
# --------------------------------------------------------------------------- #
WIDTH = 720          # lebar kanvas (satuan layout)
SS = 3               # faktor supersampling
OUT = 2              # piksel output per satuan layout (720 -> 1440 px)
MAX_SIDE = 2560      # sisi terpanjang gambar akhir (px)
MAX_HEIGHT = 5000    # batas tinggi aman (satuan), melindungi memori

Color = Tuple[int, int, int]

BG: Color = (15, 20, 36)
PANEL: Color = (26, 32, 54)
CARD: Color = (22, 28, 48)
CARD_BORDER: Color = (42, 50, 82)
RULE: Color = (35, 43, 71)
CHIP: Color = (31, 39, 66)
TEXT: Color = (232, 235, 245)
MUTED: Color = (141, 149, 176)
SUB: Color = (195, 201, 222)
GREEN: Color = (111, 211, 160)
RED: Color = (240, 138, 138)
AMBER: Color = (240, 195, 90)
AMBER_BG: Color = (58, 47, 18)
GREEN_BG: Color = (22, 58, 44)
RED_BG: Color = (66, 30, 34)
TRACK: Color = (37, 44, 71)
BAR_RED: Color = (224, 122, 122)
BAR_GREEN: Color = (96, 190, 140)

_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")

# --------------------------------------------------------------------------- #
# Font
# --------------------------------------------------------------------------- #
_REGULAR = [
    "inter-regular.ttf", "inter-regular.otf", "inter_18pt-regular.ttf",
    "inter_24pt-regular.ttf", "roboto-regular.ttf", "notosans-regular.ttf",
    "opensans-regular.ttf", "dejavusans.ttf", "liberationsans-regular.ttf",
    "arial.ttf", "segoeui.ttf", "helvetica.ttc",
]
_BOLD = [
    "inter-semibold.ttf", "inter-semibold.otf", "inter_18pt-semibold.ttf",
    "inter_24pt-semibold.ttf", "inter-bold.ttf", "inter-bold.otf",
    "roboto-medium.ttf", "roboto-bold.ttf", "notosans-bold.ttf",
    "opensans-semibold.ttf", "dejavusans-bold.ttf", "liberationsans-bold.ttf",
    "arialbd.ttf", "segoeuib.ttf",
]


@lru_cache(maxsize=1)
def _font_index() -> Dict[str, str]:
    """Peta nama-file-huruf-kecil -> path. Folder yang lebih awal diprioritaskan."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:  # pragma: no cover
        here = os.getcwd()
    dirs = [
        os.environ.get("BOT_FONT_DIR", ""),
        os.path.join(here, "fonts"),
        here,
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/.local/share/fonts"),
        "/Library/Fonts",
        "/System/Library/Fonts",
        os.path.expanduser("~/Library/Fonts"),
        "C:\\Windows\\Fonts",
    ]
    idx: Dict[str, str] = {}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            for root, _dirs, files in os.walk(d):
                for fn in files:
                    low = fn.lower()
                    if low.endswith((".ttf", ".otf", ".ttc")):
                        idx.setdefault(low, os.path.join(root, fn))
        except OSError:
            continue
    return idx


@lru_cache(maxsize=1)
def _font_paths() -> Tuple[Optional[str], Optional[str]]:
    reg = os.environ.get("BOT_FONT_REGULAR") or None
    bold = os.environ.get("BOT_FONT_BOLD") or None
    idx = _font_index()
    if not reg:
        reg = next((idx[n] for n in _REGULAR if n in idx), None)
    if not bold:
        bold = next((idx[n] for n in _BOLD if n in idx), None)
    if reg and not bold:
        bold = reg
    if bold and not reg:
        reg = bold
    if not reg:
        log.warning("Font TrueType tidak ditemukan; memakai font bawaan Pillow. "
                    "Taruh Inter-Regular.ttf dan Inter-SemiBold.ttf di folder 'fonts' "
                    "di samping imgkit.py, atau set BOT_FONT_REGULAR / BOT_FONT_BOLD.")
    return reg, bold


def font_ok() -> bool:
    """True jika font TrueType ditemukan."""
    return _font_paths()[0] is not None


_ITALIC = [
    "inter-italic.ttf", "inter-italic.otf", "inter_18pt-italic.ttf", "roboto-italic.ttf",
    "notosans-italic.ttf", "opensans-italic.ttf", "dejavusans-oblique.ttf",
    "liberationsans-italic.ttf", "ariali.ttf", "segoeuii.ttf",
]


@lru_cache(maxsize=1)
def _italic_path() -> Optional[str]:
    env = os.environ.get("BOT_FONT_ITALIC") or None
    if env:
        return env
    idx = _font_index()
    return next((idx[n] for n in _ITALIC if n in idx), None)


@lru_cache(maxsize=64)
def get_font(size: float, bold: bool = False, italic: bool = False):
    """Font dengan ukuran `size` satuan layout (sudah dikali SS)."""
    px = max(6, int(round(size * SS)))
    reg, bld = _font_paths()
    path = bld if bold else reg
    if italic and reg:
        path = _italic_path() or path
    if path:
        try:
            return ImageFont.truetype(path, px)
        except Exception as exc:  # file rusak / format tak didukung
            log.warning("Gagal memuat font %s: %s", path, exc)
    try:
        return ImageFont.load_default(size=px)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def minus() -> str:
    return "\u2212" if font_ok() else "-"


def ellipsis() -> str:
    return "\u2026" if font_ok() else "..."


def text_w(s: str, font) -> float:
    """Lebar teks dalam satuan layout."""
    try:
        return font.getlength(s) / SS
    except AttributeError:  # pragma: no cover (Pillow lama + font bitmap)
        return font.getsize(s)[0] / SS


def asc(font) -> float:
    try:
        return font.getmetrics()[0] / SS
    except Exception:  # pragma: no cover
        return getattr(font, "size", 10 * SS) * 0.9 / SS


def line_h(font) -> float:
    try:
        a, d = font.getmetrics()
        return (a + d) / SS
    except Exception:  # pragma: no cover
        return getattr(font, "size", 10 * SS) * 1.2 / SS


def fit_text(s: str, font, max_w: float) -> str:
    """Potong teks dengan elipsis supaya muat dalam max_w."""
    s = s or ""
    if max_w <= 0:
        return ""
    if text_w(s, font) <= max_w:
        return s
    ell = ellipsis()
    lo, hi = 0, len(s)
    while lo < hi:  # cari panjang terbesar yang masih muat
        mid = (lo + hi + 1) // 2
        if text_w(s[:mid].rstrip() + ell, font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (s[:lo].rstrip() + ell) if lo > 0 else ell


def wrap_text(s: str, font, max_w: float, max_lines: Optional[int] = None) -> List[str]:
    """Bungkus teks per kata; kata yang terlalu panjang dipecah per huruf."""
    words = (s or "").split()
    lines: List[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if text_w(trial, font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while text_w(w, font) > max_w and len(w) > 1:
            k = len(w)
            while k > 1 and text_w(w[:k], font) > max_w:
                k -= 1
            lines.append(w[:k])
            w = w[k:]
        cur = w
    if cur:
        lines.append(cur)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = fit_text(lines[-1] + " " + ellipsis(), font, max_w)
    return lines


# --------------------------------------------------------------------------- #
# Kanvas
# --------------------------------------------------------------------------- #
class Canvas:
    """Kanvas gambar. Jika `height` kosong, semua perintah gambar diabaikan
    (mode ukur) sehingga fungsi layout yang sama bisa dipakai untuk menghitung tinggi."""

    def __init__(self, width: int = WIDTH, height: Optional[float] = None):
        self.width = width
        self.measure = height is None
        self.height = None if height is None else int(math.ceil(height))
        if not self.measure:
            if self.height > MAX_HEIGHT:
                raise ValueError("Gambar terlalu tinggi (%s satuan)" % self.height)
            self.img = Image.new("RGB", (int(width * SS), int(self.height * SS)), BG)
            self.d = ImageDraw.Draw(self.img)

    def rrect(self, x0, y0, x1, y1, r=12, fill=None, outline=None, width=1):
        if self.measure or x1 <= x0 or y1 <= y0:
            return
        r = max(0.0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
        self.d.rounded_rectangle(
            [x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1],
            radius=r * SS, fill=fill, outline=outline,
            width=max(1, int(round(width * SS))),
        )

    def hrule(self, x0, x1, y, fill=RULE, thickness=1):
        if self.measure:
            return
        self.d.rectangle([x0 * SS, y * SS, x1 * SS - 1, (y + thickness) * SS - 1], fill=fill)

    def polygon(self, pts, fill):
        if self.measure:
            return
        self.d.polygon([(x * SS, y * SS) for x, y in pts], fill=fill)

    def text(self, x, y, s, font, fill, anchor="ls"):
        """Teks dengan y = garis dasar (baseline). anchor: ls (kiri), rs (kanan), ms (tengah)."""
        if self.measure or not s:
            return
        try:
            self.d.text((x * SS, y * SS), s, font=font, fill=fill, anchor=anchor)
        except (ValueError, TypeError):  # pragma: no cover (font bitmap tanpa anchor)
            w = text_w(s, font)
            xx = x - (w if anchor[0] == "r" else w / 2 if anchor[0] == "m" else 0)
            self.d.text((xx * SS, (y - asc(font)) * SS), s, font=font, fill=fill)

    def paste(self, sprite: Image.Image, x, y):
        if self.measure:
            return
        self.img.paste(sprite, (int(round(x * SS)), int(round(y * SS))), sprite)

    def finish(self) -> Image.Image:
        if self.measure:
            raise RuntimeError("Kanvas ukur tidak punya gambar")
        # Sisi terpanjang dibatasi MAX_SIDE px: Telegram mengecilkan foto yang lebih besar,
        # jadi gambar tinggi (6-7 koin) lebih baik kita kecilkan sendiri dengan benar.
        scale = min(float(OUT), MAX_SIDE / float(self.height)) if self.height else float(OUT)
        size = (max(1, int(round(self.width * scale))), max(1, int(round(self.height * scale))))
        return self.img.resize(size, _LANCZOS)


MEASURE = Canvas(WIDTH)  # kanvas ukur bersama (tanpa gambar)
_RENDER_LOCK = threading.RLock()

# Watermark kiri atas (ubah lewat variabel lingkungan BOT_WATERMARK; kosongkan untuk mematikan).
WATERMARK = os.environ.get("BOT_WATERMARK", "achevtaaa")
WATERMARK_COLOR: Color = (112, 120, 150)


def watermark(cv: "Canvas", x: float, y: float, text: Optional[str] = None) -> float:
    """Gambar watermark miring kecil di (x, y) dan kembalikan tinggi yang dipakai (0 bila kosong)."""
    t = WATERMARK if text is None else text
    if not t:
        return 0.0
    f = get_font(14, False, True)
    cv.text(x, y + asc(f), t, f, WATERMARK_COLOR)
    return line_h(f) + 8


def render(draw_fn: Callable[[Canvas], float], width: int = WIDTH) -> bytes:
    """Jalankan draw_fn dua kali (ukur, lalu gambar) dan kembalikan bytes PNG.
    draw_fn(canvas) wajib mengembalikan tinggi total (y paling bawah + padding)."""
    with _RENDER_LOCK:  # objek font Pillow tidak aman dipakai bersamaan dari banyak thread
        h = draw_fn(Canvas(width))
        cv = Canvas(width, h)
        draw_fn(cv)
        return to_png(cv.finish())


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Ikon api (emoji 🔥)
# --------------------------------------------------------------------------- #
def _bezier(p0, p1, p2, p3, n=20):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        pts.append((
            u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1],
        ))
    return pts


_FLAME = [
    ((0.50, 1.00), (0.22, 1.00), (0.04, 0.82), (0.08, 0.62)),
    ((0.08, 0.62), (0.10, 0.48), (0.22, 0.42), (0.28, 0.28)),
    ((0.28, 0.28), (0.32, 0.16), (0.42, 0.08), (0.50, 0.00)),
    ((0.50, 0.00), (0.60, 0.14), (0.70, 0.20), (0.76, 0.34)),
    ((0.76, 0.34), (0.86, 0.46), (0.94, 0.58), (0.92, 0.70)),
    ((0.92, 0.70), (0.90, 0.90), (0.74, 1.00), (0.50, 1.00)),
]


def _flame_pts(k: float):
    pts = []
    for seg in _FLAME:
        pts += _bezier(*seg)
    # skala terhadap titik dasar (0.5, 0.97)
    return [(0.5 + (x - 0.5) * k, 0.97 - (0.97 - y) * k) for x, y in pts]


def _vector_fire(px: int) -> Image.Image:
    big = 512
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for k, col in ((1.0, (244, 81, 30, 255)), (0.78, (255, 152, 0, 255)), (0.50, (255, 213, 79, 255))):
        d.polygon([(x * (big - 12) + 6, y * (big - 12) + 6) for x, y in _flame_pts(k)], fill=col)
    return im.resize((px, px), _LANCZOS)


def _emoji_sprite(px: int) -> Optional[Image.Image]:
    path = os.environ.get("BOT_EMOJI_FONT") or _font_index().get("notocoloremoji.ttf")
    if not path:
        return None
    f = ImageFont.truetype(path, 109)  # NotoColorEmoji hanya mendukung ukuran 109
    tmp = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((20, 20), "\U0001F525", font=f, embedded_color=True)
    bbox = tmp.split()[3].getbbox()
    if not bbox:
        return None
    tmp = tmp.crop(bbox)
    scale = px / float(max(tmp.size))
    tmp = tmp.resize((max(1, int(round(tmp.width * scale))), max(1, int(round(tmp.height * scale)))), _LANCZOS)
    out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    out.paste(tmp, ((px - tmp.width) // 2, (px - tmp.height) // 2), tmp)
    return out


@lru_cache(maxsize=8)
def fire_sprite(px: int) -> Image.Image:
    """Sprite 🔥. Pakai font Noto Color Emoji jika ada, kalau tidak pakai gambar vektor."""
    if not os.environ.get("BOT_FIRE_VECTOR"):
        try:
            spr = _emoji_sprite(px)
            if spr is not None:
                return spr
        except Exception as exc:
            log.warning("Emoji font gagal dipakai (%s); memakai ikon api vektor.", exc)
    return _vector_fire(px)


def emoji_font_available() -> bool:
    return bool(os.environ.get("BOT_EMOJI_FONT") or _font_index().get("notocoloremoji.ttf"))


def draw_fire(cv: Canvas, x: float, y: float, size: float) -> None:
    """Gambar 🔥 dengan sudut kiri-atas di (x, y), ukuran `size` satuan."""
    if cv.measure:
        return
    cv.paste(fire_sprite(max(4, int(round(size * SS)))), x, y)


# --------------------------------------------------------------------------- #
# Format angka dan waktu (gaya Indonesia: koma desimal)
# --------------------------------------------------------------------------- #
def num(x) -> Optional[float]:
    """Ubah apa pun menjadi float, atau None jika tidak valid (None, NaN, inf, teks aneh)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        if isinstance(x, str):
            try:
                v = float(x.replace("%", "").replace("$", "").replace(",", ".").strip())
            except ValueError:
                return None
        else:
            return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def fmt_pct(x, decimals: int = 1, signed: bool = True) -> str:
    v = num(x)
    if v is None:
        return "-"
    r = round(abs(v), decimals)
    body = ("%.*f" % (decimals, r)).replace(".", ",")
    if r == 0:
        return body + "%"
    sign = minus() if v < 0 else ("+" if signed else "")
    return sign + body + "%"


def fmt_int(x) -> str:
    v = num(x)
    return "-" if v is None else str(int(round(v)))


def fmt_score(x) -> str:
    v = num(x)
    if v is None:
        return "-"
    if abs(v - round(v)) < 0.05:
        return str(int(round(v)))
    return ("%.1f" % v).replace(".", ",")


def fmt_usd(x) -> str:
    """$23,8 / $120 / $1.234 (format Indonesia)."""
    v = num(x)
    if v is None:
        return "-"
    if v >= 1000:
        return "$" + format(int(round(v)), ",").replace(",", ".")
    if v >= 100 or abs(v - round(v)) < 0.05:
        return "$%d" % int(round(v))
    return "$" + ("%.1f" % v).replace(".", ",")


def fmt_mcap(x) -> str:
    v = num(x)
    if v is None:
        return "-"
    if v >= 1e12:
        return ("$%.2fT" % (v / 1e12)).replace(".", ",")
    if v >= 1e9:
        return ("$%.1fB" % (v / 1e9)).replace(".", ",")
    return ("$%.1fM" % (v / 1e6)).replace(".", ",")


_BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
WIB = timezone(timedelta(hours=7))


def now_wib() -> datetime:
    return datetime.now(WIB)


def fmt_stamp(dt: Optional[datetime] = None) -> str:
    """'11 Nov 2026 · 20.01 WIB'. Waktu tanpa zona dianggap sudah WIB."""
    if dt is None:
        dt = now_wib()
    elif dt.tzinfo is not None:
        dt = dt.astimezone(WIB)
    return "%d %s %d \u00b7 %02d.%02d WIB" % (dt.day, _BULAN[dt.month - 1], dt.year, dt.hour, dt.minute)


# --------------------------------------------------------------------------- #
# Diagnostik: python imgkit.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import PIL
    print("Pillow            :", PIL.__version__)
    reg, bold = _font_paths()
    print("Font regular      :", reg or "(tidak ada - pakai font bawaan)")
    print("Font tebal        :", bold or "(tidak ada - pakai font bawaan)")
    print("Font emoji (Noto) :", "ada" if emoji_font_available() else "tidak ada -> pakai ikon api vektor")
