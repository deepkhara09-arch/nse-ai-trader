"""
Recommendations Engine — the agent's final output to the user.

4-layer scoring (totals 100 points):
  Technical   40 pts — EMA alignment, RSI zone, MACD cross, BB, volume, S/R
  Fundamental 30 pts — P/E, ROE, debt, revenue growth, promoter holding
  News/Sent   20 pts — recent headline sentiment + corporate actions
  Patterns    10 pts — historical reliability of patterns seen today

Recommendation fires when:
  • Total confidence >= 65 points
  • R:R >= 2.0 on Target 1
  • Market mood is NOT bearish (VIX < 25)
  • Signal is BUY or SELL (not WATCH)

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
from agent.paper_trader import compute_stats
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
        tech_score = min(40, opinion["buy_score"] * 4 if opinion["signal"] == "BUY"
                         else opinion["sell_score"] * 4)

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
        reliable_pats = get_reliable_patterns_list(ticker, patterns, min_rel=0.55)
        pat_score     = min(10, len(reliable_pats) * 2.5)

        # ── Paper trade bonus (+0 to +5, not a gate) ──────────────────────────
        stock_trades = [t for t in book.get("closed_trades", []) if t["ticker"] == ticker]
        stock_stats  = _mini_stats(stock_trades)
        paper_bonus  = 0.0
        if stock_stats["total"] >= 3 and stock_stats["win_rate"] > 0.55:
            paper_bonus = 5.0  # small bonus for validated paper performance

        total_confidence = round(tech_score + fund_score + news_score + pat_score + paper_bonus, 1)

        if total_confidence < 65:
            continue

        # ── Support/Resistance ────────────────────────────────────────────────
        ph     = entry_data.get("price_history_60d", [])
        vh     = entry_data.get("volume_history_20d", [])
        atr    = d.get("atr", close * 0.025)
        levels = compute_levels(ph, vh, close)
        n_sup  = nearest_strong_support(levels)
        n_res  = nearest_strong_resistance(levels)

        # ── Stop loss and targets ─────────────────────────────────────────────
        if opinion["signal"] == "BUY":
            atr_stop   = close - atr * ATR_STOP_MULTIPLIER
            sr_stop    = n_sup * 0.992 if n_sup > 0 else atr_stop
            stop_loss  = round(max(atr_stop, sr_stop), 2)
            target1    = round(n_res if n_res > close else close + atr * 2.5, 2)
            target2    = round(close + atr * ATR_TARGET_MULTIPLIER, 2)
            entry_low  = round(close * 0.998, 2)
            entry_high = round(close * 1.005, 2)
        else:
            atr_stop   = close + atr * ATR_STOP_MULTIPLIER
            sr_stop    = n_res * 1.008 if n_res > 0 else atr_stop
            stop_loss  = round(min(atr_stop, sr_stop), 2)
            target1    = round(n_sup if n_sup < close else close - atr * 2.5, 2)
            target2    = round(close - atr * ATR_TARGET_MULTIPLIER, 2)
            entry_low  = round(close * 0.995, 2)
            entry_high = round(close * 1.002, 2)

        risk_amt = abs(close - stop_loss)
        rew1_amt = abs(target1 - close)
        rew2_amt = abs(target2 - close)
        risk_pct = round(risk_amt / close * 100, 2)
        rr1      = round(rew1_amt / risk_amt, 2) if risk_amt > 0 else 0
        rr2      = round(rew2_amt / risk_amt, 2) if risk_amt > 0 else 0

        if rr1 < 2.0:
            continue

        # ── Position sizing ───────────────────────────────────────────────────
        risk_per_trade_inr = INITIAL_CAPITAL * 0.02
        qty_by_risk = max(1, int(risk_per_trade_inr / risk_amt)) if risk_amt > 0 else 1
        qty_by_cap  = max(1, int(INITIAL_CAPITAL * 0.12 / close))
        recommended_qty = min(qty_by_risk, qty_by_cap)
        invested        = round(close * recommended_qty, 2)

        # ── Hold period ───────────────────────────────────────────────────────
        style = patterns.get(ticker, {}).get("preferred_style", "swing")
        hold  = {"swing": "5–10 trading days",
                 "intraday": "Same day (exit before 3:15 PM IST)"}.get(style, "3–7 trading days")

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

        rank_info = rank_table.get(ticker, {})

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
            "signal":       opinion["signal"],
            "style":        style,
            "hold_period":  hold,

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
            "paper_bonus":       paper_bonus,

            # Probability scores from ranking engine
            "success_probability": rank_info.get("success_probability", 0.5),
            "profit_probability":  rank_info.get("profit_probability", 0.0),
            "composite_score":     rank_info.get("composite_score", 0.0),
            "focus_rank":          rank_info.get("rank", 99),
            "rank_delta":          rank_info.get("rank_delta", 0),

            "buy_score":         opinion["buy_score"],
            "sell_score":        opinion["sell_score"],
            "reasons":           reasons,
            "patterns_seen":     opinion.get("patterns", []),
            "reliable_patterns": reliable_pats,

            "nearest_support":    n_sup,
            "nearest_resistance": n_res,

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
    if os.path.exists(RECOMMENDATIONS_FILE):
        with open(RECOMMENDATIONS_FILE) as f:
            return json.load(f)
    return []


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
