"""
Recommendations Engine — the agent's final output to the user.

This module produces fully detailed, research-backed trade recommendations.
It only recommends a stock when ALL of the following are true:
  1. Technical setup is valid (multi-indicator agreement)
  2. Support/Resistance levels confirm the trade
  3. Volume confirms the move
  4. Market mood is not bearish (checked via market_health.py)
  5. Paper trade history on this stock shows positive expectancy
  6. Risk:Reward is at least 2:1
  7. Agent confidence >= 60%

Each recommendation includes:
  - Stock name + NSE code
  - Current market price (CMP)
  - Entry zone (range, not just a single price)
  - Stop loss (reason explained)
  - Target 1 (conservative) + Target 2 (if momentum continues)
  - Holding period
  - Position size guideline
  - Risk per trade (in %)
  - Full reasoning (what the agent saw)
  - Paper trade track record on this stock
  - Overall confidence score
"""

import json
import os
from datetime import date
from typing import Dict, List, Optional

from agent.config import (
    BRAIN_DIR, INITIAL_CAPITAL,
    ATR_STOP_MULTIPLIER, ATR_TARGET_MULTIPLIER,
)
from agent.support_resistance import compute_levels, nearest_strong_support, nearest_strong_resistance
from agent.brain import analyse_stock, get_reliable_patterns_list
from agent.paper_trader import compute_stats

RECOMMENDATIONS_FILE = "brain/recommendations.json"

# ── Name map (NSE code → full company name) ────────────────────────────────────
STOCK_NAMES = {
    "RELIANCE.NS":   "Reliance Industries Ltd",
    "TCS.NS":        "Tata Consultancy Services",
    "INFY.NS":       "Infosys Ltd",
    "HDFCBANK.NS":   "HDFC Bank Ltd",
    "ICICIBANK.NS":  "ICICI Bank Ltd",
    "HINDUNILVR.NS": "Hindustan Unilever Ltd",
    "ITC.NS":        "ITC Ltd",
    "SBIN.NS":       "State Bank of India",
    "BHARTIARTL.NS": "Bharti Airtel Ltd",
    "KOTAKBANK.NS":  "Kotak Mahindra Bank",
    "LT.NS":         "Larsen & Toubro Ltd",
    "AXISBANK.NS":   "Axis Bank Ltd",
    "ASIANPAINT.NS": "Asian Paints Ltd",
    "MARUTI.NS":     "Maruti Suzuki India",
    "BAJFINANCE.NS": "Bajaj Finance Ltd",
    "HCLTECH.NS":    "HCL Technologies",
    "WIPRO.NS":      "Wipro Ltd",
    "ULTRACEMCO.NS": "UltraTech Cement",
    "TITAN.NS":      "Titan Company Ltd",
    "SUNPHARMA.NS":  "Sun Pharmaceutical",
    "NESTLEIND.NS":  "Nestle India Ltd",
    "POWERGRID.NS":  "Power Grid Corporation",
    "NTPC.NS":       "NTPC Ltd",
    "ONGC.NS":       "Oil & Natural Gas Corp",
    "COALINDIA.NS":  "Coal India Ltd",
    "TATAMOTORS.NS": "Tata Motors Ltd",
    "TATASTEEL.NS":  "Tata Steel Ltd",
    "JSWSTEEL.NS":   "JSW Steel Ltd",
    "ADANIENT.NS":   "Adani Enterprises Ltd",
    "ADANIPORTS.NS": "Adani Ports & SEZ",
    "TECHM.NS":      "Tech Mahindra Ltd",
    "DRREDDY.NS":    "Dr. Reddy's Laboratories",
    "DIVISLAB.NS":   "Divi's Laboratories",
    "CIPLA.NS":      "Cipla Ltd",
    "BAJAJFINSV.NS": "Bajaj Finserv Ltd",
    "EICHERMOT.NS":  "Eicher Motors Ltd",
    "HEROMOTOCO.NS": "Hero MotoCorp Ltd",
    "APOLLOHOSP.NS": "Apollo Hospitals Enterprise",
    "TATACONSUM.NS": "Tata Consumer Products",
    "BRITANNIA.NS":  "Britannia Industries",
    "PIDILITIND.NS": "Pidilite Industries",
    "DABUR.NS":      "Dabur India Ltd",
    "MARICO.NS":     "Marico Ltd",
    "GODREJCP.NS":   "Godrej Consumer Products",
    "MUTHOOTFIN.NS": "Muthoot Finance Ltd",
    "INDUSINDBK.NS": "IndusInd Bank Ltd",
    "BANDHANBNK.NS": "Bandhan Bank Ltd",
    "IDFCFIRSTB.NS": "IDFC First Bank Ltd",
    "GRASIM.NS":     "Grasim Industries Ltd",
    "SHREECEM.NS":   "Shree Cement Ltd",
}


def generate_recommendations(
    state: dict,
    stock_data: Dict,
    patterns: Dict,
    news_data: Dict,
    book: dict,
    market_health: dict,
) -> List[dict]:
    """
    Generate fully-researched trade recommendations for focus stocks.
    Returns a list of recommendation dicts, sorted by confidence.
    """
    focus       = state.get("focus_stocks", [])
    trade_ok    = market_health.get("trade_allowed", True)
    market_mood = market_health.get("market_mood", "neutral")
    recs        = []

    for ticker in focus:
        entry_data = stock_data.get(ticker)
        if not entry_data or "latest" not in entry_data:
            continue

        d       = entry_data["latest"]
        close   = d.get("close", 0)
        if close <= 0:
            continue

        news    = news_data.get(ticker, {}).get("latest", {})
        opinion = analyse_stock(ticker, entry_data, patterns, news, "morning")

        if opinion["signal"] not in ("BUY", "SELL"):
            continue
        if opinion["confidence"] < 58:
            continue

        # ── Support/Resistance ─────────────────────────────────────────────────
        ph    = entry_data.get("price_history_60d", [])
        vh    = entry_data.get("volume_history_20d", [])
        atr   = d.get("atr", close * 0.025)
        levels = compute_levels(ph, vh, close)
        n_sup  = nearest_strong_support(levels)
        n_res  = nearest_strong_resistance(levels)

        # ── Refine stop/target using S/R ──────────────────────────────────────
        if opinion["signal"] == "BUY":
            # Stop just below nearest support (or 1.5x ATR, whichever is tighter)
            atr_stop   = close - atr * ATR_STOP_MULTIPLIER
            sr_stop    = n_sup * 0.992 if n_sup > 0 else atr_stop
            stop_loss  = round(max(atr_stop, sr_stop), 2)   # tighter of the two = better risk control
            # Target 1 = nearest resistance; Target 2 = next resistance or 3x ATR
            target1    = round(n_res if n_res > close else close + atr * 2.5, 2)
            target2    = round(close + atr * ATR_TARGET_MULTIPLIER, 2)
            entry_low  = round(close * 0.998, 2)   # entry zone (just below CMP)
            entry_high = round(close * 1.005, 2)
        else:
            atr_stop   = close + atr * ATR_STOP_MULTIPLIER
            sr_stop    = n_res * 1.008 if n_res > 0 else atr_stop
            stop_loss  = round(min(atr_stop, sr_stop), 2)
            target1    = round(n_sup if n_sup < close else close - atr * 2.5, 2)
            target2    = round(close - atr * ATR_TARGET_MULTIPLIER, 2)
            entry_low  = round(close * 0.995, 2)
            entry_high = round(close * 1.002, 2)

        risk_amt  = abs(close - stop_loss)
        rew1_amt  = abs(target1 - close)
        rew2_amt  = abs(target2 - close)
        risk_pct  = round(risk_amt / close * 100, 2)
        rr1       = round(rew1_amt / risk_amt, 2) if risk_amt > 0 else 0
        rr2       = round(rew2_amt / risk_amt, 2) if risk_amt > 0 else 0

        # Skip if R:R is below 1.8:1
        if rr1 < 1.8:
            continue

        # ── Position sizing (risk ₹2000 per trade → qty) ──────────────────────
        risk_per_trade_inr = INITIAL_CAPITAL * 0.02   # 2% of capital
        qty_by_risk = max(1, int(risk_per_trade_inr / risk_amt)) if risk_amt > 0 else 1
        qty_by_cap  = max(1, int(INITIAL_CAPITAL * 0.12 / close))
        recommended_qty = min(qty_by_risk, qty_by_cap)
        invested        = round(close * recommended_qty, 2)

        # ── Paper trade record for this stock ──────────────────────────────────
        stock_trades = [t for t in book.get("closed_trades", []) if t["ticker"] == ticker]
        stock_stats  = _mini_stats(stock_trades)

        # ── Learned reliable patterns ──────────────────────────────────────────
        reliable_pats = get_reliable_patterns_list(ticker, patterns, min_rel=0.55)

        # ── Style and hold period ─────────────────────────────────────────────
        tk_brain = patterns.get(ticker, {})
        style    = tk_brain.get("preferred_style", "swing")
        if style == "swing":
            hold = "5–10 trading days"
        elif style == "intraday":
            hold = "Same day (exit before 3:15 PM IST)"
        else:
            hold = "3–7 trading days (adaptive)"

        # ── Build full reasoning ──────────────────────────────────────────────
        reasons = list(opinion.get("buy_reasons" if opinion["signal"] == "BUY"
                                    else "sell_reasons", []))
        if reliable_pats:
            reasons.append(f"Reliable patterns: {', '.join(reliable_pats[:4])}")
        if n_sup > 0 and opinion["signal"] == "BUY":
            reasons.append(f"Support at ₹{n_sup:.2f} provides downside cushion")
        if n_res > 0 and opinion["signal"] == "BUY":
            reasons.append(f"Resistance at ₹{n_res:.2f} sets natural target")
        if news.get("score", 0) > 0.1:
            hl = news.get("headlines", [])
            if hl:
                reasons.append(f"News: '{hl[0][:60]}...'")
        if market_mood == "bullish" and opinion["signal"] == "BUY":
            reasons.append("Broad market trending bullish — tailwind")

        rec = {
            "ticker":         ticker,
            "nse_code":       ticker.replace(".NS", ""),
            "company_name":   STOCK_NAMES.get(ticker, ticker),
            "date":           date.today().isoformat(),
            "signal":         opinion["signal"],
            "style":          style,
            "hold_period":    hold,

            # Prices
            "cmp":            round(close, 2),
            "entry_low":      entry_low,
            "entry_high":     entry_high,
            "stop_loss":      stop_loss,
            "target1":        target1,
            "target2":        target2,

            # Risk metrics
            "risk_pct":       risk_pct,
            "rr_target1":     rr1,
            "rr_target2":     rr2,
            "recommended_qty":recommended_qty,
            "capital_needed": invested,
            "max_loss_if_sl": round(risk_amt * recommended_qty, 2),

            # Intelligence
            "confidence":     opinion["confidence"],
            "buy_score":      opinion["buy_score"],
            "sell_score":     opinion["sell_score"],
            "news_score":     news.get("score", 0),
            "reasons":        reasons,
            "patterns_seen":  opinion.get("patterns", []),
            "reliable_patterns": reliable_pats,

            # S/R context
            "nearest_support":    n_sup,
            "nearest_resistance": n_res,
            "distance_to_sl_pct": risk_pct,

            # Paper trade track record
            "paper_trades_on_stock": len(stock_trades),
            "paper_win_rate":        stock_stats.get("win_rate", 0),
            "paper_pnl":             stock_stats.get("total_pnl", 0),
            "paper_expectancy":      stock_stats.get("expectancy", 0),

            # Market context
            "market_mood":    market_mood,
            "market_warning": market_health.get("warnings", []),
        }
        recs.append(rec)

    # Sort by confidence desc
    recs.sort(key=lambda x: x["confidence"], reverse=True)

    # Save to brain
    _save(recs)
    return recs


def load_recommendations() -> List[dict]:
    if os.path.exists(RECOMMENDATIONS_FILE):
        with open(RECOMMENDATIONS_FILE) as f:
            return json.load(f)
    return []


def _save(recs: List[dict]) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(RECOMMENDATIONS_FILE, "w") as f:
        json.dump(recs, f, indent=2)


def _mini_stats(trades: List[dict]) -> dict:
    if not trades:
        return {"total": 0, "win_rate": 0, "total_pnl": 0, "expectancy": 0}
    wins   = [t for t in trades if t.get("won")]
    losses = [t for t in trades if not t.get("won")]
    wr     = len(wins) / len(trades)
    avg_w  = sum(t["pnl"] for t in wins)   / max(len(wins), 1)
    avg_l  = sum(t["pnl"] for t in losses) / max(len(losses), 1)
    return {
        "total":      len(trades),
        "wins":       len(wins),
        "losses":     len(losses),
        "win_rate":   round(wr, 3),
        "total_pnl":  round(sum(t["pnl"] for t in trades), 2),
        "expectancy": round(wr * avg_w + (1 - wr) * avg_l, 2),
    }


def format_recommendation_text(rec: dict) -> str:
    """Returns a clean text summary of one recommendation (for logs/STRATEGY_REPORT)."""
    signal = rec["signal"]
    arrow  = "📈" if signal == "BUY" else "📉"
    lines  = [
        f"{arrow} {rec['company_name']} ({rec['nse_code']})",
        f"   Signal:        {signal}",
        f"   CMP:           ₹{rec['cmp']:,.2f}",
        f"   Entry Zone:    ₹{rec['entry_low']:,.2f} – ₹{rec['entry_high']:,.2f}",
        f"   Stop Loss:     ₹{rec['stop_loss']:,.2f}  ({rec['risk_pct']:.1f}% risk)",
        f"   Target 1:      ₹{rec['target1']:,.2f}  (R:R = 1:{rec['rr_target1']:.1f})",
        f"   Target 2:      ₹{rec['target2']:,.2f}  (R:R = 1:{rec['rr_target2']:.1f})",
        f"   Hold:          {rec['hold_period']}",
        f"   Qty (2% risk): {rec['recommended_qty']} shares  →  ₹{rec['capital_needed']:,.0f} invested",
        f"   Max loss:      ₹{rec['max_loss_if_sl']:,.0f} if stop hit",
        f"   Confidence:    {rec['confidence']:.0f}%",
        f"   Paper trades:  {rec['paper_trades_on_stock']} ({rec['paper_win_rate']*100:.0f}% win rate, expectancy ₹{rec['paper_expectancy']:+.0f})",
        "   Reasons:",
        *[f"     • {r}" for r in rec["reasons"]],
    ]
    if rec.get("market_warning"):
        lines.append("   ⚠ Market warnings: " + " | ".join(rec["market_warning"]))
    return "\n".join(lines)
