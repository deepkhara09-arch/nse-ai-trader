"""
Recommendation Changelog — tracks what changed between sessions.

Every preclose, before saving new recommendations, we diff them against
the previous ones and log:
  - New recommendations (stock newly crossed 65 threshold)
  - Removed recommendations (dropped below threshold or market changed)
  - Entry/exit level moves (ATR shifted, S/R level changed)
  - Rank changes (moved up or down in focus rankings)
  - Confidence score changes
  - Signal flip (BUY → SELL)

Stored in brain/rec_changelog.json, shown in dashboard.
"""

import json
import os
from datetime import date, datetime
from typing import Dict, List
from agent.trading_calendar import ist_today

CHANGELOG_FILE = "brain/rec_changelog.json"


def compute_changes(prev_recs: List[dict], new_recs: List[dict]) -> List[dict]:
    """Diff two recommendation lists and return a list of change events."""
    changes = []
    now = datetime.utcnow().isoformat()
    today = ist_today().isoformat()

    prev_map = {r["ticker"]: r for r in prev_recs}
    new_map  = {r["ticker"]: r for r in new_recs}

    # New entries
    for ticker, rec in new_map.items():
        if ticker not in prev_map:
            changes.append({
                "type":    "new",
                "ticker":  ticker,
                "nse_code": rec.get("nse_code", ticker),
                "signal":  rec.get("signal"),
                "date":    today,
                "ts":      now,
                "detail":  f"New {rec.get('signal')} recommendation — confidence {rec.get('confidence',0):.0f}/100",
                "confidence": rec.get("confidence", 0),
            })

    # Removed entries
    for ticker, rec in prev_map.items():
        if ticker not in new_map:
            changes.append({
                "type":    "removed",
                "ticker":  ticker,
                "nse_code": rec.get("nse_code", ticker),
                "signal":  rec.get("signal"),
                "date":    today,
                "ts":      now,
                "detail":  f"Dropped — no longer meets threshold (was {rec.get('confidence',0):.0f}/100)",
                "confidence": 0,
            })

    # Changed entries
    for ticker, new in new_map.items():
        if ticker not in prev_map:
            continue
        prev = prev_map[ticker]
        sub_changes = []

        # Signal flip
        if prev.get("signal") != new.get("signal"):
            sub_changes.append(
                f"Signal flipped {prev.get('signal')} → {new.get('signal')}"
            )

        # Confidence shift
        conf_diff = new.get("confidence", 0) - prev.get("confidence", 0)
        if abs(conf_diff) >= 3:
            sub_changes.append(
                f"Confidence {'▲' if conf_diff>0 else '▼'}{abs(conf_diff):.0f} "
                f"({prev.get('confidence',0):.0f} → {new.get('confidence',0):.0f})"
            )

        # Rank change
        rank_diff = prev.get("focus_rank", 99) - new.get("focus_rank", 99)
        if abs(rank_diff) >= 1:
            sub_changes.append(
                f"Rank {'▲' if rank_diff>0 else '▼'}{abs(rank_diff)} "
                f"(#{prev.get('focus_rank','?')} → #{new.get('focus_rank','?')})"
            )

        # Stop loss move
        sl_prev = prev.get("stop_loss", 0)
        sl_new  = new.get("stop_loss", 0)
        if sl_prev and sl_new and abs(sl_new - sl_prev) / max(sl_prev, 0.01) > 0.005:
            sub_changes.append(
                f"Stop loss moved ₹{sl_prev:.2f} → ₹{sl_new:.2f}"
            )

        # Target move
        t1_prev = prev.get("target1", 0)
        t1_new  = new.get("target1", 0)
        if t1_prev and t1_new and abs(t1_new - t1_prev) / max(t1_prev, 0.01) > 0.005:
            sub_changes.append(
                f"Target 1 moved ₹{t1_prev:.2f} → ₹{t1_new:.2f}"
            )

        if sub_changes:
            changes.append({
                "type":      "updated",
                "ticker":    ticker,
                "nse_code":  new.get("nse_code", ticker),
                "signal":    new.get("signal"),
                "date":      today,
                "ts":        now,
                "detail":    " | ".join(sub_changes),
                "confidence": new.get("confidence", 0),
            })

    return changes


def save_changelog(changes: List[dict]) -> None:
    if not changes:
        return
    existing = load_changelog()
    existing = (existing + changes)[-600:]   # rec-change history (display feed)
    os.makedirs("brain", exist_ok=True)
    with open(CHANGELOG_FILE, "w") as f:
        json.dump(existing, f, indent=2)


def load_changelog() -> List[dict]:
    from agent.io_safe import load_json_list
    return load_json_list(CHANGELOG_FILE)
