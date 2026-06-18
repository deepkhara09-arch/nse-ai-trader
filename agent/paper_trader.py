"""
Paper trading engine — no real money, pure simulation.

Works with analyst opinions from brain.py.
Respects session timing (morning opens, preclose closes intraday positions).
Tracks every trade with full context for the dashboard.
"""

import json
import os
from datetime import date, datetime
from typing import Dict, List, Optional

from agent.config import (
    BRAIN_DIR, PAPER_TRADES_FILE,
    INITIAL_CAPITAL, MAX_POSITION_SIZE_PCT, MAX_OPEN_POSITIONS,
    MAX_DAILY_LOSS_PCT, WIN_RATE_THRESHOLD, MIN_TRADES_FOR_SIGNAL,
)
from agent.brain import learn_from_trade

Tuple2 = tuple   # (book, patterns_db)


def load_book() -> dict:
    if os.path.exists(PAPER_TRADES_FILE):
        with open(PAPER_TRADES_FILE) as f:
            return json.load(f)
    return _fresh_book()


def save_book(book: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(PAPER_TRADES_FILE, "w") as f:
        json.dump(book, f, indent=2)


def _fresh_book() -> dict:
    return {
        "capital":          INITIAL_CAPITAL,
        "open_positions":   [],
        "closed_trades":    [],
        "daily_snapshots":  [],
        "daily_pnl_today":  0.0,
        "last_snapshot_date": None,
    }


# ── Session actions ────────────────────────────────────────────────────────────

def morning_session(book: dict, opinions: List[dict], patterns_db: Dict) -> Tuple2:
    """Open new positions from morning signals. No intraday exits yet."""
    book = _reset_daily_pnl_if_new_day(book)
    book, patterns_db = _try_open_positions(book, opinions, patterns_db, session="morning")
    return book, patterns_db


def midday_session(book: dict, opinions: List[dict], stock_data: Dict, patterns_db: Dict) -> Tuple2:
    """Update mark-to-market, close intraday positions gone wrong, add swing signals."""
    book = _mark_to_market(book, stock_data)
    book = _update_trailing_stops(book, stock_data)
    book, patterns_db = _check_exits(book, stock_data, session="midday", patterns_db=patterns_db)
    # Add new swing signals identified midday
    swing_opinions = [o for o in opinions if o.get("style") == "swing"]
    book, patterns_db = _try_open_positions(book, swing_opinions, patterns_db, session="midday")
    return book, patterns_db


def preclose_session(book: dict, opinions: List[dict], stock_data: Dict, patterns_db: Dict) -> Tuple2:
    """Force-close all intraday positions before market close. Check swing exits."""
    book = _mark_to_market(book, stock_data)
    book = _update_trailing_stops(book, stock_data)
    # Force-close intraday positions
    book = _close_intraday_positions(book, stock_data)
    # Check swing positions for stop/target hit
    book, patterns_db = _check_exits(book, stock_data, session="preclose", patterns_db=patterns_db)
    # Record daily snapshot
    book = _snapshot(book)
    return book, patterns_db


# ── Position management ────────────────────────────────────────────────────────

def _try_open_positions(book: dict, opinions: List[dict], patterns_db: Dict, session: str):
    open_tickers = {p["ticker"] for p in book["open_positions"]}
    daily_loss = book.get("daily_pnl_today", 0)

    # Stop all new trades if daily loss limit hit
    if daily_loss < -INITIAL_CAPITAL * MAX_DAILY_LOSS_PCT:
        print(f"[paper] Daily loss limit hit (₹{daily_loss:.0f}). No new trades today.")
        return book, patterns_db

    # Sort by confidence desc
    sorted_ops = sorted(opinions, key=lambda x: x.get("confidence", 0), reverse=True)

    for op in sorted_ops:
        ticker = op.get("ticker")
        signal = op.get("signal")

        if signal not in ("BUY", "SELL"):
            continue
        if ticker in open_tickers:
            continue
        if len(book["open_positions"]) >= MAX_OPEN_POSITIONS:
            print(f"[paper] Max open positions ({MAX_OPEN_POSITIONS}) reached.")
            break

        entry = op.get("entry", 0)
        if entry <= 0:
            continue

        max_invest = book["capital"] * MAX_POSITION_SIZE_PCT
        qty = int(max_invest // entry)
        if qty < 1:
            continue

        pos = {
            "ticker":      ticker,
            "open_date":   date.today().isoformat(),
            "open_session":session,
            "action":      signal,
            "entry":       entry,
            "qty":         qty,
            "invested":    round(entry * qty, 2),
            "stop_loss":   op.get("stop_loss"),
            "target":      op.get("target"),
            "style":       op.get("style", "swing"),
            "confidence":  op.get("confidence"),
            "patterns":    op.get("patterns", []),
            "buy_reasons": op.get("buy_reasons", []) + op.get("sell_reasons", []),
            "current_price": entry,
            "unrealized_pnl": 0.0,
            "max_held_days": 10 if op.get("style") == "swing" else 1,
        }
        book["open_positions"].append(pos)
        book["capital"] -= pos["invested"]
        open_tickers.add(ticker)
        print(f"[paper] OPEN {signal} {qty}x {ticker} @ ₹{entry} | SL=₹{op.get('stop_loss')} T=₹{op.get('target')} | {session}")

    return book, patterns_db


def _update_trailing_stops(book: dict, stock_data: Dict) -> dict:
    """
    Trailing stop: once a position is up > 4%, move stop loss to breakeven.
    Once up > 6%, trail stop to lock in half the profit.
    """
    for pos in book["open_positions"]:
        if pos.get("style") == "intraday":
            continue   # no trailing for intraday — exit is forced at preclose
        ticker  = pos["ticker"]
        data    = stock_data.get(ticker, {}).get("latest", {})
        current = data.get("current_price") or data.get("close", pos["entry"])
        entry   = pos["entry"]
        if entry <= 0:
            continue

        if pos["action"] == "BUY":
            profit_pct = (current - entry) / entry * 100
            if profit_pct >= 6.0:
                # Trail stop to lock in 50% of profit
                new_sl = round(entry + (current - entry) * 0.5, 2)
                if new_sl > pos["stop_loss"]:
                    pos["stop_loss"] = new_sl
                    pos["trailing_active"] = True
            elif profit_pct >= 4.0:
                # Move stop to breakeven
                if pos["stop_loss"] < entry:
                    pos["stop_loss"] = round(entry * 1.001, 2)
                    pos["trailing_active"] = True
        else:
            profit_pct = (entry - current) / entry * 100
            if profit_pct >= 6.0:
                new_sl = round(entry - (entry - current) * 0.5, 2)
                if new_sl < pos["stop_loss"]:
                    pos["stop_loss"] = new_sl
                    pos["trailing_active"] = True
            elif profit_pct >= 4.0:
                if pos["stop_loss"] > entry:
                    pos["stop_loss"] = round(entry * 0.999, 2)
                    pos["trailing_active"] = True
    return book


def _check_exits(book: dict, stock_data: Dict, session: str, patterns_db: Dict):
    still_open = []
    today = date.today().isoformat()

    for pos in book["open_positions"]:
        ticker  = pos["ticker"]
        data    = stock_data.get(ticker, {}).get("latest", {})
        current = data.get("current_price") or data.get("close", pos["entry"])
        high_   = data.get("session_high") or data.get("high", current)
        low_    = data.get("session_low")  or data.get("low",  current)

        hit_target = (pos["action"] == "BUY"  and high_ >= pos["target"]) or \
                     (pos["action"] == "SELL" and low_  <= pos["target"])
        hit_stop   = (pos["action"] == "BUY"  and low_  <= pos["stop_loss"]) or \
                     (pos["action"] == "SELL" and high_ >= pos["stop_loss"])

        open_days = _days_between(pos["open_date"], today)
        expired   = open_days >= pos.get("max_held_days", 10)

        if hit_target or hit_stop or expired:
            if hit_target:
                exit_price = pos["target"]
                exit_reason = "target_hit"
            elif hit_stop:
                exit_price = pos["stop_loss"]
                exit_reason = "stop_hit"
            else:
                exit_price = current
                exit_reason = "time_exit"

            pnl = (exit_price - pos["entry"]) * pos["qty"] * (1 if pos["action"] == "BUY" else -1)
            won = pnl > 0
            pnl_pct = round(pnl / pos["invested"] * 100, 2)

            trade = {
                **pos,
                "close_date":    today,
                "close_session": session,
                "exit_price":    round(exit_price, 2),
                "exit_reason":   exit_reason,
                "pnl":           round(pnl, 2),
                "pnl_pct":       pnl_pct,
                "won":           won,
                "open_days":     open_days,
            }
            book["closed_trades"].append(trade)
            book["capital"] += pos["invested"] + pnl
            book["daily_pnl_today"] = book.get("daily_pnl_today", 0) + pnl

            icon = "✅" if won else "❌"
            print(f"[paper] CLOSE {icon} {ticker} | {exit_reason} | PnL ₹{pnl:+.0f} ({pnl_pct:+.1f}%)")

            # Teach the brain
            patterns_db = learn_from_trade(
                ticker, pos.get("patterns", []), won, pos.get("style", "swing"), patterns_db
            )
        else:
            pos["current_price"] = round(current, 2)
            pos["unrealized_pnl"] = round(
                (current - pos["entry"]) * pos["qty"] * (1 if pos["action"] == "BUY" else -1), 2
            )
            still_open.append(pos)

    book["open_positions"] = still_open
    return book, patterns_db


def _close_intraday_positions(book: dict, stock_data: Dict) -> dict:
    """Force-close any intraday positions at preclose."""
    today = date.today().isoformat()
    still_open = []
    for pos in book["open_positions"]:
        if pos.get("style") == "intraday":
            ticker  = pos["ticker"]
            data    = stock_data.get(ticker, {}).get("latest", {})
            current = data.get("current_price") or data.get("close", pos["entry"])
            pnl     = (current - pos["entry"]) * pos["qty"] * (1 if pos["action"] == "BUY" else -1)
            trade   = {**pos,
                "close_date": today, "close_session": "preclose",
                "exit_price": round(current, 2), "exit_reason": "intraday_forced_close",
                "pnl": round(pnl, 2), "pnl_pct": round(pnl / pos["invested"] * 100, 2),
                "won": pnl > 0,
            }
            book["closed_trades"].append(trade)
            book["capital"] += pos["invested"] + pnl
            book["daily_pnl_today"] = book.get("daily_pnl_today", 0) + pnl
            print(f"[paper] INTRADAY CLOSE {ticker} @ ₹{current:.2f} | PnL ₹{pnl:+.0f}")
        else:
            still_open.append(pos)
    book["open_positions"] = still_open
    return book


def _mark_to_market(book: dict, stock_data: Dict) -> dict:
    for pos in book["open_positions"]:
        data = stock_data.get(pos["ticker"], {}).get("latest", {})
        current = data.get("current_price") or data.get("close", pos["entry"])
        pos["current_price"] = round(current, 2)
        pos["unrealized_pnl"] = round(
            (current - pos["entry"]) * pos["qty"] * (1 if pos["action"] == "BUY" else -1), 2
        )
    return book


def _snapshot(book: dict) -> dict:
    today = date.today().isoformat()
    if book.get("last_snapshot_date") == today:
        return book
    total_unrealized = sum(p.get("unrealized_pnl", 0) for p in book["open_positions"])
    total_invested   = sum(p.get("invested", 0) for p in book["open_positions"])
    portfolio_value  = round(book["capital"] + total_invested + total_unrealized, 2)
    book["daily_snapshots"].append({
        "date":            today,
        "portfolio_value": portfolio_value,
        "capital":         round(book["capital"], 2),
        "open_positions":  len(book["open_positions"]),
        "daily_pnl":       round(book.get("daily_pnl_today", 0), 2),
    })
    book["daily_snapshots"] = book["daily_snapshots"][-90:]
    book["last_snapshot_date"] = today
    return book


def _reset_daily_pnl_if_new_day(book: dict) -> dict:
    today = date.today().isoformat()
    if book.get("last_snapshot_date") != today:
        book["daily_pnl_today"] = 0.0
    return book


def _days_between(d1: str, d2: str) -> int:
    a = date.fromisoformat(d1)
    b = date.fromisoformat(d2)
    return (b - a).days


# ── Stats ──────────────────────────────────────────────────────────────────────

def compute_stats(book: dict) -> dict:
    trades = book.get("closed_trades", [])
    if not trades:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0}
    wins   = [t for t in trades if t["won"]]
    losses = [t for t in trades if not t["won"]]
    wr     = len(wins) / len(trades)
    avg_w  = sum(t["pnl"] for t in wins)   / max(len(wins),   1)
    avg_l  = sum(t["pnl"] for t in losses) / max(len(losses), 1)
    # Expectancy: E = (WR × avg_win) + (LR × avg_loss)
    expectancy = round(wr * avg_w + (1 - wr) * avg_l, 2)
    return {
        "total":      len(trades),
        "wins":       len(wins),
        "losses":     len(losses),
        "win_rate":   round(wr, 3),
        "total_pnl":  round(sum(t["pnl"] for t in trades), 2),
        "avg_win":    round(avg_w, 2),
        "avg_loss":   round(avg_l, 2),
        "expectancy": expectancy,
    }


def is_ready_to_alert(stats: dict) -> bool:
    return (stats["total"] >= MIN_TRADES_FOR_SIGNAL and
            stats["win_rate"] >= WIN_RATE_THRESHOLD and
            stats["expectancy"] > 0)


# type alias for readability
Tuple2 = tuple
