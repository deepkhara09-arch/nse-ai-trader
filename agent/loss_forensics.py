"""
Outcome memory & loss forensics — learn WHY trades win AND lose, then let that
memory PUT A WEIGHT on the next buy/no-buy decision (it nudges, never dictates).

Two jobs:

1. LOSS POST-MORTEM  (diagnose_loss / record_outcomes)
   After every closed LOSS, attribute it to concrete, recurring CAUSES from the
   context the tool already stored — a weak/noise pattern was present, bought
   against a bearish tape, entered a high-VIX chop, stopped out immediately, no
   market tailwind, etc. Tallies causes so the tool can SEE its failure modes.

2. PATTERN EDGE MEMORY  (record_outcomes builds it, pattern_edge_weight reads it)
   For every pattern, remember wins vs losses AND the average PnL% of each. From
   that it derives a net "edge" per pattern: patterns that have repeatedly made
   money get a small positive weight; patterns that have repeatedly lost get a
   small negative one. `pattern_edge_weight()` blends the edges of a candidate's
   patterns into ONE bounded nudge the ranking engine adds to the score. It only
   bites once a pattern has enough samples, so one unlucky/lucky trade never moves
   it — exactly "not the very next trade decides it, but it weighs the decision."

No LLM, no network — pure analysis of data the tool already collected. Everything
is bounded and explainable, and it can only nudge the score, never veto a trade.

Storage: brain/loss_forensics.json
  { "causes":  { "<cause>": {count, avg_loss_pct, last, examples[]} },
    "patterns":{ "<pat>":   {wins, losses, sum_win_pct, sum_loss_pct, edge} },
    "recent":  [ {date, ticker, loss_pct, causes[]}, ... last 60 ] }
"""

import json
import os
from typing import Dict, List

from agent.config import BRAIN_DIR
from agent.trading_calendar import ist_today

LOSS_FILE = "brain/loss_forensics.json"

# Patterns that are inherently low-conviction / noise — their presence on a losing
# trade is a red flag the setup was thin. (Kept explicit + auditable.)
_WEAK_PATTERNS = {
    "low_volume_drift", "adx_ranging_market", "spinning_top", "doji",
    "inside_bar", "vwap_magnet", "pivot_point_test",
}

# A pattern needs at least this many closed trades before its edge is allowed to
# move any decision — protects against reacting to one lucky/unlucky trade.
_MIN_SAMPLES = 4
# Hard cap on how much the whole outcome memory can shift a rank score (points on
# the 0-100 composite). Deliberately small: it advises, it does not decide.
_MAX_NUDGE = 3.0


def _load() -> dict:
    from agent.io_safe import load_json_dict
    d = load_json_dict(LOSS_FILE)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("causes", {})
    d.setdefault("patterns", {})
    d.setdefault("recent", [])
    return d


def _save(d: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    d["recent"] = d["recent"][-60:]
    with open(LOSS_FILE, "w") as f:
        json.dump(d, f, indent=2)


# ── 1. Loss post-mortem ──────────────────────────────────────────────────────

def diagnose_loss(trade: dict) -> List[str]:
    """Return the concrete cause(s) this losing trade most likely failed for.
    Deterministic and explainable — each cause maps to real, stored evidence."""
    causes = []
    em      = trade.get("entry_market", {}) or {}
    pats    = set(trade.get("patterns", []) or [])
    action  = trade.get("action", "BUY")
    reason  = trade.get("exit_reason", "")
    days    = trade.get("open_days", 0)

    # 1. Thin setup — a known weak/noise pattern was in the mix.
    if pats & _WEAK_PATTERNS:
        causes.append(f"weak_pattern:{sorted(pats & _WEAK_PATTERNS)[0]}")

    # 2. Fought the market mood (bought a bearish tape / shorted a bullish one).
    mood = em.get("mood") or em.get("regime") or trade.get("market_mood")
    if action == "BUY" and mood == "bearish":
        causes.append("bought_bearish_tape")
    elif action == "SELL" and mood == "bullish":
        causes.append("shorted_bullish_tape")

    # 3. High-volatility chop at entry — stops get run in these regimes.
    vix = em.get("vix")
    try:
        if vix is not None and float(vix) >= 18:
            causes.append("entered_high_vix")
    except (TypeError, ValueError):
        pass

    # 4. Failed FAST — thesis was wrong almost immediately (not a slow fade).
    if reason == "stop_hit" and days is not None and days <= 1:
        causes.append("failed_immediately")

    # 5. Reversal we caught (thesis broke) — a GOOD kind of loss, tag separately.
    if reason == "reversal_exit":
        causes.append("thesis_reversed_cut_early")

    # 6. Nifty had no upward trend to ride at entry.
    if em.get("nifty_trend") in ("sideways", "down", "strong_down") and action == "BUY":
        causes.append("no_market_tailwind")

    if not causes:
        causes.append("clean_setup_still_lost")   # nothing obviously wrong — noise
    return causes


# ── 2. Outcome recorder (wins + losses) ──────────────────────────────────────

def record_outcomes(closed_trades: List[dict]) -> int:
    """Post-mortem every not-yet-analysed LOSS, AND fold every not-yet-analysed
    trade (win or loss) into the per-pattern edge memory. Idempotent via a
    'forensic_done' flag written onto each trade dict. Returns new-analysed count."""
    data = _load()
    seen = {(r.get("ticker"), r.get("date")) for r in data["recent"]}
    added = 0
    for t in closed_trades:
        if t.get("forensic_done"):
            continue
        won      = bool(t.get("won", t.get("pnl", 0) > 0))
        pnl_pct  = t.get("pnl_pct", 0)
        ticker   = t.get("ticker", "").replace(".NS", "")

        # (a) per-pattern edge memory — learns what worked and what didn't
        for pat in (t.get("patterns", []) or []):
            pp = data["patterns"].setdefault(
                pat, {"wins": 0, "losses": 0, "sum_win_pct": 0.0, "sum_loss_pct": 0.0})
            if won:
                pp["wins"] += 1
                pp["sum_win_pct"] += abs(pnl_pct)
            else:
                pp["losses"] += 1
                pp["sum_loss_pct"] += abs(pnl_pct)
            pp["edge"] = _pattern_edge(pp)

        # (b) loss post-mortem — only for losses
        if not won:
            causes = diagnose_loss(t)
            for c in causes:
                cc = data["causes"].setdefault(
                    c, {"count": 0, "sum_loss_pct": 0.0, "last": "", "examples": []})
                cc["count"] += 1
                cc["sum_loss_pct"] += abs(pnl_pct)
                cc["avg_loss_pct"] = round(cc["sum_loss_pct"] / cc["count"], 2)
                cc["last"] = ist_today().isoformat()
                if ticker and ticker not in cc["examples"]:
                    cc["examples"] = (cc["examples"] + [ticker])[-6:]
            key = (ticker, t.get("close_date"))
            if key not in seen:
                data["recent"].append({
                    "date":     t.get("close_date"),
                    "ticker":   ticker,
                    "loss_pct": round(abs(pnl_pct), 2),
                    "causes":   causes,
                })
                seen.add(key)

        t["forensic_done"] = True
        added += 1

    if added:
        _save(data)
        print(f"[forensics] analysed {added} closed trade(s) — top loss cause: {top_cause(data)}")
    return added


# Backwards-compatible alias (older callers may import record_losses).
record_losses = record_outcomes


def _pattern_edge(pp: dict) -> float:
    """Net edge for one pattern in [-1, 1]. Combines HOW OFTEN it wins with HOW
    BIG the wins vs losses are (expectancy). Returns 0 until it has enough data."""
    n = pp["wins"] + pp["losses"]
    if n < _MIN_SAMPLES:
        return 0.0
    wr = pp["wins"] / n                                   # win rate 0..1
    avg_win  = pp["sum_win_pct"]  / pp["wins"]   if pp["wins"]   else 0.0
    avg_loss = pp["sum_loss_pct"] / pp["losses"] if pp["losses"] else 0.0
    # Expectancy in %: what this pattern makes on average per trade.
    expectancy = wr * avg_win - (1 - wr) * avg_loss
    # Squash to [-1, 1]; ±3% expectancy ≈ near the caps.
    edge = max(-1.0, min(1.0, expectancy / 3.0))
    return round(edge, 3)


# ── 3. Decision weight the ranking engine consumes ───────────────────────────

def pattern_edge_weight(patterns: List[str]) -> float:
    """Blend the learned edges of a candidate's patterns into ONE bounded nudge
    (in composite-score points, roughly [-_MAX_NUDGE, +_MAX_NUDGE]). Positive =
    these setups have historically made money, rank it a little higher; negative =
    they've historically lost, rank it a little lower. Advisory only — it can never
    swing a decision on its own, and returns 0 until patterns have real history."""
    try:
        data = _load()
        pmem = data.get("patterns", {})
        edges = []
        for p in (patterns or []):
            info = pmem.get(p)
            if info and (info.get("wins", 0) + info.get("losses", 0)) >= _MIN_SAMPLES:
                edges.append(info.get("edge", 0.0))
        if not edges:
            return 0.0
        avg_edge = sum(edges) / len(edges)
        return round(avg_edge * _MAX_NUDGE, 3)
    except Exception:
        return 0.0


def weak_setup_caution(patterns: List[str]) -> float:
    """Legacy helper (kept for any existing caller): a small penalty if a KNOWN
    weak pattern has repeatedly caused losses. pattern_edge_weight now subsumes
    this more generally, but this stays for the loss-cause path."""
    try:
        data = _load()
        pats = set(patterns or [])
        worst = 0.0
        for p in (pats & _WEAK_PATTERNS):
            info = data.get("causes", {}).get(f"weak_pattern:{p}")
            if info and info.get("count", 0) >= 3:
                worst = max(worst, min(1.5, info["count"] * 0.25))
        return worst
    except Exception:
        return 0.0


# ── Read helpers (dashboard / diagnostics) ───────────────────────────────────

def top_cause(data: dict = None) -> str:
    data = data or _load()
    causes = data.get("causes", {})
    if not causes:
        return "none yet"
    c, info = max(causes.items(), key=lambda kv: kv[1]["count"])
    return f"{c} ({info['count']}x, avg -{info.get('avg_loss_pct',0)}%)"


def load_forensics() -> dict:
    return _load()
