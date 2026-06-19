"""
Win rate attribution — breaks down paper trading performance by:
  • Pattern name (which setups actually work)
  • Session (morning / midday / preclose)
  • Market mood (bullish / neutral / bearish at time of trade)
  • Style (intraday vs swing)

Results are stored per-ticker in patterns["ticker"]["attribution"]
and also aggregated into a summary dict for the dashboard.
"""

from typing import Dict, List


def update_attribution(patterns: Dict, closed_trades: List[dict]) -> Dict:
    """
    Recompute attribution stats from all closed trades.
    Called every preclose after paper trades are settled.
    """
    # Reset all attribution counters
    for ticker in patterns:
        patterns[ticker]["attribution"] = {
            "by_pattern": {},
            "by_session": {},
            "by_mood":    {},
            "by_style":   {},
        }

    for trade in closed_trades:
        ticker = trade.get("ticker")
        if not ticker or ticker not in patterns:
            continue

        attr  = patterns[ticker]["attribution"]
        won   = trade.get("won", trade.get("pnl", 0) > 0)

        # ── By pattern ────────────────────────────────────────────────────────
        for pat_name in trade.get("patterns", []):
            if pat_name not in attr["by_pattern"]:
                attr["by_pattern"][pat_name] = {"wins": 0, "losses": 0, "total": 0}
            attr["by_pattern"][pat_name]["total"] += 1
            if won:
                attr["by_pattern"][pat_name]["wins"] += 1
            else:
                attr["by_pattern"][pat_name]["losses"] += 1

        # ── By session ────────────────────────────────────────────────────────
        sess = trade.get("open_session", "unknown")
        if sess not in attr["by_session"]:
            attr["by_session"][sess] = {"wins": 0, "losses": 0, "total": 0}
        attr["by_session"][sess]["total"] += 1
        if won:
            attr["by_session"][sess]["wins"] += 1
        else:
            attr["by_session"][sess]["losses"] += 1

        # ── By style ─────────────────────────────────────────────────────────
        style = trade.get("style", "swing")
        if style not in attr["by_style"]:
            attr["by_style"][style] = {"wins": 0, "losses": 0, "total": 0}
        attr["by_style"][style]["total"] += 1
        if won:
            attr["by_style"][style]["wins"] += 1
        else:
            attr["by_style"][style]["losses"] += 1

        # ── By market mood ────────────────────────────────────────────────────
        mood = trade.get("market_mood", "neutral")
        if mood not in attr["by_mood"]:
            attr["by_mood"][mood] = {"wins": 0, "losses": 0, "total": 0}
        attr["by_mood"][mood]["total"] += 1
        if won:
            attr["by_mood"][mood]["wins"] += 1
        else:
            attr["by_mood"][mood]["losses"] += 1

    return patterns


def aggregate_attribution(patterns: Dict) -> dict:
    """
    Merge per-ticker attribution into a single summary for the dashboard.
    Returns {by_pattern, by_session, by_mood, by_style} each with win rates.
    """
    agg: dict = {
        "by_pattern": {},
        "by_session": {},
        "by_mood":    {},
        "by_style":   {},
    }

    for ticker, tk in patterns.items():
        attr = tk.get("attribution", {})
        for dimension in agg:
            for key, counts in attr.get(dimension, {}).items():
                if key not in agg[dimension]:
                    agg[dimension][key] = {"wins": 0, "losses": 0, "total": 0}
                agg[dimension][key]["wins"]   += counts.get("wins", 0)
                agg[dimension][key]["losses"] += counts.get("losses", 0)
                agg[dimension][key]["total"]  += counts.get("total", 0)

    # Compute win_rate on each bucket
    for dimension in agg:
        for key, counts in agg[dimension].items():
            t = counts["total"]
            counts["win_rate"] = round(counts["wins"] / t, 3) if t else 0.0

    # Sort by_pattern descending by win_rate (only show buckets with ≥3 trades)
    agg["by_pattern"] = dict(
        sorted(
            {k: v for k, v in agg["by_pattern"].items() if v["total"] >= 3}.items(),
            key=lambda x: x[1]["win_rate"],
            reverse=True,
        )
    )

    return agg
