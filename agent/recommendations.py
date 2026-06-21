"""
Recommendations Engine — the agent's final output to the user.

Multi-layer scoring (capped at 100 points):
  Technical    40 pts — EMA alignment, RSI zone, MACD cross, BB, volume, S/R
  Fundamental  30 pts — P/E, ROE, debt, revenue growth, promoter holding
  News/Sent    20 pts — recent headline sentiment + corporate actions
  Patterns     10 pts — proven patterns + strong patterns firing today
  Confluence   10 pts — bonus for N independent signal families agreeing
  Paper bonus   5 pts — validated paper-trade performance on the stock

Recommendation fires when:
  • Total confidence >= 65 points
  • R:R >= 2.0 on Target 1
  • Market is not in DANGER mode (VIX < 25 / trade_allowed)
  • Signal is BUY or SELL (not WATCH)

Both directions are supported: BUY recs are long setups, SELL recs are short
setups (targets/stops flip accordingly). SELL recs fire in down markets too —
a bearish mood does NOT block them.

Each rec is auto-classified into one of three explicit horizons:
  • Intraday    — same-day momentum/gap/VWAP setups
  • Short-term  — 5–15 day swing setups
  • Long-term   — fundamentally strong names in an uptrend, weeks to months

Probabilities carry a data-confidence tag (Estimated / Forming / Validated)
reflecting how many real paper trades back the number — early probabilities are
indicator estimates, not proven odds, until the stock has been traded enough.

Paper trading is a BONUS signal — if we have trades on this stock with
positive expectancy, confidence gets a small boost. It is NOT a gate.
"""

import json
import os
from datetime import date, datetime
from typing import Dict, List

from agent.config import (
    BRAIN_DIR, INITIAL_CAPITAL,
    ATR_STOP_MULTIPLIER, ATR_TARGET_MULTIPLIER,
    REC_STALE_PRICE_MOVE_PCT, REC_SESSION_VALID_UNTIL,
)
from agent.support_resistance import compute_levels, nearest_strong_support, nearest_strong_resistance
from agent.brain import analyse_stock, get_reliable_patterns_list
from agent.ranking_engine import rank_focus_stocks

RECOMMENDATIONS_FILE = "brain/recommendations.json"

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


def _classify_trade_type(opinion: dict, d: dict, fund: dict, learned_style: str) -> dict:
    """
    Classify a setup into one of three explicit horizons:
      • intraday    — fast momentum signals meant to be closed the same day
      • short_term  — swing setups, typically 5–15 trading days
      • long_term   — fundamentally strong names in an uptrend, weeks to months

    The decision blends the SIGNAL CHARACTER (which patterns fired) with the
    FUNDAMENTAL strength. Returns a dict with the type and all timing guidance.
    """
    patterns = set(opinion.get("patterns", []))

    # Signals that are inherently same-day in character
    intraday_signals = {
        "gap_up_continuation", "gap_down_continuation", "gap_up_reversal",
        "gap_down_reversal", "above_vwap_strong", "below_vwap_strong",
        "vwap_magnet", "volume_climax_top", "volume_climax_bottom",
        "pivot_r1_breakout", "pivot_s1_bounce", "pivot_point_test",
        "stoch_rsi_bullish_cross", "stoch_rsi_bearish_cross",
    }
    # Signals that imply a multi-day swing
    swing_signals = {
        "supertrend_bullish", "supertrend_bearish", "ichimoku_bullish_kumo",
        "ichimoku_bearish_kumo", "ichimoku_tk_cross_bullish", "macd_bullish_divergence",
        "macd_bearish_divergence", "52w_breakout", "pivot_r2_breakout",
        "adx_strong_trend_up", "adx_strong_trend_down", "engulfing_bullish",
        "morning_star", "three_white_soldiers",
    }

    intraday_hits = len(patterns & intraday_signals)
    swing_hits    = len(patterns & swing_signals)

    # Fundamental strength gate for long-term: needs genuinely good numbers
    from agent.fundamentals_fetcher import score_fundamentals
    fund_strength = score_fundamentals(fund) if fund else 0   # 0–100
    roe           = fund.get("roe") or 0
    roce          = fund.get("roce") or 0
    de            = fund.get("debt_equity")
    rev_g         = fund.get("revenue_growth_pct") or 0
    trend         = d.get("trend_10d") or ""
    delivery_sig  = d.get("delivery_signal", "neutral")

    strong_fundamentals = (
        fund_strength >= 65 and roe >= 12 and roce >= 12
        and (de is None or de < 1.0) and rev_g >= 0
    )
    # Delivery-based accumulation supports a longer hold (institutions buying to hold)
    accumulating = delivery_sig in ("accumulation", "strong_accumulation")

    # ── Decide horizon ────────────────────────────────────────────────────────
    if intraday_hits > swing_hits and learned_style != "swing":
        ttype = "intraday"
    elif strong_fundamentals and trend in ("up", "strong_up") and (swing_hits or accumulating):
        ttype = "long_term"
    else:
        ttype = "short_term"

    # ── Timing/guidance per horizon ───────────────────────────────────────────
    if ttype == "intraday":
        return {
            "trade_type":       "Intraday",
            "trade_type_key":   "intraday",
            "hold_period":      "Same day — square off before 3:15 PM IST",
            "entry_window":     "9:30 AM – 10:30 AM IST (first-hour momentum)",
            "exit_window":      "Exit by 3:00 PM IST latest — never carry overnight",
            "target_timeframe": "2–5 hours after entry",
            "action_urgency":   "ACT THIS SESSION — intraday setups expire at market close",
        }
    if ttype == "long_term":
        return {
            "trade_type":       "Long-term / Investment",
            "trade_type_key":   "long_term",
            "hold_period":      "Several weeks to months (fundamentally backed)",
            "entry_window":     "Accumulate in 3–4 tranches over several sessions; no rush",
            "exit_window":      "Review on quarterly results / thesis change, not daily noise",
            "target_timeframe": "1–6 months — let the compounding play out",
            "action_urgency":   "Patient build — averaging on dips is fine for this horizon",
        }
    return {
        "trade_type":       "Short-term / Swing",
        "trade_type_key":   "short_term",
        "hold_period":      "5–15 trading days (swing)",
        "entry_window":     "Buy in 2–3 tranches across 9:30–11:30 AM IST; avoid last 30 mins",
        "exit_window":      "Monitor at each daily open; trail stop as it moves in favour",
        "target_timeframe": "1–3 trading weeks",
        "action_urgency":   "No rush — swing setups allow flexible entry within the zone",
    }


def _data_confidence(paper_trades: int) -> dict:
    """
    Tag how trustworthy the probability is, based on how many real paper trades
    back it. Early probabilities are estimates from indicators; they only become
    grounded once the tool has actually traded the stock enough times.
    """
    if paper_trades >= 15:
        return {"tag": "Validated", "key": "validated",
                "note": f"Backed by {paper_trades} real paper trades — probability is grounded in outcomes"}
    if paper_trades >= 5:
        return {"tag": "Forming", "key": "forming",
                "note": f"{paper_trades} paper trades so far — probability firming up but still partly estimated"}
    return {"tag": "Estimated", "key": "estimated",
            "note": f"Only {paper_trades} paper trade(s) — probability is an indicator-based estimate, not yet proven"}


def _confluence_score(opinion: dict, d: dict, news: dict, fund: dict) -> dict:
    """
    Count how many INDEPENDENT signal families confirm the trade direction.
    Independent families (not overlapping math) give a true confluence read:
      1. Trend structure (EMA stack / long-term trend)
      2. Momentum (RSI + MACD)
      3. Volume / delivery (real participation)
      4. Pattern (candlestick / chart pattern fired)
      5. News sentiment
      6. Fundamentals (only counts for longer holds)
      7. Historical regime (52w position / long trend agree)
    Returns {count, families} — count is how many agree with the signal.
    """
    sig      = opinion["signal"]
    families = []

    # 1. Trend structure
    ema_s = d.get("ema_short", 0); ema_l = d.get("ema_long", 0); ema_t = d.get("ema_trend", 0)
    if sig == "BUY" and ema_s > ema_l > ema_t:
        families.append("trend")
    elif sig == "SELL" and ema_s < ema_l < ema_t:
        families.append("trend")
    elif d.get("hist_long_trend") in (("uptrend", "strong_uptrend") if sig == "BUY"
                                      else ("downtrend", "strong_downtrend")):
        families.append("trend")

    # 2. Momentum
    rsi = d.get("rsi", 50); macd_h = d.get("macd_hist", 0)
    if sig == "BUY" and macd_h > 0 and rsi > 45:
        families.append("momentum")
    elif sig == "SELL" and macd_h < 0 and rsi < 55:
        families.append("momentum")

    # 3. Volume / delivery
    vol_rel = d.get("vol_rel", 1); dsig = d.get("delivery_signal", "neutral")
    if vol_rel >= 1.3 or dsig in ("accumulation", "strong_accumulation",
                                  "distribution"):
        families.append("volume")

    # 4. Pattern
    pats = set(opinion.get("patterns", []))
    strong_pats = pats - {"vwap_magnet", "adx_ranging_market", "pivot_point_test"}
    if strong_pats:
        families.append("pattern")

    # 5. News
    ns = news.get("score", 0)
    if (sig == "BUY" and ns > 0.1) or (sig == "SELL" and ns < -0.1):
        families.append("news")

    # 6. Fundamentals
    from agent.fundamentals_fetcher import score_fundamentals
    if fund and score_fundamentals(fund) >= 60 and sig == "BUY":
        families.append("fundamentals")

    # 7. Historical regime
    pos = d.get("hist_pct_of_52w_range")
    if pos is not None:
        if (sig == "BUY" and (pos >= 85 or pos <= 15)) or (sig == "SELL" and pos >= 80):
            families.append("regime")

    return {"count": len(families), "families": families}


def generate_recommendations(
    state: dict,
    stock_data: Dict,
    patterns: Dict,
    news_data: Dict,
    book: dict,
    market_health: dict,
    fundamentals: Dict = None,
    session: str = "preclose",
) -> List[dict]:
    focus       = state.get("focus_stocks", [])
    trade_ok    = market_health.get("trade_allowed", True)
    market_mood = market_health.get("market_mood", "neutral")
    fundamentals = fundamentals or {}
    recs = []

    # Load deep 2-year history context once (for backtest + regime in confluence)
    from agent.history_engine import load_history_context
    history_ctx = load_history_context()

    # Don't generate recommendations when market is in danger mode
    if not trade_ok:
        print("[recs] Market in danger mode — skipping recommendations")
        _save([])
        return []

    # Build rank table for all focus stocks (used for rank/probability fields below)
    rank_table = {
        r["ticker"]: r
        for r in rank_focus_stocks(focus, stock_data, patterns, news_data,
                                   fundamentals, book, market_health)
    }

    for ticker in focus:
        entry_data = stock_data.get(ticker)
        if not entry_data or "latest" not in entry_data:
            continue

        d     = entry_data["latest"]
        close = d.get("close", 0)
        if close <= 0:
            continue

        news    = news_data.get(ticker, {}).get("latest", {})
        fund    = fundamentals.get(ticker, {})
        opinion = analyse_stock(ticker, entry_data, patterns, news, "morning")

        if opinion["signal"] not in ("BUY", "SELL"):
            continue

        # ── Layer 1: Technical score (0–40) ──────────────────────────────────
        # buy_score/sell_score have no fixed ceiling — they accumulate per pattern fired.
        # A "very strong" signal is ~10+; we treat 15 as the practical maximum for scaling.
        # clamp at 15 then scale to 40, so a perfect signal gets full 40 pts.
        raw_score  = opinion["buy_score"] if opinion["signal"] == "BUY" else opinion["sell_score"]
        tech_score = round(min(40, raw_score / 15.0 * 40), 1)

        # ── Layer 2: Fundamental score (0–30) ─────────────────────────────────
        from agent.fundamentals_fetcher import score_fundamentals
        fund_raw    = score_fundamentals(fund)          # 0–100
        fund_score  = round(fund_raw * 0.30, 1)        # scaled to 30

        # ── Layer 3: News/Sentiment score (0–20) ──────────────────────────────
        news_raw   = news.get("score", 0)              # -1 to +1
        if opinion["signal"] == "BUY":
            news_points = max(0, news_raw) * 20        # positive sentiment helps BUY
        else:
            news_points = max(0, -news_raw) * 20       # negative sentiment helps SELL
        news_score = round(min(20, news_points + 10), 1)  # baseline 10 (neutral)

        # ── Layer 4: Pattern reliability score (0–10) ─────────────────────────
        # Proven patterns (validated through closed trades) are worth the most, but
        # a stock with no trade history yet would otherwise score 0 here forever
        # (chicken-and-egg). So we also credit strong patterns firing TODAY at a
        # lower weight until they earn a track record.
        reliable_pats = get_reliable_patterns_list(ticker, patterns, min_rel=0.55)
        todays_pats   = opinion.get("patterns", [])
        HIGH_VALUE_PATS = {
            "supertrend_bullish", "supertrend_bearish", "ichimoku_bullish_kumo",
            "ichimoku_bearish_kumo", "macd_bullish_divergence", "macd_bearish_divergence",
            "52w_breakout", "engulfing_bullish", "engulfing_bearish", "morning_star",
            "evening_star", "rsi_bullish_divergence", "rsi_bearish_divergence",
            "pivot_r2_breakout", "volume_climax_bottom", "volume_climax_top",
        }
        unproven_strong = [p for p in todays_pats if p in HIGH_VALUE_PATS and p not in reliable_pats]
        pat_score = min(10, len(reliable_pats) * 2.5 + len(unproven_strong) * 1.0)

        # ── Multi-signal confluence ───────────────────────────────────────────
        # The single biggest accuracy lever: several INDEPENDENT signal families
        # agreeing. Used BOTH as a gate (>=3) AND as a scored bonus (0–10), since
        # broad agreement is the strongest evidence a setup is real.
        confluence = _confluence_score(opinion, d, news, fund)
        if confluence["count"] < 3:
            # Fewer than 3 independent confirmations — skip; quality over quantity
            continue
        confluence_bonus = min(10, max(0, confluence["count"] - 2) * 2.5)  # 3→2.5 ... 6+→10

        # ── Paper trade bonus (+0 to +5, not a gate) ──────────────────────────
        stock_trades = [t for t in book.get("closed_trades", []) if t["ticker"] == ticker]
        stock_stats  = _mini_stats(stock_trades)
        paper_bonus  = 0.0
        if stock_stats["total"] >= 3 and stock_stats["win_rate"] > 0.55:
            paper_bonus = 5.0  # small bonus for validated paper performance

        # Total now includes confluence — capped at 100
        total_confidence = round(min(100,
            tech_score + fund_score + news_score + pat_score + confluence_bonus + paper_bonus), 1)

        if total_confidence < 65:
            continue

        # ── Backtest this setup on the stock's own 2-year history ──────────────
        from agent.history_engine import backtest_setup
        bt = backtest_setup(ticker, opinion["signal"], d, history_ctx)

        # ── Support/Resistance ────────────────────────────────────────────────
        ph     = entry_data.get("price_history_60d", [])
        vh     = entry_data.get("volume_history_20d", [])
        atr    = d.get("atr", close * 0.025)
        levels = compute_levels(ph, vh, close)
        n_sup  = nearest_strong_support(levels)
        n_res  = nearest_strong_resistance(levels)

        # ── Stop loss and targets ─────────────────────────────────────────────
        # Target 1 is the primary profit goal and must satisfy R:R >= 2.0. A nearby
        # support/resistance is shown as an INTERIM level but never caps Target 1
        # below the 2:1 floor — otherwise good setups get discarded just because a
        # minor level sits close to price. We therefore size Target 1 from ATR to
        # guarantee the R:R, and expose the S/R level separately as "interim".
        if opinion["signal"] == "BUY":
            atr_stop   = close - atr * ATR_STOP_MULTIPLIER
            sr_stop    = n_sup * 0.992 if n_sup > 0 else atr_stop
            stop_loss  = round(max(atr_stop, sr_stop), 2)
            risk_amt   = max(close - stop_loss, 1e-6)
            # Target 1 = max(2:1 R:R floor, ATR-based) so it always clears the gate
            target1    = round(max(close + risk_amt * 2.0, close + atr * 2.5), 2)
            target2    = round(close + atr * ATR_TARGET_MULTIPLIER, 2)
            interim_lvl = round(n_res, 2) if (n_res > close) else 0
            entry_low  = round(close * 0.998, 2)
            entry_high = round(close * 1.005, 2)
        else:
            atr_stop   = close + atr * ATR_STOP_MULTIPLIER
            sr_stop    = n_res * 1.008 if n_res > 0 else atr_stop
            stop_loss  = round(min(atr_stop, sr_stop), 2)
            risk_amt   = max(stop_loss - close, 1e-6)
            target1    = round(min(close - risk_amt * 2.0, close - atr * 2.5), 2)
            target2    = round(close - atr * ATR_TARGET_MULTIPLIER, 2)
            interim_lvl = round(n_sup, 2) if (0 < n_sup < close) else 0
            entry_low  = round(close * 0.995, 2)
            entry_high = round(close * 1.002, 2)

        # Target 2 should always be beyond Target 1 ( atr mult can be < the 2:1 floor)
        if opinion["signal"] == "BUY":
            target2 = round(max(target2, target1 + atr), 2)
        else:
            target2 = round(min(target2, target1 - atr), 2)

        rew1_amt = abs(target1 - close)
        rew2_amt = abs(target2 - close)
        risk_pct = round(risk_amt / close * 100, 2)
        rr1      = round(rew1_amt / risk_amt, 2) if risk_amt > 0 else 0
        rr2      = round(rew2_amt / risk_amt, 2) if risk_amt > 0 else 0

        # Guarantee the floor (should always pass now, but keep as a guard)
        if rr1 < 2.0:
            continue

        # ── Position sizing ───────────────────────────────────────────────────
        risk_per_trade_inr = INITIAL_CAPITAL * 0.02
        qty_by_risk = max(1, int(risk_per_trade_inr / risk_amt)) if risk_amt > 0 else 1
        qty_by_cap  = max(1, int(INITIAL_CAPITAL * 0.12 / close))
        recommended_qty = min(qty_by_risk, qty_by_cap)
        invested        = round(close * recommended_qty, 2)

        # ── Trade type, hold period, timing guidance ──────────────────────────
        # Auto-classify into intraday / short-term / long-term from the signal
        # character + fundamental strength (not just a single learned preference).
        learned_style   = patterns.get(ticker, {}).get("preferred_style", "swing")
        tt              = _classify_trade_type(opinion, d, fund, learned_style)
        trade_type       = tt["trade_type"]
        trade_type_key   = tt["trade_type_key"]
        style            = trade_type_key   # keep legacy field meaningful
        hold             = tt["hold_period"]
        entry_window     = tt["entry_window"]
        exit_window      = tt["exit_window"]
        target_timeframe = tt["target_timeframe"]
        action_urgency   = tt["action_urgency"]

        # ── Explicit direction wording ────────────────────────────────────────
        direction       = "BUY (go long)" if opinion["signal"] == "BUY" else "SELL / SHORT (go short)"
        direction_short = "BUY" if opinion["signal"] == "BUY" else "SELL"
        nse             = ticker.replace(".NS", "")

        # ── Plain-English INTENTION the user can act on directly ───────────────
        # One clear verdict per stock — what the tool wants to do and why.
        if trade_type_key == "intraday":
            intention = (
                f"{direction_short} {nse} for an INTRADAY trade today — "
                f"enter in the first hour and square off before close. Same-day move only."
            )
        elif trade_type_key == "long_term":
            intention = (
                f"{direction_short} {nse} as a LONG-TERM hold — "
                f"accumulate and hold for weeks to months; backed by strong fundamentals "
                f"and a long-term uptrend."
            )
        else:
            intention = (
                f"{direction_short} {nse} for a SHORT-TERM swing — "
                f"buy and hold {('5–15 days' if opinion['signal']=='BUY' else 'for a few days')} "
                f"to ride the move, then exit at target or trail the stop."
            )
        headline = f"{direction_short} {nse} — {trade_type} ({'long' if opinion['signal']=='BUY' else 'short'} setup)"

        # ── Build full reasoning ──────────────────────────────────────────────
        reasons = list(opinion.get("buy_reasons" if opinion["signal"] == "BUY"
                                    else "sell_reasons", []))

        # Fundamental insights
        if fund:
            pe         = fund.get("pe_ratio")
            roe        = fund.get("roe")
            roce       = fund.get("roce")
            rev_g      = fund.get("revenue_growth_pct")
            np_var     = fund.get("np_qtr_var_pct")
            sales_var  = fund.get("sales_qtr_var_pct")
            np_qtr     = fund.get("np_qtr_cr")
            sales_qtr  = fund.get("sales_qtr_cr")
            mktcap     = fund.get("market_cap_cr")
            analyst_up = fund.get("analyst_upside_pct")
            promo      = fund.get("promoter_holding_pct")
            div_yield  = fund.get("dividend_yield_pct")
            de         = fund.get("debt_equity")
            if pe:         reasons.append(f"Valuation: P/E {pe:.1f}x")
            if roe:        reasons.append(f"ROE: {roe:.1f}%")
            if roce:       reasons.append(f"ROCE: {roce:.1f}%")
            if rev_g and rev_g > 0:   reasons.append(f"Revenue growing {rev_g:.1f}% YoY")
            if np_var and np_var > 0: reasons.append(f"Net profit up {np_var:.1f}% QoQ")
            if sales_var and sales_var > 0: reasons.append(f"Sales up {sales_var:.1f}% QoQ")
            if np_qtr:    reasons.append(f"Latest quarter NP: ₹{np_qtr:,.0f} Cr")
            if sales_qtr: reasons.append(f"Latest quarter Sales: ₹{sales_qtr:,.0f} Cr")
            if mktcap:    reasons.append(f"Market Cap: ₹{mktcap:,.0f} Cr")
            if analyst_up and analyst_up > 5:
                reasons.append(f"Analyst target: +{analyst_up:.1f}% upside")
            if promo and promo > 50:
                reasons.append(f"Promoter holding: {promo:.1f}%")
            if div_yield and div_yield > 1:
                reasons.append(f"Dividend yield: {div_yield:.1f}%")
            if de is not None and de < 0.5:
                reasons.append(f"Low debt: D/E {de:.2f}")
            et = fund.get("earnings_trend", "")
            if et in ("beat", "consistent_beat", "improving"):
                reasons.append(f"Earnings trend: {et.replace('_', ' ')}")

        if reliable_pats:
            reasons.append(f"Reliable patterns on this stock: {', '.join(reliable_pats[:4])}")
        if n_sup > 0 and opinion["signal"] == "BUY":
            reasons.append(f"Support at ₹{n_sup:.2f} cushions downside")
        if n_res > 0 and opinion["signal"] == "BUY":
            reasons.append(f"Resistance at ₹{n_res:.2f} is natural target")
        hl = news.get("headlines", [])
        if hl:
            reasons.append(f"Latest news: '{hl[0][:70]}'")
        if market_mood == "bullish" and opinion["signal"] == "BUY":
            reasons.append("Broad market bullish — tailwind in play")
        if paper_bonus > 0:
            reasons.append(
                f"Paper trade validation: {stock_stats['total']} trades, "
                f"{stock_stats['win_rate']*100:.0f}% win rate, "
                f"₹{stock_stats['expectancy']:+.0f}/trade expectancy"
            )

        # ── Confluence + backtest reasoning ───────────────────────────────────
        reasons.append(
            f"Confluence: {confluence['count']} independent signal families agree "
            f"({', '.join(confluence['families'])})"
        )
        if bt.get("tested"):
            reasons.append(
                f"History backtest: this kind of {direction_short} setup worked "
                f"{bt['hit_rate_5d']*100:.0f}% of the time over ~1 week on this stock "
                f"(sample: {bt['sample']})"
            )

        rank_info = rank_table.get(ticker, {})

        # ── Blended success probability ───────────────────────────────────────
        # Combine the ranking-engine probability with the stock's own historical
        # backtest hit-rate. Backtest only nudges it (weight 0.3) so one is a
        # sanity check on the other, not a blind override.
        base_success = rank_info.get("success_probability", 0.5)
        if bt.get("tested") and bt.get("hit_rate_5d") is not None:
            blended_success = round(0.7 * base_success + 0.3 * bt["hit_rate_5d"], 3)
        else:
            blended_success = base_success

        # Validity metadata — tells the user when this rec expires and if it's stale
        generated_at    = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        valid_until_ist = REC_SESSION_VALID_UNTIL.get(session, "next session")
        entry_mid       = round((entry_low + entry_high) / 2, 2)
        price_moved_pct = round(abs(close - entry_mid) / entry_mid * 100, 2) if entry_mid else 0
        is_stale        = price_moved_pct > REC_STALE_PRICE_MOVE_PCT
        stale_reason    = (
            f"CMP ₹{close:,.2f} has moved {price_moved_pct:.1f}% from entry zone midpoint ₹{entry_mid:,.2f}"
            if is_stale else ""
        )

        rec = {
            "ticker":       ticker,
            "nse_code":     ticker.replace(".NS", ""),
            "company_name": STOCK_NAMES.get(ticker, ticker),
            "date":         date.today().isoformat(),
            "generated_at": generated_at,
            "valid_until":  valid_until_ist,
            "session":      session,
            "is_stale":     is_stale,
            "stale_reason": stale_reason,
            "signal":           opinion["signal"],
            "direction":        direction,          # "BUY (go long)" / "SELL / SHORT (go short)"
            "direction_short":  direction_short,    # "BUY" / "SELL"
            "headline":         headline,           # plain-English one-liner
            "trade_type":       trade_type,         # "Intraday" / "Short-term / Swing" / "Long-term / Investment"
            "trade_type_key":   trade_type_key,     # intraday / short_term / long_term
            "style":            style,
            "hold_period":      hold,
            "entry_window":     entry_window,
            "exit_window":      exit_window,
            "target_timeframe": target_timeframe,
            "action_urgency":   action_urgency,

            "cmp":          round(close, 2),
            "entry_low":    entry_low,
            "entry_high":   entry_high,
            "stop_loss":    stop_loss,
            "target1":      target1,
            "target2":      target2,

            "risk_pct":          risk_pct,
            "rr_target1":        rr1,
            "rr_target2":        rr2,
            "recommended_qty":   recommended_qty,
            "capital_needed":    invested,
            "max_loss_if_sl":    round(risk_amt * recommended_qty, 2),

            # Score breakdown
            "confidence":        total_confidence,
            "tech_score":        tech_score,
            "fund_score":        fund_score,
            "news_score":        news_score,
            "pattern_score":     pat_score,
            "confluence_bonus":  confluence_bonus,
            "paper_bonus":       paper_bonus,

            # Probability scores — blended (ranking engine + own-history backtest)
            "success_probability": blended_success,
            "base_success_prob":   base_success,
            "profit_probability":  rank_info.get("profit_probability", 0.0),
            "composite_score":     rank_info.get("composite_score", 0.0),
            "focus_rank":          rank_info.get("rank", 99),
            "rank_delta":          rank_info.get("rank_delta", 0),

            # Intention — one clear plain-English verdict for this stock
            "intention":           intention,

            # Confluence — how many independent families agree
            "confluence_count":    confluence["count"],
            "confluence_families": confluence["families"],

            # Backtest on the stock's own 2-year history
            "backtest_tested":     bt.get("tested", False),
            "backtest_hit_5d":     bt.get("hit_rate_5d"),
            "backtest_hit_10d":    bt.get("hit_rate_10d"),
            "backtest_sample":     bt.get("sample", 0),

            # Historical regime snapshot (for display + transparency)
            "hist_long_trend":     d.get("hist_long_trend"),
            "hist_52w_position":   d.get("hist_pct_of_52w_range"),
            "hist_personality":    d.get("hist_personality"),
            "hist_vol_state":      d.get("hist_vol_state"),

            # Data-confidence: how trustworthy the probability is (based on real trades)
            "data_confidence":      _data_confidence(len(stock_trades))["tag"],
            "data_confidence_key":  _data_confidence(len(stock_trades))["key"],
            "data_confidence_note": _data_confidence(len(stock_trades))["note"],
            # Expected edge in R-multiples per trade, plain wording
            "expected_edge_r":      rank_info.get("profit_probability", 0.0),

            "buy_score":         opinion["buy_score"],
            "sell_score":        opinion["sell_score"],
            "reasons":           reasons,
            "patterns_seen":     opinion.get("patterns", []),
            "reliable_patterns": reliable_pats,

            "nearest_support":    n_sup,
            "nearest_resistance": n_res,
            "interim_level":      interim_lvl,   # nearby S/R to watch en route to T1

            # Fundamentals snapshot (all metrics for dashboard display)
            "pe_ratio":          fund.get("pe_ratio"),
            "pb_ratio":          fund.get("pb_ratio"),
            "market_cap_cr":     fund.get("market_cap_cr"),
            "roe":               fund.get("roe"),
            "roce":              fund.get("roce"),
            "debt_equity":       fund.get("debt_equity"),
            "revenue_growth":    fund.get("revenue_growth_pct"),
            "np_qtr_cr":         fund.get("np_qtr_cr"),
            "np_qtr_var_pct":    fund.get("np_qtr_var_pct"),
            "sales_qtr_cr":      fund.get("sales_qtr_cr"),
            "sales_qtr_var_pct": fund.get("sales_qtr_var_pct"),
            "dividend_yield":    fund.get("dividend_yield_pct"),
            "promoter_holding":  fund.get("promoter_holding_pct"),
            "analyst_upside":    fund.get("analyst_upside_pct"),
            "analyst_target":    fund.get("analyst_target"),
            "earnings_trend":    fund.get("earnings_trend"),

            "paper_trades_count": len(stock_trades),
            "paper_win_rate":     stock_stats.get("win_rate", 0),
            "paper_pnl":          stock_stats.get("total_pnl", 0),
            "paper_expectancy":   stock_stats.get("expectancy", 0),

            "market_mood":    market_mood,
            "market_warning": market_health.get("warnings", []),
        }
        recs.append(rec)

    recs.sort(key=lambda x: x["confidence"], reverse=True)
    _save(recs)
    print(f"[recs] {len(recs)} recommendation(s) generated")
    return recs


def load_recommendations() -> List[dict]:
    from agent.io_safe import load_json_list
    return load_json_list(RECOMMENDATIONS_FILE)


def _save(recs):
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(RECOMMENDATIONS_FILE, "w") as f:
        json.dump(recs, f, indent=2)


def _mini_stats(trades):
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
    signal = rec["signal"]
    lines = [
        f"{'BUY' if signal == 'BUY' else 'SELL'} {rec['company_name']} ({rec['nse_code']})",
        f"   CMP:           Rs.{rec['cmp']:,.2f}",
        f"   Entry Zone:    Rs.{rec['entry_low']:,.2f} - Rs.{rec['entry_high']:,.2f}",
        f"   Stop Loss:     Rs.{rec['stop_loss']:,.2f}  ({rec['risk_pct']:.1f}% risk)",
        f"   Target 1:      Rs.{rec['target1']:,.2f}  (R:R 1:{rec['rr_target1']:.1f})",
        f"   Target 2:      Rs.{rec['target2']:,.2f}  (R:R 1:{rec['rr_target2']:.1f})",
        f"   Hold:          {rec['hold_period']}",
        f"   Qty (2% risk): {rec['recommended_qty']} shares -> Rs.{rec['capital_needed']:,.0f} invested",
        f"   Max loss:      Rs.{rec['max_loss_if_sl']:,.0f} if SL hit",
        f"   Confidence:    {rec['confidence']:.0f}/100  "
        f"(Tech:{rec['tech_score']:.0f} Fund:{rec['fund_score']:.0f} "
        f"News:{rec['news_score']:.0f} Pat:{rec['pattern_score']:.0f})",
        "   Reasons:",
        *[f"     - {r}" for r in rec["reasons"]],
    ]
    if rec.get("market_warning"):
        lines.append("   Market warnings: " + " | ".join(rec["market_warning"]))
    return "\n".join(lines)
