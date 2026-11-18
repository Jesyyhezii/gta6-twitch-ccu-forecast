# Perkiraan penonton Twitch GTA VI di hari rilis

Entri untuk kompetisi `gta6-twitch-ccu-forecast-01`. Tugasnya: menebak
berapa penonton kategori Grand Theft Auto VI di Twitch pada tiap jam
hari peluncuran, jam 2 sampai 24, dalam bentuk lima kuantil per jam
(5%, 25%, 50%, 75%, 95%).

## Gagasan model

Kurva satu hari peluncuran sangat ditentukan oleh jam pertamanya:
peluncuran yang dibuka kecil bisa memuncak ratusan kali lipat dari jam
pertama, sedangkan peluncuran yang dibuka besar hanya naik beberapa
kali. Jadi model ini tidak mencampur semua peluncuran lama begitu saja.

Untuk tiap jam, model mengumpulkan semua peluncuran pembanding, lalu:

1. memberi bobot pada tiap pembanding dengan kernel Gaussian pada
   selisih log jumlah penonton jam pertama (lebar 1,5) — pembanding
   yang bukanya mirip dengan target berbobot paling besar;
2. memasang garis lurus berbobot antara log pertumbuhan jam itu dan
   selisih log ukuran jam pertama, lalu membaca garis tersebut tepat
   pada ukuran jam pertama target;
3. menarik kemiringan garis 87,5% ke arah datar, karena kemiringan di
   ujung-ujung sebaran itu berisik;
4. memakai sebaran berbobot di sekitar garis (dikalikan 0,75, dengan
   lantai 0,25) sebagai lebar pita kuantil normal.

Riwayat kategori pra-rilis target sendiri bukan sebuah peluncuran dan
tidak dipakai sebagai pembanding.

## Menjalankan

```sh
MAKEFUN_INPUT_DIR=<dir berisi *.in> MAKEFUN_OUTPUT_DIR=<dir keluaran> python3 main.py
```

Satu file `*.out` ditulis untuk tiap kasus `*.in`. Kalau sebuah kasus
rusak atau sebuah jam kekurangan pembanding, model jatuh ke tebakan
bertahan (jam pertama dipertahankan, pita melebar mengikuti akar
waktu) supaya tidak pernah gagal total.

## Hasil validasi

- Practice loss (4 kasus latihan, pinball loss skala log): **0,1763**
  (per kasus 0,1837 / 0,2097 / 0,1311 / 0,1809).
- Baseline bawaan kit pada kasus yang sama: 0,3871.
- Cross-validation leave-one-out pada 37 peluncuran lama: 0,2057 untuk
  target berjam-pertama >= 100 ribu, 0,1189 untuk 6 target terbesar.
- Deterministik: dua kali jalan menghasilkan byte yang identik.
- Hanya pustaka standar Python; tanpa dependensi luar.

## Berkas

- `main.py` — seluruh entri.
- `LICENSE` — MIT.
