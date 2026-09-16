"""
Recommendation outcome tracker — did the tool's OWN live recommendations come true?

This is the feedback loop that makes recommendations trustworthy and replicable:
the tool gives a call, then days later checks the real price to see if it hit its
target, hit its stop, or is still working. It accumulates an HONEST track record —
overall and per confidence band — so the dashboard can tell you exactly how
accurate the calls you'd actually replicate have been ("BUY calls at 80+ confidence
hit target 61% of the time"), instead of a vague promise.

It is separate from paper_trader (which simulates a full portfolio). This tracks
the RECOMMENDATION as you would trade it: enter at the rec price, win if target is
reached first, lose if stop is reached first, within the rec's horizon.

Storage: brain/rec_outcomes.json
  { "calls": [ {date, ticker, direction, entry, stop, target, confidence,
                horizon_days, evaluated, outcome, resolved_date, move_pct}, ...],
    "record": { "overall": {hit,miss,open,flat},
                "by_conf": { "80+": {...}, "70-80": {...}, "<70": {...} } } }

No LLM, no network — pure evaluation against price data the tool already fetches.
"""

import json
import os
from typing import Dict, List

from agent.config import BRAIN_DIR
from agent.trading_calendar import ist_today

REC_OUTCOMES_FILE = "brain/rec_outcomes.json"
MAX_CALLS_KEPT    = 800
DEFAULT_HORIZON   = 10     # trading days to give a swing rec to resolve


def _load() -> dict:
    from agent.io_safe import load_json_dict
    d = load_json_dict(REC_OUTCOMES_FILE)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("calls", [])
    d.setdefault("record", {"overall": {"hit": 0, "miss": 0, "open": 0, "flat": 0},
                            "by_conf": {}})
    return d


def _save(d: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    d["calls"] = d["calls"][-MAX_CALLS_KEPT:]
    with open(REC_OUTCOMES_FILE, "w") as f:
        json.dump(d, f, indent=2)


def _conf_band(conf: float) -> str:
    if conf >= 80:
        return "80+"
    if conf >= 70:
        return "70-80"
    return "<70"


def record_recommendations(recs: List[dict], stock_data: Dict) -> int:
    """Log each live recommendation as a forward-tested call. One per ticker/day.
    Records the exact plan (entry/stop/target/direction) so evaluation is objective."""
    data  = _load()
    today = ist_today().isoformat()
    seen  = {(c.get("ticker"), c.get("date")) for c in data["calls"]}
    added = 0
    for r in recs or []:
        ticker = r.get("ticker")
        sig    = r.get("signal")
        if sig not in ("BUY", "SELL") or not ticker or (ticker, today) in seen:
            continue
        # Entry = midpoint of the recommended entry ZONE (what you'd actually pay),
        # falling back to CMP if the zone isn't present.
        el, eh = r.get("entry_low"), r.get("entry_high")
        if el and eh:
            entry = (float(el) + float(eh)) / 2
        else:
            entry = r.get("entry") or r.get("cmp")
        stop   = r.get("stop_loss")
        target = r.get("target1") or r.get("target")
        if not (entry and stop and target):
            continue
        data["calls"].append({
            "date":         today,
            "ticker":       ticker,
            "direction":    1 if sig == "BUY" else -1,
            "entry":        round(float(entry), 2),
            "stop":         round(float(stop), 2),
            "target":       round(float(target), 2),
            "confidence":   round(float(r.get("confidence", 0) or 0), 1),
            "horizon_days": int(r.get("horizon_days", DEFAULT_HORIZON) or DEFAULT_HORIZON),
            "evaluated":    False,
            "outcome":      "",
        })
        added += 1
    if added:
        _save(data)
        print(f"[rec-track] recorded {added} recommendation(s) to forward-test")
    return added


def evaluate_recommendations(stock_data: Dict) -> int:
    """Score past recommendations against the real price. A call resolves when the
    price hits its target (HIT) or its stop (MISS); if neither within the horizon it
    is judged on where it ended (small profit=hit, else flat/miss). Uses the running
    session high/low so an intraday touch of the level counts, mirroring paper exits."""
    from datetime import date as _date, timedelta as _td
    from agent.trading_calendar import is_trading_day

    data  = _load()
    today = ist_today()

    def _age(d_iso: str) -> int:
        try:
            start = _date.fromisoformat(d_iso)
        except Exception:
            return 0
        n, probe = 0, start
        while probe < today and n <= 30:
            probe += _td(days=1)
            if is_trading_day(probe):
                n += 1
        return n

    scored = 0
    for c in data["calls"]:
        if c.get("evaluated"):
            continue
        latest = stock_data.get(c["ticker"], {}).get("latest", {})
        cur  = latest.get("current_price") or latest.get("close")
        high = latest.get("session_high") or latest.get("day_high") or latest.get("high", cur)
        low  = latest.get("session_low")  or latest.get("day_low")  or latest.get("low",  cur)
        if not cur:
            continue

        d1, entry, stop, target = c["direction"], c["entry"], c["stop"], c["target"]
        age = _age(c.get("date", ""))

        hit_target = (d1 == 1 and high >= target) or (d1 == -1 and low <= target)
        hit_stop   = (d1 == 1 and low  <= stop)   or (d1 == -1 and high >= stop)

        outcome = None
        if hit_target and hit_stop:
            outcome = "flat"          # both touched — ambiguous, don't over-credit
        elif hit_target:
            outcome = "hit"
        elif hit_stop:
            outcome = "miss"
        elif age >= c.get("horizon_days", DEFAULT_HORIZON):
            # horizon reached without touching either — judge on realised move
            move = (cur - entry) / entry * d1
            outcome = "hit" if move > 0.005 else ("miss" if move < -0.005 else "flat")

        if outcome is None:
            continue   # still working, within horizon — leave open

        c["evaluated"]     = True
        c["outcome"]       = outcome
        c["resolved_date"] = today.isoformat()
        c["move_pct"]      = round((cur - entry) / entry * 100 * d1, 2)
        scored += 1

        rec = data["record"]
        rec["overall"][outcome] = rec["overall"].get(outcome, 0) + 1
        band = rec["by_conf"].setdefault(_conf_band(c["confidence"]),
                                         {"hit": 0, "miss": 0, "open": 0, "flat": 0})
        band[outcome] = band.get(outcome, 0) + 1

    if scored:
        _save(data)
        print(f"[rec-track] scored {scored} recommendation(s) — {accuracy_str()}")
    return scored


def accuracy() -> dict:
    """Return the honest hit-rate record: overall and by confidence band.
    hit_rate = hits / (hits + misses), ignoring flats/opens (they prove nothing)."""
    data = _load()
    def _rate(bucket):
        h, m = bucket.get("hit", 0), bucket.get("miss", 0)
        decided = h + m
        return {
            "hit": h, "miss": m, "flat": bucket.get("flat", 0),
            "decided": decided,
            "hit_rate": round(h / decided, 3) if decided else None,
        }
    out = {"overall": _rate(data["record"]["overall"]), "by_conf": {}}
    for band, b in data["record"].get("by_conf", {}).items():
        out["by_conf"][band] = _rate(b)
    return out


def accuracy_str() -> str:
    a = accuracy()["overall"]
    if not a["decided"]:
        return "no calls resolved yet"
    return f"{a['hit']}/{a['decided']} target-first ({a['hit_rate']*100:.0f}%)"


def load_rec_outcomes() -> dict:
    return _load()
