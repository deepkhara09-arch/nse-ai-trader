"""
Monte Carlo outcome simulation — "from here, what are the odds?"

For a proposed trade (entry, stop, target), this projects thousands of possible
future price paths using the stock's OWN recent volatility plus the trade's
expected drift (the backtested recovery edge for a deep-dip). It then reports the
distribution of outcomes: how often the path hits the TARGET first, the STOP
first, or neither within the horizon, plus the expected and median return.

Why it matters for the goal (precise, replicable, trustworthy calls):
  • It turns a recommendation into a probability-of-success statement you can
    trust — "in 3,000 simulated futures this hits target 64% of the time".
  • It automatically DOWN-RANKS high-volatility setups: a jumpy stock can hit a
    wide stop by noise before the recovery plays out, and the sim exposes that
    (a 50/50 coin-flip) where the raw technicals look fine.

Pure standard library (random + math), no network. Deterministic per call via a
fixed seed so the same setup gives the same odds (reproducible/replicable).
"""

import math
import random
import statistics
from typing import List, Optional

# Backtested deep-dip recovery edge: ~+3.2% over ~40 trading days ≈ +0.08%/day.
# This is the drift a dip-buy path carries (it is NOT a random walk — it has a
# proven recovery bias). Non-dip trades pass drift=0 (no assumed edge).
DIP_DAILY_DRIFT = 0.0008
DEFAULT_RUNS    = 3000
DEFAULT_SEED    = 1234


def _daily_vol(price_history: List[float]) -> float:
    """Daily log-return standard deviation from recent closes. Falls back to a
    sane 1.5%/day if history is too short."""
    if not price_history or len(price_history) < 6:
        return 0.015
    rets = [math.log(price_history[i] / price_history[i - 1])
            for i in range(1, len(price_history))
            if price_history[i - 1] > 0 and price_history[i] > 0]
    if len(rets) < 5:
        return 0.015
    v = statistics.pstdev(rets)
    # Clamp to a believable band (0.4%–8%/day) so a data glitch can't distort odds.
    return max(0.004, min(0.08, v))


def simulate_trade(
    entry: float,
    stop: float,
    target: float,
    price_history: Optional[List[float]] = None,
    drift: float = DIP_DAILY_DRIFT,
    horizon_days: int = 40,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Project `runs` geometric-brownian paths from `entry`. Each step applies the
    stock's daily vol + drift; a path resolves the first time it touches target or
    stop, else it rides to the horizon. Returns the outcome distribution."""
    if entry <= 0 or target <= 0 or stop <= 0:
        return {"ok": False}
    rng = random.Random(seed)
    vol = _daily_vol(price_history)
    is_long = target > entry   # BUY if target above entry, else SELL/short

    hit_target = hit_stop = 0
    finals = []
    for _ in range(runs):
        p = entry
        resolved = False
        for _ in range(horizon_days):
            p *= math.exp(rng.gauss(drift if is_long else -drift, vol))
            if is_long:
                if p >= target:
                    hit_target += 1; resolved = True; break
                if p <= stop:
                    hit_stop += 1; resolved = True; break
            else:
                if p <= target:
                    hit_target += 1; resolved = True; break
                if p >= stop:
                    hit_stop += 1; resolved = True; break
        finals.append((p - entry) / entry * 100 * (1 if is_long else -1))

    finals.sort()
    decided = hit_target + hit_stop
    n = len(finals)
    return {
        "ok": True,
        "runs": runs,
        "daily_vol_pct":   round(vol * 100, 2),
        "target_pct":      round(hit_target / runs * 100, 1),   # % of paths hitting target first
        "stop_pct":        round(hit_stop / runs * 100, 1),
        "neither_pct":     round((runs - decided) / runs * 100, 1),
        # Win-rate among paths that actually RESOLVED (target vs stop) — the clean
        # read of "when this trade concludes, how often is it a winner?".
        "win_if_decided":  round(hit_target / decided * 100, 1) if decided else None,
        "expected_ret_pct": round(statistics.mean(finals), 2),
        "median_ret_pct":   round(statistics.median(finals), 2),
        "p25_ret_pct":      round(finals[n // 4], 2),
        "p75_ret_pct":      round(finals[n * 3 // 4], 2),
    }


def sim_grade(sim: dict) -> str:
    """One-word read of the simulated odds, for display/ranking."""
    if not sim or not sim.get("ok"):
        return "unknown"
    w = sim.get("win_if_decided")
    exp = sim.get("expected_ret_pct", 0)
    if w is None:
        return "flat"              # rarely resolves — drifts sideways
    if w >= 65 and exp > 0:
        return "strong"
    if w >= 55 and exp > 0:
        return "favourable"
    if w >= 45:
        return "coin-flip"
    return "unfavourable"
