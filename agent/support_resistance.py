"""
Support & Resistance engine — computed purely from price data.

Methods:
  1. Pivot Points (Classic + Fibonacci variants)
  2. Rolling swing highs/lows (last 60 bars)
  3. Volume-weighted price clusters (high-volume nodes are strong S/R)
  4. Round-number levels (psychological S/R)

These levels feed into:
  - Signal generation (buy near support, sell near resistance)
  - Stop loss placement (just below nearest support)
  - Target setting (nearest resistance above entry)
"""

from typing import Dict, List, Tuple
import math


def compute_levels(
    price_history: List[float],
    volume_history: List[int],
    current_price: float,
    high_history: List[float] = None,
    low_history: List[float] = None,
) -> Dict:
    """
    Returns a dict with support and resistance levels, ranked by strength.
    All levels computed from raw price data — no external inputs.
    """
    if len(price_history) < 20 or not current_price or current_price <= 0:
        return {"supports": [], "resistances": [], "nearest_support": 0,
                "nearest_resistance": 0, "distance_to_support_pct": 0,
                "distance_to_resistance_pct": 0}

    levels = []

    # ── 1. Pivot points (last 5 sessions) ─────────────────────────────────────
    if high_history and low_history and len(high_history) >= 2:
        for i in range(-5, -1):
            try:
                H = high_history[i]
                L = low_history[i]
                C = price_history[i]
                if H <= 0 or L <= 0:
                    continue
                pivot = (H + L + C) / 3
                r1 = 2 * pivot - L
                s1 = 2 * pivot - H
                r2 = pivot + (H - L)
                s2 = pivot - (H - L)
                levels.extend([
                    (pivot, "pivot",   2),
                    (r1,    "pivot_r1", 2),
                    (s1,    "pivot_s1", 2),
                    (r2,    "pivot_r2", 1),
                    (s2,    "pivot_s2", 1),
                ])
            except IndexError:
                pass

    # ── 2. Swing highs and lows (rolling window) ──────────────────────────────
    closes = price_history[-60:]
    highs  = high_history[-60:] if high_history else closes
    lows   = low_history[-60:]  if low_history  else closes
    window = 5

    for i in range(window, len(highs) - window):
        local_h = highs[i]
        if all(local_h >= highs[j] for j in range(i-window, i+window+1) if j != i):
            strength = _vol_strength(volume_history, i, len(price_history))
            levels.append((local_h, "swing_high", strength))

    for i in range(window, len(lows) - window):
        local_l = lows[i]
        if all(local_l <= lows[j] for j in range(i-window, i+window+1) if j != i):
            strength = _vol_strength(volume_history, i, len(price_history))
            levels.append((local_l, "swing_low", strength))

    # ── 3. Volume-weighted clusters ───────────────────────────────────────────
    if len(price_history) >= 20 and len(volume_history) >= 20:
        prices_vol = list(zip(price_history[-20:], volume_history[-20:]))
        prices_vol.sort(key=lambda x: x[1], reverse=True)
        for price, vol in prices_vol[:5]:   # top 5 volume nodes
            levels.append((price, "vol_node", 3))

    # ── 4. Round numbers (psychological levels) ───────────────────────────────
    if current_price > 0:
        mag = 10 ** (len(str(int(current_price))) - 2)   # e.g. ₹2845 → 100
        mag = max(mag, 10)
        base = math.floor(current_price / mag) * mag
        for mult in range(-3, 4):
            lvl = base + mult * mag
            if lvl > 0:
                levels.append((lvl, "round_number", 1))

    # ── Classify and rank ─────────────────────────────────────────────────────
    supports     = []
    resistances  = []
    tolerance    = current_price * 0.015   # 1.5% band

    for price, kind, strength in levels:
        if price <= 0:
            continue
        if price < current_price - tolerance:
            supports.append({"level": round(price, 2), "type": kind, "strength": strength})
        elif price > current_price + tolerance:
            resistances.append({"level": round(price, 2), "type": kind, "strength": strength})

    # Cluster nearby levels (within 0.5%)
    supports    = _cluster(supports, current_price)
    resistances = _cluster(resistances, current_price)

    # Sort: supports desc (nearest first), resistances asc (nearest first)
    supports.sort(key=lambda x: x["level"], reverse=True)
    resistances.sort(key=lambda x: x["level"])

    nearest_support    = supports[0]["level"]    if supports    else round(current_price * 0.96, 2)
    nearest_resistance = resistances[0]["level"] if resistances else round(current_price * 1.06, 2)

    return {
        "supports":           supports[:5],
        "resistances":        resistances[:5],
        "nearest_support":    nearest_support,
        "nearest_resistance": nearest_resistance,
        "distance_to_support_pct":    round((current_price - nearest_support)    / current_price * 100, 2),
        "distance_to_resistance_pct": round((nearest_resistance - current_price) / current_price * 100, 2),
    }


def _vol_strength(vol_history: List[int], idx: int, total_len: int) -> int:
    """Assign strength 1-3 based on volume at swing point."""
    if not vol_history or idx >= len(vol_history):
        return 1
    avg = sum(vol_history) / len(vol_history) if vol_history else 1
    vol_at_point = vol_history[idx] if idx < len(vol_history) else avg
    if vol_at_point > avg * 2:
        return 3
    elif vol_at_point > avg * 1.3:
        return 2
    return 1


def _cluster(levels: List[dict], current_price: float) -> List[dict]:
    """Merge levels within 0.5% of each other, summing their strength."""
    if not levels:
        return []
    merged = []
    band   = current_price * 0.005
    for lvl in sorted(levels, key=lambda x: x["level"]):
        if merged and abs(lvl["level"] - merged[-1]["level"]) <= band:
            # Merge: weighted average, sum strength
            a = merged[-1]
            total_s = a["strength"] + lvl["strength"]
            a["level"]    = round((a["level"] * a["strength"] + lvl["level"] * lvl["strength"]) / total_s, 2)
            a["strength"] = total_s
            a["type"]     = a["type"] + "+" + lvl["type"] if a["type"] != lvl["type"] else a["type"]
        else:
            merged.append(dict(lvl))
    return merged


def nearest_strong_support(levels: dict, min_strength: int = 2) -> float:
    """Return the nearest support level with strength >= min_strength."""
    for s in levels.get("supports", []):
        if s["strength"] >= min_strength:
            return s["level"]
    return levels.get("nearest_support", 0)


def nearest_strong_resistance(levels: dict, min_strength: int = 2) -> float:
    """Return the nearest resistance level with strength >= min_strength."""
    for r in levels.get("resistances", []):
        if r["strength"] >= min_strength:
            return r["level"]
    return levels.get("nearest_resistance", 0)
