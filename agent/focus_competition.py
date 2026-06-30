"""
Focus Competition log — an honest, visible record of every change to the focus
list, and why it happened.

The tool runs a perpetual competition: background batches re-scan the whole
universe and a clearly-stronger candidate can unseat a weaker focus stock. This
module records each promotion/demotion (with the reason and the scores involved)
so the dashboard can show WHY the focus list evolved over time — never a silent,
unexplained change. Honesty first: every swap is logged with its real driver.

Storage: brain/focus_competition.json
  [ {ts, action: promoted|demoted, ticker, reason, score, replaced, ...}, ... ]
"""

import json
import os

from agent.config import BRAIN_DIR
from agent.trading_calendar import ist_now

LOG_FILE = "brain/focus_competition.json"
_MAX_EVENTS = 300   # audit/display log — capped (learning data lives elsewhere)


def record_focus_changes(promoted, demoted, scores=None, driver="competition"):
    """
    Append one event per promotion/demotion. `scores` maps ticker -> composite
    score (optional). `driver` explains the mechanism (competition / bottom_rank /
    initial_selection). Never raises — logging must not break a run.
    """
    if not promoted and not demoted:
        return
    try:
        events = _load()
        ts = ist_now().strftime("%Y-%m-%d %H:%M IST")
        scores = scores or {}
        # Pair demoted→promoted where possible so the log reads as a head-to-head.
        for i, tkr in enumerate(promoted or []):
            replaced = demoted[i] if demoted and i < len(demoted) else None
            events.append({
                "ts":       ts,
                "action":   "promoted",
                "ticker":   tkr.replace(".NS", ""),
                "score":    round(scores.get(tkr, 0), 1),
                "replaced": replaced.replace(".NS", "") if replaced else None,
                "driver":   driver,
                "reason":   _promote_reason(tkr, replaced, scores, driver),
            })
        # Any demoted not already paired with a promotion.
        paired = set((demoted or [])[:len(promoted or [])])
        for tkr in (demoted or []):
            if tkr in paired:
                continue
            events.append({
                "ts":       ts,
                "action":   "demoted",
                "ticker":   tkr.replace(".NS", ""),
                "score":    round(scores.get(tkr, 0), 1),
                "replaced": None,
                "driver":   driver,
                "reason":   "Dropped from focus — persistently weak ranking.",
            })
        events = events[-_MAX_EVENTS:]
        _save(events)
        print(f"[competition] logged {len(promoted or [])} promo / {len(demoted or [])} demo events")
    except Exception as e:
        print(f"[competition] log failed (non-fatal): {e}")


def _promote_reason(tkr, replaced, scores, driver):
    t = tkr.replace(".NS", "")
    if driver == "competition" and replaced:
        cs = scores.get(tkr, 0)
        rs = scores.get(replaced, 0)
        r = replaced.replace(".NS", "")
        if cs and rs:
            return (f"{t} beat {r} on the composite score "
                    f"({cs:.1f} vs {rs:.1f}) — a background batch found it stronger.")
        return f"{t} unseated {r} — a background batch found it stronger."
    if driver == "bottom_rank" and replaced:
        return f"{t} promoted to replace a persistently bottom-ranked focus stock."
    return f"{t} promoted into focus."


def _load() -> list:
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                d = json.load(f)
            return d if isinstance(d, list) else []
        except Exception:
            return []
    return []


def _save(events: list) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(LOG_FILE, "w") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)


def load_focus_competition() -> list:
    return _load()
