"""
Persistent state manager — the agent's memory across all runs.
Tracks phase, day, session, focus stocks, and cumulative learning.
"""

import json
import os
from datetime import date, datetime
from agent.config import STATE_FILE, BRAIN_DIR


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return _fresh_state()


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
    Advance the session pointer and increment day when we've done preclose.
    Returns updated state.
    """
    order = ["morning", "midday", "preclose"]
    idx = order.index(current_session)
    if idx == len(order) - 1:
        # preclose done → end of trading day
        state["day"] += 1
        state["session"] = "morning"
        state["last_run_date"] = date.today().isoformat()
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
