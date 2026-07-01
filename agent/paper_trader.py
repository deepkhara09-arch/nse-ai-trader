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
    PAPER_TRADING_DAYS, MAX_SECTOR_POSITIONS, MAX_HELD_DAYS,
    VOL_REGIME_NORMAL_MAX_PCT, VOL_REGIME_CAUTION_MAX_PCT, VOL_REGIME_DANGER_MAX_PCT,
)
from agent.brain import learn_from_trade
from agent.trading_calendar import ist_today

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
        # Lifetime aggregates — survive any trimming of the trade records below, so
        # the all-time win-rate / count is never lost even years out.
        "lifetime_wins":    0,
        "lifetime_losses":  0,
        "lifetime_pnl":     0.0,
    }


# A high cap on STORED trade records (≈ many years of trading). Records beyond this
# are dropped from storage, but lifetime aggregates are preserved separately so
# stats and the validation gate stay correct. This only bounds disk growth; it is
# deliberately high so it never interferes with normal operation.
MAX_STORED_TRADES = 3000


def _record_closed_trade(book: dict, trade: dict) -> None:
    """Append a closed trade, update lifetime aggregates, and bound stored records.
    Lifetime counters persist even if old records are trimmed."""
    book.setdefault("closed_trades", []).append(trade)
    won = trade.get("won", trade.get("pnl", 0) > 0)
    book["lifetime_wins"]   = book.get("lifetime_wins", 0)   + (1 if won else 0)
    book["lifetime_losses"] = book.get("lifetime_losses", 0) + (0 if won else 1)
    book["lifetime_pnl"]    = round(book.get("lifetime_pnl", 0.0) + trade.get("pnl", 0), 2)
    if len(book["closed_trades"]) > MAX_STORED_TRADES:
        book["closed_trades"] = book["closed_trades"][-MAX_STORED_TRADES:]


# ── Session actions ────────────────────────────────────────────────────────────

def morning_session(book: dict, opinions: List[dict], patterns_db: Dict, market_health: dict = None,
                    stock_data: Dict = None) -> Tuple2:
    """At the open: FIRST mark-to-market and check exits on existing positions —
    so a swing/long-term position that gapped through its stop or target overnight
    is exited right at the open, not hours later at midday. THEN open new positions.
    (Checking exits every session is what guarantees a held position is evaluated
    every trading day for its stop/target, and force-closed at its max-held day.)"""
    book = _reset_daily_pnl_if_new_day(book)
    if stock_data:
        book = _mark_to_market(book, stock_data)
        book = _update_trailing_stops(book, stock_data)
        book, patterns_db = _check_exits(book, stock_data, session="morning", patterns_db=patterns_db)
    book, patterns_db = _try_open_positions(book, opinions, patterns_db, session="morning", market_health=market_health)
    return book, patterns_db


def midday_session(book: dict, opinions: List[dict], stock_data: Dict, patterns_db: Dict, market_health: dict = None) -> Tuple2:
    """Update mark-to-market, close intraday positions gone wrong, add longer-horizon
    (swing / long-term) signals. Intraday is only opened in the morning session —
    entering an intraday trade at midday leaves too little runway before the close."""
    book = _mark_to_market(book, stock_data)
    book = _update_trailing_stops(book, stock_data)
    book, patterns_db = _check_exits(book, stock_data, session="midday", patterns_db=patterns_db)
    positional_opinions = [o for o in opinions if o.get("style") in ("swing", "long_term")]
    book, patterns_db = _try_open_positions(book, positional_opinions, patterns_db, session="midday", market_health=market_health)
    return book, patterns_db


def intraday_close_session(book: dict, stock_data: Dict, patterns_db: Dict) -> Tuple2:
    """Square off ONLY intraday positions at ~3:15 PM (before the 3:30 close), as a
    real intraday trader would. Swing positions are untouched (they ride to their
    stop/target across days). Runs at the dedicated 3:15 'intraday_close' session.

    Resilient to a late trigger: it first checks if each intraday position hit its
    stop/target during the day (exit at that level), otherwise squares off at the
    3:15-reference price — so even if this fires at 3:25/3:35 the exit is anchored
    to the intended ~3:15 level, not the late-fire price."""
    book = _mark_to_market(book, stock_data)
    # Honour stop/target hits that occurred earlier in the day first…
    book, patterns_db = _check_exits(book, stock_data, session="intraday_close", patterns_db=patterns_db)
    # …then square off whatever intraday remains at the 3:15 reference price.
    book, patterns_db = _close_intraday_positions(book, stock_data, patterns_db,
                                                  close_session="intraday_close")
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
    # Defensive daily reset: normally done in morning_session, but if the morning
    # run was ever missed and a later session opens first, yesterday's daily_pnl
    # must not carry over and wrongly block today's trades via the loss limit.
    book = _reset_daily_pnl_if_new_day(book)
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
        # ── Stale-price safety: never OPEN a position on a non-live price ────────
        # If the live quote fetch failed, the tool falls back to a (possibly day-
        # old) close. Opening a real entry on a stale price is a correctness risk —
        # the entry/stop/target would be anchored to a wrong number. Skip it; we'll
        # re-evaluate next session when a fresh price is available.
        if op.get("price_is_live") is False:
            print(f"[paper] Skipping {ticker} — price not live this run (stale fetch), won't open on stale price")
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

        # ── Earnings guard: halve size for a swing held through results (gap risk).
        # Intraday is squared off same day so earnings gap risk doesn't apply.
        pos_pct = vol_max_pct
        dte = op.get("days_to_earnings")
        earnings_soon = (dte is not None and 0 <= dte <= 3 and op.get("style") != "intraday")
        if earnings_soon:
            pos_pct = vol_max_pct * 0.5
            print(f"[paper] {ticker}: earnings in {dte}d → size halved (overnight gap risk)")

        max_invest = book["capital"] * pos_pct
        qty = int(max_invest // entry)
        if qty < 1:
            continue

        pos = {
            "ticker":        ticker,
            "sector":        sector,
            "open_date":     ist_today().isoformat(),
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
            # Hold period is driven by the trade's STYLE (config), so intraday exits
            # same day, swing rides ~2 weeks, and long_term holds ~3 months.
            "max_held_days": MAX_HELD_DAYS.get(op.get("style", "swing"), 10),
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

    IMPORTANT (avoids a self-trigger bug): a newly-raised stop must not sit ABOVE
    (for a BUY) the current session's low — otherwise the very next exit-check
    would stop us out on a low that happened BEFORE the stop was raised (the low
    and the trail are computed from the same session snapshot). We clamp the new
    stop just below the session low so trailing only bites on FUTURE downside.
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
        sess_low  = data.get("session_low")  or data.get("day_low")  or data.get("low",  current)
        sess_high = data.get("session_high") or data.get("day_high") or data.get("high", current)

        if pos["action"] == "BUY":
            profit_pct = (current - entry) / entry * 100
            def _clamp_buy(sl):
                # never above this session's low (leave a hair of room)
                return min(sl, round(sess_low * 0.999, 2)) if sess_low else sl
            if profit_pct >= 6.0:
                # Trail stop to lock in 50% of profit
                new_sl = _clamp_buy(round(entry + (current - entry) * 0.5, 2))
                if new_sl > pos["stop_loss"]:
                    pos["stop_loss"] = new_sl
                    pos["trailing_active"] = True
            elif profit_pct >= 4.0:
                # Move stop to breakeven
                new_sl = _clamp_buy(round(entry * 1.001, 2))
                if pos["stop_loss"] < entry and new_sl > pos["stop_loss"]:
                    pos["stop_loss"] = new_sl
                    pos["trailing_active"] = True
        else:
            profit_pct = (entry - current) / entry * 100
            def _clamp_sell(sl):
                # never below this session's high (for a short, stop is above)
                return max(sl, round(sess_high * 1.001, 2)) if sess_high else sl
            if profit_pct >= 6.0:
                new_sl = _clamp_sell(round(entry - (entry - current) * 0.5, 2))
                if new_sl < pos["stop_loss"]:
                    pos["stop_loss"] = new_sl
                    pos["trailing_active"] = True
            elif profit_pct >= 4.0:
                new_sl = _clamp_sell(round(entry * 0.999, 2))
                if pos["stop_loss"] > entry and new_sl < pos["stop_loss"]:
                    pos["stop_loss"] = new_sl
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
    today = ist_today().isoformat()

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
            _record_closed_trade(book, trade)
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


def _close_intraday_positions(book: dict, stock_data: Dict, patterns_db: Dict = None,
                              close_session: str = "preclose") -> Tuple2:
    """Force-close any intraday positions. Also teaches the brain from each outcome
    so intraday trades update pattern reliability too.

    close_session="intraday_close" → this is the dedicated 3:15 square-off. We
    anchor the exit to the ~3:15 reference price (price_315 if the fetcher captured
    it, else the current price) so a LATE trigger fire still books the intended
    3:15 level rather than a stale late price. close_session="preclose" is the 3:40
    backstop that mops up any intraday position the 3:15 run missed."""
    today = ist_today().isoformat()
    still_open = []
    for pos in book["open_positions"]:
        if pos.get("style") == "intraday":
            ticker  = pos["ticker"]
            data    = stock_data.get(ticker, {}).get("latest", {})
            # At the 3:15 square-off, prefer a captured 3:15 reference price so a
            # late-firing trigger doesn't book a later price. Fall back to current.
            if close_session == "intraday_close":
                current = data.get("price_315") or data.get("current_price") or data.get("close", pos["entry"])
            else:
                current = data.get("current_price") or data.get("close", pos["entry"])
            pnl     = (current - pos["entry"]) * pos["qty"] * (1 if pos["action"] == "BUY" else -1)
            won     = pnl > 0
            trade   = {**pos,
                "close_date": today, "close_session": close_session,
                "exit_price": round(current, 2), "exit_reason": "intraday_forced_close",
                "pnl": round(pnl, 2), "pnl_pct": round(pnl / pos["invested"] * 100, 2),
                "won": won,
                "open_days": _days_between(pos["open_date"], today),
            }
            _record_closed_trade(book, trade)
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
    today = ist_today().isoformat()
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
    """Reset the intraday P&L exactly ONCE per calendar day, tracked by its own
    date field. (Using last_snapshot_date was subtly wrong — that's only set at
    preclose, so a mid-day call could reset again and wipe the morning's losses,
    defeating the daily-loss limit.) Idempotent: safe to call every session."""
    today = ist_today().isoformat()
    if book.get("last_pnl_reset_date") != today:
        book["daily_pnl_today"]    = 0.0
        book["last_pnl_reset_date"] = today
    return book


def _days_between(d1: str, d2: str) -> int:
    """TRADING days between two ISO dates (weekends/holidays excluded), so a
    max_held_days limit means trading days — consistent with the rest of the tool
    and with how the config comments describe it. A swing opened before a weekend
    doesn't lose 2 of its held-days to Sat/Sun."""
    try:
        from agent.trading_calendar import is_trading_day
        from datetime import timedelta
        a = date.fromisoformat(d1)
        b = date.fromisoformat(d2)
        if b <= a:
            return 0
        n = 0
        probe = a
        while probe < b:
            probe += timedelta(days=1)
            if is_trading_day(probe):
                n += 1
        return n
    except Exception:
        # Fall back to calendar days if anything goes wrong — never break an exit.
        return (date.fromisoformat(d2) - date.fromisoformat(d1)).days


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
    avg_w  = sum(t["pnl"] for t in wins)   / max(len(wins),   1)
    avg_l  = sum(t["pnl"] for t in losses) / max(len(losses), 1)

    # All-time totals: prefer lifetime aggregates if old records were ever trimmed
    # (records bounded at MAX_STORED_TRADES). Until then they equal the record
    # counts. This keeps the headline count/win-rate honest for the long haul.
    lt_w = book.get("lifetime_wins", 0)
    lt_l = book.get("lifetime_losses", 0)
    if lt_w + lt_l >= len(trades):
        n_wins, n_losses = lt_w, lt_l
        total_pnl = round(book.get("lifetime_pnl", sum(t["pnl"] for t in trades)), 2)
    else:
        n_wins, n_losses = len(wins), len(losses)
        total_pnl = round(sum(t["pnl"] for t in trades), 2)
    total = n_wins + n_losses
    wr    = n_wins / max(total, 1)
    # Expectancy: E = (WR × avg_win) + (LR × avg_loss)
    expectancy = round(wr * avg_w + (1 - wr) * avg_l, 2)
    return {
        "total":      total,
        "wins":       n_wins,
        "losses":     n_losses,
        "win_rate":   round(wr, 3),
        "total_pnl":  total_pnl,
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
