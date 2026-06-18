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

FUNDAMENTALS_FILE = "brain/fundamentals.json"

# ₹ Cr conversion: Yahoo gives values in absolute INR — divide by 1e7 for Cr
_CR = 1e7


def fetch_fundamentals(tickers: List[str]) -> Dict:
    from agent.data_fetcher import _YF, _yf_crumb
    crumb  = _yf_crumb()
    result = {}
    for ticker in tickers:
        try:
            entry = _fetch_one(ticker, _YF, crumb)
            if entry:
                result[ticker] = entry
            time.sleep(0.8)
        except Exception as e:
            print(f"[fund] {ticker}: {e}")
    print(f"[fund] fetched fundamentals for {len(result)}/{len(tickers)} stocks")
    return result


def load_fundamentals() -> Dict:
    if os.path.exists(FUNDAMENTALS_FILE):
        with open(FUNDAMENTALS_FILE) as f:
            return json.load(f)
    return {}


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

def _fetch_one(ticker: str, session, crumb: str) -> dict:
    modules = (
        "financialData,defaultKeyStatistics,summaryDetail,"
        "earningsTrend,incomeStatementHistoryQuarterly,"
        "cashflowStatementHistoryQuarterly"
    )
    url = (
        f"https://query1.finance.yahoo.com/v11/finance/quoteSummary/{ticker}"
        f"?modules={modules}&crumb={crumb}"
    )
    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            return {}
        data   = r.json()
        result = data.get("quoteSummary", {}).get("result", [])
        if not result:
            return {}

        res = result[0]
        fd  = res.get("financialData", {})
        ks  = res.get("defaultKeyStatistics", {})
        sd  = res.get("summaryDetail", {})
        et  = res.get("earningsTrend", {})
        ish = res.get("incomeStatementHistoryQuarterly", {})

        def v(d, key):
            x = d.get(key, {})
            return x.get("raw") if isinstance(x, dict) else x

        # ── 52-week range ──────────────────────────────────────────────────────
        low52  = v(sd, "fiftyTwoWeekLow")
        high52 = v(sd, "fiftyTwoWeekHigh")
        price  = v(fd, "currentPrice")
        wk52_pos = None
        if low52 and high52 and high52 > low52 and price:
            wk52_pos = round((price - low52) / (high52 - low52) * 100, 1)

        # ── Market cap in Rs. Cr. ─────────────────────────────────────────────
        mktcap_raw = v(ks, "marketCap")
        mktcap_cr  = round(mktcap_raw / _CR, 2) if mktcap_raw else None

        # ── Quarterly income statement (last 2 quarters) ──────────────────────
        stmts = ish.get("statements", [])
        np_qtr       = None   # Net Profit latest quarter Rs. Cr.
        np_qtr_prev  = None
        sales_qtr    = None   # Sales latest quarter Rs. Cr.
        sales_qtr_prev = None
        np_qtr_var   = None   # % change vs prior quarter
        sales_qtr_var = None

        if len(stmts) >= 1:
            q0 = stmts[0]
            ni0     = v(q0, "netIncome")
            rev0    = v(q0, "totalRevenue")
            np_qtr   = round(ni0  / _CR, 2) if ni0  else None
            sales_qtr = round(rev0 / _CR, 2) if rev0 else None

        if len(stmts) >= 2:
            q1 = stmts[1]
            ni1      = v(q1, "netIncome")
            rev1     = v(q1, "totalRevenue")
            np_qtr_prev   = round(ni1  / _CR, 2) if ni1  else None
            sales_qtr_prev = round(rev1 / _CR, 2) if rev1 else None
            if np_qtr and np_qtr_prev and np_qtr_prev != 0:
                np_qtr_var = round((np_qtr - np_qtr_prev) / abs(np_qtr_prev) * 100, 1)
            if sales_qtr and sales_qtr_prev and sales_qtr_prev != 0:
                sales_qtr_var = round((sales_qtr - sales_qtr_prev) / abs(sales_qtr_prev) * 100, 1)

        # ── ROCE approximation ────────────────────────────────────────────────
        # ROCE = EBIT / Capital Employed; Yahoo gives operatingIncome & totalDebt + equity
        # We approximate: ROCE ≈ operatingMargins * (revenue / (totalDebt + bookValue))
        op_margin = v(fd, "operatingMargins")
        total_rev = v(fd, "totalRevenue")
        total_debt = v(fd, "totalDebt")
        book_val   = v(ks, "bookValue")
        shares_out = v(ks, "sharesOutstanding")
        roce = None
        if op_margin and total_rev and total_debt is not None and book_val and shares_out:
            ebit            = op_margin * total_rev
            equity_val      = book_val * shares_out
            capital_employed = equity_val + total_debt
            if capital_employed > 0:
                roce = round(ebit / capital_employed * 100, 2)

        # ── Earnings trend ────────────────────────────────────────────────────
        trends = et.get("trend", [])
        earnings_trend = _earnings_trend(trends)

        # ── Analyst target & upside ───────────────────────────────────────────
        target_price = v(fd, "targetMeanPrice")
        analyst_upside = None
        if price and target_price and price > 0:
            analyst_upside = round((target_price - price) / price * 100, 1)

        entry = {
            "ticker":               ticker,
            "fetched_at":           datetime.utcnow().isoformat(),

            # Valuation
            "pe_ratio":             _r(v(sd, "trailingPE") or v(ks, "forwardPE")),
            "forward_pe":           _r(v(ks, "forwardPE")),
            "pb_ratio":             _r(v(ks, "priceToBook")),
            "ev_ebitda":            _r(v(ks, "enterpriseToEbitda")),

            # Size
            "market_cap_cr":        mktcap_cr,

            # Profitability
            "roe":                  _pct(v(fd, "returnOnEquity")),
            "roa":                  _pct(v(fd, "returnOnAssets")),
            "roce":                 roce,
            "net_margin_pct":       _pct(v(fd, "profitMargins")),
            "operating_margin_pct": _pct(v(fd, "operatingMargins")),

            # Quarterly financials (the ones you asked for)
            "np_qtr_cr":            np_qtr,           # Net Profit latest qtr Rs.Cr.
            "np_qtr_prev_cr":       np_qtr_prev,      # Net Profit prior qtr Rs.Cr.
            "np_qtr_var_pct":       np_qtr_var,       # Qtr Profit Var %
            "sales_qtr_cr":         sales_qtr,        # Sales latest qtr Rs.Cr.
            "sales_qtr_prev_cr":    sales_qtr_prev,   # Sales prior qtr Rs.Cr.
            "sales_qtr_var_pct":    sales_qtr_var,    # Qtr Sales Var %

            # Annual growth
            "revenue_growth_pct":   _pct(v(fd, "revenueGrowth")),
            "earnings_growth_pct":  _pct(v(fd, "earningsGrowth")),

            # Balance sheet
            "debt_equity":          _r(v(fd, "debtToEquity")),
            "current_ratio":        _r(v(fd, "currentRatio")),

            # Returns to shareholders
            "dividend_yield_pct":   _pct(v(sd, "dividendYield")),
            "payout_ratio":         _pct(v(sd, "payoutRatio")),

            # Ownership
            "promoter_holding_pct":  _pct(v(ks, "heldPercentInsiders")),
            "institutional_pct":     _pct(v(ks, "heldPercentInstitutions")),

            # 52-week context
            "week52_high":          _r(high52),
            "week52_low":           _r(low52),
            "week52_position_pct":  wk52_pos,

            # Analyst view
            "analyst_target":       _r(target_price),
            "analyst_upside_pct":   analyst_upside,
            "analyst_recommendation": fd.get("recommendationKey", ""),
            "analyst_count":        v(fd, "numberOfAnalystOpinions"),

            # Earnings quality
            "earnings_trend":       earnings_trend,
            "beta":                 _r(v(ks, "beta")),
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
