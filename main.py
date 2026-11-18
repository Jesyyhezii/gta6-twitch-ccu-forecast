"""Perkiraan penonton Twitch per jam di hari peluncuran GTA VI.

Cara kerja: bentuk kurva hari peluncuran sangat bergantung pada besarnya
jam pertama. Peluncuran kecil memuncak ratusan kali jam pertamanya,
peluncuran besar hanya beberapa kali. Jadi untuk setiap jam setelah jam
pertama, model ini mengumpulkan rasio log penonton tiap peluncuran
pembanding terhadap jam pertamanya, memberi bobot kernel Gaussian pada
log ukuran jam pertama (lebar 1,5), lalu memasang garis lurus berbobot
antara rasio log dan selisih log ukuran jam pertama, dan membaca garis
itu pada ukuran jam pertama target sendiri.

Kemiringan garis ditarik 87,5% ke arah datar karena ujung-ujungnya
berisik. Sebaran di sekitar garis (dikalikan 0,75, lantai 0,25) menjadi
lebar pita kuantil normal. Riwayat kategori pra-rilis target sendiri
bukan peluncuran, jadi dikeluarkan dari kumpulan pembanding.

Parameter yang tervalidasi (round 6): bw=1,5, sigma_mult=0,75,
slope shrink=0,875, sigma floor=0,25. Practice loss 0,1763.
"""

import glob
import json
import math
import os
from datetime import datetime, timezone

# Titik-titik z untuk kuantil 5/25/50/75/95 persen.
Z_SCORES = (-1.6448536269514722, -0.6744897501960817, 0.0,
            0.6744897501960817, 1.6448536269514722)

BANDWIDTH = 1.5       # lebar kernel Gaussian pada log ukuran jam pertama
SIGMA_FLOOR = 0.25    # lebar pita minimum (skala log)
SIGMA_MULT = 0.75     # pengali sebaran di sekitar garis
SLOPE_SHRINK = 0.875  # penarik kemiringan ke arah datar
PERSIST_SPREAD = 0.15  # lebar pita fallback saat data kurang
MAX_VIEWERS = 1e9


def _to_cents(value):
    """Bulatkan ke sen terdekat, seperti format keluaran yang divalidasi."""
    return math.floor(value * 100.0 + 0.5) / 100.0


def _hour_slot(day_start, stamp):
    """Jam ke berapa (1..24) sebuah titik waktu jatuh di hari peluncuran."""
    try:
        start = datetime.fromisoformat(day_start.replace("Z", "+00:00"))
        at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    slot = int((at - start).total_seconds() // 3600) + 1
    return slot if 1 <= slot <= 24 else None


def _series_of(comp):
    """{jam: penonton} untuk hari peluncuran satu pembanding."""
    series = {}
    day_start = comp.get("day_start") or ""
    for point in comp.get("hourly") or []:
        if not isinstance(point, dict):
            continue
        hour = _hour_slot(day_start, point.get("from") or "")
        viewers = point.get("viewers")
        if hour is not None and isinstance(viewers, (int, float)) and viewers > 0:
            series[hour] = float(viewers)
    return series


def _is_own_past(comp):
    """True kalau pembanding ini riwayat target sendiri, bukan peluncuran."""
    return "target-history" in (comp.get("unlock") or "")


def _comparable_series(case):
    """Kumpulan kurva peluncuran pembanding yang lengkap (jam 1 ada, >=20 jam)."""
    pool = []
    for comp in case.get("comparables") or []:
        if not isinstance(comp, dict) or _is_own_past(comp):
            continue
        series = _series_of(comp)
        if 1 in series and len(series) >= 20:
            pool.append(series)
    return pool


def _opening_viewers(case):
    """Penonton jam pertama dari baris known; 1.0 kalau tidak ditemukan."""
    for row in case.get("known") or []:
        if isinstance(row, dict) and row.get("hour") == 1:
            viewers = row.get("viewers")
            if isinstance(viewers, (int, float)) and viewers > 0:
                return float(viewers)
    return 1.0


def _hours_to_forecast(case):
    """Jam yang diminta di blok forecast; default jam 2..24."""
    hours = []
    for row in case.get("forecast") or []:
        if isinstance(row, dict) and "hour" in row:
            try:
                hours.append(int(row["hour"]))
            except (TypeError, ValueError):
                continue
    return hours or list(range(2, 25))


def _fit_hour(anchor, pool, hour):
    """Pasang garis berbobot untuk satu jam; kembalikan (mu, sigma) log-ratio.

    x = selisih log ukuran jam pertama pembanding vs target,
    y = log rasio penonton jam ini terhadap jam pertama,
    w = bobot kernel Gaussian pada x.
    Garis y = mu + b*x dipasang berbobot; mu dibaca pada x=0 (target
    sendiri), b ditarik ke arah datar. sigma dari sebaran berbobot
    di sekitar garis.
    """
    anchor_log = math.log(max(anchor, 1.0))
    points = []
    for series in pool:
        if hour not in series:
            continue
        gap = math.log(series[1]) - anchor_log
        growth = math.log(series[hour] / series[1])
        weight = math.exp(-0.5 * (gap / BANDWIDTH) ** 2)
        points.append((gap, growth, weight))
    if len(points) < 2:
        return None
    total_w = sum(w for _, _, w in points)
    if not (total_w > 1e-300) or not math.isfinite(total_w):
        return None
    x_bar = sum(w * x for x, _, w in points) / total_w
    y_bar = sum(w * y for _, y, w in points) / total_w
    sxx = sum(w * (x - x_bar) ** 2 for x, _, w in points)
    if sxx > 1e-12:
        sxy = sum(w * (x - x_bar) * (y - y_bar) for x, y, w in points)
        slope = sxy / sxx
    else:
        slope = 0.0
    slope *= SLOPE_SHRINK
    mu = y_bar - slope * x_bar
    resid = sum(w * (y - (y_bar + slope * (x - x_bar))) ** 2
                for x, y, w in points) / total_w
    sigma = max(SIGMA_FLOOR, math.sqrt(max(resid, 0.0)) * SIGMA_MULT)
    if not (math.isfinite(mu) and math.isfinite(sigma)):
        return None
    return mu, sigma


def _quantile_row(anchor, mu, sigma):
    """Lima kuantil penonton dari (mu, sigma) log-ratio, dibulatkan ke sen."""
    base = max(anchor, 1.0)
    values = []
    for z in Z_SCORES:
        value = base * math.exp(mu + z * sigma)
        values.append(_to_cents(min(max(value, 0.01), MAX_VIEWERS)))
    for i in range(1, 5):
        if values[i] < values[i - 1]:
            values[i] = values[i - 1]
    return values


def _persistence_row(anchor, hour):
    """Fallback: jam pertama dipertahankan, pita melebar dengan akar waktu."""
    return _quantile_row(anchor, 0.0, PERSIST_SPREAD * math.sqrt(hour - 1))


def forecast(case):
    """Hasilkan {"hours": [{"hour": h, "q": [..5..]}]} untuk satu kasus."""
    anchor = _opening_viewers(case)
    pool = _comparable_series(case)
    rows = []
    for hour in _hours_to_forecast(case):
        try:
            fitted = _fit_hour(anchor, pool, hour)
            if fitted is None:
                quantiles = _persistence_row(anchor, hour)
            else:
                mu, sigma = fitted
                quantiles = _quantile_row(anchor, mu, sigma)
        except Exception:
            quantiles = _persistence_row(anchor, hour)
        rows.append({"hour": hour, "q": quantiles})
    return {"hours": rows}


def _flat_output():
    """Keluaran darurat kalau sebuah kasus tidak bisa dibaca sama sekali."""
    return {"hours": [{"hour": h, "q": [1.0] * 5} for h in range(2, 25)]}


def run():
    """Baca semua *.in dari MAKEFUN_INPUT_DIR, tulis *.out ke MAKEFUN_OUTPUT_DIR."""
    src = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    dst = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(dst, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(src, "*.in"))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, encoding="utf-8") as handle:
                case = json.load(handle)
            result = forecast(case)
        except Exception:
            result = _flat_output()
        with open(os.path.join(dst, name + ".out"), "w", encoding="utf-8") as handle:
            json.dump(result, handle, separators=(",", ":"))
            handle.write("\n")


if __name__ == "__main__":
    run()
