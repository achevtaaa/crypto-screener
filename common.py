"""Bagian bersama untuk screener.py dan narasi.py:
gaya kartu, font, panggilan OKX (dengan jeda anti rate-limit), kirim Telegram.
"""
import io
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

S = 2          # supersampling agar tepi halus
W = 1080

BG = (13, 17, 27)
CARD = (24, 30, 44)
BORDER = (40, 48, 66)
TXT = (236, 240, 247)
MUTED = (139, 149, 170)
GREEN = (74, 200, 150)
RED = (240, 110, 110)
AMBER = (232, 184, 94)
BLUE = (110, 150, 240)
TRACK = (31, 38, 55)
DIV = (36, 44, 62)
DIM = (96, 106, 128)

FONT_DIRS = ["/usr/share/fonts/opentype/inter/", "/usr/share/fonts/truetype/inter/",
             "/usr/share/fonts/truetype/dejavu/"]
FALLBACK = {"Inter-Italic.otf": "DejaVuSans-Oblique.ttf", "Inter-Bold.otf": "DejaVuSans-Bold.ttf",
            "Inter-SemiBold.otf": "DejaVuSans-Bold.ttf", "Inter-Medium.otf": "DejaVuSans.ttf",
            "Inter-Regular.otf": "DejaVuSans.ttf"}

BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def font(name, size):
    for base in FONT_DIRS:
        for n in (name, FALLBACK.get(name, "")):
            p = base + n
            if n and os.path.exists(p):
                return ImageFont.truetype(p, size * S)
    return ImageFont.load_default()


def now_wib():
    return datetime.now(ZoneInfo("Asia/Jakarta"))


def stamp(now=None):
    n = now or now_wib()
    return f"{n.day} {BULAN[n.month - 1]} {n.year} · {n:%H.%M} WIB"


def pct(v, d=2, sign=True):
    if v is None:
        return "—"
    s = f"{v:+.{d}f}%" if sign else f"{v:.{d}f}%"
    return s.replace(".", ",").replace("-", "−")


# --------------------------------------------------------------------------
# Kanvas
# --------------------------------------------------------------------------

class Pen:
    def __init__(self, h):
        self.h = h
        glow = Image.new("RGB", (W * S, h * S), BG)
        ImageDraw.Draw(glow).ellipse([-200 * S, -420 * S, 900 * S, 300 * S], fill=(22, 32, 58))
        self.img = glow.filter(ImageFilter.GaussianBlur(140 * S))
        self.dr = ImageDraw.Draw(self.img)

    def rr(self, x, y, w, h, r=24, fill=CARD, outline=BORDER):
        self.dr.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], r * S,
                                  fill=fill, outline=outline, width=S)

    def box(self, x0, y0, x1, y1, r, fill, outline=None):
        self.dr.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], r * S, fill=fill,
                                  outline=outline, width=S if outline else 0)

    def t(self, x, y, s, f, fill=TXT, anchor="la"):
        self.dr.text((x * S, y * S), s, font=f, fill=fill, anchor=anchor)

    def line(self, x1, y1, x2, y2, fill=DIV):
        self.dr.line([x1 * S, y1 * S, x2 * S, y2 * S], fill=fill, width=S)

    def width(self, s, f):
        return self.dr.textlength(s, font=f) / S

    def watermark(self):
        self.t(48, 38, "achevtaaa", font("Inter-Italic.otf", 20), fill=(150, 160, 182))

    def footer(self, text):
        self.t(W / 2, self.h - 28, text, font("Inter-Regular.otf", 17), fill=DIM, anchor="mm")

    def done(self):
        return self.img.resize((W, self.h), Image.LANCZOS)


# --------------------------------------------------------------------------
# OKX
# --------------------------------------------------------------------------

OKX = "https://www.okx.com"
_last = [0.0]


def okx(path, params=None, gap=0.07):
    """GET OKX publik. Mengembalikan list `data`, atau None bila gagal.

    gap = jeda minimal antar panggilan (detik). Endpoint candles/ticker/funding
    ~20 req per 2 detik -> 0.07-0.12. Endpoint rubik ~5 req per 2 detik -> 0.45.
    """
    for i in range(4):
        wait = gap - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = requests.get(OKX + path, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            j = r.json()
            code = j.get("code")
            if code == "0":
                return j.get("data")
            if code == "50011":  # rate limit
                time.sleep(2 * (i + 1))
                continue
            print(f"[warn] OKX {path} {params} code={code} msg={j.get('msg')}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001
            print(f"[warn] OKX {path} gagal ({e})", file=sys.stderr)
            time.sleep(1.5 * (i + 1))
    return None


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def _target(thread_envs):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    thread = next((os.environ[e] for e in thread_envs if os.environ.get(e)), None)
    return token, chat, thread


def send_photo(img, caption, thread_envs=()):
    token, chat, thread = _target(thread_envs)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.seek(0)
    data = {"chat_id": chat, "caption": caption[:1024]}
    if thread:
        data["message_thread_id"] = thread
    r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto", data=data,
                      files={"photo": ("kartu.png", buf, "image/png")}, timeout=60)
    r.raise_for_status()
    print("Terkirim ke Telegram.")


def send_text(text, thread_envs=()):
    token, chat, thread = _target(thread_envs)
    data = {"chat_id": chat, "text": text[:4096]}
    if thread:
        data["message_thread_id"] = thread
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30)
    r.raise_for_status()
    print("Teks terkirim ke Telegram.")
