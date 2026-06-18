"""
Fundamentals fetcher — pulls quarterly financials, key ratios, and
corporate actions from Yahoo Finance info endpoint (free, no auth needed
once the crumb session is warm).

Data collected per stock:
  - P/E, P/B, EV/EBITDA
  - Revenue growth (TTM vs prior year)
  - Net profit margin
  - Debt/Equity ratio
  - Promoter holding % (from shareholding pattern)
  - Return on Equity (ROE)
  - Earnings trend (beat/miss last 4 quarters)
  - Dividend yield
  - 52-week high/low position
"""

import json
import os
import time
from datetime import datetime
from typing import Dict, List

from agent.config import BRAIN_DIR

FUNDAMENTALS_FILE = "brain/fundamentals.json"


def fetch_fundamentals(tickers: List[str]) -> Dict:
    """Fetch fundamental data for a list of tickers."""
    from agent.data_fetcher import _YF, _yf_crumb
    import yfinance as yf

    crumb = _yf_crumb()
    result = {}

    for ticker in tickers:
        try:
            entry = _fetch_one_yf(ticker, _YF, crumb)
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
    # Merge with existing so we keep data for stocks not fetched this run
    existing = load_fundamentals()
    existing.update(data)
    with open(FUNDAMENTALS_FILE, "w") as f:
        json.dump(existing, f, indent=2)


def score_fundamentals(fund: dict) -> float:
    """
    Score fundamentals 0-100. Used as one input to recommendation engine.
    Higher is better.
    """
    if not fund:
        return 50.0  # neutral if no data

    score = 0.0
    max_score = 0.0

    def add(condition, weight):
        nonlocal score, max_score
        max_score += weight
        if condition:
            score += weight

    pe = fund.get("pe_ratio")
    pb = fund.get("pb_ratio")
    roe = fund.get("roe")
    de = fund.get("debt_equity")
    rev_growth = fund.get("revenue_growth_pct")
    margin = fund.get("net_margin_pct")
    promoter = fund.get("promoter_holding_pct")
    div_yield = fund.get("dividend_yield_pct", 0)
    wk52_pos = fund.get("week52_position_pct")  # 0=at 52w low, 100=at 52w high
    earnings_trend = fund.get("earnings_trend", "neutral")

    # P/E: reasonable valuation (5-35 is good for India large-caps)
    if pe is not None:
        add(5 <= pe <= 35, 15)
        add(pe < 25, 5)   # bonus for cheap

    # P/B: below 5 is reasonable
    if pb is not None:
        add(pb < 5, 10)
        add(pb < 3, 5)

    # ROE > 15% is good
    if roe is not None:
        add(roe > 15, 15)
        add(roe > 25, 5)

    # Debt/Equity < 1 is healthy
    if de is not None:
        add(de < 1.0, 10)
        add(de < 0.5, 5)

    # Revenue growth > 10% YoY
    if rev_growth is not None:
        add(rev_growth > 10, 15)
        add(rev_growth > 20, 5)

    # Net margin > 10%
    if margin is not None:
        add(margin > 10, 10)
        add(margin > 20, 5)

    # Promoter holding > 50% signals confidence
    if promoter is not None:
        add(promoter > 50, 10)
        add(promoter > 65, 5)

    # Not near 52-week high (avoid buying tops for swing trades)
    if wk52_pos is not None:
        add(wk52_pos < 85, 5)   # not in top 15% of 52w range
        add(wk52_pos > 20, 5)   # not near 52w lows either (momentum needed)

    # Earnings trend
    add(earnings_trend in ("beat", "improving"), 10)

    if max_score == 0:
        return 50.0
    return round(score / max_score * 100, 1)


# ── Internal ──────────────────────────────────────────────────────────────────

def _fetch_one_yf(ticker: str, session, crumb: str) -> dict:
    """Fetch from Yahoo Finance /quoteSummary endpoint."""
    modules = "financialData,defaultKeyStatistics,summaryDetail,earningsTrend"
    url = (
        f"https://query1.finance.yahoo.com/v11/finance/quoteSummary/{ticker}"
        f"?modules={modules}&crumb={crumb}"
    )
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            return {}
        data = r.json()
        result = data.get("quoteSummary", {}).get("result", [])
        if not result:
            return {}

        res  = result[0]
        fd   = res.get("financialData", {})
        ks   = res.get("defaultKeyStatistics", {})
        sd   = res.get("summaryDetail", {})
        et   = res.get("earningsTrend", {})

        def v(d, key):
            x = d.get(key, {})
            if isinstance(x, dict):
                return x.get("raw")
            return x

        # 52-week position
        low52  = v(sd, "fiftyTwoWeekLow")
        high52 = v(sd, "fiftyTwoWeekHigh")
        price  = v(fd, "currentPrice")
        wk52_pos = None
        if low52 and high52 and high52 > low52 and price:
            wk52_pos = round((price - low52) / (high52 - low52) * 100, 1)

        # Earnings trend (last 4 quarters)
        trends = et.get("trend", [])
        earnings_trend = _earnings_trend(trends)

        # Revenue growth: compare TTM to prior
        rev_ttm   = v(fd, "totalRevenue")
        rev_prior = None
        if trends:
            # earningsTrend doesn't give revenue — skip if not available
            pass

        entry = {
            "ticker":                ticker,
            "fetched_at":            datetime.utcnow().isoformat(),
            "pe_ratio":              v(sd, "trailingPE") or v(ks, "forwardPE"),
            "pb_ratio":              v(ks, "priceToBook"),
            "ev_ebitda":             v(ks, "enterpriseToEbitda"),
            "roe":                   _pct(v(fd, "returnOnEquity")),
            "roa":                   _pct(v(fd, "returnOnAssets")),
            "debt_equity":           v(fd, "debtToEquity"),
            "net_margin_pct":        _pct(v(fd, "profitMargins")),
            "operating_margin_pct":  _pct(v(fd, "operatingMargins")),
            "revenue_growth_pct":    _pct(v(fd, "revenueGrowth")),
            "earnings_growth_pct":   _pct(v(fd, "earningsGrowth")),
            "current_ratio":         v(fd, "currentRatio"),
            "dividend_yield_pct":    _pct(v(sd, "dividendYield")),
            "payout_ratio":          _pct(v(sd, "payoutRatio")),
            "beta":                  v(ks, "beta"),
            "shares_outstanding":    v(ks, "sharesOutstanding"),
            "promoter_holding_pct":  _pct(v(ks, "heldPercentInsiders")),
            "institutional_pct":     _pct(v(ks, "heldPercentInstitutions")),
            "week52_high":           high52,
            "week52_low":            low52,
            "week52_position_pct":   wk52_pos,
            "earnings_trend":        earnings_trend,
            "analyst_target":        v(fd, "targetMeanPrice"),
            "analyst_upside_pct":    _upside(price, v(fd, "targetMeanPrice")),
            "recommendation":        fd.get("recommendationKey", ""),
        }
        return {k: v2 for k, v2 in entry.items() if v2 is not None}

    except Exception as e:
        print(f"[fund] {ticker} parse error: {e}")
        return {}


def _pct(val) -> float:
    """Convert decimal to percentage, e.g. 0.15 → 15.0"""
    if val is None:
        return None
    try:
        return round(float(val) * 100, 2)
    except Exception:
        return None


def _upside(price, target) -> float:
    if not price or not target or price <= 0:
        return None
    return round((target - price) / price * 100, 1)


def _earnings_trend(trends: list) -> str:
    """Classify earnings trend from last 2 quarters."""
    if not trends:
        return "unknown"
    beats = 0
    misses = 0
    for t in trends[:2]:
        eps_est = t.get("earningsEstimate", {}).get("avg", {}).get("raw")
        eps_act  = t.get("earningsActual",   {}).get("raw")
        if eps_est is not None and eps_act is not None:
            if eps_act >= eps_est * 1.05:
                beats += 1
            elif eps_act < eps_est * 0.95:
                misses += 1
    if beats >= 2:
        return "beat"
    if misses >= 2:
        return "miss"
    if beats > misses:
        return "improving"
    return "neutral"
