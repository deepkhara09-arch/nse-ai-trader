"""
Run Health logger — records non-fatal failures during a run so they surface on
the dashboard instead of vanishing into the Actions logs.

The tool is designed to keep running when an external API/feed fails (it falls
back to last-known data). But those failures used to be invisible. This module
captures each one with enough context to tell a one-time blip from a recurring
problem, and the dashboard shows them in a "System Health" panel.

Usage anywhere a non-fatal failure is caught:
    from agent.run_health import record_issue
    record_issue("data_fetch", "RELIANCE.NS", str(e), session)

Storage: brain/run_health.json
  {
    "issues":  [ {ts, session, component, detail, message}, ... last 100 ],
    "counts":  { "<component>": {"total": n, "last_seen": ts, "last_session": s} },
    "last_run": {date, session, ok, issue_count}
  }
"""

import json
import os
from datetime import datetime, date, timezone, timedelta
from typing import Dict, List

from agent.config import BRAIN_DIR

RUN_HEALTH_FILE = "brain/run_health.json"
IST = timezone(timedelta(hours=5, minutes=30))

_MAX_ISSUES = 100


def _load() -> dict:
    if os.path.exists(RUN_HEALTH_FILE):
        try:
            with open(RUN_HEALTH_FILE) as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("issues", [])
                d.setdefault("counts", {})
                d.setdefault("last_run", {})
                return d
        except Exception:
            pass
    return {"issues": [], "counts": {}, "last_run": {}}


def _save(data: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(RUN_HEALTH_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_issue(component: str, detail: str, message: str, session: str = "") -> None:
    """
    Record a single non-fatal failure. `component` is a stable category
    (e.g. "data_fetch", "macro_feeds", "delivery", "coach", "phase"); `detail`
    is the specific thing (ticker, feed name, etc.); `message` is the error text.
    Never raises — health logging must never itself break a run.
    """
    try:
        data = _load()
        now  = datetime.now(IST)
        ts   = now.strftime("%Y-%m-%d %H:%M IST")
        entry = {
            "ts":        ts,
            "session":   session,
            "component": component,
            "detail":    str(detail)[:80],
            "message":   str(message)[:200],
        }
        data["issues"].append(entry)
        data["issues"] = data["issues"][-_MAX_ISSUES:]

        c = data["counts"].setdefault(component, {"total": 0, "last_seen": "", "last_session": ""})
        c["total"]        += 1
        c["last_seen"]     = ts
        c["last_session"]  = session
        data["counts"][component] = c

        _save(data)
        print(f"[health] recorded issue: {component} / {detail} — {str(message)[:80]}")
    except Exception:
        pass   # never let health logging break anything


def start_run(session: str) -> None:
    """Mark the beginning of a run so we can report a per-run issue count."""
    try:
        data = _load()
        data["_run_start_count"] = len(data["issues"])
        data["last_run"] = {
            "date":        date.today().isoformat(),
            "session":     session,
            "started_at":  datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
            "ok":          True,
            "issue_count": 0,
        }
        _save(data)
    except Exception:
        pass


def finish_run(session: str) -> None:
    """Finalise the run summary — how many issues occurred this run."""
    try:
        data = _load()
        start_count = data.pop("_run_start_count", len(data["issues"]))
        this_run = len(data["issues"]) - start_count
        data["last_run"] = {
            "date":         date.today().isoformat(),
            "session":      session,
            "finished_at":  datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
            "ok":           this_run == 0,
            "issue_count":  this_run,
        }
        _save(data)
    except Exception:
        pass


def load_run_health() -> dict:
    return _load()


def clear_run_health() -> None:
    """Reset the health log (e.g. after you've reviewed and fixed issues)."""
    _save({"issues": [], "counts": {}, "last_run": {}})
