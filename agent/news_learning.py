"""
News-outcome learning — does news sentiment actually PREDICT this stock's moves?

The tool scores headlines into a sentiment signal, but a scorer can chronically
misread a particular stock (sarcastic coverage, ambiguous sector words, chronic
hype). This module closes the loop the same honest way the dry-decision
forward-tester does:

  1. Each preclose, every focus stock with a MEANINGFUL news signal
     (|score| >= CALL_MIN_SCORE) gets a "news call" recorded: date, direction
     implied by the sentiment, and the price at call time. Predict first.
  2. EVAL_TRADING_DAYS later the call is scored against the real price:
     moved >= +BAND in the news direction → the news was RIGHT
     moved <= -BAND against it            → the news was WRONG
     inside the band                      → flat, no lesson.
  3. Per-stock reliability (recency-decayed, same half-life as patterns) then
     SCALES how much weight news gets in the brain's scoring for that stock —
     predictive news counts up to ~1.4x, noisy news fades toward 0.6x.

Storage: brain/news_calls.json
  { "calls": [ {date, ticker, direction, score, price_at_call,
                evaluated, outcome}, ... capped ],
    "reliability": { ticker: {wins, losses, reliability, last_seen} } }
"""

import json
import os
from typing import Dict, List

from agent.config import BRAIN_DIR, PATTERN_DECAY_HALFLIFE_DAYS
from agent.trading_calendar import ist_today

NEWS_CALLS_FILE   = "brain/news_calls.json"
CALL_MIN_SCORE    = 0.2     # |sentiment| below this is too weak to be a "call"
EVAL_TRADING_DAYS = 3       # news impact is fast — judge within ~3 trading days
BAND              = 0.01    # ±1% noise band — smaller moves prove nothing
MAX_CALLS_KEPT    = 500


def _load() -> dict:
    from agent.io_safe import load_json_dict
    d = load_json_dict(NEWS_CALLS_FILE)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("calls", [])
    d.setdefault("reliability", {})
    return d


def _save(d: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    d["calls"] = d["calls"][-MAX_CALLS_KEPT:]
    with open(NEWS_CALLS_FILE, "w") as f:
        json.dump(d, f, indent=2)


def record_news_calls(focus: List[str], news_data: Dict, stock_data: Dict) -> int:
    """At preclose: log a directional 'call' for each focus stock whose news
    sentiment is strong enough to influence scoring. One call per ticker per day."""
    data  = _load()
    today = ist_today().isoformat()
    seen  = {(c.get("ticker"), c.get("date")) for c in data["calls"]}
    added = 0
    for t in focus:
        sent  = news_data.get(t, {}).get("latest", news_data.get(t, {})) or {}
        score = sent.get("weighted_score", sent.get("score", 0)) or 0
        if abs(score) < CALL_MIN_SCORE or (t, today) in seen:
            continue
        price = (stock_data.get(t, {}).get("latest", {}) or {}).get("close")
        if not price:
            continue
        data["calls"].append({
            "date": today, "ticker": t,
            "direction": 1 if score > 0 else -1,
            "score": round(score, 3),
            "price_at_call": round(price, 2),
            "evaluated": False, "outcome": "",
        })
        added += 1
    if added:
        _save(data)
        print(f"[news-learn] recorded {added} news call(s) to forward-test")
    return added


def evaluate_news_calls(stock_data: Dict) -> int:
    """Score past news calls against the real price and update per-stock news
    reliability (recency-decayed). Flat moves teach nothing."""
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
        while probe < today and n <= 15:
            probe += _td(days=1)
            if is_trading_day(probe):
                n += 1
        return n

    scored = 0
    for c in data["calls"]:
        if c.get("evaluated"):
            continue
        if _age(c.get("date", "")) < EVAL_TRADING_DAYS:
            continue
        latest = stock_data.get(c["ticker"], {}).get("latest", {})
        cur = latest.get("current_price") or latest.get("close")
        if not cur:
            continue
        fwd = (cur - c["price_at_call"]) / c["price_at_call"] * c["direction"]
        if abs(fwd) < BAND:
            c["evaluated"] = True; c["outcome"] = "flat"
            continue
        right = fwd > 0
        c["evaluated"] = True
        c["outcome"]   = "right" if right else "wrong"
        scored += 1

        rel = data["reliability"].setdefault(
            c["ticker"], {"wins": 0.0, "losses": 0.0, "reliability": 0.5, "last_seen": ""})
        # Recency decay — same philosophy as pattern learning
        try:
            last = _date.fromisoformat(rel.get("last_seen") or c["date"])
            gap  = max(0, (today - last).days)
        except Exception:
            gap = 0
        if gap > 0:
            decay = 0.5 ** (gap / PATTERN_DECAY_HALFLIFE_DAYS)
            rel["wins"]   = round(rel["wins"] * decay, 3)
            rel["losses"] = round(rel["losses"] * decay, 3)
        if right:
            rel["wins"] = round(rel["wins"] + 1, 3)
        else:
            rel["losses"] = round(rel["losses"] + 1, 3)
        total = rel["wins"] + rel["losses"]
        prior = max(3 - total, 0)
        rel["reliability"] = round((rel["wins"] + prior * 0.5) / (total + prior), 3)
        rel["last_seen"]   = today.isoformat()

    if scored:
        _save(data)
        print(f"[news-learn] scored {scored} news call(s) against real price moves")
    elif any(c.get("evaluated") and c.get("outcome") == "flat" for c in data["calls"]):
        _save(data)   # persist flat markings too
    return scored


def news_weight(ticker: str) -> float:
    """Multiplier for the news component in signal scoring: 1.0 when unknown,
    up to ~1.4x when news has proven predictive on this stock, down to ~0.6x
    when it's proven noise. Never zero — news can regain trust."""
    rel = _load().get("reliability", {}).get(ticker)
    if not rel:
        return 1.0
    return round(max(0.6, min(1.4, 0.2 + 1.6 * rel.get("reliability", 0.5))), 2)
