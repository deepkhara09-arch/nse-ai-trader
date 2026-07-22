"""
My Trades — the USER's real positions, managed by the tool.

When you actually act on a recommendation, you tell the tool via the GitHub
Actions "My Trades" workflow (a simple form: bought/sold, ticker, price, qty).
From then on, every session the tool manages YOUR position exactly like its own
paper positions — mark-to-market with the live price, trail the stop with the
same rules, and flag EXIT NOW when your stop/target/time is hit — all anchored
to YOUR fill price, shown in a "My Positions" panel on the Trade tab.

The tool NEVER auto-closes your position (it can't know your real fill): it
flags the exit and you confirm by logging "sold" in the same form.

Storage: brain/my_positions.json
  { "open":   [ {ticker, action, entry, qty, open_date, stop_loss, target,
                 style, source, exit_signal, ...} ],
    "closed": [ ... + {close_date, exit_price, pnl, pnl_pct} ] }

CLI (used by the workflow):
  python -m agent.my_trades bought BAJFINANCE 1015.50 9
  python -m agent.my_trades sold   BAJFINANCE 1088.00
"""

import json
import os
import sys

from agent.config import BRAIN_DIR, NSE_UNIVERSE, ATR_STOP_MULTIPLIER, ATR_TARGET_MULTIPLIER
from agent.trading_calendar import ist_today

MY_POSITIONS_FILE = "brain/my_positions.json"


def load_my_positions() -> dict:
    from agent.io_safe import load_json_dict
    d = load_json_dict(MY_POSITIONS_FILE)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("open", [])
    d.setdefault("closed", [])
    return d


def save_my_positions(data: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(MY_POSITIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper().replace(" ", "")
    if not t.endswith(".NS"):
        t += ".NS"
    return t


def _personal_stop_target(ticker: str, side: str, entry: float):
    """Stop/target anchored to YOUR fill price.

    Preference order:
      1. The live recommendation for this stock — reuse its RISK GEOMETRY (the
         rupee distance from its entry to its stop/target) around your fill, so
         your plan matches the call you acted on even if your fill differs a bit.
      2. ATR from current stock data (same multipliers as paper trades).
      3. Flat % fallback (3.5% stop / 7% target) if no data at all.
    Returns (stop, target, source_str).
    """
    sgn = 1 if side == "BUY" else -1
    # 1) live rec geometry
    try:
        from agent.recommendations import load_recommendations
        for r in load_recommendations():
            if r.get("ticker") == ticker:
                mid = ((r.get("entry_low") or 0) + (r.get("entry_high") or 0)) / 2 or r.get("cmp") or entry
                d_stop = abs(mid - (r.get("stop_loss") or 0))
                d_tgt  = abs((r.get("target1") or 0) - mid)
                if d_stop > 0 and d_tgt > 0:
                    return (round(entry - sgn * d_stop, 2),
                            round(entry + sgn * d_tgt, 2),
                            "recommendation geometry")
    except Exception:
        pass
    # 2) ATR
    try:
        from agent.data_fetcher import load_stock_data
        atr = load_stock_data().get(ticker, {}).get("latest", {}).get("atr")
        if atr:
            return (round(entry - sgn * atr * ATR_STOP_MULTIPLIER, 2),
                    round(entry + sgn * atr * ATR_TARGET_MULTIPLIER, 2),
                    "ATR")
    except Exception:
        pass
    # 3) flat
    return (round(entry * (1 - sgn * 0.035), 2),
            round(entry * (1 + sgn * 0.07), 2), "flat %")


def _patterns_now(ticker: str) -> list:
    """Patterns firing on this stock right now — from the live recommendation if
    present, else re-detected from stored stock data. Used to learn from the
    user's real outcome later."""
    try:
        from agent.recommendations import load_recommendations
        for r in load_recommendations():
            if r.get("ticker") == ticker and r.get("patterns_seen"):
                return list(r["patterns_seen"])
    except Exception:
        pass
    try:
        from agent.data_fetcher import load_stock_data
        from agent.brain import detect_all_patterns
        e = load_stock_data().get(ticker, {})
        if e.get("latest"):
            return detect_all_patterns(e["latest"], e.get("prev_bar"), e.get("prev2_bar"))
    except Exception:
        pass
    return []


def record_bought(ticker_raw: str, price: float, qty: int, side: str = "BUY") -> str:
    ticker = _normalize_ticker(ticker_raw)
    data = load_my_positions()
    if any(p["ticker"] == ticker for p in data["open"]):
        return f"REJECTED: you already have an open position in {ticker} — log 'sold' first."
    stop, target, src = _personal_stop_target(ticker, side, price)
    pos = {
        "ticker":       ticker,
        "action":       side,
        "entry":        round(price, 2),
        "qty":          int(qty),
        "invested":     round(price * int(qty), 2),
        "open_date":    ist_today().isoformat(),
        "stop_loss":    stop,
        "target":       target,
        "style":        "swing",            # user trades are managed as swing
        "plan_source":  src,
        "known_stock":  ticker in NSE_UNIVERSE,
        "exit_signal":  "",                  # set by session runs: target_hit/stop_hit
        "current_price": round(price, 2),
        "unrealized_pnl": 0.0,
        # Snapshot the patterns firing NOW so the tool can learn from the real
        # outcome when you later close this trade (prefer the live rec's list;
        # fall back to re-detecting on current stock data).
        "patterns":     _patterns_now(ticker),
    }
    data["open"].append(pos)
    save_my_positions(data)
    note = "" if pos["known_stock"] else " (NOT in the tool's universe — no live price management!)"
    return (f"RECORDED: {side} {qty}x {ticker} @ {price:.2f} | plan from {src}: "
            f"stop {stop:.2f}, target {target:.2f}{note}")


def record_sold(ticker_raw: str, price: float) -> str:
    ticker = _normalize_ticker(ticker_raw)
    data = load_my_positions()
    pos = next((p for p in data["open"] if p["ticker"] == ticker), None)
    if not pos:
        return f"REJECTED: no open position in {ticker} to close."
    sgn = 1 if pos.get("action", "BUY") == "BUY" else -1
    pnl = round((price - pos["entry"]) * pos["qty"] * sgn, 2)
    pos.update({
        "close_date":  ist_today().isoformat(),
        "exit_price":  round(price, 2),
        "pnl":         pnl,
        "pnl_pct":     round(pnl / max(pos.get("invested", 1), 1) * 100, 2),
        "won":         pnl > 0,
    })
    data["open"] = [p for p in data["open"] if p["ticker"] != ticker]
    data["closed"] = (data["closed"] + [pos])[-100:]
    save_my_positions(data)

    # ── Learn from the user's REAL outcome ─────────────────────────────────────
    # A real, executed trade the user actually took is the highest-quality signal
    # the tool can get — better than a paper trade. Feed it into pattern
    # reliability (full weight) so the tool's edge improves from your real results.
    try:
        from agent.brain import load_patterns, save_patterns, learn_from_trade
        pats = pos.get("patterns") or []
        if pats:
            db = load_patterns()
            db = learn_from_trade(ticker, pats, pnl > 0, pos.get("style", "swing"), db,
                                  exit_reason="user_closed")
            save_patterns(db)
            print(f"[my-trades] taught the brain from your real {ticker} outcome ({'win' if pnl>0 else 'loss'})")
    except Exception as e:
        print(f"[my-trades] learn-from-real non-fatal: {e}")

    return (f"CLOSED: {ticker} @ {price:.2f} | P&L {pnl:+,.2f} ({pos['pnl_pct']:+.2f}%) "
            f"| {'WIN' if pnl > 0 else 'LOSS'}")


def manage_positions(stock_data: dict, save: bool = True) -> dict:
    """Run once per session on YOUR real positions:
      • mark-to-market at the live price
      • TRAIL the stop up as profit grows (never down)
      • REFRESH the target from the tool's current live recommendation (so your
        plan tracks the tool's updated view of the stock), never pulling it in
        below what you've already achieved
      • read the tool's CURRENT view (still a BUY? flipped to SELL/weak?) and set
        an advisory action: HOLD / EXIT NOW (stop or target hit) / CONSIDER EXIT
        (the tool no longer likes it)
    NEVER auto-closes — you confirm the exit."""
    data = load_my_positions()
    if not data["open"]:
        return data

    # Trail stops with the same proven rules as paper positions.
    try:
        from agent.paper_trader import _update_trailing_stops
        wrapper = {"open_positions": data["open"]}
        _update_trailing_stops(wrapper, stock_data)
        data["open"] = wrapper["open_positions"]
    except Exception as e:
        print(f"[my-trades] trailing skipped (non-fatal): {e}")

    # The tool's live recommendations, keyed by ticker, to refresh targets + view.
    live_recs = {}
    try:
        from agent.recommendations import load_recommendations
        live_recs = {r.get("ticker"): r for r in load_recommendations()}
    except Exception:
        pass

    for pos in data["open"]:
        tk  = pos["ticker"]
        d   = stock_data.get(tk, {}).get("latest", {})
        cur = d.get("current_price") or d.get("close")
        if not cur:
            continue
        buy = pos.get("action", "BUY") == "BUY"
        sgn = 1 if buy else -1
        pos["current_price"]  = round(cur, 2)
        pos["unrealized_pnl"] = round((cur - pos["entry"]) * pos["qty"] * sgn, 2)

        # ── Refresh TARGET from the tool's current rec (track its updated view) ──
        rec = live_recs.get(tk)
        if rec and rec.get("target1"):
            new_t = rec["target1"]
            # Only extend the target in the trade's favour (don't cut a target you
            # may already be near); this keeps the plan live without whipsawing.
            if buy and new_t > pos.get("target", 0):
                pos["target"] = round(new_t, 2)
            elif (not buy) and new_t < pos.get("target", 1e9):
                pos["target"] = round(new_t, 2)

        # ── The tool's CURRENT view on this stock (advisory) ────────────────────
        view = "hold"
        if rec:
            rsig = rec.get("direction_short") or rec.get("signal")
            if (buy and rsig_is_sell(rsig)) or ((not buy) and rsig_is_buy(rsig)):
                view = "reversed"     # tool now leans the OTHER way — consider exit
        elif tk not in live_recs and pos.get("was_recommended"):
            view = "dropped"          # tool no longer surfaces it as a setup

        hi = d.get("session_high") or d.get("day_high") or cur
        lo = d.get("session_low")  or d.get("day_low")  or cur
        if buy:
            if lo <= pos["stop_loss"]:   pos["exit_signal"] = "stop_hit"
            elif hi >= pos["target"]:    pos["exit_signal"] = "target_hit"
            else:                        pos["exit_signal"] = ""
        else:
            if hi >= pos["stop_loss"]:   pos["exit_signal"] = "stop_hit"
            elif lo <= pos["target"]:    pos["exit_signal"] = "target_hit"
            else:                        pos["exit_signal"] = ""
        pos["tool_view"] = view
        pos["was_recommended"] = pos.get("was_recommended") or (tk in live_recs)

    if save:
        save_my_positions(data)
    return data


def rsig_is_sell(s: str) -> bool:
    return str(s or "").upper() in ("SELL", "SHORT")


def rsig_is_buy(s: str) -> bool:
    return str(s or "").upper() in ("BUY", "LONG")


if __name__ == "__main__":
    # CLI for the GitHub Actions form:  bought TICKER PRICE QTY | sold TICKER PRICE
    args = sys.argv[1:]
    if len(args) < 3:
        print("usage: python -m agent.my_trades bought TICKER PRICE QTY | sold TICKER PRICE")
        sys.exit(1)
    verb, tick, price = args[0].lower(), args[1], float(args[2])
    if verb == "bought":
        qty = int(float(args[3])) if len(args) > 3 else 1
        print(record_bought(tick, price, qty))
    elif verb == "sold":
        print(record_sold(tick, price))
    else:
        print(f"unknown action '{verb}' — use bought/sold")
        sys.exit(1)
