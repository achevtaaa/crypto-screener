"""Bagian bersama untuk screener.py, narasi.py, pasar_kartu.py:
gaya kartu, font, panggilan OKX (dengan jeda anti rate-limit), kirim Telegram.

Tujuan Telegram dibaca dari routes.json di folder yang sama, contoh isi:
  {"group": -1004389304375, "screener": 2, "intel": 3, "pasar": 9, "narasi": 14}
Token dibaca dari env TELEGRAM_BOT_TOKEN atau TELEGRAM_TOKEN (secret GitHub).
"""
import io
import json
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

ROUTES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes.json")

HINTS = [
    ("message thread not found", "ID topik salah atau topik itu tidak ada di grup. Cek routes.json."),
    ("chat not found", "Chat ID salah, atau bot belum dimasukkan ke grup."),
    ("upgraded to a supergroup", "Chat ID lama. Pakai ID supergroup yang diawali -100 di routes.json."),
    ("not enough rights", "Bot belum jadi admin atau tidak boleh mengirim di topik itu."),
    ("have no rights to send", "Bot belum jadi admin atau tidak boleh mengirim di topik itu."),
    ("TOPIC_CLOSED", "Topik sedang ditutup. Buka kembali topiknya."),
    ("Unauthorized", "Token bot salah. Cek secret TELEGRAM_TOKEN."),
]


def _routes():
    try:
        with open(ROUTES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] routes.json tidak terbaca ({e})", file=sys.stderr)
        return {}


def _target(topics):
    """topics = nama topik di routes.json, urut prioritas, mis. ("narasi", "pasar")."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise SystemExit("Token Telegram tidak ada. Isi secret TELEGRAM_TOKEN dan petakan di workflow.")
    routes = _routes()
    chat = routes.get("group") or os.environ.get("TELEGRAM_CHAT_ID")
    if not chat:
        raise SystemExit('Chat ID tidak ada. Isi "group" di routes.json.')
    for name in topics:
        if routes.get(name):
            print(f"Tujuan: topik '{name}' (thread {routes[name]})")
            return token, str(chat), str(routes[name])
    for name in topics:
        v = os.environ.get(f"TELEGRAM_THREAD_ID_{name.upper()}")
        if v:
            print(f"Tujuan: topik '{name}' dari secret (thread {v})")
            return token, str(chat), v
    raise SystemExit(f"ID topik {list(topics)} tidak ada di routes.json. "
                     "Tambahkan, contoh: {\"group\": -100..., \"narasi\": 14}")


def _post(token, method, **kw):
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", timeout=60, **kw)
    if not r.ok:
        try:
            desc = r.json().get("description", r.text[:300])
        except Exception:  # noqa: BLE001
            desc = r.text[:300]
        hint = next((h for k, h in HINTS if k.lower() in desc.lower()), "")
        raise SystemExit(f"Telegram menolak ({r.status_code}): {desc}" + (f"\n=> {hint}" if hint else ""))
    return r


def send_photo(img, caption, topics=()):
    token, chat, thread = _target(topics)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.seek(0)
    data = {"chat_id": chat, "caption": caption[:1024], "message_thread_id": thread}
    _post(token, "sendPhoto", data=data, files={"photo": ("kartu.png", buf, "image/png")})
    print("Terkirim ke Telegram.")


def send_text(text, topics=()):
    token, chat, thread = _target(topics)
    _post(token, "sendMessage", data={"chat_id": chat, "text": text[:4096], "message_thread_id": thread})
    print("Teks terkirim ke Telegram.")
