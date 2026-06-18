"""
Stock scorer — used during selection phase to pick focus stocks.
Scores each stock 0–100 using purely its computed data (no hardcoded favourites).
"""

from typing import Dict, List, Tuple
from agent.config import FOCUS_STOCK_COUNT, MAX_STOCK_PRICE


def score_stock(ticker: str, entry: dict, sentiment: dict) -> float:
    if not entry or "latest" not in entry:
        return 0.0
    d = entry["latest"]
    score = 0.0

    # Trend (25 pts)
    trend_map = {"strong_up": 25, "up": 18, "sideways": 8, "down": 3, "strong_down": 0}
    score += trend_map.get(entry.get("trend_10d", "sideways"), 8)

    # EMA alignment (20 pts)
    ema_diff = (d.get("ema_short", 1) - d.get("ema_long", 1)) / d.get("ema_long", 1) * 100
    if ema_diff > 2:   score += 20
    elif ema_diff > 0.5: score += 13
    elif ema_diff > -0.5: score += 5

    # RSI tradeable zone (15 pts)
    rsi = d.get("rsi", 50)
    if 38 <= rsi <= 62: score += 15
    elif 28 <= rsi < 38 or 62 < rsi <= 72: score += 7

    # MACD positive (10 pts)
    if d.get("macd_hist", 0) > 0: score += 10
    elif d.get("macd_hist", 0) > -0.3: score += 4

    # ATR in swing-friendly range (10 pts) — 1–5% of price
    atr_pct = d.get("atr_pct", 2)
    if 1.0 <= atr_pct <= 5.0: score += 10
    elif 0.5 <= atr_pct < 1.0 or 5 < atr_pct <= 7: score += 4

    # Volume (10 pts)
    vol_rel = d.get("vol_rel", 1.0)
    if vol_rel >= 1.5: score += 10
    elif vol_rel >= 1.0: score += 5

    # News (10 pts)
    ns = sentiment.get("score", 0) if sentiment else 0
    if ns > 0.2: score += 10
    elif ns > 0: score += 4
    elif ns < -0.2: score -= 5

    return round(min(score, 100), 2)


def select_focus_stocks(
    stock_data: Dict,
    sentiment: Dict,
    n: int = FOCUS_STOCK_COUNT,
) -> List[Tuple[str, float]]:
    scored = []
    filtered_out = []
    for ticker, entry in stock_data.items():
        # Hard filter: skip stocks above ₹5000 CMP — poor position sizing
        cmp = entry.get("latest", {}).get("close", 0)
        if cmp > MAX_STOCK_PRICE:
            filtered_out.append((ticker, cmp))
            continue
        sent = sentiment.get(ticker, {}).get("latest", {})
        s = score_stock(ticker, entry, sent)
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
