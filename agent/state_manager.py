"""
Persistent state manager — the agent's memory across all runs.
Tracks phase, day, session, focus stocks, and cumulative learning.
"""

import json
import os
from datetime import date, datetime
from agent.config import STATE_FILE, BRAIN_DIR


def load_state() -> dict:
    # State is the most critical file — a corrupt/empty one must never crash the
    # run. Fall back to a fresh state if it's missing, corrupt, or not a dict.
    from agent.io_safe import load_json_dict
    loaded = load_json_dict(STATE_FILE, default=None)
    if not loaded:   # missing, corrupt, empty {}, or not a dict
        return _fresh_state()
    return loaded


def save_state(state: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    state["last_updated"] = datetime.utcnow().isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"[state] phase={state['phase']} day={state['day']} session={state.get('session','?')}")


def _fresh_state() -> dict:
    return {
        "phase":             "exploration",
        "day":               1,
        "session":           "morning",
        "start_date":        date.today().isoformat(),
        "phase_start_date":  date.today().isoformat(),
        "last_run_date":     None,
        "last_counted_date": None,   # last date the day-counter advanced (dedupe)
        "last_updated":      None,
        "focus_stocks":      [],
        "dropped_stocks":    [],   # stocks removed due to poor pattern reliability
        "paper_trade_stats": {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "total_pnl": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0,
            "expectancy": 0.0,
        },
        "brain_notes":       [],   # agent's own reasoning log
        "alert_sent":        False,
    }


def advance_session(state: dict, current_session: str) -> dict:
    """
    Advance the session pointer, and increment the phase "day" ONLY when a real
    trading day completes.

    A day advances at preclose, but ONLY if BOTH are true:
      1. Today is a weekday (Mon–Fri) — NSE doesn't trade weekends, so weekend
         runs never count toward exploration/analysis/paper-trading day counts.
      2. We haven't already counted today — guards against multiple preclose runs
         on the same calendar date double-counting the day (e.g. duplicate crons).

    This means every phase counts only genuine trading days that the tool observed,
    exactly as intended. (Holidays where no run happens simply don't advance, which
    is also correct.)
    """
    order = ["morning", "midday", "afternoon", "preclose"]
    try:
        idx = order.index(current_session)
    except ValueError:
        idx = 0

    if idx == len(order) - 1:
        # preclose done → candidate for end-of-trading-day rollover
        today = date.today()
        today_str = today.isoformat()
        is_weekday = today.weekday() < 5          # 0–4 = Mon–Fri
        already_counted_today = state.get("last_counted_date") == today_str

        if is_weekday and not already_counted_today:
            state["day"] += 1
            state["last_counted_date"] = today_str
            print(f"[state] Trading day complete → day advanced to {state['day']} ({today_str})")
        else:
            reason = ("weekend — not a trading day" if not is_weekday
                      else "day already counted today (duplicate preclose)")
            print(f"[state] Preclose ran but day NOT advanced ({reason}); staying on day {state['day']}")

        state["session"] = "morning"
        state["last_run_date"] = today_str
    else:
        state["session"] = order[idx + 1]
    return state


def set_phase(state: dict, phase: str, note: str = "") -> dict:
    msg = f"Phase → {phase} on day {state['day']} ({date.today().isoformat()})"
    if note:
        msg += f" | {note}"
    print(f"[state] {msg}")
    state["phase"] = phase
    state["phase_start_date"] = date.today().isoformat()
    state["brain_notes"].append(msg)
    state["brain_notes"] = state["brain_notes"][-100:]   # keep last 100
    return state


def add_brain_note(state: dict, note: str) -> dict:
    stamped = f"[{date.today().isoformat()}] {note}"
    state["brain_notes"].append(stamped)
    state["brain_notes"] = state["brain_notes"][-100:]
    return state
