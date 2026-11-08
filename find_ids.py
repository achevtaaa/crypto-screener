#!/usr/bin/env python3
"""
Cari ID grup dan ID topik Telegram, lalu kirim hasilnya ke chat pribadi (TELEGRAM_CHAT_ID).

Cara pakai:
  1. Di tiap topik grup, kirim satu perintah: /screener di topik Screener, /intel di topik Intel,
     /pasar di topik Pasar. (Pesan berawalan / selalu sampai ke bot.)
  2. Jalankan workflow "Cari ID". Hasil dikirim ke chat pribadi kamu.
Token tidak ditampilkan di mana pun.
"""
import os
import sys

import requests

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
NAMES = {"screener": "SCREENER", "intel": "INTEL", "pasar": "PASAR"}


def api(method, **params):
    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/{method}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def collect(updates):
    groups = {}   # chat_id -> {"title": ..., "topics": {label: thread_id}, "seen": {thread_id}}
    for u in updates:
        m = u.get("message") or u.get("edited_message")
        if not m:
            continue
        chat = m.get("chat", {})
        if chat.get("type") not in ("group", "supergroup"):
            continue
        g = groups.setdefault(chat["id"], {"title": chat.get("title", "?"), "topics": {}, "seen": set()})
        thread = m.get("message_thread_id")
        g["seen"].add(thread if thread else "general")
        text = (m.get("text") or "").strip().lower().split("@")[0].lstrip("/")
        if text in NAMES and thread:
            g["topics"][NAMES[text]] = thread
    return groups


def build_message(groups):
    if not groups:
        return ("ID belum ditemukan. Pastikan bot sudah jadi admin di grup, lalu kirim /screener, /intel, "
                "/pasar di topik masing-masing (gunakan menu perintah bila ada), kemudian jalankan lagi.")
    lines = []
    for cid, g in groups.items():
        lines.append(f"Grup: {g['title']}\nGROUP_ID = {cid}")
        for label in ("SCREENER", "INTEL", "PASAR"):
            t = g["topics"].get(label)
            lines.append(f"TOPIK_{label} = {t}" if t else f"TOPIK_{label} = belum ketemu (kirim /{label.lower()} di topiknya)")
        lines.append("")
    lines.append("Simpan angka-angka ini. Jangan dibagikan ke orang lain.")
    return "\n".join(lines)


def main():
    if not (TOKEN and CHAT):
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID belum diisi.")
        sys.exit(1)
    data = api("getUpdates", timeout=0, allowed_updates='["message","edited_message"]')
    msg = build_message(collect(data.get("result", [])))
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHAT, "text": msg}, timeout=15)
    r.raise_for_status()
    print("Hasil dikirim ke Telegram pribadi.")


if __name__ == "__main__":
    main()
