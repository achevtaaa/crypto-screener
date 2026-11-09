# Tim Trading Otomatis (Telegram + GitHub Actions)

Kumpulan bot gratis yang berjalan sendiri di GitHub Actions dan mengirim pesan ke satu grup Telegram bertopik.
Semua alert adalah petunjuk untuk dicek sendiri, bukan sinyal beli atau jual.

## Topik dan isinya

| Topik | Isi | Skrip | Jadwal |
|---|---|---|---|
| ⚡ Screener | Koin oversold (Stoch 5,3,3 + EMA 13/21, TF 1D + 4H, top 150), skor 1-10, tanda bandar, hasil backtest | screener.py, backtest.py, backtest_vol.py | 07:00 dan 19:00 WIB; backtest tiap Minggu |
| 🔥 Intel | 🚨 whale Hyperliquid, 🔄 rotasi whale, ❗️ berita tokoh dari 13 situs (diterjemahkan ke Indonesia), 🐳 wallet besar, 💵 stablecoin, 🧨 funding ekstrem, 💤 koin bangun tidur, 🕵️ wallet pantauan | whales.py, intel2.py | tiap 30 menit |
| 💸 Pasar | Kalender makro + skenario dampak, lampu lalu lintas 🟢🟡🔴, ringkasan pagi, "bot masih hidup" tiap Minggu | pasar.py | tiap 30 menit |
| 🧭 Narasi | Ringkasan harian narasi yang naik (CoinGecko) | pasar.py | harian |

## File penting
- `.github/workflows/*.yml`: jadwal tiap bot (screener, whales, pasar, intel2, backtest, find_ids).
- `routes.json`: ID grup dan ID topik (dibuat dengan workflow "Cari ID"). Tanpa file ini, pesan masuk ke chat pribadi.
- `bantu.py`: helper bersama (terjemah berita ke Indonesia, label narasi koin). `narasi_map.json` dibuat otomatis seminggu sekali.
- `wallets.json`: daftar wallet pantauan. Isi hanya alamat yang sudah diverifikasi dari sumber publik.
- `*_state.json`, `backtest*_stats.json`, `screener_log.csv`: dibuat otomatis oleh bot, jangan diedit.

## Keamanan
- Token bot dan chat ID hanya di GitHub Secrets (`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`). Jangan masukkan ke file mana pun.
- Aktifkan 2FA di akun GitHub.
- Repo Public: semua kode dan file state bisa dilihat orang. Jangan taruh data pribadi.

## Perawatan
- Cek pesan "BOT MASIH HIDUP" di topik Pasar tiap Minggu. Kalau tidak muncul, buka tab Actions.
- GitHub bisa menonaktifkan workflow terjadwal di repo yang tidak aktif 60 hari. Bot kita rutin commit state, jadi risikonya kecil.

## Batasan yang jujur
- Backtest pertama menunjukkan skor screener belum lebih baik dari entry acak. Pakai screener sebagai daftar kandidat.
- Sinyal "koin bangun tidur" juga diuji lewat `backtest_vol.py`. Baca hasilnya sebelum memakainya.
- Tanda bandar, lampu lalu lintas, dan skenario dampak adalah heuristik, bukan ramalan.
- Forecast kalender berasal dari feed publik dan bisa telat atau salah.
