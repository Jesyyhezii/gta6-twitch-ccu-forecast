"""gta6-twitch-ccu-forecast-01 entry: hour-1-conditioned local-linear
comparable-shape forecaster (v3).

Method (validated on the 4 practice cases and by leave-one-out CV on 37 past
launches; see rounds.md in the work directory):
  - Launch-day curves vary enormously with the size of hour 1: small-hour-1
    launches peak at 100-1000x hour 1, big-hour-1 launches at 1-3x. So each
    comparable launch is weighted by a Gaussian kernel on log(hour-1 viewers)
    around the target's own hour 1 (bandwidth 1.5 in log units).
  - For each hour h = 2..24, fit a kernel-weighted LOCAL-LINEAR regression of
    the log-ratios ln(viewers_h / viewers_1) on ln(hour-1) across the
    comparables, and predict at the target's own hour 1. The plain kernel
    mean (v2) is biased at the extremes of hour-1 size -- it is pulled
    toward the middle of the pool -- and the final (GTA VI) sits beyond the
    top of the pool, so the trend correction matters. The fitted slope is
    shrunk by 0.875 toward zero (a continuum between the kernel mean and
    full local-linear; tuned on CV + practice).
  - Spread: residual standard deviation around the shrunk fit, floored at
    0.25 and scaled by 0.75 (both tuned on CV + practice).
  - Quantiles: q_tau(h) = p1 * exp(mu_h + z_tau * sigma_h), p1 the case's
    hour-1 print, z the standard normal quantiles at 0.05/0.25/0.5/0.75/0.95.

Validation: practice loss 0.1763 (v2: 0.2103; house baseline 0.387; user's
current entry 0.235); LOO-CV pinball loss 0.2057 on h1 >= 100k launches
(n=16; v2: 0.2148), 0.1189 on the top-6 h1 >= 300k launches (v2: 0.1404),
0.2560 on h1 >= 20k (n=27; v2: 0.2525 -- slightly worse mid-range, where
slope noise costs a little; the final is not a mid-range launch).
Tried and rejected: tier weights (worse), channels kernel (no gain),
effective-sample-size shrinkage of the spread (worse), separate variance
bandwidth (worse), median-bias correction (CV better, practice worse --
contradictory, rejected), comparable-tier-only training (practice better,
CV much worse -- rejected), wider bandwidths (practice gains only from
the low-h1 practice case, do not transfer to the final).

The target's own history (the "target-history" comparable) is excluded: it is
the same category's pre-release history, not a launch-day curve. The four
practice launches are in the final case's comparables and are used like any
other launch; no practice answer is used anywhere in this program.

Interface: same as the kit starter. Reads every <id>.in in $MAKEFUN_INPUT_DIR
(or /input), writes <id>.out to $MAKEFUN_OUTPUT_DIR (or /output): 23 rows,
hours 2..24, five quantiles each. Standard library only, no network,
deterministic: two runs write identical bytes.
"""

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.6448536269514722, -0.6744897501960817, 0.0, 0.6744897501960817, 1.6448536269514722]

# Tuned on leave-one-out CV (37 launches) and the 4 practice cases.
BANDWIDTH = 1.5       # Gaussian kernel width on ln(hour-1 viewers)
SIGMA_FLOOR = 0.25    # minimum log-spread per hour
SIGMA_MULT = 0.75     # spread calibration (CV + practice)
SLOPE_SHRINK = 0.875  # local-linear slope shrunk toward 0 (0 = kernel mean)
BASE_SIGMA = 0.15     # fallback persistence spread per sqrt hour


def round2(x: float):
    """x rounded to 2 decimals, half up (same convention as the starter)."""
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value.is_integer() else value


def tier_of(comp: dict) -> str:
    unlock = comp.get("unlock") or ""
    for t in ("early-access-contaminated", "target-history", "comparable", "weak"):
        if f"Tier: {t}" in unlock:
            return t
    return "unknown"


def day_curve(comp: dict) -> dict:
    """{hour 1..24: viewers} for the comparable's launch UTC day."""
    ds = (comp.get("day_start") or "")[:10]
    out = {}
    for p in comp.get("hourly") or []:
        if not p.get("from") or p["from"][:10] != ds:
            continue
        h = int(p["from"][11:13]) + 1
        if 1 <= h <= 24 and p.get("viewers"):
            out[h] = float(p["viewers"])
    return out


def comparables(case: dict) -> list:
    """Usable launch-day curves: every comparable except the target's own
    history, with an hour-1 print and at least 20 launch-day hours."""
    out = []
    for c in case.get("comparables") or []:
        if tier_of(c) == "target-history":
            continue
        curve = day_curve(c)
        if 1 in curve and len(curve) >= 20:
            out.append(curve)
    return out


def hour_quantiles(p1: float, h: int, curves: list) -> list:
    """Kernel-weighted local-linear log-ratio quantiles for hour h, with
    the slope shrunk toward zero; persistence fallback when fewer than
    two comparables have that hour."""
    lt = math.log(max(1.0, p1))
    pairs = []
    for curve in curves:
        if h in curve and 1 in curve:
            lc = math.log(max(1.0, curve[1]))
            w = math.exp(-0.5 * ((lt - lc) / BANDWIDTH) ** 2)
            pairs.append((math.log(curve[h] / curve[1]), w, lc - lt))
    if len(pairs) >= 2:
        sw = sum(w for _, w, _ in pairs)
        sx = sum(w * x for _, w, x in pairs)
        sy = sum(w * v for v, w, _ in pairs)
        sxx = sum(w * x * x for _, w, x in pairs)
        sxy = sum(w * x * v for v, w, x in pairs)
        det = sw * sxx - sx * sx
        xbar, ybar = sx / sw, sy / sw
        slope = (sw * sxy - sx * sy) / det if abs(det) > 1e-12 else 0.0
        slope *= SLOPE_SHRINK
        mu = ybar - slope * xbar
        var = sum(w * (v - (ybar + slope * (x - xbar))) ** 2
                  for v, w, x in pairs) / sw
        sigma = max(SIGMA_FLOOR, math.sqrt(max(var, 0.0)) * SIGMA_MULT)
    else:
        mu = 0.0
        sigma = BASE_SIGMA * math.sqrt(h - 1)
    qs = []
    for z in Z:
        q = p1 * math.exp(mu + z * sigma)
        q = min(max(q, 0.01), 1e9)
        qs.append(round2(q))
    # rounding is monotone, but keep the non-decreasing contract explicit
    for i in range(1, 5):
        if qs[i] < qs[i - 1]:
            qs[i] = qs[i - 1]
    return qs


def forecast_case(case: dict) -> dict:
    if case.get("levels") != LEVELS:
        raise ValueError(f"levels {case.get('levels')} != {LEVELS}")
    first = next((k for k in case.get("known") or [] if k.get("hour") == 1), None)
    if first is None:
        raise ValueError("no hour-1 print")
    p1 = max(1.0, float(first["viewers"]))
    curves = comparables(case)
    rows = []
    for row in case["forecast"]:
        h = int(row["hour"])
        rows.append({"hour": h, "q": hour_quantiles(p1, h, curves)})
    return {"hours": rows}


def persistence_case(case: dict) -> dict:
    """Fallback forecast (house baseline shape) so a case never comes out
    missing or malformed: every hour at hour 1's print, widening spread."""
    first = next((k for k in case.get("known") or [] if k.get("hour") == 1), None)
    p1 = max(1.0, float(first["viewers"])) if first else 1.0
    rows = []
    for row in case.get("forecast") or []:
        h = int(row["hour"])
        rows.append({"hour": h, "q": [round2(p1 * math.exp(z * BASE_SIGMA * math.sqrt(h - 1))) for z in Z]})
    return {"hours": rows}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        try:
            forecast = forecast_case(case)
        except Exception:
            forecast = persistence_case(case)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
