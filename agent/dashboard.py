"""
Dashboard generator — writes docs/index.html served via GitHub Pages.
Completely self-contained HTML/CSS/JS — no external CDN dependencies.
Design: Minimal dark theme — Deep's NSE AI Tracker
"""

import json
import os
from datetime import datetime
from typing import Dict, List

from agent.config import (
    DASHBOARD_FILE, INITIAL_CAPITAL, WIN_RATE_THRESHOLD,
    EXPLORATION_DAYS, ANALYSIS_DAYS, FOCUS_STOCK_COUNT, MIN_TRADES_FOR_SIGNAL,
)

# Real phase boundaries derived from config — the dashboard must never display a
# day-range or count that contradicts the actual logic. (Honesty guarantee.)
_ANALYSIS_END = EXPLORATION_DAYS + ANALYSIS_DAYS          # last analysis day
_PAPER_START  = _ANALYSIS_END + 1                         # first paper-trading day
from agent.paper_trader import compute_stats


# ─────────────────────────────────────────────────────────────────────────────

def build_dashboard(
    state: dict,
    stock_data: Dict,
    book: dict,
    patterns: Dict,
    decisions: List,
    news_data: Dict,
    market_health: dict = None,
    recommendations: List = None,
    fundamentals: Dict = None,
    ranked_stocks: List = None,
    sector_scores: Dict = None,
    changelog: List = None,
    attribution: Dict = None,
    coach_memory: Dict = None,
    run_health: Dict = None,
    my_positions: Dict = None,
) -> None:
    os.makedirs("docs", exist_ok=True)
    if market_health is None:
        market_health = {}
    if recommendations is None:
        recommendations = []
    if fundamentals is None:
        fundamentals = {}
    if ranked_stocks is None:
        ranked_stocks = []
    if sector_scores is None:
        sector_scores = {}
    if changelog is None:
        changelog = []

    stats   = compute_stats(book)
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    phase   = state.get("phase", "exploration")
    day     = state.get("day", 1)
    focus   = state.get("focus_stocks", [])
    alert   = state.get("alert_sent", False)

    open_pos   = book.get("open_positions", [])
    unrealized = sum(p.get("unrealized_pnl", 0) for p in open_pos)
    invested   = sum(p.get("invested", 0) for p in open_pos)
    portfolio  = round(book.get("capital", INITIAL_CAPITAL) + invested + unrealized, 2)
    pnl_total  = round(portfolio - INITIAL_CAPITAL, 2)
    pnl_pct    = round(pnl_total / INITIAL_CAPITAL * 100, 2)

    nifty    = market_health.get("nifty", {})
    vix      = market_health.get("vix", {})
    mood     = market_health.get("market_mood", "neutral")
    trade_ok = market_health.get("trade_allowed", True)
    mkt_warn = market_health.get("warnings", [])

    html = _build_html(
        state, stats, book, patterns, decisions, news_data,
        stock_data, focus, phase, day, alert, now_utc,
        portfolio, pnl_total, pnl_pct, nifty, vix, mood,
        trade_ok, mkt_warn, recommendations, market_health, fundamentals,
        ranked_stocks, sector_scores, changelog, attribution or {},
        coach_memory=coach_memory or {},
        run_health=run_health or {},
        my_positions=my_positions or {},
    )

    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[dashboard] {DASHBOARD_FILE} ({len(recommendations)} recs, {len(focus)} stocks)")


# ─────────────────────────────────────────────────────────────────────────────
# HTML assembly
# ─────────────────────────────────────────────────────────────────────────────

def _build_html(
    state, stats, book, patterns, decisions, news_data, stock_data,
    focus, phase, day, alert, now_utc, portfolio, pnl_total, pnl_pct,
    nifty, vix, mood, trade_ok, mkt_warn, recommendations, market_health,
    fundamentals=None, ranked_stocks=None, sector_scores=None, changelog=None,
    attribution=None, coach_memory=None, run_health=None, my_positions=None,
):
    fundamentals  = fundamentals  or {}
    ranked_stocks = ranked_stocks or []
    sector_scores = sector_scores or {}
    changelog     = changelog     or []
    attribution   = attribution   or {}
    coach_memory  = coach_memory  or {}
    run_health    = run_health    or {}
    nifty_val = nifty.get("value", "")
    vix_val   = vix.get("value", "")
    nifty_str = f"{nifty_val:,.0f}" if isinstance(nifty_val, (int, float)) else "—"
    vix_str   = f"{vix_val}" if vix_val != "" else "—"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Deep's NSE AI Tracker</title>
<meta http-equiv="refresh" content="300">
<meta name="description" content="AI-powered NSE stock tracker for Nifty 100">
<meta name="theme-color" content="#0e0e0e" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#fafaf8" media="(prefers-color-scheme: light)">
<meta name="color-scheme" content="dark light">
<!-- PWA / iPhone home screen -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="NSE AI">
<link rel="apple-touch-icon" href="icon-180.png">
<link rel="apple-touch-icon" sizes="152x152" href="icon-152.png">
<link rel="apple-touch-icon" sizes="167x167" href="icon-167.png">
<link rel="apple-touch-icon" sizes="180x180" href="icon-180.png">
<link rel="icon" type="image/png" sizes="32x32" href="icon-32.png">
<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">
<link rel="manifest" href="manifest.json">
<style>{_css()}</style>
</head>
<body>
{_header(phase, day, now_utc, mood, trade_ok, nifty_str, vix_str)}

<div class="container">
  {_alert_banner(alert, stats) if alert else ""}

  <!-- ── HOME tab ── -->
  <section class="tab-panel" id="tab-home">
    {_market_bar(nifty, vix, mood, mkt_warn, market_health)}
    {_section_status(state, phase, day, focus, stock_data)}
    {_section_heatmap(stock_data)}
  </section>

  <!-- ── TRADE tab ── -->
  <section class="tab-panel" id="tab-trade" hidden>
    {_section_my_positions(my_positions or {}, focus, recommendations)}
    {_section_recommendations(recommendations, validated=alert, stats=stats, decisions=decisions, book=book)}
    {_section_rankings(ranked_stocks)}
    {_section_changelog(changelog)}
  </section>

  <!-- ── WATCH tab ── -->
  <section class="tab-panel" id="tab-watch" hidden>
    {_section_portfolio(stats, portfolio, pnl_total, pnl_pct, book)}
    {_section_watchlist(focus, stock_data, news_data, patterns, fundamentals)}
    {_section_sectors(sector_scores)}
    {_section_trades(book)}
  </section>

  <!-- ── LEARN tab ── -->
  <section class="tab-panel" id="tab-learn" hidden>
    {_section_learning_progress(decisions)}
    {_section_coach(coach_memory)}
    {_section_focus_competition()}
    {_section_attribution(attribution)}
    {_section_research(state, decisions)}
    {_section_brain(focus, patterns)}
  </section>

  <!-- ── LOG tab ── -->
  <section class="tab-panel" id="tab-log" hidden>
    {_section_runlog(state)}
    {_section_health(run_health, coach_memory)}
  </section>
</div>

{_bottom_nav()}
{_scripts()}
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────

def _css() -> str:
    return """
/* ── Flowr-inspired design system (dark default, light via prefers-color-scheme) ── */
:root {
  /* surfaces */
  --bg:     #0e0e0e;
  --card:   #181818;
  --card2:  #222;
  --card3:  #262626;
  --border: #282828;
  --border2:#333;
  /* ink */
  --text:   #efefef;
  --muted:  #888;
  --muted2: #aaa;
  --ink3:   #444;
  /* accents (Flowr palette) */
  --green:  #3ecf8e;  --g-bg: #0a2318;
  --red:    #e07070;  --r-bg: #280e0e;
  --amber:  #e6a93a;  --a-bg: #2a1e00;
  --bull:   #3ecf8e;
  --bear:   #e07070;
  --yellow: #e6a93a;
  --blue:   #7eb3ff;
  --cyan:   #3ecf8e;
  --font:   -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --mono:   'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg:     #fafaf8;
    --card:   #f2f2ef;
    --card2:  #e8e8e4;
    --card3:  #e8e8e4;
    --border: #e4e4e0;
    --border2:#d8d8d2;
    --text:   #111;
    --muted:  #777;
    --muted2: #555;
    --ink3:   #bbb;
    --green:  #1a6b42;  --g-bg: #edf6f1;
    --red:    #c0392b;  --r-bg: #fdf0ef;
    --amber:  #c8860a;  --a-bg: #fdf4e3;
    --bull:   #1a6b42;
    --bear:   #c0392b;
    --yellow: #c8860a;
    --blue:   #2563eb;
    --cyan:   #1a6b42;
  }
}
*,*::before,*::after { box-sizing: border-box; margin: 0; padding: 0 }
html { scroll-behavior: smooth; -webkit-tap-highlight-color: transparent }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.5;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}
a { color: inherit; text-decoration: none }
h2 {
  font-size: 1rem;
  font-weight: 500;
  color: var(--text);
  margin-bottom: 12px;
  display: flex;
  align-items: baseline;
  gap: 8px;
  letter-spacing: -.03em;
  flex-wrap: wrap;
}
h2 span { font-size: .68rem; color: var(--muted); font-weight: 400; letter-spacing: 0 }
h3 { font-size: .85rem; font-weight: 500; letter-spacing: -.02em }

/* ── Header (Flowr: clean, hairline, system font) ── */
.header {
  background: color-mix(in srgb, var(--bg) 92%, transparent);
  border-bottom: 0.5px solid var(--border);
  padding: 0 20px;
  height: 54px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.logo { display: flex; align-items: center; gap: 9px; flex-shrink: 0 }
.logo-icon {
  width: 28px; height: 28px;
  background: var(--green);
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: .72rem; font-weight: 700; color: var(--bg);
  flex-shrink: 0;
}
.logo-text { font-size: 1rem; font-weight: 600; letter-spacing: -.03em; white-space: nowrap }
.hdr-chips {
  display: flex; align-items: center; gap: 6px;
  overflow-x: auto; -ms-overflow-style: none; scrollbar-width: none;
  flex-shrink: 1; min-width: 0;
}
.hdr-chips::-webkit-scrollbar { display: none }
.hdr-chips .badge { flex-shrink: 0; font-size: .6rem }

/* ── Tab panels ── */
.tab-panel { animation: fadeIn .18s ease }
.tab-panel[hidden] { display: none }
@keyframes fadeIn { from { opacity: 0; transform: translateY(4px) } to { opacity: 1; transform: none } }

/* ── Bottom navigation (Flowr-style) ── */
.bottom-nav {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 200;
  display: flex; justify-content: space-around; align-items: flex-start;
  background: color-mix(in srgb, var(--bg) 94%, transparent);
  backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
  border-top: 0.5px solid var(--border);
  /* Lift the whole bar above the iPhone home indicator: a generous base gap PLUS
     the device's safe-area inset (requires viewport-fit=cover in the meta tag,
     otherwise iOS reports the inset as 0 and the bar sits too low). */
  padding-top: 8px;
  padding-bottom: calc(18px + env(safe-area-inset-bottom));
}
.nt {
  flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px;
  background: none; border: none; cursor: pointer;
  color: var(--muted); padding: 2px 0; min-height: 44px;
  font-family: inherit; font-size: .6rem; font-weight: 500; letter-spacing: .02em;
  transition: color .15s;
}
.nt svg { width: 22px; height: 22px }
.nt-active { color: var(--green) }
.nt:active { opacity: .6 }
.logo-sub { font-size: .6rem; color: var(--muted); margin-top: -1px }
.header-right { display: flex; align-items: center; gap: 14px }
.hdr-stat { display: flex; flex-direction: column; align-items: flex-end }
.hdr-val { font-size: .78rem; font-weight: 600; font-variant-numeric: tabular-nums }
.hdr-lbl { font-size: .57rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em }

/* ── Phase strip ── */
.phase-strip {
  background: var(--card);
  border-bottom: 1px solid var(--border);
  height: 30px;
  display: flex;
  align-items: center;
  padding: 0 20px;
  overflow-x: auto;
  -ms-overflow-style: none; scrollbar-width: none;
}
.phase-strip::-webkit-scrollbar { display: none }
.ps-item {
  display: flex; align-items: center; gap: 5px;
  flex-shrink: 0; font-size: .65rem; color: var(--muted); padding: 0 6px;
}
.ps-item.ps-done { color: var(--bull) }
.ps-item.ps-active { color: var(--cyan) }
.ps-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--border); border: 1.5px solid var(--muted); flex-shrink: 0;
}
.ps-item.ps-done .ps-dot { background: var(--bull); border-color: var(--bull) }
.ps-item.ps-active .ps-dot { background: var(--cyan); border-color: var(--cyan); box-shadow: 0 0 5px var(--cyan) }
.ps-line { flex: 1; height: 1px; background: var(--border); min-width: 16px; max-width: 50px }
.ps-line.ps-line-done { background: var(--bull) }

/* ── Layout ── */
.container { max-width: 1200px; margin: 0 auto; padding: 14px 16px;
  padding-bottom: calc(100px + env(safe-area-inset-bottom)) }  /* clear the taller bottom nav */
.section { margin-bottom: 24px; scroll-margin-top: 88px }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px }
.grid3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px }
.grid4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px }

/* ── Cards (Flowr: soft surface, hairline border, gentle radius) ── */
.card {
  background: var(--card);
  border: 0.5px solid var(--border);
  border-radius: 12px;
  padding: 16px;
}
.stat-card {
  background: var(--card);
  border: 0.5px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
}
.stat-val { font-size: 1.6rem; font-weight: 400; line-height: 1.1; margin-bottom: 3px; letter-spacing: -.04em; font-variant-numeric: tabular-nums }
.stat-label { font-size: .62rem; color: var(--muted); font-weight: 500; text-transform: uppercase; letter-spacing: .07em }

/* ── Badges (Flowr: 99px pills with soft accent backgrounds) ── */
.badge {
  display: inline-block; padding: 2px 9px; border-radius: 99px;
  font-size: .63rem; font-weight: 600; white-space: nowrap; letter-spacing: .01em;
}
.badge-green  { background: var(--g-bg); color: var(--green) }
.badge-red    { background: var(--r-bg); color: var(--red) }
.badge-yellow { background: var(--a-bg); color: var(--amber) }
.badge-blue   { background: var(--card2); color: var(--blue) }
.badge-cyan   { background: var(--g-bg); color: var(--green) }
.badge-orange { background: var(--a-bg); color: var(--amber) }
.badge-gray   { background: var(--card2); color: var(--muted) }
.pill {
  display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 99px;
  font-size: .63rem; font-weight: 600; white-space: nowrap;
}
.pill-green  { background: var(--g-bg); color: var(--green) }
.pill-red    { background: var(--r-bg); color: var(--red) }
.pill-yellow { background: var(--a-bg); color: var(--amber) }
.pill-blue   { background: var(--card2); color: var(--blue) }
.pill-cyan   { background: var(--g-bg); color: var(--green) }
.pill-gray   { background: var(--card2); color: var(--muted) }

/* ── Colors ── */
.green  { color: var(--green) }
.red    { color: var(--red) }
.yellow { color: var(--amber) }
.blue   { color: var(--blue) }
.cyan   { color: var(--green) }
.muted  { color: var(--muted) }
.muted2 { color: var(--muted2) }

/* ── Tables ── */
table { width: 100%; border-collapse: collapse }
th {
  color: var(--muted); font-size: .62rem; font-weight: 500;
  text-transform: uppercase; letter-spacing: .07em;
  padding: 8px 10px; border-bottom: 0.5px solid var(--border); text-align: left; white-space: nowrap;
}
td { padding: 9px 10px; border-bottom: 0.5px solid var(--border); font-size: .8rem; vertical-align: middle }
tr:last-child td { border: none }
tr:hover td { background: var(--card2) }
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch }

/* ── Progress (Flowr: 2px hairline bar) ── */
.progress { height: 2px; border-radius: 99px; background: var(--border); overflow: hidden }
.progress-fill { height: 100%; border-radius: 99px; transition: width .4s }

/* ── Market bar ── */
.market-bar {
  background: var(--card);
  border: 0.5px solid var(--border);
  border-radius: 12px;
  padding: 12px 16px;
  margin-bottom: 14px;
  display: flex; flex-wrap: wrap; gap: 18px; align-items: center;
}
.mkt-item { display: flex; flex-direction: column; gap: 0 }
.mkt-val { font-size: .95rem; font-weight: 500; letter-spacing: -.02em; font-variant-numeric: tabular-nums }
.mkt-lbl { font-size: .58rem; color: var(--muted); text-transform: uppercase; letter-spacing: .07em }

/* ── Info rows ── */
.irow {
  display: flex; justify-content: space-between; align-items: center;
  padding: 6px 0; border-bottom: 0.5px solid var(--border); font-size: .8rem;
}
.irow:last-child { border: none }
.fund-row {
  display: flex; justify-content: space-between;
  padding: 5px 0; border-bottom: 0.5px solid var(--border); font-size: .73rem;
}
.fund-row:last-child { border: none }

/* ── Nav (Flowr pill tabs) ── */
.nav {
  display: flex; gap: 4px; margin-bottom: 16px;
  padding-bottom: 10px; border-bottom: 0.5px solid var(--border);
  overflow-x: auto; white-space: nowrap;
  -ms-overflow-style: none; scrollbar-width: none;
}
.nav::-webkit-scrollbar { display: none }
.nav a {
  color: var(--muted); padding: 6px 12px; border-radius: 99px;
  font-size: .72rem; border: 0.5px solid transparent;
  min-height: 32px; display: inline-flex; align-items: center;
  transition: color .12s, border-color .12s, background .12s;
}
.nav a:hover { color: var(--text); background: var(--card2) }

/* ── Heatmap ── */
.heatmap-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(74px, 1fr)); gap: 4px }
.hmap-cell {
  border: 1px solid; border-radius: 6px; padding: 6px 4px;
  text-align: center; cursor: default; transition: transform .1s;
}
.hmap-cell:hover { transform: scale(1.05); position: relative; z-index: 5 }
.hmap-name { font-size: .63rem; font-weight: 700; color: var(--text); margin-bottom: 1px }
.hmap-chg  { font-size: .76rem; font-weight: 700; font-variant-numeric: tabular-nums }
.hmap-rsi  { font-size: .56rem; color: var(--muted); margin-top: 1px }

/* ── Banners (advisory = amber 'heads up', not alarming red) ── */
.warn-banner {
  background: var(--a-bg); border: 0.5px solid var(--amber); border-radius: 10px;
  padding: 9px 13px; margin-bottom: 8px; font-size: .76rem; color: var(--amber);
}
.alert-banner {
  background: var(--g-bg); border: 0.5px solid var(--green); border-radius: 12px;
  padding: 11px 14px; margin-bottom: 14px; display: flex; align-items: center; gap: 10px;
}

/* ── Notes ── */
.note-line { padding: 4px 0; border-bottom: 0.5px solid var(--border); font-size: .73rem; color: var(--muted) }
.note-line:last-child { border: none }
.note-line strong { color: var(--text) }

/* ── Stock card ── */
.stock-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  padding: 11px; transition: border-color .12s;
}
.stock-card:hover { border-color: var(--blue) }

/* ── Recommendation card ── */
.rec-card { border-radius: 10px; padding: 16px; border: 1px solid; margin-bottom: 10px }
.rec-buy  { background: linear-gradient(135deg, #16a34a05 0%, #060608 100%); border-color: #16a34a25 }
.rec-sell { background: linear-gradient(135deg, #dc262605 0%, #060608 100%); border-color: #dc262625 }
.rec-header {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 10px; flex-wrap: wrap; gap: 8px;
}
.rec-name { font-size: .95rem; font-weight: 700; letter-spacing: -.01em }
.rec-code { font-size: .68rem; color: var(--muted); margin-top: 1px }
.rec-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(125px, 1fr));
  gap: 6px; margin: 10px 0;
}
.rec-field { background: var(--card2); border-radius: 6px; padding: 8px 10px; border: 1px solid var(--border) }
.rec-field-val { font-size: .9rem; font-weight: 700; font-variant-numeric: tabular-nums }
.rec-field-lbl { font-size: .58rem; color: var(--muted); margin-top: 1px; text-transform: uppercase; letter-spacing: .04em }

/* ── Price ladder ── */
.price-ladder { display: flex; flex-direction: column; gap: 3px; margin: 8px 0 }
.pl-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 10px; border-radius: 5px; background: var(--card2);
  border: 1px solid var(--border); font-size: .76rem;
}
.pl-label { color: var(--muted); font-size: .66rem }
.pl-price  { font-weight: 700; font-variant-numeric: tabular-nums }

/* ── Timing box (new) ── */
.timing-box {
  background: var(--card2); border: 1px solid var(--border); border-radius: 7px;
  padding: 10px 12px; margin: 8px 0;
}
.timing-row { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 5px }
.timing-row:last-child { margin-bottom: 0 }
.timing-icon { font-size: .8rem; flex-shrink: 0; margin-top: 1px }
.timing-label { font-size: .6rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 1px }
.timing-val { font-size: .73rem; color: var(--text) }
.urgency-intraday { background: #ea580c0c; border-color: #ea580c28; color: #fb923c }
.urgency-delivery { background: #0891b20c; border-color: #0891b228 }

/* ── Score bars ── */
.score-bar-wrap { margin-bottom: 4px }
.score-bar-head { display: flex; justify-content: space-between; font-size: .6rem; color: var(--muted); margin-bottom: 2px }
.score-bar-track { height: 3px; background: var(--border); border-radius: 2px }
.score-bar-fill  { height: 100%; border-radius: 2px }

/* ── Reasons ── */
.reasons-list { margin-top: 8px; padding: 8px 10px; background: var(--card2); border-radius: 6px; border: 1px solid var(--border) }
.reasons-list li { font-size: .72rem; color: var(--muted2); margin-bottom: 2px; list-style: none; padding-left: 10px; position: relative }
.reasons-list li::before { content: "›"; position: absolute; left: 0; color: var(--blue) }
.conf-bar { margin-top: 8px }

/* ── Phase steps ── */
.phase-progress { display: flex; align-items: flex-start; margin: 12px 0; overflow-x: auto; padding: 4px 0 }
.phase-step { display: flex; flex-direction: column; align-items: center; gap: 3px; min-width: 80px }
.phase-step span  { font-size: .67rem; font-weight: 600; color: var(--muted); text-align: center }
.phase-step small { font-size: .58rem; color: var(--muted) }
.phase-step.active span { color: var(--cyan) }
.phase-step.done   span { color: var(--bull) }
.phase-dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--border); border: 2px solid var(--muted);
}
.phase-step.active .phase-dot { background: var(--cyan); border-color: var(--cyan); box-shadow: 0 0 6px var(--cyan) }
.phase-step.done   .phase-dot { background: var(--bull); border-color: var(--bull) }
.phase-line { flex: 1; height: 2px; background: var(--border); margin-top: 4px; min-width: 20px }

/* ── Timeline ── */
.timeline { display: flex; align-items: center; flex-wrap: wrap; gap: 0; margin-top: 5px }
.tphase { display: flex; align-items: center; gap: 4px; padding: 4px 8px; border-radius: 4px; font-size: .69rem }
.tphase-done   { background: #16a34a0e; color: #4ade80 }
.tphase-active { background: #ca8a040e; color: #fbbf24 }
.tphase-future { color: var(--muted) }
.tarrow { color: var(--muted); margin: 0 1px; font-size: .67rem }

/* ── Scrollbar (webkit) ── */
::-webkit-scrollbar { width: 5px; height: 5px }
::-webkit-scrollbar-track { background: var(--bg) }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px }
::-webkit-scrollbar-thumb:hover { background: var(--border2) }

/* ── Intraday chart canvas ── */
.intraday-chart-wrap {
  position: relative; width: 100%; border-radius: 6px; overflow: hidden;
  background: var(--card2); border: 1px solid var(--border);
}
.intraday-chart-wrap canvas { display: block; width: 100% !important }
.chart-label {
  position: absolute; top: 6px; left: 8px;
  font-size: .6rem; color: var(--muted); font-family: var(--mono);
  pointer-events: none;
}
.chart-price {
  position: absolute; top: 6px; right: 8px;
  font-size: .68rem; font-weight: 700; font-family: var(--mono);
  pointer-events: none;
}

/* ── Responsive — iPhone first ── */
@media (max-width: 480px) {
  body { font-size: 13px }
  .header { height: 48px; padding: 0 12px }
  .logo-icon { width: 24px; height: 24px; font-size: .68rem }
  .logo-text { font-size: .85rem }
  .logo-sub { display: none }
  .header-right .hdr-stat { display: none }
  .phase-strip { height: 26px; padding: 0 12px }
  /* keep generous bottom padding so content clears the fixed bottom nav */
  .container { padding: 8px 10px; padding-bottom: calc(110px + env(safe-area-inset-bottom)) }
  .section { margin-bottom: 16px; scroll-margin-top: 80px }
  .card { padding: 13px }
  .stat-val { font-size: 1.3rem }
  .stat-label { font-size: .58rem }
  .heatmap-grid { grid-template-columns: repeat(auto-fill, minmax(62px, 1fr)); gap: 3px }
  .hmap-cell { padding: 5px 3px }
  .rec-card { padding: 13px }
  .rec-grid { grid-template-columns: 1fr 1fr }
  .rec-name { font-size: .85rem }
  td, th { padding: 6px 7px; font-size: .73rem }
  .nav a { padding: 6px 11px; font-size: .68rem; min-height: 34px }
  .grid2, .grid3, .grid4 { grid-template-columns: 1fr }
  h2 { font-size: .82rem }
  .market-bar { gap: 10px; padding: 7px 10px }
  .mkt-val { font-size: .82rem }
  .price-ladder { gap: 2px }
  .pl-row { padding: 4px 8px }
}
@media (min-width: 481px) and (max-width: 767px) {
  .header-right .hdr-stat:not(:last-child) { display: none }
  .logo-sub { display: none }
  .container { padding: 10px 12px; padding-bottom: calc(110px + env(safe-area-inset-bottom)) }
  .grid2 { grid-template-columns: 1fr }
  .grid3, .grid4 { grid-template-columns: 1fr 1fr }
  .heatmap-grid { grid-template-columns: repeat(auto-fill, minmax(66px, 1fr)) }
  td, th { padding: 5px 7px }
}
@media (min-width: 768px) and (max-width: 1023px) {
  .grid3 { grid-template-columns: repeat(2, 1fr) }
  .grid4 { grid-template-columns: repeat(2, 1fr) }
}
@media (min-width: 1024px) {
  .grid3 { grid-template-columns: repeat(3, 1fr) }
  .grid4 { grid-template-columns: repeat(4, 1fr) }
}
/* Safe area insets for iPhone notch/home bar */
@supports (padding: max(0px)) {
  .header { padding-left: max(20px, env(safe-area-inset-left)); padding-right: max(20px, env(safe-area-inset-right)) }
  .container { padding-left: max(14px, env(safe-area-inset-left)); padding-right: max(14px, env(safe-area-inset-right)) }
  body { padding-bottom: env(safe-area-inset-bottom) }
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────

def _header(phase, day, now_utc, mood, trade_ok, nifty_str, vix_str) -> str:
    """Compact single-row header (Flowr-style) — no wrapping on phones.
    Logo + name on the left; a tidy phase/day/status chip row on the right that
    stays on one line and scrolls horizontally if ever needed."""
    phase_label = {"exploration": "Exploration", "analysis": "Analysis",
                   "paper_trading": "Paper Trade", "alerting": "Live"}.get(phase, phase.title())
    trade_cls = "badge-green" if trade_ok else "badge-red"
    trade_txt = "Trading" if trade_ok else "Paused"

    return f"""<div class="header">
  <div class="logo">
    <div class="logo-icon">AI</div>
    <div class="logo-text">Deep's&nbsp;NSE&nbsp;Tracker</div>
  </div>
  <div class="hdr-chips">
    <span class="badge badge-blue">{phase_label}</span>
    <span class="badge badge-gray">Day&nbsp;{day}</span>
    <span class="badge {trade_cls}">{trade_txt}</span>
  </div>
</div>"""


def _bottom_nav() -> str:
    """Flowr-style fixed bottom navigation. Each item switches a tab panel.
    Five tabs cover all sections; touch-friendly with safe-area inset."""
    items = [
        ("home",  "Home",  "M3 11l9-8 9 8M5 10v10h14V10"),
        ("trade", "Trade", "M3 17l6-6 4 4 8-8M21 7h-5M21 7v5"),
        ("watch", "Watch", "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z"),
        ("learn", "Learn", "M12 3L2 8l10 5 10-5-10-5zM6 10v5c0 1 3 3 6 3s6-2 6-3v-5"),
        ("log",   "Log",   "M4 5h16M4 12h16M4 19h10"),
    ]
    btns = ""
    for i, (key, label, path) in enumerate(items):
        active = " nt-active" if i == 0 else ""
        btns += (
            f'<button class="nt{active}" data-tab="{key}" onclick="switchTab(\'{key}\')">'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round"><path d="{path}"/></svg>'
            f'<span>{label}</span></button>'
        )
    return f'<nav class="bottom-nav">{btns}</nav>'


def _alert_banner(alert, stats) -> str:
    return f"""<div class="alert-banner">
  <div style="font-size:1.6rem">&#127919;</div>
  <div>
    <div style="font-weight:700;font-size:.95rem;color:var(--green)">Strategy Confidence Reached!</div>
    <div class="muted" style="font-size:.78rem;margin-top:2px">
      Win rate: <strong class="green">{stats['win_rate']*100:.1f}%</strong> over
      <strong>{stats['total']}</strong> paper trades &middot;
      Expectancy: <strong class="{'green' if stats['expectancy']>0 else 'red'}">
        &#8377;{stats['expectancy']:+,.0f}</strong> per trade.
      See <a href="#recommendations" style="color:var(--cyan)">Recommendations</a> below.
    </div>
  </div>
</div>"""


def _market_bar(nifty, vix, mood, warnings, market_health) -> str:
    n_val = nifty.get("value", "—")
    n_chg = nifty.get("day_change_pct", 0)
    v_val = vix.get("value", "—")
    v_lvl = vix.get("level", "normal")
    v_cls = {"normal": "green", "caution": "yellow", "danger": "red"}.get(v_lvl, "muted")
    n_cls = "green" if n_chg >= 0 else "red"
    leaders = market_health.get("leading_sectors", [])
    leader_str = " &middot; ".join(leaders[:4]) if leaders else "—"

    # market_health writes this under "bank_nifty" (underscore); accept both for safety
    bnifty = market_health.get("bank_nifty") or market_health.get("banknifty", {})
    bn_val = bnifty.get("value", "")
    bn_chg = bnifty.get("day_change_pct", 0)
    bn_cls = "green" if bn_chg >= 0 else "red"
    bn_str = f"{bn_val:,.0f}" if isinstance(bn_val, (int, float)) else "—"

    # Market warnings are ADVISORY heads-ups (e.g. "Nifty choppy — wait for cleaner
    # setups"), not errors — the tool keeps running normally. Labelled as such.
    warn_html = "".join(
        f'<div class="warn-banner">&#9432; Heads up: {w}</div>' for w in warnings
    )

    n_str = f"{n_val:,.0f}" if isinstance(n_val, (int, float)) else str(n_val)
    mood_color = {"bullish": "var(--green)", "bearish": "var(--red)"}.get(mood, "var(--yellow)")

    # ── Macro (global + India) sentiment panel ─────────────────────────────────
    macro = market_health.get("macro", {})
    macro_html = ""
    if macro:
        mm   = macro.get("mood", "neutral")
        mscore = macro.get("overall_score", 0)
        gscore = macro.get("global_score", 0)
        iscore = macro.get("india_score", 0)
        mcol = {"risk_on": "var(--green)", "risk_off": "var(--red)"}.get(mm, "var(--yellow)")
        cues = macro.get("global_cues", {})
        cue_chips = ""
        for k in ("dow", "nasdaq", "nikkei", "crude", "usdinr"):
            if k in cues:
                cv = cues[k].get("chg_pct", 0)
                ccol = "var(--green)" if cv >= 0 else "var(--red)"
                cue_chips += (f'<span style="margin-right:10px">{k.upper()} '
                              f'<b style="color:{ccol}">{cv:+.1f}%</b></span>')
        summary = macro.get("summary", "")
        playbook = macro.get("playbook", [])
        pb_html = ""
        if playbook:
            pb_html = ('<div style="margin-top:6px;font-size:.66rem;color:var(--muted)">'
                       + "<br>".join(f"&#9656; {p}" for p in playbook[:3]) + "</div>")
        # ── Real FII/DII flows + Nifty PCR row ─────────────────────────────────
        flow_html = ""
        fd = market_health.get("fii_dii", {})
        pcr = market_health.get("pcr", {})
        chips = []
        if fd and fd.get("date"):
            fii = fd.get("fii_net_cr", 0); dii = fd.get("dii_net_cr", 0)
            fcol = "var(--green)" if fii >= 0 else "var(--red)"
            dcol = "var(--green)" if dii >= 0 else "var(--red)"
            stale = _stale_tag(fd.get("date"))
            chips.append(f'FII <b style="color:{fcol}">&#8377;{fii:+,.0f} Cr</b>{stale}')
            chips.append(f'DII <b style="color:{dcol}">&#8377;{dii:+,.0f} Cr</b>')
        if pcr and pcr.get("pcr"):
            chips.append(f'PCR <b>{pcr.get("pcr")}</b>{_stale_tag(pcr.get("date"))}')
        if chips:
            flow_html = ('<div style="margin-top:7px;font-size:.68rem;color:var(--text)">'
                         + ' &nbsp;·&nbsp; '.join(chips) + '</div>')
        macro_html = f"""<div class="card" style="margin-bottom:12px;padding:12px 14px">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">
    <div style="font-size:.72rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)">
      Global &amp; India Macro Sentiment
    </div>
    <div style="font-size:.85rem;font-weight:700;color:{mcol}">
      {mm.replace('_','-').title()} ({mscore:+.2f})
      <span style="font-size:.62rem;color:var(--muted);font-weight:400">
        &nbsp;global {gscore:+.2f} &middot; india {iscore:+.2f}</span>
    </div>
  </div>
  <div style="margin-top:7px;font-size:.68rem;color:var(--text)">{cue_chips or ''}</div>
  {flow_html}
  {f'<div style="margin-top:7px;font-size:.74rem;line-height:1.5;color:var(--text)">{summary}</div>' if summary else ''}
  {pb_html}
</div>"""

    return f"""{warn_html}
{macro_html}
<div class="market-bar">
  <div class="mkt-item">
    <div class="mkt-val {n_cls}">{n_str} <span style="font-size:.72rem">({n_chg:+.2f}%)</span></div>
    <div class="mkt-lbl">Nifty 50</div>
  </div>
  <div class="mkt-item">
    <div class="mkt-val {bn_cls}">{bn_str} <span style="font-size:.72rem">({bn_chg:+.2f}%)</span></div>
    <div class="mkt-lbl">BankNifty</div>
  </div>
  <div class="mkt-item">
    <div class="mkt-val {v_cls}">{v_val} <span style="font-size:.65rem">[{v_lvl}]</span></div>
    <div class="mkt-lbl">India VIX</div>
  </div>
  <div class="mkt-item">
    <div class="mkt-val" style="color:{mood_color}">{mood.title()}</div>
    <div class="mkt-lbl">Market Mood</div>
  </div>
  <div class="mkt-item" style="flex:1">
    <div class="mkt-val" style="font-size:.82rem;color:var(--cyan)">{leader_str}</div>
    <div class="mkt-lbl">Leading Sectors</div>
  </div>
</div>"""


def _section_status(state, phase, day, focus, stock_data=None) -> str:
    stock_data = stock_data or {}
    phase_order  = ["exploration", "analysis", "paper_trading", "alerting"]
    phase_labels = {
        "exploration":   "1. Explore Universe",
        "analysis":      "2. Deep Analysis",
        "paper_trading": "3. Paper Trade",
        "alerting":      "4. Signal Ready",
    }
    current_idx = phase_order.index(phase) if phase in phase_order else 0

    timeline_html = ""
    for i, p in enumerate(phase_order):
        lbl = phase_labels[p]
        if i < current_idx:
            timeline_html += f'<div class="tphase tphase-done">&#10003; {lbl}</div>'
        elif i == current_idx:
            timeline_html += f'<div class="tphase tphase-active">&#9658; {lbl}</div>'
        else:
            timeline_html += f'<div class="tphase tphase-future">{lbl}</div>'
        if i < len(phase_order) - 1:
            timeline_html += '<span class="tarrow">&#8594;</span>'

    start        = state.get("start_date", "—")
    phase_start  = state.get("phase_start_date", "—")
    focus_html   = "".join(
        f'<span class="badge badge-cyan" style="margin:2px">{t.replace(".NS","")}</span>'
        for t in focus
    )

    def _phase_cls(p_name):
        if p_name == phase:
            return "active"
        idx = phase_order.index(p_name) if p_name in phase_order else 0
        return "done" if idx < current_idx else ""

    phase_descs = {
        "exploration":   f"Days 1-{EXPLORATION_DAYS}",
        "analysis":      f"Days {EXPLORATION_DAYS+1}-{_ANALYSIS_END}",
        "paper_trading": f"Days {_PAPER_START}+",
        "alerting":      "Live",
    }
    phase_names_map = {
        "exploration":   "Exploration",
        "analysis":      "Analysis",
        "paper_trading": "Paper Trading",
        "alerting":      "Recommendations",
    }
    phase_step_html = ""
    for i, p_name in enumerate(phase_order):
        cls = _phase_cls(p_name)
        phase_step_html += (
            f'<div class="phase-step {cls}">'
            f'<div class="phase-dot"></div>'
            f'<span>{phase_names_map[p_name]}</span>'
            f'<small>{phase_descs[p_name]}</small>'
            f'</div>'
        )
        if i < len(phase_order) - 1:
            phase_step_html += '<div class="phase-line"></div>'

    if phase == "exploration":
        stocks_fetched = len(stock_data)
        days_left = max(0, EXPLORATION_DAYS - day)
        next_ms = (
            f"Focus stock selection in ~{days_left} day(s)" if days_left > 0
            else "Focus stock selection due at next preclose"
        )
        activity_lines = [
            f"Watching all {stocks_fetched} stocks every session — building price, volume, and indicator baselines",
            "Detecting candlestick patterns on every bar: hammer, engulfing, morning star, doji, and more",
            "Scoring each stock on: momentum trend, RSI zone, MACD direction, ADX trend-strength, volume",
            f"Next milestone: {next_ms} — top {FOCUS_STOCK_COUNT} by momentum score selected for deep analysis",
        ]
    elif phase == "analysis":
        days_left = max(0, _ANALYSIS_END - day)
        activity_lines = [
            f"Deep-analysing {len(focus)} focus stocks — running full pattern detection each session",
            "Recording what it WOULD trade each day, then forward-testing each call days later "
            "against the REAL price — right calls and wrong calls both teach pattern reliability",
            "Building 2-year regime/personality profiles + quarterly fundamentals per stock",
            f"Paper trading starts in ~{days_left} day(s) — with reliability priors already learned",
        ]
    elif phase in ("paper_trading", "alerting"):
        activity_lines = [
            f"Paper trading {len(focus)} focus stocks with virtual capital",
            "Each session: opening new positions, updating stops, closing completed trades",
            "Scoring: Technical + Fundamental + News + Pattern, with multi-timeframe & options-flow context",
            "Recommendations appear when a stock scores 65/100 with R:R 2:1 or better",
        ]
    else:
        activity_lines = ["Agent initialising — check back after first run."]

    activity_html = "".join(
        f'<div style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">'
        f'<span style="color:var(--cyan);margin-top:1px">&#8250;</span>'
        f'<span style="font-size:.8rem;color:var(--text)">{line}</span></div>'
        for line in activity_lines
    )

    next_milestone_map = {
        "exploration":   f"Day {min(day+1, EXPLORATION_DAYS)}: Continue building baselines",
        "analysis":      f"Day {min(day+1, _ANALYSIS_END)}: Paper trading begins",
        "paper_trading": "Await 65+ score setup",
        "alerting":      "Live signals active",
    }
    next_ms_text = next_milestone_map.get(phase, "—")

    return f"""<div class="section" id="status">
  <h2>Agent Status <span>What the agent is doing right now</span></h2>
  <div class="card">
    <div class="timeline">{timeline_html}</div>
    <div class="phase-progress">{phase_step_html}</div>
    <div style="margin-top:14px;background:var(--card2);border-radius:8px;padding:11px 13px">
      <div style="font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">Current activity</div>
      {activity_html}
    </div>
    <div style="margin-top:13px;display:flex;flex-wrap:wrap;gap:22px">
      <div><div class="stat-label">Started</div><div style="font-weight:600;font-size:.85rem">{start}</div></div>
      <div><div class="stat-label">Phase since</div><div style="font-weight:600;font-size:.85rem">{phase_start}</div></div>
      <div><div class="stat-label">Days run</div><div style="font-weight:600;font-size:.85rem">{day}</div></div>
      <div><div class="stat-label">Stocks tracked</div><div style="font-weight:600;font-size:.85rem">{len(stock_data)}</div></div>
      <div><div class="stat-label">Next milestone</div><div style="font-weight:600;font-size:.85rem;color:var(--cyan)">{next_ms_text}</div></div>
    </div>
    {f'<div style="margin-top:11px"><div class="stat-label" style="margin-bottom:5px">Focus Stocks</div>{focus_html}</div>' if focus else ""}
  </div>
</div>"""


def _section_heatmap(stock_data: dict) -> str:
    if not stock_data:
        return ""
    cells = []
    for ticker, entry in sorted(stock_data.items()):
        d     = entry.get("latest", {})
        close = d.get("close", 0)
        ph    = entry.get("price_history_60d", [])
        chg   = (ph[-1] - ph[-2]) / ph[-2] * 100 if len(ph) >= 2 and ph[-2] else 0
        rsi   = d.get("rsi", 50)
        code  = ticker.replace(".NS", "")

        intensity = max(-1.0, min(1.0, chg / 3.0))
        if intensity >= 0:
            r = int(16  + (1 - intensity) * 55)
            g = int(197 - (1 - intensity) * 80)
            b = int(94  - intensity * 70)
        else:
            r = int(239 - (1 + intensity) * 90)
            g = int(68  + (1 + intensity) * 55)
            b = int(68  + (1 + intensity) * 26)
        bg     = f"#{r:02x}{g:02x}{b:02x}1e"
        border = f"#{r:02x}{g:02x}{b:02x}48"
        chg_color = "var(--green)" if chg >= 0 else "var(--red)"
        rsi_color = "var(--green)" if rsi < 40 else ("var(--red)" if rsi > 65 else "var(--yellow)")
        sign  = "+" if chg >= 0 else ""

        cells.append(
            f'<div class="hmap-cell" style="background:{bg};border-color:{border}"'
            f' title="{code}: Rs.{close:.2f} | RSI {rsi:.0f}">'
            f'<div class="hmap-name">{code}</div>'
            f'<div class="hmap-chg" style="color:{chg_color}">{sign}{chg:.1f}%</div>'
            f'<div class="hmap-rsi" style="color:{rsi_color}">RSI {rsi:.0f}</div>'
            f'</div>'
        )

    return f"""<div class="section" id="heatmap">
  <h2>Market Snapshot <span>all monitored stocks &mdash; day change &amp; RSI</span></h2>
  <div class="heatmap-grid">{"".join(cells)}</div>
</div>"""


def _section_portfolio(stats, portfolio, pnl_total, pnl_pct, book) -> str:
    p_cls   = "green" if pnl_total >= 0 else "red"
    # Honesty: a win-rate over ZERO trades is not 0% — it's "not measured yet".
    # Showing 0.0% would look like a failed track record. Use "—" until trades exist.
    has_trades = stats["total"] > 0
    wr_pct  = round(stats["win_rate"] * 100, 1)
    wr_disp = f"{wr_pct:.1f}%" if has_trades else "—"
    wr_cls  = ("green" if stats["win_rate"] >= WIN_RATE_THRESHOLD else "yellow") if has_trades else "muted"
    exp_disp = f"&#8377;{stats['expectancy']:+,.0f}" if has_trades else "—"
    exp_cls = ("green" if stats["expectancy"] > 0 else "red") if has_trades else "muted"
    prog_color = "var(--green)" if wr_pct >= WIN_RATE_THRESHOLD * 100 else "var(--yellow)"

    def sc(label, val, cls=""):
        color_attr = f' class="{cls}"' if cls else ""
        return (
            f'<div class="stat-card">'
            f'<div class="stat-val{color_attr}">{val}</div>'
            f'<div class="stat-label">{label}</div>'
            f'</div>'
        )

    equity_html   = _equity_curve(book)
    open_pos_html = _open_positions_table(book)

    max_dd      = book.get("max_drawdown_pct", 0.0)
    curr_dd     = book.get("current_drawdown_pct", 0.0)
    peak_val    = book.get("portfolio_peak", portfolio)
    sess_peak   = book.get("sessions_since_peak", 0)
    dd_cls      = "red" if curr_dd > 5 else "yellow" if curr_dd > 2 else "green"
    max_dd_cls  = "red" if max_dd > 10 else "yellow" if max_dd > 5 else "green"

    return f"""<div class="section" id="portfolio">
  <h2>Paper Portfolio <span>Virtual &#8377;1,00,000 &mdash; no real money</span></h2>
  <div class="grid4" style="margin-bottom:13px">
    {sc("Portfolio Value",   f"&#8377;{portfolio:,.0f}", p_cls)}
    {sc("Total P&amp;L",    f"&#8377;{pnl_total:+,.0f} ({pnl_pct:+.1f}%)", p_cls)}
    {sc("Win Rate",          wr_disp, wr_cls)}
    {sc("Total Trades",      f"{stats['total']} ({stats['wins']}W / {stats['losses']}L)")}
  </div>
  <div class="grid4" style="margin-bottom:13px">
    {sc("Peak Value",        f"&#8377;{peak_val:,.0f}")}
    {sc("Current Drawdown",  f"{curr_dd:.1f}%", dd_cls)}
    {sc("Max Drawdown",      f"{max_dd:.1f}%", max_dd_cls)}
    {sc("Sessions Since Peak", f"{sess_peak}")}
  </div>
  <div style="margin-bottom:13px">
    <div style="display:flex;justify-content:space-between;font-size:.68rem;color:var(--muted);margin-bottom:3px">
      <span>Win rate toward {WIN_RATE_THRESHOLD*100:.0f}% target</span>
      <span>{wr_disp} / {WIN_RATE_THRESHOLD*100:.0f}%{'' if has_trades else ' (no trades yet)'}</span>
    </div>
    <div class="progress">
      <div class="progress-fill" style="width:{(min(wr_pct/(WIN_RATE_THRESHOLD*100)*100, 100) if has_trades else 0):.1f}%;background:{prog_color}"></div>
    </div>
  </div>
  <div class="grid2">
    <div>{equity_html}</div>
    <div class="card">
      <h3 style="margin-bottom:10px">Trade Stats</h3>
      <div class="irow"><span class="muted">Avg winning trade</span><span class="green">&#8377;{stats['avg_win']:+,.0f}</span></div>
      <div class="irow"><span class="muted">Avg losing trade</span><span class="red">&#8377;{stats['avg_loss']:+,.0f}</span></div>
      <div class="irow"><span class="muted">Win rate</span><span class="{wr_cls}">{wr_disp}</span></div>
      <div class="irow"><span class="muted">Expectancy / trade</span><span class="{exp_cls}">{exp_disp}</span></div>
      <div class="irow"><span class="muted">Free capital</span><span>&#8377;{book.get('capital', INITIAL_CAPITAL):,.0f}</span></div>
      <div class="irow"><span class="muted">Open positions</span><span class="blue">{len(book.get('open_positions', []))}</span></div>
      <div class="irow"><span class="muted">Max drawdown</span><span class="{max_dd_cls}">{max_dd:.1f}%</span></div>
    </div>
  </div>
  {open_pos_html}
</div>"""


def _equity_curve(book) -> str:
    snaps = book.get("daily_snapshots", [])
    if len(snaps) < 2:
        return (
            '<div class="card" style="padding:20px;text-align:center;color:var(--muted);font-size:.8rem">'
            'Equity curve appears after the first trading day.</div>'
        )
    vals  = [s["portfolio_value"] for s in snaps]
    dates = [s["date"] for s in snaps]
    color = "#22c55e" if vals[-1] >= vals[0] else "#ef4444"
    trend_cls = "green" if vals[-1] >= vals[0] else "red"
    return f"""<div class="card">
  <div style="display:flex;justify-content:space-between;margin-bottom:7px;font-size:.74rem">
    <span class="muted">Portfolio Equity Curve</span>
    <span class="muted">{dates[0]} &#8594; {dates[-1]}</span>
  </div>
  <div data-spark='{json.dumps(vals)}' data-color='{color}' style="height:68px"></div>
  <div style="display:flex;justify-content:space-between;font-size:.68rem;color:var(--muted);margin-top:5px">
    <span>Start &#8377;{INITIAL_CAPITAL:,}</span>
    <span>Min &#8377;{min(vals):,.0f}</span>
    <span>Max &#8377;{max(vals):,.0f}</span>
    <span class="{trend_cls}">Now &#8377;{vals[-1]:,.0f}</span>
  </div>
</div>"""


def _open_positions_table(book) -> str:
    positions = book.get("open_positions", [])
    if not positions:
        return ""
    rows = ""
    for p in positions:
        unr = p.get("unrealized_pnl", 0)
        pct = round(unr / p["invested"] * 100, 2) if p.get("invested") else 0
        cls = "green" if unr >= 0 else "red"
        rows += (
            f'<tr>'
            f'<td><strong>{p["ticker"].replace(".NS","")}</strong></td>'
            f'<td><span class="pill {"pill-green" if p["action"]=="BUY" else "pill-red"}">{p["action"]}</span></td>'
            f'<td>&#8377;{p["entry"]:.2f}</td>'
            f'<td>&#8377;{p.get("current_price", p["entry"]):.2f}</td>'
            f'<td class="{cls}">&#8377;{unr:+.0f} ({pct:+.1f}%)</td>'
            f'<td class="red">&#8377;{p["stop_loss"]:.2f}</td>'
            f'<td class="green">&#8377;{p["target"]:.2f}</td>'
            f'<td><span class="pill pill-blue">{p.get("style","swing")}</span></td>'
            f'<td class="muted">{p["open_date"]}</td>'
            f'</tr>'
        )
    return f"""<div class="card table-wrap" style="margin-top:13px">
  <h3 style="margin-bottom:10px">Open Positions</h3>
  <table>
    <thead><tr>
      <th>Stock</th><th>Side</th><th>Entry</th><th>Current</th>
      <th>Unrealised P&amp;L</th><th>Stop Loss</th><th>Target</th><th>Style</th><th>Opened</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""


def _sparkline(prices: list, width=180, height=40) -> str:
    if len(prices) < 2:
        return ""
    prices = prices[-30:]
    mn, mx = min(prices), max(prices)
    rng = mx - mn or 1
    pts = []
    for i, p in enumerate(prices):
        x = i / (len(prices) - 1) * width
        y = height - (p - mn) / rng * height
        pts.append(f"{x:.1f},{y:.1f}")
    color    = "#22c55e" if prices[-1] >= prices[0] else "#ef4444"
    path     = "M" + " L".join(pts)
    grad_id  = f"sg{abs(hash(str(prices[0]))) % 9999}"
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"'
        f' style="display:block;overflow:visible">'
        f'<defs><linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.28"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/>'
        f'</linearGradient></defs>'
        f'<path d="{path} L{width},{height} L0,{height} Z" fill="url(#{grad_id})"/>'
        f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.5"/>'
        f'</svg>'
    )


def _intraday_chart_svg(candles: list, width=280, height=52) -> str:
    """Render a compact SVG candlestick chart from 5-min intraday candles."""
    if not candles or len(candles) < 3:
        return ""
    candles = candles[-60:]  # last 60 bars (~5 hours)
    opens  = [c.get("open",  c.get("close", 0)) for c in candles]
    highs  = [c.get("high",  c.get("close", 0)) for c in candles]
    lows   = [c.get("low",   c.get("close", 0)) for c in candles]
    closes = [c.get("close", 0)                 for c in candles]
    mn = min(lows);  mx = max(highs)
    rng = mx - mn or 1
    n   = len(candles)
    cw  = max(2, (width - 4) / n - 1)  # candle width
    pad = 2

    def y(v):
        return height - pad - (v - mn) / rng * (height - 2 * pad)

    bodies = ""
    wicks  = ""
    for i, (o, h, l, c) in enumerate(zip(opens, highs, lows, closes)):
        cx  = pad + i * (width - 2 * pad) / n
        col = "#22c55e" if c >= o else "#ef4444"
        # wick
        wicks += f'<line x1="{cx+cw/2:.1f}" y1="{y(h):.1f}" x2="{cx+cw/2:.1f}" y2="{y(l):.1f}" stroke="{col}" stroke-width="0.8" opacity="0.7"/>'
        # body
        by = min(y(o), y(c));  bh = max(abs(y(c) - y(o)), 1)
        bodies += f'<rect x="{cx:.1f}" y="{by:.1f}" width="{cw:.1f}" height="{bh:.1f}" fill="{col}" opacity="0.9"/>'

    last_close  = closes[-1]
    first_close = closes[0]
    trend_col   = "#22c55e" if last_close >= first_close else "#ef4444"
    chg_pct     = (last_close - first_close) / (first_close or 1) * 100

    return (
        f'<div class="intraday-chart-wrap" style="height:{height+4}px">'
        f'<span class="chart-label">5m candles · {n} bars</span>'
        f'<span class="chart-price" style="color:{trend_col}">{chg_pct:+.2f}%</span>'
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="display:block;width:100%;height:{height}px">'
        f'{wicks}{bodies}'
        f'</svg></div>'
    )


def _rsi_bar(rsi: float) -> str:
    pct   = max(0, min(100, rsi))
    color = "#22c55e" if rsi < 40 else ("#ef4444" if rsi > 65 else "#eab308")
    return (
        f'<div style="margin-top:5px">'
        f'<div style="display:flex;justify-content:space-between;font-size:.62rem;color:var(--muted);margin-bottom:2px">'
        f'<span>RSI</span><span style="color:{color}">{rsi:.0f}</span></div>'
        f'<div style="height:3px;background:var(--border);border-radius:2px">'
        f'<div style="width:{pct}%;height:100%;background:{color};border-radius:2px;transition:width .3s"></div>'
        f'</div></div>'
    )


def _score_bar(label: str, score, max_score, color: str) -> str:
    pct = min(100, score / max_score * 100) if max_score > 0 else 0
    return (
        f'<div class="score-bar-wrap">'
        f'<div class="score-bar-head">'
        f'<span>{label}</span><span style="color:{color}">{score:.0f}/{max_score}</span>'
        f'</div>'
        f'<div class="score-bar-track">'
        f'<div class="score-bar-fill" style="width:{pct:.1f}%;background:{color}"></div>'
        f'</div></div>'
    )


def _fund_table(fund: dict) -> str:
    if not fund:
        return ""
    rows = []

    def row(label, val, fmt="{}", color=None):
        if val is None:
            return
        try:
            display = fmt.format(val)
        except Exception:
            display = str(val)
        col = f' style="color:{color}"' if color else ""
        rows.append(
            f'<div class="fund-row">'
            f'<span class="muted">{label}</span>'
            f'<span{col}>{display}</span>'
            f'</div>'
        )

    pe        = fund.get("pe_ratio")
    mktcap    = fund.get("market_cap_cr")
    div_y     = fund.get("dividend_yield_pct")
    np_q      = fund.get("np_qtr_cr")
    np_v      = fund.get("np_qtr_var_pct")
    sal_q     = fund.get("sales_qtr_cr")
    sal_v     = fund.get("sales_qtr_var_pct")
    roce      = fund.get("roce")
    roe       = fund.get("roe")
    de        = fund.get("debt_equity")
    promo     = fund.get("promoter_holding_pct")
    analyst_up = fund.get("analyst_upside_pct")
    et        = fund.get("earnings_trend", "")

    row("P/E",       pe,     "{:.1f}x")
    if mktcap:
        row("Mkt Cap",   mktcap, "&#8377;{:,.0f} Cr")
    row("Div Yield", div_y,  "{:.2f}%")
    row("NP Qtr",    np_q,   "&#8377;{:,.0f} Cr")
    row("NP Var",    np_v,   "{:+.1f}%", color=("#22c55e" if np_v and np_v > 0 else "#ef4444"))
    row("Sales Qtr", sal_q,  "&#8377;{:,.0f} Cr")
    row("Sales Var", sal_v,  "{:+.1f}%", color=("#22c55e" if sal_v and sal_v > 0 else "#ef4444"))
    row("ROCE",      roce,   "{:.1f}%",  color=("#22c55e" if roce and roce > 15 else None))
    row("ROE",       roe,    "{:.1f}%",  color=("#22c55e" if roe and roe > 15 else None))
    row("D/E",       de,     "{:.2f}",
        color=("#22c55e" if de is not None and de < 0.5 else
               "#ef4444" if de is not None and de > 1.5 else None))
    row("Promoter",  promo,  "{:.1f}%",  color=("#22c55e" if promo and promo > 55 else None))
    row("Analyst Up", analyst_up, "{:+.1f}%",
        color=("#22c55e" if analyst_up and analyst_up > 10 else None))
    if et and et not in ("unknown", "neutral"):
        et_color = "#22c55e" if ("beat" in et or et == "improving") else "#ef4444"
        rows.append(
            f'<div class="fund-row">'
            f'<span class="muted">Earnings</span>'
            f'<span style="color:{et_color}">{et.replace("_"," ")}</span>'
            f'</div>'
        )

    if not rows:
        return ""
    return (
        '<div style="margin-top:9px;background:var(--card2);border-radius:8px;padding:9px 11px">'
        '<div style="font-size:.6rem;color:var(--muted);text-transform:uppercase;'
        'letter-spacing:.05em;margin-bottom:5px">Fundamentals</div>'
        + "".join(rows)
        + '</div>'
    )


def _section_sectors(sector_scores: dict) -> str:
    if not sector_scores:
        return ""

    def mom_bar(m):
        pct  = round((m + 1) / 2 * 100)   # -1→0%, 0→50%, +1→100%
        col  = "var(--bull)" if m > 0.15 else "var(--bear)" if m < -0.15 else "var(--muted)"
        lbl  = "Bullish" if m > 0.15 else "Bearish" if m < -0.15 else "Neutral"
        return (
            f'<div style="display:flex;align-items:center;gap:8px">'
            f'<div style="flex:1;height:5px;background:var(--card2);border-radius:3px">'
            f'<div style="width:{pct}%;height:5px;background:{col};border-radius:3px"></div>'
            f'</div>'
            f'<span style="font-size:.68rem;color:{col};min-width:48px">{lbl}</span>'
            f'</div>'
        )

    # Sort by momentum descending
    sorted_sectors = sorted(
        [(s, v["latest"]) for s, v in sector_scores.items() if "latest" in v],
        key=lambda x: x[1].get("momentum", 0),
        reverse=True
    )

    cards = ""
    for sector, info in sorted_sectors:
        mom   = info.get("momentum", 0)
        rsi   = info.get("avg_rsi", 50)
        n     = info.get("stock_count", 0)
        col   = "var(--bull)" if mom > 0.15 else "var(--bear)" if mom < -0.15 else "var(--muted)"
        cards += (
            f'<div class="card" style="padding:12px 14px">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:6px">'
            f'<strong style="font-size:.82rem">{sector}</strong>'
            f'<span style="font-size:.72rem;color:{col};font-weight:600">{mom:+.2f}</span>'
            f'</div>'
            f'{mom_bar(mom)}'
            f'<div style="font-size:.68rem;color:var(--muted);margin-top:5px">'
            f'RSI {rsi:.0f} &middot; {n} stocks'
            f'</div>'
            f'</div>'
        )

    return f"""<div class="section" id="sectors">
  <h2>Sector Rotation <span>Real-time sector momentum — updates every session</span></h2>
  <div class="grid3">{cards}</div>
</div>"""


def _stale_tag(data_date: str, fresh_within_days: int = 1) -> str:
    """Return an amber '(Nd old)' marker when a feed's last value is older than
    the last trading day, so stale last-known data is never shown as if current.
    Honesty: a number from days ago must look like a number from days ago.
    Returns '' when the data is current (today or the previous trading day)."""
    if not data_date:
        return ""
    try:
        from datetime import date as _date
        from agent.trading_calendar import ist_today, is_trading_day
        d = _date.fromisoformat(str(data_date)[:10])
        today = ist_today()
        gap = (today - d).days
        # Count back over weekends/holidays: how many TRADING days stale?
        trading_gap = 0
        probe = today
        from datetime import timedelta as _td
        while probe > d and trading_gap <= 10:
            probe -= _td(days=1)
            if is_trading_day(probe):
                trading_gap += 1
        if trading_gap <= fresh_within_days:
            return ""
        return (f' <span style="color:var(--amber,#e6a93a);font-size:.6rem;font-weight:600" '
                f'title="last successful update {data_date}">({trading_gap}d old)</span>')
    except Exception:
        return ""


def _section_focus_competition() -> str:
    """Honest, visible record of every focus-list change driven by the perpetual
    background competition — what came in, what went out, and exactly why."""
    try:
        from agent.focus_competition import load_focus_competition
        events = load_focus_competition()
    except Exception:
        events = []

    if not events:
        body = ("""<div class="card" style="padding:18px;text-align:center;font-size:.8rem;color:var(--muted)">
      Background batches keep re-scanning the whole universe. When one finds a stock
      stronger than a current focus stock, the swap and its reason appear here —
      so the focus list never changes silently. No competition swaps yet.
    </div>""")
    else:
        recent = list(reversed(events))[:25]
        rows = ""
        for e in recent:
            act = e.get("action", "")
            color = "var(--green)" if act == "promoted" else "var(--red)"
            label = "IN" if act == "promoted" else "OUT"
            rows += (
                f'<tr>'
                f'<td style="color:var(--muted);font-size:.7rem;white-space:nowrap">{e.get("ts","")}</td>'
                f'<td><span style="color:{color};font-size:.7rem;font-weight:700">{label}</span></td>'
                f'<td><strong style="font-size:.8rem">{e.get("ticker","")}</strong></td>'
                f'<td style="font-size:.7rem;color:var(--muted)">{(str(e.get("score")) if e.get("score") else "")}</td>'
                f'<td style="font-size:.74rem;color:var(--fg)">{e.get("reason","")}</td>'
                f'</tr>'
            )
        body = f"""<div class="card table-wrap">
    <table>
      <thead><tr><th>When</th><th>In/Out</th><th>Stock</th><th>Score</th><th>Why</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>"""

    return f"""<div class="section" id="focus-competition">
  <h2>Focus Competition <span>Every focus change, and exactly why — never silent</span></h2>
  {body}
</div>"""


def _section_changelog(changelog: list) -> str:
    if not changelog:
        return ""

    recent = list(reversed(changelog))[:20]   # show last 20 events, newest first

    type_styles = {
        "new":     ("var(--bull)",  "NEW"),
        "removed": ("var(--bear)",  "REMOVED"),
        "updated": ("var(--cyan)",  "UPDATED"),
    }

    rows = ""
    for c in recent:
        ctype  = c.get("type", "updated")
        color, label = type_styles.get(ctype, ("var(--muted)", ctype.upper()))
        signal = c.get("signal", "")
        sig_col = "var(--bull)" if signal == "BUY" else "var(--bear)" if signal == "SELL" else "var(--muted)"
        rows += (
            f'<tr>'
            f'<td style="color:var(--muted);font-size:.7rem;white-space:nowrap">{c.get("date","")}</td>'
            f'<td><span style="color:{color};font-size:.7rem;font-weight:600">{label}</span></td>'
            f'<td><strong style="font-size:.8rem">{c.get("nse_code","")}</strong></td>'
            f'<td style="color:{sig_col};font-size:.74rem">{signal}</td>'
            f'<td style="font-size:.74rem;color:var(--fg)">{c.get("detail","")}</td>'
            f'</tr>'
        )

    return f"""<div class="section" id="changelog">
  <h2>Recommendation Changelog <span>What changed and why — session by session</span></h2>
  <div class="card table-wrap">
    <table>
      <thead><tr>
        <th>Date</th><th>Event</th><th>Stock</th><th>Signal</th><th>Detail</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>"""


def _section_rankings(ranked: list) -> str:
    if not ranked:
        return ""

    def delta_html(d):
        if d > 0:
            return f'<span style="color:var(--bull);font-size:.7rem">▲{d}</span>'
        if d < 0:
            return f'<span style="color:var(--bear);font-size:.7rem">▼{abs(d)}</span>'
        return '<span style="color:var(--muted);font-size:.7rem">—</span>'

    def prob_bar(val, color):
        pct = round(min(100, max(0, val * 100)))
        return (
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<div style="flex:1;height:5px;background:var(--card2);border-radius:3px">'
            f'<div style="width:{pct}%;height:5px;background:{color};border-radius:3px"></div>'
            f'</div>'
            f'<span style="font-size:.7rem;color:var(--muted);min-width:32px">{pct}%</span>'
            f'</div>'
        )

    rows = []
    for r in ranked:
        ticker   = r["ticker"]
        code     = r["nse_code"]
        rank     = r["rank"]
        delta    = r.get("rank_delta", 0)
        sp       = r.get("success_probability", 0.5)
        pp       = r.get("profit_probability", 0.0)
        cs       = r.get("composite_score", 0.0)
        trend    = r.get("trend", "sideways")
        n_trades = r.get("paper_trades", 0)
        wr       = r.get("paper_win_rate", 0.0)
        close    = r.get("close", 0)

        trend_color = {"strong_up": "var(--bull)", "up": "var(--bull)",
                       "sideways": "var(--muted)", "down": "var(--bear)",
                       "strong_down": "var(--bear)"}.get(trend, "var(--muted)")
        trend_label = trend.replace("_", " ").title()

        profit_color = "var(--bull)" if pp > 0 else "var(--bear)"

        rows.append(
            f'<tr>'
            f'<td style="color:var(--muted);font-size:.75rem">#{rank} {delta_html(delta)}</td>'
            f'<td><strong style="font-size:.82rem">{code}</strong></td>'
            f'<td style="font-size:.75rem;color:{trend_color}">{trend_label}</td>'
            f'<td style="font-size:.75rem">₹{close:,.0f}</td>'
            f'<td style="min-width:90px">{prob_bar(sp, "var(--bull)")}</td>'
            f'<td style="min-width:90px">{prob_bar(max(0, pp), profit_color)}</td>'
            f'<td style="font-size:.73rem;color:var(--muted)">'
            f'{n_trades}T / {wr:.0f}%WR</td>'
            f'<td><span class="pill" style="background:#1a2233;color:#7eb3ff;font-size:.68rem">'
            f'{cs:.0f}</span></td>'
            f'</tr>'
        )

    rows_html = "".join(rows)
    return f"""<div class="section" id="rankings">
  <h2>Focus Stock Rankings <span>Live rank · updated every session · promotes/demotes automatically</span></h2>
  <div style="overflow-x:auto">
  <table style="width:100%;border-collapse:collapse;font-size:.8rem">
    <thead>
      <tr style="color:var(--muted);font-size:.72rem;border-bottom:1px solid var(--card2)">
        <th style="padding:6px 8px;text-align:left">Rank</th>
        <th style="padding:6px 8px;text-align:left">Stock</th>
        <th style="padding:6px 8px;text-align:left">Trend</th>
        <th style="padding:6px 8px;text-align:left">CMP</th>
        <th style="padding:6px 8px;text-align:left">Success Prob</th>
        <th style="padding:6px 8px;text-align:left">Profit Prob</th>
        <th style="padding:6px 8px;text-align:left">Paper Stats</th>
        <th style="padding:6px 8px;text-align:left">Score</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  </div>
</div>"""


def _section_watchlist(focus, stock_data, news_data, patterns, fundamentals=None) -> str:
    fundamentals   = fundamentals or {}
    display_tickers = focus if focus else sorted(stock_data.keys())
    phase_label    = "deep monitoring" if focus else "exploration — all stocks being scored"

    if not display_tickers:
        return f"""<div class="section" id="watchlist">
  <h2>Watchlist <span>Waiting for first data fetch</span></h2>
  <div class="card" style="padding:20px;text-align:center;color:var(--muted);font-size:.8rem">
    The agent has not fetched any stock data yet. Check back after the next scheduled run.
  </div>
</div>"""

    cards = ""
    for ticker in display_tickers:
        entry  = stock_data.get(ticker, {})
        d      = entry.get("latest", {})
        news   = news_data.get(ticker, {}).get("latest", {})
        ns     = news.get("score", 0)
        trend  = entry.get("trend_10d", "?")
        rsi    = d.get("rsi", 50)
        macd_h = d.get("macd_hist", 0)
        atr_p  = d.get("atr_pct", 2)
        vol_r  = d.get("vol_rel", 1)
        close  = d.get("close", 0)
        short  = ticker.replace(".NS", "")
        ph     = entry.get("price_history_60d", [])
        t_cls  = {"strong_up": "green", "up": "green", "sideways": "yellow",
                  "down": "red", "strong_down": "red"}.get(trend, "muted")
        m_cls  = "green" if macd_h > 0 else "red"
        tk_pat = patterns.get(ticker, {})
        style  = tk_pat.get("preferred_style", "learning")

        hl = news.get("headlines", [])
        news_html = (
            f'<div class="note-line" style="margin-top:5px;font-size:.7rem">{hl[0][:70]}&#8230;</div>'
            if hl else ""
        )

        spark_svg     = _sparkline(ph) if ph else ""
        rsi_bar_html  = _rsi_bar(rsi)
        is_focus      = ticker in focus
        intraday_svg  = _intraday_chart_svg(entry.get("intraday_candles", []))

        expl_score = 0
        if trend == "strong_up":   expl_score += 3
        elif trend == "up":        expl_score += 2
        if 45 < rsi < 65:          expl_score += 2
        if macd_h > 0:             expl_score += 1
        if vol_r >= 1.3:           expl_score += 2
        expl_score  = min(expl_score, 8)
        expl_pct    = expl_score / 8 * 100
        expl_color  = "#22c55e" if expl_score >= 6 else ("#eab308" if expl_score >= 3 else "#5a5a72")

        focus_badge = '<span class="badge badge-cyan" style="font-size:.6rem">FOCUS</span>' if is_focus else ""
        style_badge = f'<span class="pill pill-blue" style="margin-left:3px;font-size:.6rem">{style}</span>' if is_focus else ""
        ns_cls      = "pill-green" if ns > 0.08 else ("pill-red" if ns < -0.08 else "pill-yellow")

        cards += (
            f'<div class="stock-card">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
            f'<div style="display:flex;align-items:center;gap:4px;flex-wrap:wrap">'
            f'<strong style="font-size:.9rem">{short}</strong>{focus_badge}{style_badge}'
            f'</div>'
            f'<span class="pill {ns_cls}" style="font-size:.6rem">news {ns:+.2f}</span>'
            f'</div>'
            f'<div style="font-size:1.05rem;font-weight:700;margin-bottom:3px">'
            f'&#8377;{close:,.2f}'
            f'<span class="{t_cls}" style="font-size:.72rem;font-weight:400"> {trend.replace("_"," ")}</span>'
            f'</div>'
            f'<div style="margin:5px 0;overflow:hidden">{intraday_svg if intraday_svg else spark_svg}</div>'
            f'{rsi_bar_html}'
            f'<div style="margin-top:5px">'
            f'<div style="display:flex;justify-content:space-between;font-size:.62rem;color:var(--muted);margin-bottom:2px">'
            f'<span>Momentum score</span><span style="color:{expl_color}">{expl_score}/8</span></div>'
            f'<div style="height:3px;background:var(--border);border-radius:2px">'
            f'<div style="width:{expl_pct:.0f}%;height:100%;background:{expl_color};border-radius:2px"></div>'
            f'</div></div>'
            f'<div class="irow" style="margin-top:5px"><span class="muted">MACD hist</span>'
            f'<span class="{m_cls}">{macd_h:+.4f}</span></div>'
            f'<div class="irow"><span class="muted">ATR%</span><span>{atr_p:.2f}%</span></div>'
            f'<div class="irow"><span class="muted">Rel Volume</span>'
            f'<span class="{"green" if vol_r>=1.3 else "muted"}">{vol_r:.2f}x</span></div>'
            f'{news_html}'
            f'{_fund_table(fundamentals.get(ticker, {})) if is_focus else ""}'
            f'</div>'
        )

    return f"""<div class="section" id="watchlist">
  <h2>Watchlist <span>{len(display_tickers)} stocks &mdash; {phase_label}</span></h2>
  <div class="grid3">{cards}</div>
</div>"""


def _section_learning_progress(decisions: list) -> str:
    """What the tool has LEARNED so far, from forward-tested reality: dry-call
    scoreboard (with the self-throttle state), and per-stock news reliability.
    This answers 'how is the tool doing?' from the dashboard itself, honestly —
    including when the answer is 'its calls have been mostly wrong lately'."""
    from collections import Counter
    scored = [d for d in (decisions or [])
              if d.get("action") == "ANALYSE" and d.get("dry_outcome") in ("win", "loss", "flat")]
    counts = Counter(d["dry_outcome"] for d in scored)
    wins, losses, flats = counts.get("win", 0), counts.get("loss", 0), counts.get("flat", 0)
    n_wl = wins + losses
    recent = [d for d in scored if d["dry_outcome"] in ("win", "loss")][-20:]
    r_n = len(recent)
    r_acc = (sum(1 for d in recent if d["dry_outcome"] == "win") / r_n * 100) if r_n else None

    if r_acc is None:
        throttle = "not enough scored calls yet"
        acc_col  = "var(--muted)"
    elif r_n >= 10 and r_acc < 25:
        throttle = "ACTIVE (strong) — recent calls mostly wrong, conviction bar raised +1.5"
        acc_col  = "var(--red)"
    elif r_n >= 10 and r_acc < 35:
        throttle = "ACTIVE — recent accuracy weak, conviction bar raised +1.0"
        acc_col  = "var(--amber,#e6a93a)"
    else:
        throttle = "off — recent form acceptable"
        acc_col  = "var(--green)"

    news_html = ""
    try:
        import json as _json
        nc = _json.load(open("brain/news_calls.json", encoding="utf-8"))
        rel = nc.get("reliability", {})
        if rel:
            rows = ""
            for t, v in sorted(rel.items(), key=lambda x: -x[1].get("reliability", 0.5)):
                r = v.get("reliability", 0.5)
                col = "var(--green)" if r >= 0.55 else ("var(--red)" if r <= 0.4 else "var(--text)")
                rows += (f'<div class="irow"><span class="muted">{t.replace(".NS","")}</span>'
                         f'<span style="color:{col}">{r:.2f}</span></div>')
            oc = Counter(c.get("outcome") for c in nc.get("calls", []) if c.get("evaluated"))
            news_html = f"""<div class="card" style="margin-top:10px;padding:12px 14px">
  <h3 style="margin-bottom:6px">Is the news actually predictive, per stock?</h3>
  <div style="font-size:.66rem;color:var(--muted);margin-bottom:8px">
    Each strong news signal is checked against the real move 3 days later —
    right {oc.get('right',0)} · wrong {oc.get('wrong',0)} · flat {oc.get('flat',0)}.
    Score &gt;0.55 = news earns extra weight here; &lt;0.40 = news is faded.
  </div>
  {rows}
</div>"""
    except Exception:
        pass

    return f"""<div class="section" id="learning">
  <h2>Learning Progress <span>forward-tested against reality — no self-grading</span></h2>
  <div class="grid4" style="margin-bottom:10px">
    <div class="stat-card"><div class="stat-val">{wins}W / {losses}L</div><div class="stat-label">Dry calls scored ({flats} flat, excluded)</div></div>
    <div class="stat-card"><div class="stat-val" style="color:{acc_col}">{f"{r_acc:.0f}%" if r_acc is not None else "—"}</div><div class="stat-label">Recent call accuracy (last {r_n})</div></div>
    <div class="stat-card"><div class="stat-val" style="font-size:.72rem;line-height:1.3;color:{acc_col}">{throttle}</div><div class="stat-label">Self-accuracy throttle</div></div>
    <div class="stat-card"><div class="stat-val">{n_wl + flats}</div><div class="stat-label">Total calls forward-tested</div></div>
  </div>
  <div style="font-size:.66rem;color:var(--muted)">
    Every daily call ("I would BUY here") is scored days later against the real price.
    Wrong calls lower the involved patterns' reliability; the throttle makes the tool
    demand extra conviction whenever its own recent form is poor.
  </div>
  {news_html}
</div>"""


_MY_TRADES_WORKFLOW = ("https://github.com/deepkhara09-arch/nse-ai-trader/"
                       "actions/workflows/my_trade.yml")


def _section_my_positions(my_positions: dict, focus: list = None, recs: list = None) -> str:
    """The USER's real positions, managed live: entry, P&L, trailed stop, a target
    that tracks the tool's current view, HOLD / EXIT NOW / CONSIDER EXIT status,
    a closed-trade record, AND an on-dashboard 'add a trade' form (focus stocks
    only) that opens the GitHub trigger pre-filled."""
    opens  = my_positions.get("open", [])
    closed = my_positions.get("closed", [])

    # ── Add-a-trade form (focus stocks only) — always shown ────────────────────
    # A static page can't save data, so 'Log it' opens the My Trades workflow with
    # your inputs shown to paste — one tap, then Run. Focus stocks only, since the
    # tool only manages what it has a live view on.
    focus = focus or []
    rec_tk = {r.get("ticker") for r in (recs or [])}
    opts = ""
    for t in focus:
        code = t.replace(".NS", "")
        star = " ★ recommended" if t in rec_tk else ""
        opts += f'<option value="{code}">{code}{star}</option>'
    add_form = f"""<div class="card" style="margin-bottom:12px;padding:12px 14px">
  <div style="font-weight:700;font-size:.82rem;margin-bottom:8px">&#10133; Add a trade you took</div>
  <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:end">
    <label style="font-size:.66rem;color:var(--muted)">Stock (focus)<br>
      <select id="mt_ticker" style="padding:5px;border-radius:5px;min-width:140px">{opts}</select></label>
    <label style="font-size:.66rem;color:var(--muted)">Avg buy price<br>
      <input id="mt_price" type="number" step="0.05" placeholder="1028.50" style="padding:5px;border-radius:5px;width:110px"></label>
    <label style="font-size:.66rem;color:var(--muted)">Quantity<br>
      <input id="mt_qty" type="number" step="1" placeholder="8" style="padding:5px;border-radius:5px;width:80px"></label>
    <button onclick="logMyTrade('bought')" style="padding:7px 14px;border-radius:6px;background:var(--green,#3ecf8e);color:#04140b;font-weight:700;border:none;cursor:pointer">Log buy</button>
  </div>
  <div id="mt_out" style="font-size:.68rem;color:var(--muted);margin-top:8px;line-height:1.5"></div>
  <script>
  function logMyTrade(action) {{
    var t=document.getElementById('mt_ticker').value;
    var p=document.getElementById('mt_price').value;
    var q=document.getElementById('mt_qty').value;
    var out=document.getElementById('mt_out');
    if(!t||!p||!q){{out.innerHTML='Fill stock, price and quantity first.';return;}}
    var body='{{"action":"'+action+'","ticker":"'+t+'","price":"'+p+'","qty":"'+q+'"}}';
    navigator.clipboard&&navigator.clipboard.writeText(body);
    out.innerHTML='<b style="color:var(--green,#3ecf8e)">Ready:</b> '+action+' '+q+'x '+t+' @ '+p+
      '<br>1) opening the <b>My Trades</b> workflow &#8594; click <b>Run workflow</b>, '+
      'set the fields the same (values copied to clipboard).';
    window.open('{_MY_TRADES_WORKFLOW}','_blank');
  }}
  </script>
</div>"""

    if not opens and not closed:
        return f"""<div class="section" id="my-positions">
  <h2>My Positions <span>your real trades — managed live by the tool</span></h2>
  {add_form}
  <div style="font-size:.66rem;color:var(--muted)">No positions yet. Add one above once you buy a focus stock — the tool will then trail your stop, keep your target current, and flag when to exit.</div>
</div>"""

    rows = ""
    for p in opens:
        sig  = p.get("exit_signal", "")
        view = p.get("tool_view", "hold")
        upnl = p.get("unrealized_pnl", 0.0)
        pcol = "var(--green)" if upnl >= 0 else "var(--red)"
        if sig == "target_hit":
            status = ('<span class="badge badge-green" style="font-weight:700">EXIT NOW — TARGET HIT &#127919;</span>')
        elif sig == "stop_hit":
            status = ('<span class="badge badge-red" style="font-weight:700">EXIT NOW — STOP HIT</span>')
        elif view == "reversed":
            status = ('<span class="badge" style="font-weight:700;background:#2a1205;color:#e6a93a;border:1px solid #e6a93a">CONSIDER EXIT — tool flipped view</span>')
        elif view == "dropped":
            status = ('<span class="badge badge-gray">HOLD — tool no longer rates this setup</span>')
        else:
            status = '<span class="badge badge-green">HOLD — plan on track</span>'
        trailed = " <span style='color:var(--green);font-size:.6rem'>(trailed up)</span>" if p.get("trailing_active") else ""
        unknown = ("<div style='color:var(--amber,#e6a93a);font-size:.62rem;margin-top:2px'>"
                   "&#9888; not in the tool's universe — prices not managed live</div>"
                   if not p.get("known_stock", True) else "")
        exit_line = ""
        if sig or view == "reversed":
            exit_line = ('<div style="font-size:.66rem;color:#e6a93a;margin-top:4px">'
                         'To close: use the form above with <b>Log sell</b> (or the workflow) so the tool books your P&amp;L and learns from it.</div>')
        rows += f"""<div class="card" style="margin-bottom:9px;padding:11px 13px">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">
    <div style="font-weight:700">{p.get('action','BUY')} {p.get('qty','?')}x {p.get('ticker','').replace('.NS','')}
      <span style="color:var(--muted);font-weight:400;font-size:.7rem"> since {p.get('open_date','')}</span></div>
    {status}
  </div>
  <div style="font-size:.74rem;margin-top:6px;color:var(--text)">
    Your avg &#8377;{p.get('entry',0):,.2f} &middot; now &#8377;{p.get('current_price',0):,.2f}
    &middot; P&amp;L <b style="color:{pcol}">&#8377;{upnl:+,.0f}</b>
  </div>
  <div style="font-size:.72rem;margin-top:3px;color:var(--muted)">
    Stop <b style="color:var(--text)">&#8377;{p.get('stop_loss',0):,.2f}</b>{trailed}
    &middot; Target <b style="color:var(--text)">&#8377;{p.get('target',0):,.2f}</b> (tracks the tool's view)
  </div>
  {exit_line}{unknown}
</div>"""

    closed_html = ""
    if closed:
        recent = list(reversed(closed[-5:]))
        items = " &middot; ".join(
            f"{t.get('ticker','').replace('.NS','')} "
            f"<b style='color:{'var(--green)' if t.get('pnl',0)>=0 else 'var(--red)'}'>"
            f"&#8377;{t.get('pnl',0):+,.0f}</b>"
            for t in recent
        )
        w = sum(1 for t in closed if t.get("pnl", 0) > 0)
        closed_html = (f'<div style="font-size:.68rem;color:var(--muted);margin-top:8px">'
                       f'Your closed trades: {w}/{len(closed)} wins &middot; recent: {items}</div>')

    return f"""<div class="section" id="my-positions">
  <h2>My Positions <span>your real trades — managed live by the tool</span></h2>
  {add_form}
  {rows}
  {closed_html}
  <div style="font-size:.63rem;color:var(--muted);margin-top:8px">
    The tool trails your stop up each session, keeps your target aligned with its
    current view, and flags EXIT NOW / CONSIDER EXIT — it never closes your trade
    itself; you confirm by logging the sell.
  </div>
</div>"""


def _section_recommendations(recs, validated: bool = False, stats: dict = None,
                             decisions: list = None, book: dict = None) -> str:
    # Honesty: until the strategy passes its paper-trade validation gate
    # (alert_sent / `validated`), any setups shown are the tool PRACTICING — not
    # proven calls. Frame them as such so a user never mistakes a pre-validation
    # candidate for a validated, track-record-backed recommendation.
    stats = stats or {}

    # ── Track record of PAST recommendations (forward-tested vs real price) ────
    # RECOMMEND decisions are snapshotted daily at preclose and scored days later:
    # win = target level reached first, loss = stop level breached first (or the
    # move went against the call). Flat drifts are excluded — they prove nothing.
    rec_hist = [d for d in (decisions or [])
                if d.get("action") == "RECOMMEND" and d.get("dry_outcome") in ("win", "loss")]
    rr_wins  = sum(1 for d in rec_hist if d["dry_outcome"] == "win")
    rr_total = len(rec_hist)
    if rr_total >= 3:
        rr_pct = rr_wins / rr_total * 100
        rr_col = "var(--green)" if rr_pct >= 55 else ("var(--amber,#e6a93a)" if rr_pct >= 45 else "var(--red)")
        track_html = (
            '<div style="font-size:.7rem;color:var(--muted);margin-bottom:10px">'
            f'Past recommendations, forward-tested against the real price: '
            f'<b style="color:{rr_col}">{rr_wins}/{rr_total} worked ({rr_pct:.0f}%)</b>'
            ' &middot; win = target hit first, loss = stop hit first</div>'
        )
    else:
        track_html = ""
    if validated:
        section_sub = "High-confidence setups with full trade details"
        practice_banner = ""
    else:
        n = stats.get("total", 0)
        section_sub = "Practice setups — strategy not yet validated"
        practice_banner = (
            '<div style="background:#1a1605;border:1px solid #e6a93a55;border-radius:8px;'
            'padding:9px 12px;margin-bottom:12px;font-size:.72rem;color:#e6a93a;line-height:1.5">'
            '<b>⚠ Not yet validated — these are practice calls.</b> The tool is still '
            f'paper-trading to prove its edge ({n}/{MIN_TRADES_FOR_SIGNAL} trades toward a '
            f'&#8805;{WIN_RATE_THRESHOLD*100:.0f}% win-rate gate). Setups below show what it '
            '<i>would</i> do, for transparency — do not treat them as proven recommendations yet.'
            '</div>'
        )
    if not recs:
        no_recs = f"""<div class="card" style="padding:20px;text-align:center">
  <div style="font-size:.8rem;color:var(--muted);line-height:2">
    <div style="font-size:1.6rem;margin-bottom:8px">&#128300;</div>
    <div style="font-weight:700;color:var(--text);margin-bottom:6px">Agent is still learning</div>
    <div>No recommendations yet. The agent needs to:<br>
    1. Complete {EXPLORATION_DAYS}-day exploration &#8594; select {FOCUS_STOCK_COUNT} focus stocks<br>
    2. Complete {ANALYSIS_DAYS}-day deep analysis on focus stocks<br>
    3. Validate its strategy with {MIN_TRADES_FOR_SIGNAL}+ paper trades at a &#8805;{WIN_RATE_THRESHOLD*100:.0f}% win rate<br>
    4. Find a setup scoring &#8805;65/100 with R:R &#8805; 2:1, a clear stop loss and target<br>
    <br>Check the Watchlist to see which stocks are being scored right now.</div>
  </div>
</div>"""
        return f"""<div class="section" id="recommendations">
  <h2>Stock Recommendations <span>{section_sub}</span></h2>
  {track_html}
  {no_recs}
</div>"""

    # Persistence per ticker: how many DISTINCT days it has appeared on the rec
    # list (from the daily RECOMMEND snapshots). A setup that survives several
    # sessions is structurally stronger than a one-day flash — this is the main
    # signal a user should use to pick which recommendation to act on.
    persist_days: dict = {}
    for d_ in (decisions or []):
        if d_.get("action") == "RECOMMEND" and d_.get("ticker"):
            persist_days.setdefault(d_["ticker"], set()).add(d_.get("date"))
    # Live paper position per ticker: when the tool itself HOLDS this stock, show
    # its actual position management (entry, trailed stop, target) so a user who
    # followed the rec can mirror the same stop/target updates.
    held = {p.get("ticker"): p for p in (book or {}).get("open_positions", [])}

    cards = ""
    for rec in recs:
        signal = rec["signal"]
        cls    = "rec-buy" if signal == "BUY" else "rec-sell"

        n_persist = len(persist_days.get(rec.get("ticker"), set()) | {None}) - 1
        persist_html = ""
        if n_persist >= 2:
            persist_html = (f'<span class="badge badge-green" style="margin-left:6px;font-size:.6rem" '
                            f'title="This setup has stayed on the list {n_persist} trading days — persistent, not a one-day flash">'
                            f'on the list {n_persist} days</span>')
        elif n_persist == 1:
            persist_html = ('<span class="badge badge-gray" style="margin-left:6px;font-size:.6rem" '
                            'title="First day on the list — watch if it persists">new today</span>')

        pos = held.get(rec.get("ticker"))
        held_html = ""
        if pos:
            held_html = (
                '<div style="background:#0a1a10;border:1px solid #3ecf8e55;border-radius:8px;'
                'padding:8px 11px;margin:8px 0;font-size:.72rem;color:#3ecf8e;line-height:1.5">'
                f'<b>&#9679; The tool is holding this</b> — {pos.get("action","BUY")} '
                f'{pos.get("qty","?")}x @ &#8377;{pos.get("entry",0):,.2f} since {pos.get("open_date","?")}'
                f'<br>Live management: stop <b>&#8377;{pos.get("stop_loss",0):,.2f}</b>'
                f'{" (trailed up)" if pos.get("trailing_active") else ""} &middot; '
                f'target <b>&#8377;{pos.get("target",0):,.2f}</b> &middot; style {pos.get("style","swing")}'
                '<br>If you followed this call, mirror these stop/target updates.</div>'
            )
        rr1    = rec.get("rr_target1", 0)
        rr2    = rec.get("rr_target2", 0)
        conf   = rec.get("confidence", 0)
        conf_c = "green" if conf >= 70 else "yellow"
        pt_wr  = rec.get("paper_win_rate", 0)
        pt_n   = rec.get("paper_trades_on_stock", 0)
        pt_exp = rec.get("paper_expectancy", 0)

        reasons_html = "".join(f"<li>{r}</li>" for r in rec.get("reasons", [])[:6])
        pat_badges   = "".join(
            f'<span class="badge badge-blue" style="margin:2px;font-size:.6rem">{p.replace("_"," ")}</span>'
            for p in rec.get("reliable_patterns", [])[:5]
        )
        warn_html = "".join(
            f'<div class="warn-banner" style="margin-top:7px;font-size:.74rem">&#9888; {w}</div>'
            for w in rec.get("market_warning", [])
        )

        tech_score  = rec.get("tech_score", rec.get("buy_score", rec.get("sell_score", 0)))
        fund_score  = rec.get("fund_score", rec.get("fundamental_score", 0))
        news_score_val = rec.get("news_score", rec.get("news_score_display", 0))
        pat_score   = rec.get("pattern_score", 0)
        score_breakdown = (
            _score_bar("Technical",   min(tech_score,  40), 40, "#6366f1") +
            _score_bar("Fundamental", min(fund_score,  30), 30, "#22c55e") +
            _score_bar("News",        min(news_score_val, 20), 20, "#eab308") +
            _score_bar("Pattern",     min(pat_score,   10), 10, "#06b6d4")
        )

        focus_rank   = rec.get("focus_rank", 0)
        rank_delta   = rec.get("rank_delta", 0)
        success_prob = rec.get("success_probability", 0)
        profit_prob  = rec.get("profit_probability", 0)
        composite_sc = rec.get("composite_score", 0)

        if rank_delta > 0:
            delta_html = f'<span style="color:var(--bull);font-size:.75rem">▲{rank_delta}</span>'
        elif rank_delta < 0:
            delta_html = f'<span style="color:var(--bear);font-size:.75rem">▼{abs(rank_delta)}</span>'
        else:
            delta_html = '<span style="color:var(--muted);font-size:.75rem">—</span>'

        sp_pct = round(success_prob * 100)
        pp_pct = round(max(0, profit_prob) * 100)

        sig_badge_cls = "badge-green" if signal == "BUY" else "badge-red"
        entry_low  = rec.get("entry_low",  0)
        entry_high = rec.get("entry_high", 0)
        stop_loss  = rec.get("stop_loss",  0)
        target1    = rec.get("target1",    0)
        target2    = rec.get("target2",    0)
        risk_pct   = rec.get("risk_pct",   0)
        qty        = rec.get("recommended_qty", 0)
        max_loss   = rec.get("max_loss_if_sl",  0)
        cap_needed = rec.get("capital_needed",  0)
        support    = rec.get("nearest_support",    0)
        resistance = rec.get("nearest_resistance", 0)
        cmp        = rec.get("cmp", 0)
        is_stale      = rec.get("is_stale", False)
        stale_reason  = rec.get("stale_reason", "")
        generated_at  = rec.get("generated_at", "")
        valid_until   = rec.get("valid_until", "")

        trade_type     = rec.get("trade_type", "Short-term / Swing")
        trade_type_key = rec.get("trade_type_key", "short_term")
        entry_window   = rec.get("entry_window", "")
        exit_window    = rec.get("exit_window", "")
        tgt_timeframe  = rec.get("target_timeframe", "")
        action_urgency = rec.get("action_urgency", "")
        is_intraday    = trade_type_key == "intraday"
        timing_cls     = "urgency-intraday" if is_intraday else "urgency-delivery"
        # Distinct colour per horizon: intraday=orange, short=cyan, long=purple/green
        trade_type_badge_cls = {
            "intraday":   "badge-orange",
            "short_term": "badge-cyan",
            "long_term":  "badge-green",
        }.get(trade_type_key, "badge-cyan")

        # Explicit direction + plain-English headline + intention
        direction      = rec.get("direction", "BUY (go long)" if signal == "BUY" else "SELL / SHORT (go short)")
        headline       = rec.get("headline", f"{signal} {rec.get('nse_code','')}")
        intention      = rec.get("intention", "")
        # Data confidence on the probability
        data_conf      = rec.get("data_confidence", "Estimated")
        data_conf_key  = rec.get("data_confidence_key", "estimated")
        data_conf_note = rec.get("data_confidence_note", "")
        conf_badge_cls = {
            "validated": "badge-green", "forming": "badge-orange", "estimated": "badge-gray",
        }.get(data_conf_key, "badge-gray")
        # Confluence + backtest + regime context
        confluence_n   = rec.get("confluence_count", 0)
        bt_tested      = rec.get("backtest_tested", False)
        bt_hit_5d      = rec.get("backtest_hit_5d")
        bt_sample      = rec.get("backtest_sample", 0)
        hist_trend     = rec.get("hist_long_trend") or ""
        hist_52w       = rec.get("hist_52w_position")
        hist_pers      = rec.get("hist_personality") or ""

        stale_banner = ""
        if is_stale:
            stale_banner = f"""<div style="background:#1f0a0a;border:1px solid #dc262640;border-radius:6px;padding:7px 11px;margin-bottom:8px;display:flex;align-items:center;gap:8px">
  <span style="font-size:.85rem">⚠</span>
  <div>
    <div style="font-size:.7rem;font-weight:700;color:#f87171">STALE — Price Has Moved</div>
    <div style="font-size:.64rem;color:#cc7777;margin-top:1px">{stale_reason}. Wait for the next session update.</div>
  </div>
</div>"""

        validity_line = ""
        if generated_at or valid_until:
            validity_line = f'<div style="font-size:.59rem;color:var(--muted);margin-top:2px">Generated {generated_at} &nbsp;·&nbsp; Valid until <strong style="color:#22d3ee">{valid_until}</strong></div>'

        timing_box = ""
        if entry_window or exit_window or tgt_timeframe:
            timing_box = f"""<div class="timing-box {timing_cls}">
  <div style="font-size:.6rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">Timing Guide</div>
  {'<div class="timing-row"><span class="timing-icon">🕐</span><div><div class="timing-label">When to Enter</div><div class="timing-val">' + entry_window + '</div></div></div>' if entry_window else ""}
  {'<div class="timing-row"><span class="timing-icon">🎯</span><div><div class="timing-label">Target Timeframe</div><div class="timing-val">' + tgt_timeframe + '</div></div></div>' if tgt_timeframe else ""}
  {'<div class="timing-row"><span class="timing-icon">🚪</span><div><div class="timing-label">Exit / Monitoring</div><div class="timing-val">' + exit_window + '</div></div></div>' if exit_window else ""}
  {'<div style="margin-top:6px;padding:5px 8px;border-radius:4px;background:var(--card2);font-size:.67rem;font-weight:600;color:' + ('#fb923c' if is_intraday else '#9ca3af') + '">' + action_urgency + '</div>' if action_urgency else ""}
</div>"""

        # ── READY-TO-ACT: one unmistakable aggregate state per rec ─────────────
        # The user's workflow is "watch fresh recs, act when the tool is
        # confident". Instead of mentally combining five signals, one state:
        # every gate green -> READY; otherwise name exactly what's missing.
        _gates = [
            ("strategy validated",       validated),
            ("setup persisted 2+ days",  n_persist >= 2),
            ("price still in entry zone", not is_stale),
            ("confidence 70+",           conf >= 70),
        ]
        _missing = [g for g, ok in _gates if not ok]
        if not _missing:
            ready_html = ('<div style="background:#0a2416;border:1px solid #3ecf8e;border-radius:8px;'
                          'padding:10px 13px;margin:8px 0;font-size:.82rem;font-weight:700;color:#3ecf8e">'
                          '&#9889; READY TO ACT — all gates green: validated strategy · persistent setup · '
                          'fresh price · high confidence</div>')
        else:
            ready_html = ('<div style="background:var(--card2);border:1px solid var(--border);border-radius:8px;'
                          'padding:8px 12px;margin:8px 0;font-size:.7rem;color:var(--muted)">'
                          f'<b>WATCH</b> — waiting on: {" · ".join(_missing)}</div>')

        cards += f"""<div class="rec-card {cls}">
  <div class="rec-header">
    <div>
      <div class="rec-name">{rec.get('company_name','')}</div>
      <div class="rec-code">NSE: <strong>{rec.get('nse_code','')}</strong> &middot; {rec.get('date','')}</div>
      {validity_line}
    </div>
    <div style="display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end;align-items:flex-start">
      <span class="badge {sig_badge_cls}">{signal}</span>
      <span class="badge {trade_type_badge_cls}">{trade_type}</span>
      {persist_html}
      {f'<span class="badge" style="background:#0e1624;color:#7eb3ff;border:1px solid #1e3050">#{focus_rank} {delta_html}</span>' if focus_rank else ""}
      {'<span class="badge badge-red">STALE</span>' if is_stale else ""}
    </div>
  </div>

  {held_html}
  {ready_html}

  <div style="margin:6px 0 9px;padding:9px 11px;border-radius:6px;background:var(--card2);border:1px solid var(--border)">
    <div style="font-size:.8rem;font-weight:700;color:{'#4ade80' if signal=='BUY' else '#f87171'};line-height:1.4">
      {intention or headline}
    </div>
    <div style="font-size:.62rem;font-weight:400;color:var(--muted);margin-top:4px">
      Direction: <strong style="color:{'#4ade80' if signal=='BUY' else '#f87171'}">{direction}</strong>
      &nbsp;·&nbsp; Horizon: <strong style="color:var(--text)">{trade_type}</strong>
      {f'&nbsp;·&nbsp; Confluence: <strong style="color:#22d3ee">{confluence_n}/7 agree</strong>' if confluence_n else ''}
    </div>
    {f'<div style="font-size:.6rem;color:var(--muted);margin-top:3px">2yr context: <strong style="color:var(--text)">{hist_trend.replace("_"," ")}</strong>' + (f" · {hist_52w:.0f}% of 52w range" if hist_52w is not None else "") + (f" · {hist_pers}" if hist_pers else "") + '</div>' if hist_trend else ''}
    {f'<div style="font-size:.6rem;color:#4ade80;margin-top:3px">✓ Historical backtest: this setup worked {bt_hit_5d*100:.0f}% of the time (~1wk, {bt_sample} samples on this stock)</div>' if bt_tested and bt_hit_5d is not None else ''}
  </div>

  {stale_banner}
  {timing_box}

  <div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap">
    <div style="flex:1;min-width:110px;background:var(--card2);border-radius:6px;padding:7px 9px;border:1px solid var(--border)">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px">
        <span style="font-size:.58rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">Success %</span>
        <span class="badge {conf_badge_cls}" style="font-size:.5rem;padding:1px 4px" title="{data_conf_note}">{data_conf}</span>
      </div>
      <div style="font-size:1rem;font-weight:700;color:{'#4ade80' if sp_pct>=55 else '#fbbf24'}">{sp_pct}%</div>
      <div style="height:3px;background:var(--border);border-radius:2px;margin-top:4px"><div style="width:{sp_pct}%;height:3px;background:{'#4ade80' if sp_pct>=55 else '#fbbf24'};border-radius:2px"></div></div>
    </div>
    <div style="flex:1;min-width:110px;background:var(--card2);border-radius:6px;padding:7px 9px;border:1px solid var(--border)">
      <div style="font-size:.58rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Profit Edge</div>
      <div style="font-size:1rem;font-weight:700;color:{'#4ade80' if profit_prob>0 else '#f87171'}">{pp_pct}%</div>
      <div style="height:3px;background:var(--border);border-radius:2px;margin-top:4px"><div style="width:{pp_pct}%;height:3px;background:{'#4ade80' if profit_prob>0 else '#f87171'};border-radius:2px"></div></div>
    </div>
    <div style="flex:1;min-width:110px;background:var(--card2);border-radius:6px;padding:7px 9px;border:1px solid var(--border)">
      <div style="font-size:.58rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">AI Score</div>
      <div style="font-size:1rem;font-weight:700;color:#22d3ee">{composite_sc:.0f}/100</div>
      <div style="height:3px;background:var(--border);border-radius:2px;margin-top:4px"><div style="width:{min(100,composite_sc):.0f}%;height:3px;background:#22d3ee;border-radius:2px"></div></div>
    </div>
  </div>

  <div class="price-ladder">
    <div class="pl-row"><span class="pl-label">Current Price (CMP)</span><span class="pl-price">&#8377;{cmp:,.2f}</span></div>
    <div class="pl-row"><span class="pl-label">Entry Zone</span><span class="pl-price">&#8377;{entry_low:,.2f} &ndash; &#8377;{entry_high:,.2f}</span></div>
    <div class="pl-row"><span class="pl-label">Stop Loss ({risk_pct:.1f}%)</span><span class="pl-price red">&#8377;{stop_loss:,.2f}</span></div>
    <div class="pl-row"><span class="pl-label">Target 1 (R:R 1:{rr1:.1f})</span><span class="pl-price green">&#8377;{target1:,.2f}</span></div>
    <div class="pl-row"><span class="pl-label">Target 2 (R:R 1:{rr2:.1f})</span><span class="pl-price" style="color:var(--cyan)">&#8377;{target2:,.2f}</span></div>
  </div>

  <div class="rec-grid">
    <div class="rec-field">
      <div class="rec-field-val blue">{qty} shares</div>
      <div class="rec-field-lbl">Recommended Qty</div>
    </div>
    <div class="rec-field">
      <div class="rec-field-val">&#8377;{cap_needed:,.0f}</div>
      <div class="rec-field-lbl">Capital Needed</div>
    </div>
    <div class="rec-field">
      <div class="rec-field-val red">&#8377;{max_loss:,.0f}</div>
      <div class="rec-field-lbl">Max Loss (2% rule)</div>
    </div>
    <div class="rec-field">
      <div class="rec-field-val muted">&#8377;{support:,.2f}</div>
      <div class="rec-field-lbl">Nearest Support</div>
    </div>
    <div class="rec-field">
      <div class="rec-field-val muted">&#8377;{resistance:,.2f}</div>
      <div class="rec-field-lbl">Nearest Resistance</div>
    </div>
  </div>

  <div class="conf-bar">
    <div style="display:flex;justify-content:space-between;font-size:.68rem;margin-bottom:3px">
      <span class="muted">Confidence</span>
      <span class="{conf_c}">{conf:.0f}%</span>
    </div>
    <div class="progress">
      <div class="progress-fill" style="width:{conf:.0f}%;background:{'var(--green)' if conf>=70 else 'var(--yellow)'}"></div>
    </div>
  </div>

  <div style="margin-top:9px;padding:9px 11px;background:var(--card2);border-radius:8px">
    <div style="font-size:.6rem;color:var(--muted);margin-bottom:5px;text-transform:uppercase;letter-spacing:.05em">Score Breakdown</div>
    {score_breakdown}
  </div>

  {f'<div style="margin-top:7px">{pat_badges}</div>' if pat_badges else ""}

  <div class="reasons-list">
    <div style="font-size:.68rem;color:var(--muted);margin-bottom:3px;text-transform:uppercase;letter-spacing:.05em">Why this trade</div>
    <ul>{reasons_html}</ul>
  </div>

  {f'''<div style="margin-top:9px;background:var(--card2);border-radius:8px;padding:9px 11px;font-size:.76rem">
    <span class="muted">Paper record on {rec.get("nse_code","")}: </span>
    <span>{pt_n} trades</span> &middot;
    <span class="{"green" if pt_wr>=0.55 else "yellow"}">{pt_wr*100:.0f}% win rate</span> &middot;
    <span class="{"green" if pt_exp>0 else "red"}">&#8377;{pt_exp:+,.0f} expectancy</span>
  </div>''' if pt_n > 0 else ""}

  {warn_html}
  <div style="margin-top:9px;font-size:.66rem;color:var(--muted);border-top:1px solid var(--border);padding-top:7px">
    Paper trade recommendation only. Not financial advice. Always use your own judgement and consult a SEBI-registered advisor.
  </div>
</div>"""

    rec_word = "high-confidence setup(s)" if validated else "practice setup(s)"
    return f"""<div class="section" id="recommendations">
  <h2>Stock Recommendations <span>{len(recs)} {rec_word}</span></h2>
  {practice_banner}
  {track_html}
  {cards}
</div>"""


def _section_trades(book) -> str:
    trades = list(reversed(book.get("closed_trades", [])))
    if not trades:
        return f"""<div class="section" id="trades">
  <h2>Paper Trade History</h2>
  <div class="card" style="padding:20px;text-align:center;color:var(--muted);font-size:.8rem">
    Paper trades appear here once the agent enters the trading phase.
  </div>
</div>"""
    rows = ""
    for t in trades[:60]:
        pnl    = t.get("pnl", 0)
        action = t.get("action", "?")
        rows += (
            f'<tr>'
            f'<td class="muted">{t.get("close_date","?")}</td>'
            f'<td><strong>{t.get("ticker","?").replace(".NS","")}</strong></td>'
            f'<td><span class="pill {"pill-green" if action=="BUY" else "pill-red"}">{action}</span></td>'
            f'<td>&#8377;{t.get("entry",0):.2f}</td>'
            f'<td>&#8377;{t.get("exit_price",0):.2f}</td>'
            f'<td class="{"green" if pnl>=0 else "red"}">&#8377;{pnl:+.0f}</td>'
            f'<td class="{"green" if pnl>=0 else "red"}">{t.get("pnl_pct",0):+.1f}%</td>'
            f'<td><span class="pill {"pill-green" if t.get("won") else "pill-red"}">{"WIN" if t.get("won") else "LOSS"}</span></td>'
            f'<td class="muted" style="font-size:.7rem">{t.get("exit_reason","?").replace("_"," ")}</td>'
            f'<td><span class="pill pill-blue">{t.get("style","?")}</span></td>'
            f'<td class="muted">{t.get("open_date","?")}</td>'
            f'</tr>'
        )
    return f"""<div class="section" id="trades">
  <h2>Paper Trade History <span>{len(trades)} closed trades</span></h2>
  <div class="card table-wrap">
    <table>
      <thead><tr>
        <th>Date</th><th>Stock</th><th>Side</th><th>Entry</th><th>Exit</th>
        <th>P&amp;L</th><th>P&amp;L%</th><th>Result</th><th>Reason</th><th>Style</th><th>Opened</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>"""


def _section_research(state, decisions) -> str:
    notes = state.get("brain_notes", [])
    notes_html = "".join(
        f'<div class="note-line">'
        f'<strong>{n.split("]")[0].replace("[","").strip() if "]" in n else ""}</strong> '
        f'{n.split("]",1)[1].strip() if "]" in n else n}'
        f'</div>'
        for n in reversed(notes[-30:])
    ) or '<div class="muted" style="padding:8px;font-size:.8rem">No brain notes yet.</div>'

    recent_dec = [d for d in reversed(decisions[-50:]) if d.get("signal") in ("BUY","SELL")][:15]
    dec_rows   = ""
    for d in recent_dec:
        dec_rows += (
            f'<tr>'
            f'<td class="muted">{d.get("date","?")}</td>'
            f'<td>{d.get("ticker","").replace(".NS","")}</td>'
            f'<td>{d.get("session","?")}</td>'
            f'<td><span class="pill {"pill-green" if d["signal"]=="BUY" else "pill-red"}">{d["signal"]}</span></td>'
            f'<td>&#8377;{d.get("entry",0):.2f}</td>'
            f'<td class="muted" style="font-size:.7rem">{", ".join(d.get("patterns",[])[:3])}</td>'
            f'<td class="muted">{d.get("confidence",0):.0f}%</td>'
            f'</tr>'
        )

    dec_table = f"""<div class="card table-wrap" style="margin-top:11px">
  <h3 style="margin-bottom:9px">Recent Analyst Decisions</h3>
  <table>
    <thead><tr><th>Date</th><th>Stock</th><th>Session</th><th>Signal</th><th>Price</th><th>Patterns</th><th>Conf</th></tr></thead>
    <tbody>{dec_rows}</tbody>
  </table>
</div>""" if dec_rows else ""

    return f"""<div class="section" id="research">
  <h2>Research Log <span>Agent's autonomous notes and decisions</span></h2>
  <div class="card">{notes_html}</div>
  {dec_table}
</div>"""


def _section_brain(focus, patterns) -> str:
    if not patterns:
        return f"""<div class="section" id="brain">
  <h2>Brain Insights <span>What the agent has learned</span></h2>
  <div class="card" style="padding:20px;text-align:center;color:var(--muted);font-size:.8rem">
    Pattern learning begins after paper trades start resolving.
  </div>
</div>"""

    cards = ""
    for ticker in focus:
        tk = patterns.get(ticker, {})
        if not tk:
            continue
        rp    = tk.get("reliable_patterns", {})
        style = tk.get("preferred_style", "learning")
        sw    = tk.get("swing_wins", 0)
        sl_   = tk.get("swing_losses", 0)
        iw    = tk.get("intraday_wins", 0)
        il    = tk.get("intraday_losses", 0)

        sorted_pats = sorted(rp.items(), key=lambda x: x[1]["wins"] + x[1]["losses"], reverse=True)[:6]
        pat_rows    = ""
        for name, p in sorted_pats:
            total = p["wins"] + p["losses"]
            rel   = p["reliability"]
            r_cls = "green" if rel >= 0.55 else ("red" if rel <= 0.42 else "yellow")
            bar_w = int(rel * 100)
            bar_color = "var(--green)" if rel >= 0.55 else ("var(--red)" if rel <= 0.42 else "var(--yellow)")
            pat_rows += (
                f'<div style="margin-bottom:7px">'
                f'<div style="display:flex;justify-content:space-between;font-size:.72rem;margin-bottom:2px">'
                f'<span>{name.replace("_"," ")}</span>'
                f'<span class="{r_cls}">{rel:.0%} ({p["wins"]}W/{p["losses"]}L / {total})</span>'
                f'</div>'
                f'<div class="progress">'
                f'<div class="progress-fill" style="width:{bar_w}%;background:{bar_color}"></div>'
                f'</div></div>'
            )

        _no_pat_msg = '<div class="muted" style="font-size:.74rem">No resolved patterns yet.</div>'
        cards += (
            f'<div class="card">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:9px">'
            f'<strong>{ticker.replace(".NS","")}</strong>'
            f'<span class="pill pill-blue">{style}</span>'
            f'</div>'
            f'<div style="display:flex;gap:14px;font-size:.72rem;color:var(--muted);margin-bottom:9px">'
            f'<span>Swing: {sw}W/{sl_}L</span>'
            f'<span>Intraday: {iw}W/{il}L</span>'
            f'</div>'
            f'{pat_rows or _no_pat_msg}'
            f'</div>'
        )

    return f"""<div class="section" id="brain">
  <h2>Brain Insights <span>Learned pattern reliability per focus stock</span></h2>
  <div class="grid2">{cards}</div>
</div>"""


def _section_attribution(attribution: dict) -> str:
    if not attribution:
        return ""

    def _table(data: dict, label_col: str) -> str:
        if not data:
            return '<p style="color:var(--muted);font-size:.8rem">No data yet — needs more closed trades.</p>'
        rows = ""
        for key, v in data.items():
            wr   = v.get("win_rate", 0)
            tot  = v.get("total", 0)
            bar  = int(wr * 60)
            color = "#22c55e" if wr >= 0.60 else "#f59e0b" if wr >= 0.45 else "#ef4444"
            rows += (
                f'<tr>'
                f'<td style="color:var(--fg)">{key}</td>'
                f'<td style="text-align:center">{tot}</td>'
                f'<td style="text-align:center">{v.get("wins",0)}W / {v.get("losses",0)}L</td>'
                f'<td><div style="display:flex;align-items:center;gap:6px">'
                f'<div style="width:{bar}px;height:6px;border-radius:3px;background:{color}"></div>'
                f'<span style="color:{color};font-size:.75rem">{wr*100:.0f}%</span>'
                f'</div></td>'
                f'</tr>'
            )
        return (
            f'<table style="width:100%;border-collapse:collapse;font-size:.8rem">'
            f'<thead><tr style="color:var(--muted);font-size:.72rem">'
            f'<th style="text-align:left;padding:4px 0">{label_col}</th>'
            f'<th>Trades</th><th>W/L</th><th>Win Rate</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>'
        )

    by_pattern = attribution.get("by_pattern", {})
    by_session = attribution.get("by_session", {})
    by_mood    = attribution.get("by_mood",    {})
    by_style   = attribution.get("by_style",   {})

    return f"""<div class="section" id="attribution">
  <h2>Win Rate Attribution <span>Which setups, sessions &amp; conditions actually work</span></h2>
  <div class="grid2">
    <div class="card">
      <div class="card-title">By Pattern</div>
      {_table(by_pattern, "Pattern")}
    </div>
    <div class="card">
      <div class="card-title">By Session</div>
      {_table(by_session, "Session")}
      <div class="card-title" style="margin-top:14px">By Market Mood</div>
      {_table(by_mood, "Mood")}
    </div>
    <div class="card">
      <div class="card-title">By Style</div>
      {_table(by_style, "Style")}
    </div>
  </div>
</div>"""


def _section_coach(coach_memory: dict) -> str:
    if not coach_memory or (
        not coach_memory.get("recent_lessons") and
        not coach_memory.get("structural_suggestions")
    ):
        return f"""<div class="section" id="coach">
  <h2>Coach Insights <span>AI learning journal &mdash; powered by Gemini</span></h2>
  <div class="card" style="padding:20px;text-align:center;color:var(--muted);font-size:.8rem">
    <div style="font-size:1.4rem;margin-bottom:8px">&#129504;</div>
    <div style="font-weight:700;color:var(--text);margin-bottom:4px">Coach hasn't run yet</div>
    The coach reviews trades and learns at preclose each day.<br>
    Lessons will appear here after the first paper trades are closed.
  </div>
</div>"""

    last_run   = coach_memory.get("last_run", "")
    sug_date   = coach_memory.get("suggestions_date", "")
    lessons    = coach_memory.get("recent_lessons", [])
    suggests   = coach_memory.get("structural_suggestions", [])
    total_keys = len(coach_memory.get("lessons", {}))

    # ── Structural suggestions ────────────────────────────────────────────────
    sug_html = ""
    if suggests:
        items = "".join(
            f'<li style="margin-bottom:8px;line-height:1.5">'
            f'<span style="color:var(--cyan);margin-right:6px">&#10148;</span>{s}</li>'
            for s in suggests
        )
        sug_html = f"""<div class="card" style="margin-bottom:14px">
  <div style="font-size:.68rem;color:var(--muted);margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em">
    Structural suggestions &mdash; {sug_date or "recent"}
  </div>
  <ul style="margin:0;padding-left:4px;list-style:none;font-size:.78rem">{items}</ul>
</div>"""

    # ── Recent lessons ────────────────────────────────────────────────────────
    lessons_html = ""
    if lessons:
        rows = ""
        for l in lessons[:15]:
            ticker   = l.get("ticker", "").replace(".NS", "") or "General"
            setup    = l.get("setup_key", "").replace("_", " ")
            happened = l.get("what_happened", "")
            watch    = l.get("what_to_watch", "")
            conf     = l.get("confidence", "medium")
            source   = l.get("source", "")
            ldate    = l.get("date", "")
            conf_col = {"high": "var(--green)", "medium": "var(--yellow)", "low": "var(--muted)"}.get(conf, "var(--muted)")
            src_icon = "&#128196;" if source == "trade_review" else "&#10067;"   # doc or question mark

            rows += f"""<div class="card" style="margin-bottom:10px;padding:12px 14px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:4px">
    <div style="display:flex;align-items:center;gap:6px">
      <span style="font-size:.8rem">{src_icon}</span>
      <strong style="font-size:.82rem">{ticker}</strong>
      <span class="pill pill-blue" style="font-size:.58rem">{setup[:30]}</span>
    </div>
    <div style="display:flex;align-items:center;gap:6px">
      <span style="font-size:.62rem;color:{conf_col}">{conf} confidence</span>
      <span style="font-size:.6rem;color:var(--muted)">{ldate}</span>
    </div>
  </div>
  {f'<div style="font-size:.75rem;color:var(--text);margin-bottom:4px;line-height:1.4">{happened[:200]}</div>' if happened else ""}
  {f'<div style="font-size:.72rem;color:var(--cyan);line-height:1.4">&#9654; {watch[:150]}</div>' if watch else ""}
</div>"""

        lessons_html = f"""<div style="margin-bottom:8px">
  <div style="font-size:.68rem;color:var(--muted);margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em">
    Lessons learned &mdash; {len(lessons)} recent &mdash; {total_keys} patterns in memory
  </div>
  {rows}
</div>"""

    return f"""<div class="section" id="coach">
  <h2>Coach Insights
    <span>Gemini teaches the tool &mdash; last session {last_run or "never"}</span>
  </h2>
  {sug_html}
  {lessons_html}
</div>"""


def _gemini_status_html(coach_memory: dict) -> str:
    """Show whether the Gemini learning layer is actually working — so its status
    is visible on the dashboard, not buried in Actions logs."""
    gs = (coach_memory or {}).get("gemini_status")
    if not gs:
        return ('<div class="card" style="padding:10px 13px;margin-bottom:12px;font-size:.72rem;color:var(--muted)">'
                '<b>Gemini coach:</b> not yet run this cycle (runs at preclose).</div>')
    if not gs.get("key_present"):
        return ('<div class="card" style="padding:10px 13px;margin-bottom:12px;font-size:.72rem;'
                'border:1px solid #e6a93a55;color:#e6a93a">'
                '<b>Gemini coach: OFF</b> — GEMINI_API_KEY not set in the workflow. '
                'The free context-coach still learns; add the secret to enable LLM enrichment '
                '(regime narratives + answers to the tool&rsquo;s own questions).</div>')
    if gs.get("last_error"):
        return ('<div class="card" style="padding:10px 13px;margin-bottom:12px;font-size:.72rem;'
                f'border:1px solid #dc262655;color:#f87171"><b>Gemini coach: ERROR</b> — {gs.get("last_error")} '
                f'(checked {gs.get("checked","")}). Key present but not working — check quota / model access.</div>')
    return ('<div class="card" style="padding:10px 13px;margin-bottom:12px;font-size:.72rem;color:#4ade80">'
            f'<b>Gemini coach: ONLINE</b> — last verified {gs.get("last_ok","")}. Enriching lessons.</div>')


def _section_health(run_health: dict, coach_memory: dict = None) -> str:
    last_run = run_health.get("last_run", {})
    issues   = run_health.get("issues", [])
    counts   = run_health.get("counts", {})
    gemini_html = _gemini_status_html(coach_memory)

    last_ok      = last_run.get("ok", True)
    last_count   = last_run.get("issue_count", 0)
    last_session = last_run.get("session", "")
    last_when    = last_run.get("finished_at") or last_run.get("started_at") or ""

    # Overall status banner
    if not issues:
        status_html = """<div class="card" style="padding:16px;text-align:center">
  <div style="font-size:1.4rem;margin-bottom:6px">&#9989;</div>
  <div style="font-weight:700;color:#4ade80">All systems healthy</div>
  <div style="font-size:.72rem;color:var(--muted);margin-top:4px">
    No failures recorded. Every run completed cleanly.
  </div>
</div>"""
        return f"""<div class="section" id="health">
  <h2>System Health <span>tool self-diagnostics &mdash; failures are non-fatal</span></h2>
  {gemini_html}
  {status_html}
</div>"""

    last_col = "#4ade80" if last_ok else "#fb923c"
    last_txt = ("Last run clean" if last_ok
                else f"Last run had {last_count} issue(s)")
    banner = f"""<div class="card" style="padding:12px 14px;margin-bottom:12px">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">
    <div style="font-weight:700;color:{last_col}">
      {'&#9888;' if not last_ok else '&#9989;'} {last_txt}
    </div>
    <div style="font-size:.62rem;color:var(--muted)">{last_session} &middot; {last_when}</div>
  </div>
  <div style="font-size:.66rem;color:var(--muted);margin-top:6px">
    These are non-fatal — the tool kept running on last-known data. Share this
    list if something looks recurring so it can be fixed.
  </div>
</div>"""

    # Per-component summary (helps tell one-off vs recurring)
    comp_rows = ""
    for comp, c in sorted(counts.items(), key=lambda x: -x[1].get("total", 0)):
        total = c.get("total", 0)
        recur = "recurring" if total >= 3 else ("a few times" if total == 2 else "once")
        rcol  = "#f87171" if total >= 3 else ("#fbbf24" if total == 2 else "var(--muted)")
        comp_rows += (
            f'<tr><td><strong>{comp}</strong></td>'
            f'<td style="text-align:center">{total}</td>'
            f'<td style="color:{rcol}">{recur}</td>'
            f'<td class="muted" style="font-size:.66rem">{c.get("last_seen","")}</td></tr>'
        )
    summary_table = f"""<div class="card" style="margin-bottom:12px;padding:0;overflow:hidden">
  <table style="width:100%;border-collapse:collapse">
    <thead><tr style="font-size:.62rem;color:var(--muted)">
      <th style="text-align:left;padding:7px 10px">Component</th>
      <th style="padding:7px 10px">Total</th>
      <th style="text-align:left;padding:7px 10px">Frequency</th>
      <th style="text-align:left;padding:7px 10px">Last seen</th>
    </tr></thead>
    <tbody>{comp_rows}</tbody>
  </table>
</div>"""

    # Recent issue feed (most recent first)
    rows = ""
    for it in reversed(issues[-25:]):
        rows += (
            f'<tr>'
            f'<td class="muted" style="font-size:.64rem;white-space:nowrap">{it.get("ts","")}</td>'
            f'<td><span class="pill pill-yellow" style="font-size:.58rem">{it.get("component","")}</span></td>'
            f'<td style="font-size:.68rem">{it.get("detail","")}</td>'
            f'<td style="font-size:.66rem;color:var(--muted)">{it.get("message","")[:120]}</td>'
            f'</tr>'
        )
    feed = f"""<div class="card table-wrap" style="padding:0;overflow-x:auto">
  <table style="width:100%;border-collapse:collapse">
    <thead><tr style="font-size:.62rem;color:var(--muted)">
      <th style="text-align:left;padding:7px 10px">When</th>
      <th style="text-align:left;padding:7px 10px">Component</th>
      <th style="text-align:left;padding:7px 10px">Detail</th>
      <th style="text-align:left;padding:7px 10px">Message</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

    return f"""<div class="section" id="health">
  <h2>System Health <span>{len(issues)} recorded issue(s) &mdash; all non-fatal</span></h2>
  {gemini_html}
  {banner}
  {summary_table}
  {feed}
</div>"""


def _section_runlog(state: dict) -> str:
    import json as _json
    import os as _os
    from agent.config import DAILY_LOG_FILE

    log = []
    if _os.path.exists(DAILY_LOG_FILE):
        try:
            with open(DAILY_LOG_FILE) as f:
                log = _json.load(f)
            if not isinstance(log, list):
                log = []
        except Exception:
            log = []

    # Always render the section (so the nav anchor #runlog always works), even
    # when there are no runs yet.
    if not log:
        return """<div class="section" id="runlog">
  <h2>Run Log <span>Each trigger &mdash; time fired, session, what happened</span></h2>
  <div class="card" style="padding:18px;text-align:center;color:var(--muted);font-size:.8rem">
    No runs recorded yet. Each scheduled or manual trigger will appear here with
    the time it fired and a short summary of what it did.
  </div>
</div>"""

    sess_badge_map = {
        "preopen":   '<span class="pill pill-blue">Pre-open</span>',
        "morning":   '<span class="pill pill-green">Morning</span>',
        "midday":    '<span class="pill pill-blue">Midday</span>',
        "afternoon": '<span class="pill pill-cyan">Afternoon</span>',
        "preclose":  '<span class="pill pill-yellow">Preclose</span>',
        "test":      '<span class="pill pill-gray">Test</span>',
    }

    rows = ""
    for entry in reversed(log[-40:]):
        when_    = entry.get("triggered_at") or entry.get("date", "")
        sess_    = entry.get("session", "")
        summary_ = entry.get("summary", "")
        stats_   = entry.get("stats", {})
        pnl_     = stats_.get("total_pnl", 0)
        badge    = sess_badge_map.get(sess_, f'<span class="pill">{sess_}</span>')
        if not summary_:   # fallback summary for older log entries without one
            summary_ = (f"{entry.get('phase','')} day {entry.get('day','')} — "
                        f"{entry.get('open',0)} open, {stats_.get('total',0)} trades")
        rows += (
            f'<tr style="border-bottom:1px solid var(--border)">'
            f'<td style="padding:8px 6px;color:var(--muted);font-size:.72rem;white-space:nowrap">{when_}</td>'
            f'<td style="padding:8px 6px">{badge}</td>'
            f'<td style="padding:8px 6px;font-size:.76rem;color:var(--text);line-height:1.4">{summary_}</td>'
            f'<td style="padding:8px 6px;text-align:right;font-size:.76rem;white-space:nowrap;color:{"#22c55e" if pnl_>=0 else "#ef4444"}">'
            f'&#8377;{pnl_:+,.0f}</td>'
            f'</tr>'
        )

    last = log[-1]
    last_when = last.get("triggered_at", last.get("date", ""))
    return f"""<div class="section" id="runlog">
  <h2>Run Log <span>Each trigger &mdash; time fired, session, what happened</span></h2>
  <div class="card" style="padding:10px 14px;margin-bottom:10px;font-size:.74rem;color:var(--muted)">
    Last run: <strong style="color:var(--text)">{last_when}</strong>
    &middot; {len(log)} total runs logged. The dashboard refreshes at the end of every run.
  </div>
  <div class="card" style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:.8rem;min-width:480px">
      <thead><tr style="color:var(--muted);font-size:.72rem">
        <th style="text-align:left;padding:6px 6px">Time fired (IST)</th>
        <th style="text-align:left;padding:6px 6px">Session</th>
        <th style="text-align:left;padding:6px 6px">What happened</th>
        <th style="text-align:right;padding:6px 6px">P&amp;L</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>"""


# ─────────────────────────────────────────────────────────────────────────────
# JavaScript
# ─────────────────────────────────────────────────────────────────────────────

def _scripts() -> str:
    return """<script>
// ── Bottom-nav tab switching ──────────────────────────────────────────────
function switchTab(key) {
  document.querySelectorAll('.tab-panel').forEach(function(p) {
    p.hidden = (p.id !== 'tab-' + key);
  });
  document.querySelectorAll('.nt').forEach(function(b) {
    b.classList.toggle('nt-active', b.dataset.tab === key);
  });
  // remember last tab + scroll to top of content
  try { localStorage.setItem('nse_tab', key); } catch(e) {}
  window.scrollTo({top: 0, behavior: 'instant'});
}
// restore last-viewed tab on load (default: home)
(function() {
  var saved = 'home';
  try { saved = localStorage.getItem('nse_tab') || 'home'; } catch(e) {}
  if (saved !== 'home' && document.getElementById('tab-' + saved)) switchTab(saved);
})();

document.querySelectorAll('[data-spark]').forEach(function(el) {
  var vals = JSON.parse(el.dataset.spark || '[]');
  if (!vals.length) return;
  var mn = Math.min.apply(null, vals);
  var mx = Math.max.apply(null, vals);
  var rng = mx - mn || 1;
  var W = el.offsetWidth || 280;
  var H = parseInt(el.style.height) || 50;
  var pts = vals.map(function(v, i) {
    var x = (i / (vals.length - 1) * W).toFixed(1);
    var y = (H - ((v - mn) / rng) * (H - 4) - 2).toFixed(1);
    return x + ',' + y;
  }).join(' ');
  var col = el.dataset.color || (vals[vals.length - 1] >= vals[0] ? '#22c55e' : '#ef4444');
  var gid = 'g' + Math.random().toString(36).slice(2, 7);
  el.innerHTML =
    '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="width:100%;height:' + H + 'px;display:block">' +
    '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0%" stop-color="' + col + '" stop-opacity="0.22"/>' +
    '<stop offset="100%" stop-color="' + col + '" stop-opacity="0"/>' +
    '</linearGradient></defs>' +
    '<polygon points="' + pts + ' ' + W + ',' + H + ' 0,' + H + '" fill="url(#' + gid + ')"/>' +
    '<polyline points="' + pts + '" fill="none" stroke="' + col + '" stroke-width="1.8" stroke-linejoin="round"/>' +
    '</svg>';
});
</script>"""
