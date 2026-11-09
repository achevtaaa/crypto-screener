#!/usr/bin/env python3
"""
Bot PASAR + NARASI -> Telegram (topik Pasar dan topik Narasi)

  💸 PASAR | KALENDER MAKRO     event penting AS (CPI, NFP, FOMC, dll): pengingat 1 hari dan 1 jam sebelumnya,
                                lengkap dengan skenario dampak ke kripto kalau hasilnya di atas / sesuai / di bawah forecast
  💸 PASAR | LAMPU LALU LINTAS  status HIJAU / KUNING / MERAH dari kondisi pasar (Fear & Greed, volatilitas BTC,
                                funding, USDT.D, event besar). Dikirim tiap pagi dan saat status berubah
  💸 PASAR | BOT MASIH HIDUP    seminggu sekali (kalau tidak muncul, ada yang rusak)
  🧭 NARASI | RINGKASAN HARIAN  kategori/narasi yang sedang panas dan dingin (CoinGecko), koin trending

Judul tebal = penanda topik. Teks miring = penting.
Pesan dikirim ke topik grup kalau routes.json ada, kalau tidak ke chat pribadi.

Catatan jujur: skenario dampak adalah POLA UMUM, bukan ramalan. Reaksi pasar sering berbalik dalam beberapa jam.
Forecast dari kalender publik (Faireconomy/Forex Factory). Bisa telat atau salah, cek ulang di sumber resmi.

Pakai:
  pip install requests
  python pasar.py --dry-run      # tampil di layar, tidak kirim, tidak simpan state
  python pasar.py                # jalan normal
"""
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
CG_KEY = os.getenv("COINGECKO_API_KEY")
WIB = timezone(timedelta(hours=7))
ROUTES_FILE = "routes.json"
STATE_FILE = "pasar_state.json"

CAL_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]
CAL_REFRESH_H = 6
CAL_COUNTRY = "USD"
CAL_IMPACT = {"High"}
BRIEF_HOUR = 7                  # ringkasan pagi Pasar (WIB)
NARASI_HOUR = 8                 # ringkasan Narasi (WIB)
HOURS_BEFORE_H1 = 75            # menit: pengingat "1 jam"
FNG_URL = "https://api.alternative.me/fng/?limit=1"


# ================================================================ util
def esc(x):
    return html.escape(str(x), quote=False)


def now_utc():
    return datetime.now(timezone.utc)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def get_json(url, **kw):
    for _ in range(3):
        try:
            r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0", "accept": "application/json"}, **kw)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (403, 404):
                return None
            if r.status_code == 429:
                time.sleep(20)
                continue
        except Exception:
            pass
        time.sleep(1.5)
    return None


def cg_get(path, params=None):
    headers = {"accept": "application/json"}
    if CG_KEY:
        headers["x-cg-demo-api-key"] = CG_KEY
    for _ in range(3):
        try:
            r = requests.get("https://api.coingecko.com/api/v3" + path, params=params, headers=headers, timeout=25)
            if r.status_code == 429:
                time.sleep(25)
                continue
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            time.sleep(3)
    return None


def send(text, topic, dry=False):
    """Kirim ke topik grup (routes.json). Kalau belum diatur atau gagal, kirim ke chat pribadi."""
    if dry or not (TG_TOKEN and TG_CHAT):
        print(text + "\n" + "-" * 40)
        return
    routes = load_json(ROUTES_FILE, {})
    gid = routes.get("group")
    thread = routes.get(topic) or (routes.get("intel") if topic == "narasi" else None)
    base = {"text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": "true"}
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    if gid and thread:
        r = requests.post(url, data=dict(base, chat_id=gid, message_thread_id=thread), timeout=15)
        if r.status_code == 200:
            return
        print("kirim ke topik gagal:", r.status_code, "-> kirim ke chat pribadi")
    r = requests.post(url, data=dict(base, chat_id=TG_CHAT), timeout=15)
    r.raise_for_status()
    time.sleep(0.4)


# ================================================================ skenario dampak event
INFLASI = {
    "nama": "Data inflasi",
    "apa": "Ukuran kenaikan harga di AS. The Fed memakainya untuk menentukan suku bunga.",
    "naik": "Inflasi LEBIH PANAS dari perkiraan: peluang suku bunga naik atau tertahan tinggi membesar, dolar dan "
            "yield naik. Kripto biasanya TURUN cepat dan long banyak terlikuidasi.",
    "sama": "SESUAI perkiraan: reaksi biasanya kecil. Lonjakan awal sering mereda dan harga kembali ke range.",
    "turun": "Inflasi LEBIH DINGIN dari perkiraan: harapan suku bunga turun atau tidak naik menguat, dolar melemah. "
             "Kripto biasanya NAIK.",
}
KERJA = {
    "nama": "Data tenaga kerja",
    "apa": "Jumlah pekerjaan baru di AS. Pasar kerja yang kuat membuat The Fed sulit melonggarkan suku bunga.",
    "naik": "LEBIH KUAT dari perkiraan: The Fed bisa lebih keras, dolar naik. Kripto cenderung TURUN "
            "('kabar bagus jadi kabar buruk' untuk aset berisiko).",
    "sama": "SESUAI perkiraan: reaksi kecil, fokus pindah ke data inflasi berikutnya.",
    "turun": "JAUH LEBIH LEMAH: harapan pelonggaran naik (bisa NAIK di awal), tapi ketakutan resesi bisa membalik "
             "arah dalam beberapa jam. Waspada reaksi dua arah.",
}
SCENARIOS = [
    (r"\bcore pce\b|\bpce\b|\bcore cpi\b|\bcpi\b|\bcore ppi\b|\bppi\b|\bprice index\b", INFLASI),
    (r"non-?farm|nfp|\badp\b|employment change|jolts|job openings", KERJA),
    (r"unemployment rate", {
        "nama": "Tingkat pengangguran",
        "apa": "Persentase penganggur AS. Dibaca bersama data lapangan kerja.",
        "naik": "Pengangguran LEBIH TINGGI dari perkiraan: ekonomi melemah. Harapan pelonggaran naik, tapi ketakutan "
                "resesi bisa menekan aset berisiko. Reaksi bisa dua arah.",
        "sama": "SESUAI perkiraan: reaksi kecil.",
        "turun": "Pengangguran LEBIH RENDAH: pasar kerja ketat, peluang pelonggaran kecil. Kripto cenderung tertekan.",
    }),
    (r"jobless claims|unemployment claims", {
        "nama": "Klaim pengangguran mingguan",
        "apa": "Jumlah orang yang baru mengajukan tunjangan pengangguran. Indikator cepat kondisi pasar kerja.",
        "naik": "Klaim LEBIH TINGGI dari perkiraan: pasar kerja melemah. Efek mirip data kerja lemah: ada harapan "
                "pelonggaran, tapi juga kekhawatiran resesi.",
        "sama": "SESUAI perkiraan: efek kecil.",
        "turun": "Klaim LEBIH RENDAH: pasar kerja kuat. Cenderung menekan kripto sedikit.",
    }),
    (r"federal funds rate|fomc statement|interest rate", {
        "nama": "Keputusan suku bunga The Fed",
        "apa": "Keputusan suku bunga acuan AS. Salah satu event paling menggerakkan pasar.",
        "naik": "LEBIH KERAS dari perkiraan (suku bunga naik, atau nada pernyataan hawkish): dolar naik. Kripto biasanya TURUN "
                "tajam.",
        "sama": "SESUAI perkiraan: pasar fokus ke nada pernyataan dan konferensi pers. Gerakan liar dua arah sering "
                "terjadi dalam 1-2 jam setelahnya.",
        "turun": "LEBIH LONGGAR dari perkiraan (potong atau nada dovish): kripto cenderung NAIK, tapi hati-hati "
                 "'sell the news' (naik lalu berbalik).",
    }),
    (r"press conference|speaks|testifies|speech|minutes", {
        "nama": "Pidato / konferensi pers pejabat The Fed",
        "apa": "Nada pejabat The Fed menentukan harapan suku bunga ke depan. Tidak ada angka forecast.",
        "naik": "Nada HAWKISH (tegas soal inflasi atau suku bunga tinggi lebih lama): kripto cenderung TURUN.",
        "sama": "Nada NETRAL: reaksi kecil.",
        "turun": "Nada DOVISH (terbuka pada pelonggaran): kripto cenderung NAIK.",
    }),
    (r"gdp|retail sales|ism|pmi|consumer sentiment|consumer confidence|durable goods", {
        "nama": "Data pertumbuhan ekonomi",
        "apa": "Gambaran kekuatan ekonomi AS.",
        "naik": "LEBIH KUAT dari perkiraan: ekonomi sehat tapi suku bunga bisa bertahan tinggi lebih lama. Efek ke kripto "
                "campur, sering tertekan sedikit.",
        "sama": "SESUAI perkiraan: reaksi kecil.",
        "turun": "LEBIH LEMAH: harapan pelonggaran naik, tapi ketakutan resesi bisa menekan aset berisiko.",
    }),
]
GENERIC = {
    "nama": "Data ekonomi AS",
    "apa": "Data yang bisa menggeser harapan suku bunga The Fed.",
    "naik": "LEBIH KUAT / PANAS dari perkiraan: cenderung hawkish (dolar naik), kripto cenderung TURUN.",
    "sama": "SESUAI perkiraan: reaksi biasanya kecil.",
    "turun": "LEBIH LEMAH / DINGIN dari perkiraan: cenderung dovish (dolar turun), kripto cenderung NAIK. Kalau sangat "
             "buruk, ketakutan resesi bisa membalik arah.",
}
SAFE_TIP = ("Saran aman: hindari buka posisi baru 30 menit sebelum dan sesudah rilis, pasang SL, kecilkan leverage. "
            "Spread melebar dan SL sering tersapu. Tunggu candle 15 menit setelah rilis ditutup.")


def scenario_for(title):
    t = title.lower()
    for pat, sc in SCENARIOS:
        if re.search(pat, t):
            return sc
    return GENERIC


def parse_event_time(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_calendar(state):
    cal = state.get("cal") or {}
    if cal.get("ts") and now_utc().timestamp() - cal["ts"] < CAL_REFRESH_H * 3600 and cal.get("events"):
        return cal["events"]
    events = []
    for url in CAL_URLS:
        j = get_json(url)
        if not isinstance(j, list):
            continue
        for e in j:
            try:
                if e.get("country") == CAL_COUNTRY and e.get("impact") in CAL_IMPACT:
                    events.append({"title": e["title"], "date": e["date"], "forecast": e.get("forecast") or "",
                                   "previous": e.get("previous") or "", "impact": e["impact"]})
            except Exception:
                continue
    if events:
        state["cal"] = {"ts": now_utc().timestamp(), "events": events}
        return events
    return cal.get("events", [])


def event_message(ev, kind):
    when = parse_event_time(ev["date"])
    wib = when.astimezone(WIB)
    delta = when - now_utc()
    mins = int(delta.total_seconds() // 60)
    sc = scenario_for(ev["title"])
    left = f"dalam {mins // 60} jam {mins % 60} menit" if mins >= 60 else f"dalam {max(mins, 1)} menit"
    tag = "1 JAM LAGI" if kind == "h1" else "BESOK / 24 JAM LAGI"
    fc = ev["forecast"] or "tidak ada (nada / pidato)"
    pv = ev["previous"] or "-"
    lines = [f"<b>💸 PASAR | KALENDER MAKRO: {esc(ev['title'])}</b>",
             f"<i>⏰ {esc(wib.strftime('%a %d %b %H:%M'))} WIB ({esc(left)}) | {esc(tag)}</i>",
             esc(f"Forecast: {fc} | Sebelumnya: {pv}"),
             esc(f"Apa ini: {sc['nama']}. {sc['apa']}"),
             "",
             "<b>Kalau hasil DI ATAS forecast:</b> " + esc(sc["naik"]),
             "<b>Kalau SESUAI forecast:</b> " + esc(sc["sama"]),
             "<b>Kalau DI BAWAH forecast:</b> " + esc(sc["turun"]),
             "",
             "<i>" + esc(SAFE_TIP) + "</i>",
             esc("Ini pola umum, bukan ramalan. Reaksi sering berbalik dalam beberapa jam.")]
    return "\n".join(lines)


def check_calendar(state, dry):
    events = load_calendar(state)
    sent = state.setdefault("cal_sent", {})
    out = 0
    for ev in sorted(events, key=lambda e: e["date"]):
        try:
            when = parse_event_time(ev["date"])
        except Exception:
            continue
        mins = (when - now_utc()).total_seconds() / 60
        key = f"{ev['date']}|{ev['title']}"
        if 0 < mins <= HOURS_BEFORE_H1 and not sent.get(key + "|h1"):
            send(event_message(ev, "h1"), "pasar", dry)
            sent[key + "|h1"] = 1
            sent[key + "|d1"] = 1
            out += 1
        elif HOURS_BEFORE_H1 < mins <= 24 * 60 and not sent.get(key + "|d1"):
            send(event_message(ev, "d1"), "pasar", dry)
            sent[key + "|d1"] = 1
            out += 1
    for k in list(sent):                       # bersihkan catatan lama
        if k.split("|")[0] < (now_utc() - timedelta(days=10)).strftime("%Y-%m-%d"):
            del sent[k]
    return events


# ================================================================ penjaga kondisi pasar (lampu lalu lintas)
def okx_one(url, params):
    j = get_json(url + "?" + "&".join(f"{k}={v}" for k, v in params.items()))
    try:
        return j["data"][0] if j and j.get("code") == "0" else None
    except Exception:
        return None


def market_snapshot(state):
    snap = {"fng": None, "fng_label": "", "btc24": None, "btc": None, "fund_btc": None, "fund_eth": None,
            "usdtd": None, "btcd": None}
    j = get_json(FNG_URL)
    try:
        snap["fng"] = int(j["data"][0]["value"])
        snap["fng_label"] = j["data"][0]["value_classification"]
    except Exception:
        pass
    t = okx_one("https://www.okx.com/api/v5/market/ticker", {"instId": "BTC-USDT"})
    if t:
        last, o = float(t["last"]), float(t["open24h"])
        snap["btc"], snap["btc24"] = last, (last / o - 1) * 100 if o else None
    for k, inst in (("fund_btc", "BTC-USDT-SWAP"), ("fund_eth", "ETH-USDT-SWAP")):
        f = okx_one("https://www.okx.com/api/v5/public/funding-rate", {"instId": inst})
        if f:
            snap[k] = float(f["fundingRate"]) * 100
        time.sleep(0.3)
    g = cg_get("/global")
    try:
        d = g["data"]["market_cap_percentage"]
        snap["usdtd"], snap["btcd"] = d.get("usdt"), d.get("btc")
    except Exception:
        pass
    hist = state.setdefault("dom_hist", [])
    if snap["usdtd"] is not None:
        hist.append({"ts": now_utc().timestamp(), "usdtd": snap["usdtd"], "btcd": snap["btcd"]})
        del hist[:-80]
        old = [h for h in hist if now_utc().timestamp() - h["ts"] >= 20 * 3600]
        if old:
            snap["usdtd_chg"] = snap["usdtd"] - old[-1]["usdtd"]
            snap["btcd_chg"] = snap["btcd"] - old[-1]["btcd"]
    return snap


def assess(snap, events):
    pts, notes = 0, []
    if snap["fng"] is not None:
        if snap["fng"] <= 20 or snap["fng"] >= 80:
            pts += 1
            notes.append(f"Fear & Greed ekstrem ({snap['fng']}, {snap['fng_label']}): pasar emosional, gerakan bisa tajam")
    if snap["btc24"] is not None and abs(snap["btc24"]) >= 5:
        pts += 2 if abs(snap["btc24"]) >= 8 else 1
        notes.append(f"BTC bergerak {snap['btc24']:+.1f}% dalam 24 jam: volatilitas tinggi")
    for k, name in (("fund_btc", "BTC"), ("fund_eth", "ETH")):
        v = snap.get(k)
        if v is not None and (v > 0.05 or v < -0.03):
            pts += 1
            notes.append(f"Funding {name} ekstrem ({v:+.4f}%): posisi satu arah terlalu ramai, rawan squeeze")
            break
    if snap.get("usdtd_chg") is not None and snap["usdtd_chg"] >= 0.3:
        pts += 1
        notes.append(f"USDT.D naik {snap['usdtd_chg']:+.2f} poin dalam 24 jam: uang lari ke stablecoin (risk-off)")
    soon = []
    for ev in events:
        try:
            mins = (parse_event_time(ev["date"]) - now_utc()).total_seconds() / 60
        except Exception:
            continue
        if 0 < mins <= 24 * 60:
            soon.append(ev)
    if soon:
        pts += 1
        notes.append(f"Ada {len(soon)} event besar AS dalam 24 jam ke depan")
    level = "HIJAU" if pts == 0 else "KUNING" if pts == 1 else "MERAH"
    return level, notes, soon


LIGHT = {"HIJAU": ("🟢", "Kondisi normal. Trading sesuai rencana dan ukuran risiko biasa."),
         "KUNING": ("🟡", "Waspada. Kecilkan ukuran posisi dan perketat SL."),
         "MERAH": ("🔴", "Risiko tinggi. Kurangi leverage, pertimbangkan tidak membuka posisi baru.")}


def light_message(level, notes, snap, soon, brief):
    icon, advice = LIGHT[level]
    title = "PAGI" if brief else "STATUS BERUBAH"
    head = f"<b>💸 PASAR | LAMPU LALU LINTAS {icon} {level} ({title})</b>"
    lines = [head, f"<i>{esc(advice)}</i>"]
    data = []
    if snap["btc"] is not None:
        data.append(f"BTC ${snap['btc']:,.0f} ({snap['btc24']:+.1f}% 24j)" if snap["btc24"] is not None else f"BTC ${snap['btc']:,.0f}")
    if snap["fng"] is not None:
        data.append(f"Fear & Greed {snap['fng']} ({snap['fng_label']})")
    if snap["usdtd"] is not None:
        data.append(f"USDT.D {snap['usdtd']:.1f}%" + (f" ({snap['usdtd_chg']:+.2f})" if snap.get("usdtd_chg") is not None else ""))
    if snap["btcd"] is not None:
        data.append(f"BTC.D {snap['btcd']:.1f}%" + (f" ({snap['btcd_chg']:+.2f})" if snap.get("btcd_chg") is not None else ""))
    if snap["fund_btc"] is not None:
        data.append(f"Funding BTC {snap['fund_btc']:+.4f}%")
    if data:
        lines.append(esc(" | ".join(data)))
    if notes:
        lines.append("")
        lines += ["<i>• " + esc(n) + "</i>" for n in notes]
    if soon and brief:
        lines += ["", "<b>Event besar 24 jam ke depan:</b>"]
        for ev in soon[:6]:
            t = parse_event_time(ev["date"]).astimezone(WIB).strftime("%a %H:%M")
            lines.append(esc(f"• {t} WIB  {ev['title']} (forecast {ev['forecast'] or '-'}, sebelumnya {ev['previous'] or '-'})"))
    return "\n".join(lines)


def check_market(state, events, dry):
    snap = market_snapshot(state)
    level, notes, soon = assess(snap, events)
    last = state.get("level")
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    brief_due = datetime.now(WIB).hour >= BRIEF_HOUR and state.get("brief_date") != today
    changed = last is not None and level != last
    if brief_due:
        send(light_message(level, notes, snap, soon, True), "pasar", dry)
        state["brief_date"] = today
    elif changed:
        send(light_message(level, notes, snap, soon, False), "pasar", dry)
    state["level"] = level


def heartbeat(state, dry):
    wk = datetime.now(WIB).strftime("%G-W%V")
    if state.get("hb") != wk and datetime.now(WIB).weekday() == 6:        # hari Minggu
        send("<b>💸 PASAR | BOT MASIH HIDUP ✅</b>\n"
             + esc("Pemeriksaan mingguan: bot Pasar dan Narasi berjalan. Kalau pesan ini tidak muncul hari Minggu, "
                   "cek tab Actions di GitHub."), "pasar", dry)
        state["hb"] = wk


# ================================================================ narasi
SKIP_CAT = re.compile(r"stablecoin|wrapped|bridged|fiat|yield.?bearing|liquid stak|liquid restak|etf|portfolio|"
                      r"layer 1|smart contract|governance|exchange.?based|centralized exchange|alleged|"
                      r"made in|tokenized (gold|commodit|silver)|asset-backed|usd stablecoin|"
                      r"ecosystem$|coinbase|binance|ftx|dex$|nft marketplace", re.I)


def pretty(coin_id):
    return coin_id.replace("-", " ").title()


def narasi_message(state):
    cats = cg_get("/coins/categories")
    if not isinstance(cats, list):
        return None
    glob = cg_get("/global")
    try:
        mkt = glob["data"]["market_cap_change_percentage_24h_usd"]
    except Exception:
        mkt = None
    valid = []
    for c in cats:
        try:
            mc, ch, vol = c["market_cap"], c["market_cap_change_24h"], c.get("volume_24h") or 0
        except Exception:
            continue
        if not mc or ch is None or mc < 300e6 or vol < 30e6 or SKIP_CAT.search(c.get("name", "")):
            continue
        valid.append({"id": c["id"], "name": c["name"], "ch": ch, "flow": vol / mc, "mc": mc,
                      "coins": [pretty(x) for x in (c.get("top_3_coins_id") or [])[:3]]})
    if not valid:
        return None
    hist = state.setdefault("narr_hist", {})
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    for v in valid:
        h = hist.setdefault(v["id"], {})
        h[today] = round(v["ch"], 2)
        for d in sorted(h)[:-8]:
            del h[d]
        days = [h[d] for d in sorted(h)][::-1]
        streak = 0
        for x in days:
            if x > 0:
                streak += 1
            else:
                break
        v["streak"] = streak
    hot = sorted(valid, key=lambda v: v["ch"], reverse=True)[:6]
    hot_ids = {v["id"] for v in hot}
    cold = [v for v in sorted(valid, key=lambda v: v["ch"]) if v["id"] not in hot_ids][:3]
    rows = [f"<b>🧭 NARASI | RINGKASAN HARIAN</b> ({esc(datetime.now(WIB).strftime('%d %b %Y'))})"]
    if mkt is not None:
        rows.append(esc(f"Total pasar kripto 24 jam: {mkt:+.1f}%. Narasi dinilai terhadap angka ini."))
    rows += ["", "<b>NARASI PANAS (24 jam)</b>"]
    for v in hot:
        rel = f" | vs pasar {v['ch'] - mkt:+.1f} poin" if mkt is not None else ""
        detail = (f"{v['ch']:+.1f}%{rel} | uang masuk {v['flow'] * 100:.0f}% dari market cap"
                  f" | koin besar: {', '.join(v['coins']) or '-'}")
        line = f"<b><u>{esc(v['name'])}</u></b>"
        if v["streak"] >= 3:
            streak_txt = "naik " + str(v["streak"]) + " hari berturut-turut (lebih meyakinkan)"
            line += " <i>" + esc(streak_txt) + "</i>"
        rows.append(line + "\n  " + esc(detail))
    if cold:
        rows += ["", "<b>NARASI DINGIN (24 jam)</b>"]
        for v in cold:
            rows.append(esc(f"• {v['name']}: {v['ch']:+.1f}%"))
    tr = cg_get("/search/trending")
    try:
        names = [f"{c['item']['name']} ({c['item']['symbol']})" for c in tr["coins"][:7]]
        rows += ["", "<b>KOIN TRENDING DI COINGECKO</b>", esc(", ".join(names))]
    except Exception:
        pass
    rows += ["", "<i>" + esc("Cara baca: narasi yang naik berhari-hari dengan uang masuk besar lebih layak dipantau daripada "
                            "yang naik satu hari saja (sering cuma pump). Cek koinnya di screener dan hindari koin bervolume tipis.")
             + "</i>"]
    return "\n".join(rows)


def check_narasi(state, dry):
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    if datetime.now(WIB).hour >= NARASI_HOUR and state.get("narasi_date") != today:
        msg = narasi_message(state)
        if msg:
            send(msg, "narasi", dry)
            state["narasi_date"] = today


# ================================================================ main
def main():
    dry = "--dry-run" in sys.argv
    state = load_json(STATE_FILE, {})
    try:
        events = check_calendar(state, dry)
    except Exception as e:
        print("error calendar", e)
        events = []
    for name, fn in (("market", lambda: check_market(state, events, dry)),
                     ("heartbeat", lambda: heartbeat(state, dry)),
                     ("narasi", lambda: check_narasi(state, dry))):
        try:
            fn()
        except Exception as e:
            print("error", name, type(e).__name__, e)
    if not dry:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)


if __name__ == "__main__":
    main()
