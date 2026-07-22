"""
Stock scorer — used during selection phase to pick focus stocks.
Scores each stock 0–100 using purely its computed data (no hardcoded favourites).

Focus selection is the single most consequential decision in the pipeline: the
~15 stocks chosen here are what the tool deep-analyses and paper-trades for weeks.
So the score blends technicals with the higher-quality signals the tool now has —
institutional delivery accumulation and (when available) fundamental quality —
not just price action. Signals that aren't available yet (e.g. delivery during
early exploration) simply contribute nothing rather than penalising the stock.
"""

from typing import Dict, List, Tuple
from agent.config import FOCUS_STOCK_COUNT, MAX_STOCK_PRICE


def score_stock(ticker: str, entry: dict, sentiment: dict, fundamentals: dict = None) -> float:
    if not entry or "latest" not in entry:
        return 0.0
    d = entry["latest"]
    fundamentals = fundamentals or {}
    score = 0.0

    # Trend (22 pts)
    trend_map = {"strong_up": 22, "up": 16, "sideways": 7, "down": 3, "strong_down": 0}
    score += trend_map.get(entry.get("trend_10d", "sideways"), 7)

    # EMA alignment (16 pts)
    ema_diff = (d.get("ema_short", 1) - d.get("ema_long", 1)) / d.get("ema_long", 1) * 100
    if ema_diff > 2:   score += 16
    elif ema_diff > 0.5: score += 10
    elif ema_diff > -0.5: score += 4

    # RSI tradeable zone (12 pts)
    rsi = d.get("rsi", 50)
    if 38 <= rsi <= 62: score += 12
    elif 28 <= rsi < 38 or 62 < rsi <= 72: score += 6

    # MACD positive (8 pts)
    if d.get("macd_hist", 0) > 0: score += 8
    elif d.get("macd_hist", 0) > -0.3: score += 3

    # ATR in swing-friendly range (8 pts) — 1–5% of price
    atr_pct = d.get("atr_pct", 2)
    if 1.0 <= atr_pct <= 5.0: score += 8
    elif 0.5 <= atr_pct < 1.0 or 5 < atr_pct <= 7: score += 3

    # Volume (8 pts)
    vol_rel = d.get("vol_rel", 1.0)
    if vol_rel >= 1.5: score += 8
    elif vol_rel >= 1.0: score += 4

    # News (8 pts)
    ns = sentiment.get("score", 0) if sentiment else 0
    if ns > 0.2: score += 8
    elif ns > 0: score += 3
    elif ns < -0.2: score -= 4

    # ── Delivery accumulation (10 pts) — institutional conviction ──────────────
    # Often the difference between a real move and intraday churn. Absent during
    # early exploration → simply contributes 0 (neutral), never penalises.
    delivery_sig = d.get("delivery_signal", "neutral")
    score += {"strong_accumulation": 10, "accumulation": 6,
              "neutral": 0, "weak": -2, "distribution": -5}.get(delivery_sig, 0)

    # ── Fundamental quality (8 pts) — only if fundamentals are available ───────
    # Selecting fundamentally sound names improves long-term reliability. Skipped
    # cleanly when fundamentals haven't been fetched yet (returns nothing).
    if fundamentals:
        roe  = fundamentals.get("roe") or 0
        roce = fundamentals.get("roce") or 0
        de   = fundamentals.get("debt_equity")
        if roe >= 15:  score += 3
        elif roe >= 10: score += 1.5
        if roce >= 15: score += 3
        elif roce >= 10: score += 1.5
        if de is not None and de < 0.5: score += 2

    # ── Long-term regime context (8 pts) — from 2yr history if available ───────
    # Now available DURING exploration (full-universe history). Prefers stocks in
    # a constructive long-term trend at a healthy 52w position with a tradeable
    # personality. Absent during the first few exploration days → contributes 0.
    long_trend = d.get("hist_long_trend")
    pos52      = d.get("hist_pct_of_52w_range")
    personality = d.get("hist_personality")
    if long_trend in ("strong_uptrend", "uptrend"):
        score += 4
    elif long_trend in ("strong_downtrend", "downtrend"):
        score -= 3
    if pos52 is not None:
        if 35 <= pos52 <= 85:   score += 2   # healthy room to run
        elif pos52 > 92:        score += 1   # breakout zone
        elif pos52 < 8:         score -= 2   # falling-knife risk
    if personality == "trender":            score += 2   # cleaner to trade
    elif personality == "mean_reverter":    score += 1

    # ── Over-extension guard (mean-reversion counterweight) ────────────────────
    # The scoring above is momentum-heavy (~66 of the base points reward trend/EMA/
    # RSI/MACD/volume), which biases selection toward stocks ALREADY running hard —
    # exactly the names that then mean-revert. The live forward-test corpus showed
    # this (focus of banks/NBFCs at highs -> ~15% early hit-rate). Penalise the
    # genuinely stretched: very high RSI, or price pinned at the top of its range
    # while extended above the short EMA. This doesn't ban momentum — it stops the
    # pool from being ALL tops, leaving room for pullback/base setups too.
    rsi_now = d.get("rsi", 50)
    if rsi_now >= 75:                       score -= 6   # deeply overbought
    elif rsi_now >= 70:                     score -= 3
    ema_s = d.get("ema_short", 0); close_p = d.get("close", 0)
    if ema_s and close_p and (close_p / ema_s - 1) > 0.08:
        score -= 4                                       # >8% above short EMA = stretched
    if pos52 is not None and pos52 > 96 and rsi_now >= 68:
        score -= 3                                       # at the 52w ceiling AND overbought

    return round(max(0, min(score, 100)), 2)


def select_focus_stocks(
    stock_data: Dict,
    sentiment: Dict,
    n: int = FOCUS_STOCK_COUNT,
    fundamentals: Dict = None,
) -> List[Tuple[str, float]]:
    fundamentals = fundamentals or {}
    scored = []
    filtered_out = []
    for ticker, entry in stock_data.items():
        # Hard filter: skip stocks above ₹5000 CMP — poor position sizing
        cmp = entry.get("latest", {}).get("close", 0)
        if cmp > MAX_STOCK_PRICE:
            filtered_out.append((ticker, cmp))
            continue
        sent = sentiment.get(ticker, {}).get("latest", {})
        fund = fundamentals.get(ticker, {})
        s = score_stock(ticker, entry, sent, fund)
        scored.append((ticker, s))

    if filtered_out:
        print(f"[scorer] Filtered out {len(filtered_out)} stocks above ₹{MAX_STOCK_PRICE:,}:")
        for t, p in filtered_out:
            print(f"  {t:22s} CMP=₹{p:,.0f}")

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:n]
    print(f"\n[scorer] Top {n} stocks selected from {len(scored)} eligible:")
    for t, s in top:
        cmp = stock_data.get(t, {}).get("latest", {}).get("close", 0)
        print(f"  {t:22s} score={s:.1f}  CMP=₹{cmp:,.0f}")
    return top
