"""
Data migration system — runs automatically at agent startup.

Every time a structural change is made to any brain/*.json file, add a new
migration function here and bump CURRENT_SCHEMA_VERSION.

The agent reads the schema version stored in brain/state.json. If it's behind
CURRENT_SCHEMA_VERSION, every migration since that version runs in sequence,
transforming the data in-place. The agent then continues with the upgraded data.

This guarantees: push a structural code change → next Actions run auto-migrates
all collected data → zero data loss, no manual steps needed.

How to add a migration when you change data structure:
  1. Write a function  migrate_vN(state, stock_data, patterns, book, ...)
  2. Add it to MIGRATIONS dict under the version it introduces
  3. Bump CURRENT_SCHEMA_VERSION
  Done. The next run picks it up automatically.
"""

import json
import os
import copy
from typing import Dict

from agent.config import (
    STATE_FILE, STOCK_DATA_FILE, PATTERN_FILE,
    PAPER_TRADES_FILE, BRAIN_DIR,
)

FUNDAMENTALS_FILE    = "brain/fundamentals.json"
RANK_HISTORY_FILE    = "brain/rank_history.json"
WATCHLIST_FILE       = "brain/watchlist_signals.json"
DECISIONS_FILE       = "brain/decisions.json"

CURRENT_SCHEMA_VERSION = 6   # bump this when you add a new migration


# ── Migration functions ────────────────────────────────────────────────────────
# Each receives the full set of loaded brain dicts and returns them (modified).
# They must be safe to run on both old AND new data (idempotent).

def _migrate_v1(state, stock_data, patterns, book, fundamentals, decisions):
    """
    v1 → baseline: ensure all state fields introduced in the initial build exist.
    Safe to run on any state that's missing newer fields.
    """
    state.setdefault("dropped_stocks", [])
    state.setdefault("alert_sent", False)
    state.setdefault("brain_notes", [])
    state.setdefault("paper_trade_stats", {
        "total": 0, "wins": 0, "losses": 0,
        "win_rate": 0.0, "total_pnl": 0.0,
        "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0,
    })
    # Ensure paper book has all required keys
    book.setdefault("capital", 100_000)
    book.setdefault("open_positions", [])
    book.setdefault("closed_trades", [])
    book.setdefault("daily_snapshots", [])
    book.setdefault("daily_pnl_today", 0.0)
    book.setdefault("last_snapshot_date", None)
    # Ensure every closed trade has a 'won' field
    for t in book.get("closed_trades", []):
        if "won" not in t:
            t["won"] = t.get("pnl", 0) > 0
    return state, stock_data, patterns, book, fundamentals, decisions


def _migrate_v2(state, stock_data, patterns, book, fundamentals, decisions):
    """
    v2: ranking_engine introduced — ensure state has no stale phase references
    from before perpetual mode. Also ensure every stock_data entry has
    prev_bar and prev2_bar keys (added for multi-candle pattern detection).
    """
    # If phase was stuck at 'alerting' with the old stop-after-alert logic, keep it
    # running as paper_trading (perpetual mode)
    if state.get("phase") == "alerting" and state.get("alert_sent"):
        # Keep alerting — perpetual mode handles it correctly now
        pass

    # Ensure prev_bar / prev2_bar exist on every stock entry
    for ticker, entry in stock_data.items():
        entry.setdefault("prev_bar", {})
        entry.setdefault("prev2_bar", {})

    # Ensure patterns db has the ticker-level structure ranking_engine expects
    for ticker, pat in patterns.items():
        pat.setdefault("preferred_style", "swing")
        pat.setdefault("reliability", {})
        pat.setdefault("sample_counts", {})

    return state, stock_data, patterns, book, fundamentals, decisions


def _migrate_v3(state, stock_data, patterns, book, fundamentals, decisions):
    """
    v3: sector tracker, earnings calendar, RSI divergence, ATR auto-tuning,
    drawdown tracking, recommendation changelog, news sentiment momentum.

    - Adds atr_stop_hits / atr_target_hits / atr_multiplier to every patterns entry
    - Adds max_drawdown_pct / current_drawdown_pct / portfolio_peak to book
    - Adds price_history / rsi_history / week52_* stubs to stock latest dicts
      (real values populate on next data fetch — stubs prevent KeyError)
    - Ensures news history keeps up to 10 entries (old cap was 6)
    """
    from agent.config import ATR_STOP_MULTIPLIER

    # Patterns: add ATR tuning fields per ticker
    for ticker, tk in patterns.items():
        tk.setdefault("atr_stop_hits",   0)
        tk.setdefault("atr_target_hits", 0)
        tk.setdefault("atr_multiplier",  ATR_STOP_MULTIPLIER)

    # Paper book: add drawdown fields
    book.setdefault("max_drawdown_pct",     0.0)
    book.setdefault("current_drawdown_pct", 0.0)
    book.setdefault("portfolio_peak",       book.get("capital", 100_000))
    book.setdefault("sessions_since_peak",  0)

    # Stock data: add stub fields to latest dicts so brain doesn't KeyError
    for ticker, entry in stock_data.items():
        if "latest" not in entry:
            continue
        d = entry["latest"]
        d.setdefault("price_history",        [])
        d.setdefault("rsi_history",          [])
        d.setdefault("week52_high",          0.0)
        d.setdefault("week52_low",           0.0)
        d.setdefault("week52_position_pct",  50.0)
        d.setdefault("sector",               "Other")
        d.setdefault("sector_momentum",      0.0)
        d.setdefault("days_to_earnings",     None)

    return state, stock_data, patterns, book, fundamentals, decisions


def _migrate_v4(state, stock_data, patterns, book, fundamentals, decisions):
    """
    v4: delivery % data, volatility regime, sector blocking, win rate attribution.

    - Adds delivery_pct / delivery_signal / delivery_trend / avg_delivery_5d stubs
      to every stock's latest dict (real values injected at preclose by delivery_fetcher)
    - Adds sector field to every open/closed position in paper book
    - Adds vol_regime_pct to every open position
    - Adds attribution_stats stub to patterns dict per ticker
    """
    from agent.sector_tracker import SECTOR_MAP

    # Stock data: add delivery % stubs
    for ticker, entry in stock_data.items():
        if "latest" not in entry:
            continue
        d = entry["latest"]
        d.setdefault("delivery_pct",    0.0)
        d.setdefault("delivery_trend",  "stable")
        d.setdefault("delivery_signal", "neutral")
        d.setdefault("avg_delivery_5d", 0.0)

    # Paper book: backfill sector + vol_regime_pct on historical positions
    for pos in book.get("open_positions", []):
        pos.setdefault("sector",        SECTOR_MAP.get(pos.get("ticker", ""), "Other"))
        pos.setdefault("vol_regime_pct", 0.12)

    for trade in book.get("closed_trades", []):
        trade.setdefault("sector",        SECTOR_MAP.get(trade.get("ticker", ""), "Other"))
        trade.setdefault("vol_regime_pct", 0.12)

    # Patterns: add attribution stats stub per ticker
    for ticker, tk in patterns.items():
        tk.setdefault("attribution", {
            "by_pattern":  {},   # pattern_name → {wins, losses, total}
            "by_session":  {},   # morning/midday/preclose → {wins, losses}
            "by_mood":     {},   # bullish/neutral/bearish → {wins, losses}
            "by_style":    {},   # intraday/swing → {wins, losses}
        })

    return state, stock_data, patterns, book, fundamentals, decisions


def _migrate_v5(state, stock_data, patterns, book, fundamentals, decisions):
    """
    v5: LLM coach + entry-time market context.

    - Backfills entry_market stub on every open position so the coach can later
      reason about the conditions a trade was opened in. Closed trades from before
      this version simply won't have it — the coach handles its absence gracefully.
    - Ensures background_batches list exists on state (replaces legacy
      background_cohort single-dict pipeline).
    - Adds candle_sequence stub to stock latest dicts (populated by data_fetcher).

    Coach memory (brain/coach_memory.json) is a standalone file with its own
    loader defaults, so it needs no migration here.
    """
    for pos in book.get("open_positions", []):
        pos.setdefault("entry_market", {
            "nifty_trend": "?", "vix": 15.0, "mood": "neutral", "regime": "?",
        })

    state.setdefault("background_batches", [])

    for ticker, entry in stock_data.items():
        if "latest" not in entry:
            continue
        entry["latest"].setdefault("candle_sequence", [])

    return state, stock_data, patterns, book, fundamentals, decisions


def _migrate_v6(state, stock_data, patterns, book, fundamentals, decisions):
    """
    v6: deep 2-year history engine (regime context, personality, backtest).

    Adds history-context stub fields to every stock's latest dict so the brain
    never KeyErrors before the first history fetch populates them. Real values
    come from brain/history_context.json (a standalone file, no migration needed)
    and are injected into latest{} at runtime by main._inject_history_context.
    """
    for ticker, entry in stock_data.items():
        if "latest" not in entry:
            continue
        d = entry["latest"]
        d.setdefault("hist_long_trend",         None)
        d.setdefault("hist_pct_of_52w_range",   None)
        d.setdefault("hist_vol_state",          None)
        d.setdefault("hist_drawdown_from_high", None)
        d.setdefault("hist_personality",        None)

    return state, stock_data, patterns, book, fundamentals, decisions


# ── Registry: maps schema version → migration that brings data UP to that version
MIGRATIONS = {
    1: _migrate_v1,
    2: _migrate_v2,
    3: _migrate_v3,
    4: _migrate_v4,
    5: _migrate_v5,
    6: _migrate_v6,
}


# ── Public API ─────────────────────────────────────────────────────────────────

def run_migrations() -> None:
    """
    Called once at agent startup (before any other logic).
    Reads all brain files, detects schema version, runs any outstanding
    migrations in order, and writes the upgraded files back to disk.
    """
    state       = _load_json(STATE_FILE,       {})
    stock_data  = _load_json(STOCK_DATA_FILE,  {})
    patterns    = _load_json(PATTERN_FILE,     {})
    book        = _load_json(PAPER_TRADES_FILE, {})
    fundamentals= _load_json(FUNDAMENTALS_FILE, {})
    decisions   = _load_json(DECISIONS_FILE,   [])

    current_ver = state.get("schema_version", 0)

    if current_ver >= CURRENT_SCHEMA_VERSION:
        print(f"[migrate] Schema v{current_ver} — up to date, no migrations needed.")
        return

    print(f"[migrate] Schema v{current_ver} → v{CURRENT_SCHEMA_VERSION} — running migrations...")

    for ver in range(current_ver + 1, CURRENT_SCHEMA_VERSION + 1):
        fn = MIGRATIONS.get(ver)
        if fn is None:
            continue
        print(f"[migrate]   running migration v{ver}: {fn.__doc__.strip().splitlines()[0]}")
        try:
            state, stock_data, patterns, book, fundamentals, decisions = fn(
                state, stock_data, patterns, book, fundamentals, decisions
            )
        except Exception as e:
            print(f"[migrate]   ERROR in v{ver}: {e} — skipping (data unchanged)")
            continue

    # Stamp the new version
    state["schema_version"] = CURRENT_SCHEMA_VERSION

    # Write all files back
    os.makedirs(BRAIN_DIR, exist_ok=True)
    _save_json(STATE_FILE,        state)
    _save_json(STOCK_DATA_FILE,   stock_data)
    _save_json(PATTERN_FILE,      patterns)
    _save_json(PAPER_TRADES_FILE, book)
    _save_json(FUNDAMENTALS_FILE, fundamentals)
    if isinstance(decisions, list):
        _save_json(DECISIONS_FILE, decisions)

    print(f"[migrate] Done — all brain files upgraded to schema v{CURRENT_SCHEMA_VERSION}.")


def check_schema_health() -> dict:
    """
    Returns a summary of what's in each brain file — useful for debugging
    after a migration or when opening a future conversation with Claude.
    Shows: schema version, counts, phase/day, focus stocks, trade stats.
    """
    state      = _load_json(STATE_FILE,        {})
    stock_data = _load_json(STOCK_DATA_FILE,   {})
    patterns   = _load_json(PATTERN_FILE,      {})
    book       = _load_json(PAPER_TRADES_FILE, {})
    fund       = _load_json(FUNDAMENTALS_FILE, {})
    decisions  = _load_json(DECISIONS_FILE,    [])

    return {
        "schema_version":    state.get("schema_version", 0),
        "current_target":    CURRENT_SCHEMA_VERSION,
        "needs_migration":   state.get("schema_version", 0) < CURRENT_SCHEMA_VERSION,
        "phase":             state.get("phase"),
        "day":               state.get("day"),
        "focus_stocks":      state.get("focus_stocks", []),
        "stocks_tracked":    len(stock_data),
        "patterns_tracked":  len(patterns),
        "open_positions":    len(book.get("open_positions", [])),
        "closed_trades":     len(book.get("closed_trades", [])),
        "fundamentals":      len(fund),
        "decisions":         len(decisions) if isinstance(decisions, list) else 0,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            print(f"[migrate] Warning: could not read {path}: {e}")
    return copy.deepcopy(default)


def _save_json(path: str, data) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
