"""
Writes STRATEGY_REPORT.md — clean markdown summary of current recommendations.
"""

from datetime import date
from agent.config import STRATEGY_REPORT
from agent.paper_trader import compute_stats
from agent.recommendations import format_recommendation_text, load_recommendations


def generate_report(state, stock_data, patterns, news_data, book):
    stats = compute_stats(book)
    today = date.today().isoformat()
    recs  = load_recommendations()

    lines = [
        "# NSE AI Trader — Strategy Report",
        f"> Updated: {today} &nbsp;|&nbsp; Day {state.get('day')} &nbsp;|&nbsp; Phase: {state.get('phase')}",
        "",
        "---",
        "",
        "## Paper Trading Performance",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Trades | {stats['total']} ({stats['wins']} wins / {stats['losses']} losses) |",
        f"| Win Rate | {stats['win_rate']*100:.1f}% |",
        f"| Total P&L | ₹{stats['total_pnl']:+,.0f} |",
        f"| Avg Win | ₹{stats['avg_win']:+,.0f} |",
        f"| Avg Loss | ₹{stats['avg_loss']:+,.0f} |",
        f"| Expectancy / trade | ₹{stats['expectancy']:+,.0f} |",
        "",
        "---",
        "",
    ]

    if recs:
        lines += [
            "## Current Recommendations",
            "",
            "> **Important:** These are paper trade signals based on the agent's learned patterns.",
            "> Not financial advice. Always use your own judgement and consult a SEBI advisor.",
            "",
        ]
        for rec in recs:
            lines.append(format_recommendation_text(rec))
            lines += ["", "---", ""]
    else:
        lines += [
            "## Recommendations",
            "",
            "The agent has not yet generated confident recommendations.",
            "Check back after the paper trading phase accumulates enough data.",
            "",
        ]

    lines += [
        "## Focus Stocks Being Monitored",
        "",
        *[f"- {t}" for t in state.get("focus_stocks", [])],
        "",
        "---",
        "",
        "*Live dashboard with charts and details: enable GitHub Pages on this repo (Settings → Pages → /docs)*",
    ]

    with open(STRATEGY_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[report] STRATEGY_REPORT.md written ({len(recs)} recommendations)")
