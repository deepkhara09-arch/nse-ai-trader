"""
Fundamentals fetcher — pulls key financial metrics from Yahoo Finance
quoteSummary and financials endpoints.

Metrics collected per stock:
  Valuation:    P/E (TTM), Forward P/E, P/B, EV/EBITDA
  Size:         Market Cap (Rs. Cr.), Enterprise Value
  Profitability:Net Profit (Qtr, Rs. Cr.), NP Qtr-on-Qtr var %,
                Net margin, Operating margin, ROE, ROCE
  Revenue:      Sales Qtr (Rs. Cr.), Sales Qtr var %, Revenue growth YoY
  Health:       Debt/Equity, Current ratio, Interest coverage
  Returns:      Dividend yield %, Payout ratio
  Quality:      Promoter holding %, Institutional holding %
  Earnings:     Beat/miss trend last 4 quarters, Analyst target, upside %
  Position:     52-week high/low, position in 52w range
"""

import json
import os
import time
from datetime import datetime
from typing import Dict, List

from agent.config import BRAIN_DIR
from agent.trading_calendar import ist_today

FUNDAMENTALS_FILE = "brain/fundamentals.json"

# ₹ Cr conversion: Yahoo gives values in absolute INR — divide by 1e7 for Cr
_CR = 1e7


def fetch_fundamentals(tickers: List[str]) -> Dict:
    """Fetch via the yfinance library (it handles Yahoo's cookie/crumb dance
    internally). The old direct quoteSummary/v11 HTTP endpoint is dead — it
    silently returned 0 stocks, which is why total failure is now also
    recorded on System Health instead of vanishing into the logs."""
    result = {}
    for ticker in tickers:
        try:
            entry = _fetch_one(ticker)
            if entry:
                result[ticker] = entry
            time.sleep(0.6)
        except Exception as e:
            print(f"[fund] {ticker}: {e}")
    print(f"[fund] fetched fundamentals for {len(result)}/{len(tickers)} stocks")
    if tickers and not result:
        try:
            from agent.run_health import record_issue
            record_issue("fundamentals", f"{len(tickers)} tickers",
                         "fundamentals fetch returned 0 stocks — source may be down")
        except Exception:
            pass
    return result


def load_fundamentals() -> Dict:
    from agent.io_safe import load_json_dict
    return load_json_dict(FUNDAMENTALS_FILE)


def save_fundamentals(data: Dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    existing = load_fundamentals()
    existing.update(data)
    with open(FUNDAMENTALS_FILE, "w") as f:
        json.dump(existing, f, indent=2)


def score_fundamentals(fund: dict) -> float:
    """Score fundamentals 0–100. Higher = fundamentally stronger."""
    if not fund:
        return 50.0

    score = 0.0
    max_s = 0.0

    def add(condition, weight):
        nonlocal score, max_s
        max_s += weight
        if condition:
            score += weight

    pe          = fund.get("pe_ratio")
    pb          = fund.get("pb_ratio")
    roe         = fund.get("roe")
    roce        = fund.get("roce")
    de          = fund.get("debt_equity")
    rev_growth  = fund.get("revenue_growth_pct")
    np_var      = fund.get("np_qtr_var_pct")       # NP quarter var %
    sales_var   = fund.get("sales_qtr_var_pct")    # Sales quarter var %
    margin      = fund.get("net_margin_pct")
    promoter    = fund.get("promoter_holding_pct")
    div_yield   = fund.get("dividend_yield_pct", 0)
    wk52_pos    = fund.get("week52_position_pct")
    et          = fund.get("earnings_trend", "neutral")
    analyst_up  = fund.get("analyst_upside_pct", 0)

    # Valuation
    if pe is not None:
        add(5 <= pe <= 30, 10)
        add(pe < 20, 5)
    if pb is not None:
        add(pb < 4, 8)
        add(pb < 2, 4)

    # Profitability
    if roe is not None:
        add(roe > 15, 12)
        add(roe > 25, 5)
    if roce is not None:
        add(roce > 15, 10)
        add(roce > 25, 4)
    if margin is not None:
        add(margin > 10, 8)
        add(margin > 20, 4)

    # Quarterly growth momentum
    if np_var is not None:
        add(np_var > 10, 10)
        add(np_var > 25, 5)
    if sales_var is not None:
        add(sales_var > 8, 8)
        add(sales_var > 20, 4)
    if rev_growth is not None:
        add(rev_growth > 10, 6)

    # Balance sheet
    if de is not None:
        add(de < 1.0, 8)
        add(de < 0.5, 4)

    # Promoter confidence
    if promoter is not None:
        add(promoter > 50, 8)
        add(promoter > 65, 4)

    # 52-week position (not near top)
    if wk52_pos is not None:
        add(20 < wk52_pos < 80, 5)

    # Earnings beat + analyst upside
    add(et in ("beat", "improving"), 8)
    if analyst_up:
        add(analyst_up > 10, 5)

    if max_s == 0:
        return 50.0
    return round(score / max_s * 100, 1)


# ── Internal ──────────────────────────────────────────────────────────────────

def _fetch_one(ticker: str) -> dict:
    """One stock's fundamentals via yfinance's Ticker.info (plus quarterly
    statements and the earnings calendar). Same output schema as before."""
    try:
        import yfinance as yf
        tk   = yf.Ticker(ticker)
        info = tk.info or {}
        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            return {}

        v = info.get

        # ── 52-week range ──────────────────────────────────────────────────────
        low52  = v("fiftyTwoWeekLow")
        high52 = v("fiftyTwoWeekHigh")
        price  = v("currentPrice") or v("regularMarketPrice")
        wk52_pos = None
        if low52 and high52 and high52 > low52 and price:
            wk52_pos = round((price - low52) / (high52 - low52) * 100, 1)

        # ── Market cap in Rs. Cr. ─────────────────────────────────────────────
        mktcap_raw = v("marketCap")
        mktcap_cr  = round(mktcap_raw / _CR, 2) if mktcap_raw else None

        # ── Quarterly income statement (last 2 quarters) ──────────────────────
        np_qtr = np_qtr_prev = sales_qtr = sales_qtr_prev = None
        np_qtr_var = sales_qtr_var = None
        try:
            q = tk.quarterly_income_stmt      # DataFrame, newest column first
            if q is not None and not q.empty:
                def row(label, col):
                    try:
                        x = q.loc[label].iloc[col]
                        return round(float(x) / _CR, 2) if x == x else None
                    except Exception:
                        return None
                np_qtr,    np_qtr_prev    = row("Net Income", 0),    row("Net Income", 1)
                sales_qtr, sales_qtr_prev = row("Total Revenue", 0), row("Total Revenue", 1)
                if np_qtr and np_qtr_prev:
                    np_qtr_var = round((np_qtr - np_qtr_prev) / abs(np_qtr_prev) * 100, 1)
                if sales_qtr and sales_qtr_prev:
                    sales_qtr_var = round((sales_qtr - sales_qtr_prev) / abs(sales_qtr_prev) * 100, 1)
        except Exception:
            pass

        # ── ROCE approximation ────────────────────────────────────────────────
        # ROCE ≈ EBIT / Capital Employed, from operating margin, revenue,
        # total debt and book equity — same approximation as before.
        op_margin  = v("operatingMargins")
        total_rev  = v("totalRevenue")
        total_debt = v("totalDebt")
        book_val   = v("bookValue")
        shares_out = v("sharesOutstanding")
        roce = None
        if op_margin and total_rev and total_debt is not None and book_val and shares_out:
            capital_employed = book_val * shares_out + total_debt
            if capital_employed > 0:
                roce = round(op_margin * total_rev / capital_employed * 100, 2)

        # ── Earnings date & days to results ──────────────────────────────────
        earnings_date = None
        days_to_earnings = None
        try:
            cal = tk.calendar or {}
            earn_dates = cal.get("Earnings Date") or []
            if earn_dates:
                earn_dt = earn_dates[0]
                earn_dt = earn_dt.date() if hasattr(earn_dt, "date") and not hasattr(earn_dt, "isoformat") else earn_dt
                if hasattr(earn_dt, "isoformat"):
                    earnings_date = earn_dt.isoformat()[:10]
                    from datetime import date as _date
                    d0 = _date.fromisoformat(earnings_date)
                    days_to_earnings = (d0 - ist_today()).days
        except Exception:
            pass

        # ── Analyst target & upside ───────────────────────────────────────────
        target_price = v("targetMeanPrice")
        analyst_upside = None
        if price and target_price and price > 0:
            analyst_upside = round((target_price - price) / price * 100, 1)

        # Yahoo reports debtToEquity as a PERCENT (150.0 == 1.5x). Every consumer
        # in this tool treats debt_equity as a plain ratio (< 1.0 checks), so
        # normalise here. (Latent bug in the old fetcher — it never bit because
        # that endpoint always returned nothing.)
        de_raw = v("debtToEquity")
        debt_equity = round(de_raw / 100.0, 2) if de_raw is not None else None

        entry = {
            "ticker":               ticker,
            "fetched_at":           datetime.utcnow().isoformat(),

            # Valuation
            "pe_ratio":             _r(v("trailingPE") or v("forwardPE")),
            "forward_pe":           _r(v("forwardPE")),
            "pb_ratio":             _r(v("priceToBook")),
            "ev_ebitda":            _r(v("enterpriseToEbitda")),

            # Size
            "market_cap_cr":        mktcap_cr,

            # Profitability
            "roe":                  _pct(v("returnOnEquity")),
            "roa":                  _pct(v("returnOnAssets")),
            "roce":                 roce,
            "net_margin_pct":       _pct(v("profitMargins")),
            "operating_margin_pct": _pct(v("operatingMargins")),

            # Quarterly financials
            "np_qtr_cr":            np_qtr,           # Net Profit latest qtr Rs.Cr.
            "np_qtr_prev_cr":       np_qtr_prev,      # Net Profit prior qtr Rs.Cr.
            "np_qtr_var_pct":       np_qtr_var,       # Qtr Profit Var %
            "sales_qtr_cr":         sales_qtr,        # Sales latest qtr Rs.Cr.
            "sales_qtr_prev_cr":    sales_qtr_prev,   # Sales prior qtr Rs.Cr.
            "sales_qtr_var_pct":    sales_qtr_var,    # Qtr Sales Var %

            # Annual growth
            "revenue_growth_pct":   _pct(v("revenueGrowth")),
            "earnings_growth_pct":  _pct(v("earningsGrowth")),

            # Balance sheet (debt_equity normalised to a RATIO, see above)
            "debt_equity":          debt_equity,
            "current_ratio":        _r(v("currentRatio")),

            # Returns to shareholders
            "dividend_yield_pct":   _pct(v("dividendYield")),
            "payout_ratio":         _pct(v("payoutRatio")),

            # Ownership
            "promoter_holding_pct":  _pct(v("heldPercentInsiders")),
            "institutional_pct":     _pct(v("heldPercentInstitutions")),

            # 52-week context
            "week52_high":          _r(high52),
            "week52_low":           _r(low52),
            "week52_position_pct":  wk52_pos,

            # Analyst view
            "analyst_target":       _r(target_price),
            "analyst_upside_pct":   analyst_upside,
            "analyst_recommendation": v("recommendationKey", ""),
            "analyst_count":        v("numberOfAnalystOpinions"),

            # Earnings calendar
            "earnings_date":        earnings_date,
            "days_to_earnings":     days_to_earnings,
            "beta":                 _r(v("beta")),
        }
        # Strip None values to keep JSON clean
        return {k: val for k, val in entry.items() if val is not None}

    except Exception as e:
        print(f"[fund] {ticker} parse error: {e}")
        return {}


def _r(val, decimals=2):
    if val is None:
        return None
    try:
        return round(float(val), decimals)
    except Exception:
        return None


def _pct(val) -> float:
    if val is None:
        return None
    try:
        return round(float(val) * 100, 2)
    except Exception:
        return None


def _earnings_trend(trends: list) -> str:
    if not trends:
        return "unknown"
    beats = misses = 0
    for t in trends[:4]:
        est = (t.get("earningsEstimate", {}) or {}).get("avg", {})
        act = t.get("earningsActual", {}) or {}
        est_v = est.get("raw") if isinstance(est, dict) else None
        act_v = act.get("raw") if isinstance(act, dict) else None
        if est_v is not None and act_v is not None:
            if act_v >= est_v * 1.05:
                beats += 1
            elif act_v < est_v * 0.95:
                misses += 1
    if beats >= 3:   return "consistent_beat"
    if beats >= 2:   return "beat"
    if misses >= 3:  return "consistent_miss"
    if misses >= 2:  return "miss"
    if beats > misses: return "improving"
    return "neutral"
