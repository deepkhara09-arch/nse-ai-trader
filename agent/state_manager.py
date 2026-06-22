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
        "last_counted_date": None,   # last date the phase day-counter advanced (dedupe)
        "cohort_last_tick_date": None,  # last date background batches advanced (dedupe)
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
    NSE trading day completes.

    A day advances at preclose, but ONLY if BOTH are true:
      1. Today is an actual TRADING day — a weekday that is not an NSE holiday
         (see trading_calendar). Weekends and holidays never count, so phases take
         exactly as many calendar days as needed to gather real trading days.
      2. We haven't already counted today — guards against multiple preclose runs
         on the same calendar date double-counting (e.g. duplicate/overlapping
         triggers). Running preclose 3x in one day still advances the day only once.

    Missed triggers are harmless: the day only ever advances on a preclose that
    actually runs on an uncounted trading day, so a missed session/day simply means
    that day isn't counted — never a wrong count.
    """
    from agent.trading_calendar import is_trading_day, reason_not_trading

    order = ["morning", "midday", "afternoon", "preclose"]
    try:
        idx = order.index(current_session)
    except ValueError:
        idx = 0

    if idx == len(order) - 1:
        # preclose done → candidate for end-of-trading-day rollover
        today = date.today()
        today_str = today.isoformat()
        trading_day = is_trading_day(today)
        already_counted_today = state.get("last_counted_date") == today_str

        if trading_day and not already_counted_today:
            state["day"] += 1
            state["last_counted_date"] = today_str
            print(f"[state] Trading day complete → day advanced to {state['day']} ({today_str})")
        else:
            reason = (reason_not_trading(today) or "not a trading day") if not trading_day \
                     else "day already counted today (duplicate preclose)"
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
