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
    INITIAL_CAPITAL, MAX_OPEN_POSITIONS,
    MAX_DAILY_LOSS_PCT, WIN_RATE_THRESHOLD, MIN_TRADES_FOR_SIGNAL,
    PAPER_TRADING_DAYS, MAX_SECTOR_POSITIONS,
    VOL_REGIME_NORMAL_MAX_PCT, VOL_REGIME_CAUTION_MAX_PCT, VOL_REGIME_DANGER_MAX_PCT,
)
from agent.brain import learn_from_trade

Tuple2 = tuple   # (book, patterns_db)


def load_book() -> dict:
    from agent.io_safe import load_json_dict
    loaded = load_json_dict(PAPER_TRADES_FILE, default=None)
    if not loaded:   # missing, corrupt, empty, or not a dict
        return _fresh_book()
    return loaded


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

def morning_session(book: dict, opinions: List[dict], patterns_db: Dict, market_health: dict = None) -> Tuple2:
    """Open new positions from morning signals. No intraday exits yet."""
    book = _reset_daily_pnl_if_new_day(book)
    book, patterns_db = _try_open_positions(book, opinions, patterns_db, session="morning", market_health=market_health)
    return book, patterns_db


def midday_session(book: dict, opinions: List[dict], stock_data: Dict, patterns_db: Dict, market_health: dict = None) -> Tuple2:
    """Update mark-to-market, close intraday positions gone wrong, add swing signals."""
    book = _mark_to_market(book, stock_data)
    book = _update_trailing_stops(book, stock_data)
    book, patterns_db = _check_exits(book, stock_data, session="midday", patterns_db=patterns_db)
    swing_opinions = [o for o in opinions if o.get("style") == "swing"]
    book, patterns_db = _try_open_positions(book, swing_opinions, patterns_db, session="midday", market_health=market_health)
    return book, patterns_db


def preclose_session(book: dict, opinions: List[dict], stock_data: Dict, patterns_db: Dict, market_health: dict = None) -> Tuple2:
    """Force-close all intraday positions before market close. Check swing exits."""
    book = _mark_to_market(book, stock_data)
    book = _update_trailing_stops(book, stock_data)
    # _check_exits runs FIRST so intraday positions that hit target/stop during the day
    # are exited at the correct level (target or stop price), not the end-of-day price.
    # _close_intraday_positions then mops up any remaining intraday positions at close price.
    book, patterns_db = _check_exits(book, stock_data, session="preclose", patterns_db=patterns_db)
    book, patterns_db = _close_intraday_positions(book, stock_data, patterns_db)
    book = _snapshot(book)
    return book, patterns_db


# ── Position management ────────────────────────────────────────────────────────

def _try_open_positions(book: dict, opinions: List[dict], patterns_db: Dict, session: str, market_health: dict = None):
    open_tickers = {p["ticker"] for p in book["open_positions"]}
    daily_loss   = book.get("daily_pnl_today", 0)

    if daily_loss < -INITIAL_CAPITAL * MAX_DAILY_LOSS_PCT:
        print(f"[paper] Daily loss limit hit (₹{daily_loss:.0f}). No new trades today.")
        return book, patterns_db

    # ── Volatility regime: scale position size and ATR multiplier with VIX ────
    vix_val = (market_health or {}).get("vix", {}).get("value", 15.0)
    if vix_val >= 20.0:
        vol_max_pct = VOL_REGIME_DANGER_MAX_PCT
        vol_label   = f"VIX={vix_val:.1f} DANGER→size capped at {vol_max_pct*100:.0f}%"
    elif vix_val >= 15.0:
        vol_max_pct = VOL_REGIME_CAUTION_MAX_PCT
        vol_label   = f"VIX={vix_val:.1f} CAUTION→size capped at {vol_max_pct*100:.0f}%"
    else:
        vol_max_pct = VOL_REGIME_NORMAL_MAX_PCT
        vol_label   = None
    if vol_label:
        print(f"[paper] Vol regime: {vol_label}")

    # ── Macro risk scaling: shrink size on global risk-off days ────────────────
    # market_health.macro_risk_factor is 1.0 normally, 0.7 on global risk-off.
    macro_factor = (market_health or {}).get("macro_risk_factor", 1.0)
    if macro_factor < 1.0:
        vol_max_pct *= macro_factor
        print(f"[paper] Macro risk-off → position size scaled to {macro_factor:.0%} "
              f"(cap now {vol_max_pct*100:.1f}%)")

    # ── Sector concentration: count currently open positions per sector ────────
    from agent.sector_tracker import SECTOR_MAP
    sector_counts: Dict[str, int] = {}
    for pos in book["open_positions"]:
        sec = SECTOR_MAP.get(pos["ticker"], "Other")
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

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

        # ── Sector concentration block ─────────────────────────────────────────
        sector = SECTOR_MAP.get(ticker, "Other")
        if sector_counts.get(sector, 0) >= MAX_SECTOR_POSITIONS:
            print(f"[paper] Skipping {ticker} — already {MAX_SECTOR_POSITIONS} open in {sector} sector")
            continue

        entry = op.get("entry", 0)
        if entry <= 0:
            continue
        if book["capital"] < entry:
            print(f"[paper] Skipping {ticker} — capital ₹{book['capital']:.0f} < entry ₹{entry:.0f}")
            continue

        max_invest = book["capital"] * vol_max_pct
        qty = int(max_invest // entry)
        if qty < 1:
            continue

        pos = {
            "ticker":        ticker,
            "sector":        sector,
            "open_date":     date.today().isoformat(),
            "open_session":  session,
            "action":        signal,
            "entry":         entry,
            "qty":           qty,
            "invested":      round(entry * qty, 2),
            "stop_loss":     op.get("stop_loss"),
            "target":        op.get("target"),
            "style":         op.get("style", "swing"),
            "confidence":    op.get("confidence"),
            "patterns":      op.get("patterns", []),
            "buy_reasons":   op.get("buy_reasons", []) + op.get("sell_reasons", []),
            "current_price": entry,
            "unrealized_pnl":0.0,
            "max_held_days": 10 if op.get("style") == "swing" else 1,
            "vol_regime_pct":vol_max_pct,
            # Capture market context AT ENTRY so the coach can later explain whether
            # the outcome was driven by the setup or by the conditions at open time.
            "entry_market": {
                "nifty_trend": (market_health or {}).get("nifty", {}).get("trend_5d", "?"),
                "vix":         vix_val,
                "mood":        (market_health or {}).get("market_mood", "neutral"),
                "regime":      (market_health or {}).get("intraday_regime", "?"),
            },
        }
        book["open_positions"].append(pos)
        book["capital"] -= pos["invested"]
        open_tickers.add(ticker)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        print(f"[paper] OPEN {signal} {qty}x {ticker} ({sector}) @ ₹{entry} | SL=₹{op.get('stop_loss')} T=₹{op.get('target')} | {session}")

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


def _resolve_hit_order(pos: dict, target: float, stop_loss: float, data: dict):
    """
    When both target and stop loss are touched in the same session, determine
    which was hit first by walking through the 5-minute candle sequence.

    Each candle in candle_sequence is (high, low) in chronological order.
    For a BUY: target is hit when high >= target, stop when low <= stop_loss.
    For a SELL: target is hit when low <= target, stop when high >= stop_loss.

    Returns (exit_price, exit_reason).
    Falls back to open-price proximity heuristic if candle data unavailable.
    """
    action   = pos["action"]
    sequence = data.get("candle_sequence", [])   # list of (high, low) per 5-min bar

    if sequence:
        for (bar_high, bar_low) in sequence:
            if action == "BUY":
                # Check stop first within same bar — if low <= stop, stop came first
                # unless the open of this bar is already above target
                stop_hit   = bar_low  <= stop_loss
                target_hit = bar_high >= target
                if stop_hit and target_hit:
                    # Both in same 5-min bar — use open-price proximity for this bar
                    # (we don't have sub-bar tick data, this is the finest we can go)
                    pass   # fall through to proximity check below
                elif stop_hit:
                    return stop_loss, "stop_hit"
                elif target_hit:
                    return target, "target_hit"
            else:  # SELL
                stop_hit   = bar_high >= stop_loss
                target_hit = bar_low  <= target
                if stop_hit and target_hit:
                    pass
                elif stop_hit:
                    return stop_loss, "stop_hit"
                elif target_hit:
                    return target, "target_hit"

    # Fallback: both hit in same 5-min bar or no candle data —
    # use day open proximity as best remaining heuristic
    day_open       = data.get("day_open") or data.get("open") or pos["entry"]
    dist_to_target = abs(day_open - target)
    dist_to_stop   = abs(day_open - stop_loss)
    if dist_to_stop <= dist_to_target:
        return stop_loss, "stop_hit"
    return target, "target_hit"


def _check_exits(book: dict, stock_data: Dict, session: str, patterns_db: Dict):
    still_open = []
    today = date.today().isoformat()

    for pos in book["open_positions"]:
        ticker  = pos["ticker"]
        data    = stock_data.get(ticker, {}).get("latest", {})
        current = data.get("current_price") or data.get("close", pos["entry"])
        # Prefer live intraday day_high/day_low (populated by NSE quote API).
        # Fall back to daily bar high/low (yesterday's completed bar) if market closed.
        high_   = data.get("session_high") or data.get("day_high") or data.get("high", current)
        low_    = data.get("session_low")  or data.get("day_low")  or data.get("low",  current)

        target_   = pos.get("target")
        stop_loss_ = pos.get("stop_loss")
        hit_target = bool(target_ and (
            (pos["action"] == "BUY"  and high_ >= target_) or
            (pos["action"] == "SELL" and low_  <= target_)
        ))
        hit_stop = bool(stop_loss_ and (
            (pos["action"] == "BUY"  and low_  <= stop_loss_) or
            (pos["action"] == "SELL" and high_ >= stop_loss_)
        ))

        open_days = _days_between(pos["open_date"], today)
        expired   = open_days >= pos.get("max_held_days", 10)

        if hit_target or hit_stop or expired:
            if hit_target and hit_stop:
                # Both levels touched in the same session.
                # Use the 5-min candle sequence to find which bar first breached each level.
                exit_price, exit_reason = _resolve_hit_order(
                    pos, target_, stop_loss_, data
                )
            elif hit_target:
                exit_price = target_
                exit_reason = "target_hit"
            elif hit_stop:
                exit_price = stop_loss_
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

            # Teach the brain — pass exit_reason for ATR multiplier auto-tuning
            patterns_db = learn_from_trade(
                ticker, pos.get("patterns", []), won, pos.get("style", "swing"),
                patterns_db, exit_reason=exit_reason
            )
        else:
            pos["current_price"] = round(current, 2)
            pos["unrealized_pnl"] = round(
                (current - pos["entry"]) * pos["qty"] * (1 if pos["action"] == "BUY" else -1), 2
            )
            still_open.append(pos)

    book["open_positions"] = still_open
    return book, patterns_db


def _close_intraday_positions(book: dict, stock_data: Dict, patterns_db: Dict = None) -> Tuple2:
    """Force-close any intraday positions at preclose. Also teaches the brain
    from each outcome so intraday trades update pattern reliability too."""
    today = date.today().isoformat()
    still_open = []
    for pos in book["open_positions"]:
        if pos.get("style") == "intraday":
            ticker  = pos["ticker"]
            data    = stock_data.get(ticker, {}).get("latest", {})
            current = data.get("current_price") or data.get("close", pos["entry"])
            pnl     = (current - pos["entry"]) * pos["qty"] * (1 if pos["action"] == "BUY" else -1)
            won     = pnl > 0
            trade   = {**pos,
                "close_date": today, "close_session": "preclose",
                "exit_price": round(current, 2), "exit_reason": "intraday_forced_close",
                "pnl": round(pnl, 2), "pnl_pct": round(pnl / pos["invested"] * 100, 2),
                "won": won,
                "open_days": _days_between(pos["open_date"], today),
            }
            book["closed_trades"].append(trade)
            book["capital"] += pos["invested"] + pnl
            book["daily_pnl_today"] = book.get("daily_pnl_today", 0) + pnl
            # Teach the brain from intraday outcomes too (was previously skipped)
            if patterns_db is not None:
                patterns_db = learn_from_trade(
                    ticker, pos.get("patterns", []), won, pos.get("style", "intraday"),
                    patterns_db, exit_reason="intraday_forced_close"
                )
            print(f"[paper] INTRADAY CLOSE {ticker} @ ₹{current:.2f} | PnL ₹{pnl:+.0f}")
        else:
            still_open.append(pos)
    book["open_positions"] = still_open
    return book, patterns_db


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
    snap = {
        "date":            today,
        "portfolio_value": portfolio_value,
        "capital":         round(book["capital"], 2),
        "open_positions":  len(book["open_positions"]),
        "daily_pnl":       round(book.get("daily_pnl_today", 0), 2),
    }
    book["daily_snapshots"].append(snap)
    book["daily_snapshots"] = book["daily_snapshots"][-90:]
    book["last_snapshot_date"] = today

    # ── Drawdown tracking ─────────────────────────────────────────────────────
    values = [s["portfolio_value"] for s in book["daily_snapshots"]]
    if values:
        peak = max(values)
        current = values[-1]
        dd_pct = round((peak - current) / peak * 100, 2) if peak > 0 else 0.0
        # Running max drawdown
        max_dd = book.get("max_drawdown_pct", 0.0)
        book["max_drawdown_pct"]     = max(max_dd, dd_pct)
        book["current_drawdown_pct"] = dd_pct
        book["portfolio_peak"]       = round(peak, 2)
        # Recovery: sessions since last peak
        peak_idx = values.index(peak)
        book["sessions_since_peak"]  = len(values) - 1 - peak_idx

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
    # Use .get with a pnl fallback so a legacy/partial record without "won"
    # never raises — defensiveness for older brain data.
    wins   = [t for t in trades if t.get("won", t.get("pnl", 0) > 0)]
    losses = [t for t in trades if not t.get("won", t.get("pnl", 0) > 0)]
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


def is_ready_to_alert(stats: dict, book: dict = None) -> bool:
    """
    All three conditions must be true before we surface recommendations:
      1. Minimum number of closed paper trades (statistical sample size)
      2. Win rate at or above threshold (signal quality)
      3. Positive expectancy (edge exists)
      4. Minimum number of distinct trading DAYS observed (time-based gate)
         — prevents alerting after just a few lucky days on a hot market
    """
    if stats["total"] < MIN_TRADES_FOR_SIGNAL:
        return False
    if stats["win_rate"] < WIN_RATE_THRESHOLD:
        return False
    if stats["expectancy"] <= 0:
        return False
    # Time gate: require at least PAPER_TRADING_DAYS distinct snapshot dates
    if book is not None:
        distinct_days = len(book.get("daily_snapshots", []))
        if distinct_days < PAPER_TRADING_DAYS:
            print(f"[alert] Not ready: only {distinct_days}/{PAPER_TRADING_DAYS} paper trading days elapsed")
            return False
    return True


# type alias for readability
Tuple2 = tuple
