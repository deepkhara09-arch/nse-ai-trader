"""
Dashboard generator — writes docs/index.html served via GitHub Pages.
Completely self-contained HTML/CSS/JS — no external CDN dependencies.
Design: Minimal dark theme — Deep's NSE AI Tracker
"""

import json
import os
from datetime import datetime
from typing import Dict, List

from agent.config import DASHBOARD_FILE, INITIAL_CAPITAL, WIN_RATE_THRESHOLD
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
    attribution=None,
):
    fundamentals  = fundamentals  or {}
    ranked_stocks = ranked_stocks or []
    sector_scores = sector_scores or {}
    changelog     = changelog     or []
    attribution   = attribution   or {}
    nifty_val = nifty.get("value", "")
    vix_val   = vix.get("value", "")
    nifty_str = f"{nifty_val:,.0f}" if isinstance(nifty_val, (int, float)) else "—"
    vix_str   = f"{vix_val}" if vix_val != "" else "—"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deep's NSE AI Tracker</title>
<meta http-equiv="refresh" content="300">
<meta name="description" content="AI-powered NSE stock tracker for Nifty 100">
<meta name="theme-color" content="#0a0a0f">
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
{_phase_strip(phase)}
<div class="container">
  {_nav()}
  {_alert_banner(alert, stats) if alert else ""}
  {_market_bar(nifty, vix, mood, mkt_warn, market_health)}
  {_section_status(state, phase, day, focus, stock_data)}
  {_section_heatmap(stock_data)}
  {_section_portfolio(stats, portfolio, pnl_total, pnl_pct, book)}
  {_section_sectors(sector_scores)}
  {_section_rankings(ranked_stocks)}
  {_section_recommendations(recommendations)}
  {_section_changelog(changelog)}
  {_section_watchlist(focus, stock_data, news_data, patterns, fundamentals)}
  {_section_trades(book)}
  {_section_research(state, decisions)}
  {_section_attribution(attribution)}
  {_section_runlog(state)}
  {_section_brain(focus, patterns)}
</div>
{_scripts()}
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────

def _css() -> str:
    return """
:root {
  --bg:     #060608;
  --card:   #0d0d12;
  --card2:  #13131a;
  --card3:  #17171f;
  --border: #1e1e28;
  --border2:#252530;
  --text:   #dddde8;
  --muted:  #4a4a62;
  --muted2: #6a6a85;
  --bull:   #16a34a;
  --bear:   #dc2626;
  --yellow: #ca8a04;
  --blue:   #4f46e5;
  --cyan:   #0891b2;
  --green:  #16a34a;
  --red:    #dc2626;
  --font:   -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', sans-serif;
  --mono:   'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
}
*,*::before,*::after { box-sizing: border-box; margin: 0; padding: 0 }
html { scroll-behavior: smooth; -webkit-tap-highlight-color: transparent }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 13.5px;
  line-height: 1.55;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}
a { color: inherit; text-decoration: none }
h2 {
  font-size: .88rem;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  letter-spacing: -.01em;
}
h2 span { font-size: .68rem; color: var(--muted); font-weight: 400 }
h3 { font-size: .82rem; font-weight: 600 }

/* ── Header ── */
.header {
  background: rgba(6,6,8,.96);
  border-bottom: 1px solid var(--border);
  padding: 0 20px;
  height: 50px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
.logo { display: flex; align-items: center; gap: 10px }
.logo-icon {
  width: 28px; height: 28px;
  background: linear-gradient(135deg, #4f46e5 0%, #0891b2 100%);
  border-radius: 7px;
  display: flex; align-items: center; justify-content: center;
  font-size: .75rem; font-weight: 800; color: #fff;
  flex-shrink: 0;
}
.logo-text { font-size: .95rem; font-weight: 700; letter-spacing: -.02em }
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
.container { max-width: 1200px; margin: 0 auto; padding: 14px 16px }
.section { margin-bottom: 24px; scroll-margin-top: 88px }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px }
.grid3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px }
.grid4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px }

/* ── Cards ── */
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}
.card:hover { border-color: var(--border2) }
.stat-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.stat-card:hover { border-color: var(--border2) }
.stat-val { font-size: 1.35rem; font-weight: 700; line-height: 1.15; margin-bottom: 2px; font-variant-numeric: tabular-nums }
.stat-label { font-size: .62rem; color: var(--muted); font-weight: 500; text-transform: uppercase; letter-spacing: .06em }

/* ── Badges ── */
.badge {
  display: inline-block; padding: 1px 7px; border-radius: 20px;
  font-size: .63rem; font-weight: 600; white-space: nowrap; letter-spacing: .01em;
}
.badge-green  { background: #16a34a14; color: #4ade80; border: 1px solid #16a34a30 }
.badge-red    { background: #dc262614; color: #f87171; border: 1px solid #dc262630 }
.badge-yellow { background: #ca8a0414; color: #fbbf24; border: 1px solid #ca8a0430 }
.badge-blue   { background: #4f46e514; color: #818cf8; border: 1px solid #4f46e530 }
.badge-cyan   { background: #0891b214; color: #22d3ee; border: 1px solid #0891b230 }
.badge-orange { background: #ea580c14; color: #fb923c; border: 1px solid #ea580c30 }
.pill {
  display: inline-flex; align-items: center; padding: 1px 6px; border-radius: 3px;
  font-size: .63rem; font-weight: 600; white-space: nowrap;
}
.pill-green  { background: #16a34a12; color: #4ade80; border: 1px solid #16a34a25 }
.pill-red    { background: #dc262612; color: #f87171; border: 1px solid #dc262625 }
.pill-yellow { background: #ca8a0412; color: #fbbf24; border: 1px solid #ca8a0425 }
.pill-blue   { background: #4f46e512; color: #818cf8; border: 1px solid #4f46e525 }
.pill-cyan   { background: #0891b212; color: #22d3ee; border: 1px solid #0891b225 }

/* ── Colors ── */
.green  { color: #4ade80 }
.red    { color: #f87171 }
.yellow { color: #fbbf24 }
.blue   { color: #818cf8 }
.cyan   { color: #22d3ee }
.muted  { color: var(--muted) }
.muted2 { color: var(--muted2) }

/* ── Tables ── */
table { width: 100%; border-collapse: collapse }
th {
  color: var(--muted); font-size: .62rem; font-weight: 500;
  text-transform: uppercase; letter-spacing: .06em;
  padding: 7px 10px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap;
}
td { padding: 7px 10px; border-bottom: 1px solid #12121a; font-size: .77rem; vertical-align: middle }
tr:last-child td { border: none }
tr:hover td { background: #0f0f16 }
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch }

/* ── Progress ── */
.progress { height: 3px; border-radius: 2px; background: var(--border); overflow: hidden }
.progress-fill { height: 100%; border-radius: 2px; transition: width .4s }

/* ── Market bar ── */
.market-bar {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 9px 14px;
  margin-bottom: 14px;
  display: flex; flex-wrap: wrap; gap: 16px; align-items: center;
}
.mkt-item { display: flex; flex-direction: column; gap: 0 }
.mkt-val { font-size: .9rem; font-weight: 700; font-variant-numeric: tabular-nums }
.mkt-lbl { font-size: .58rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em }

/* ── Info rows ── */
.irow {
  display: flex; justify-content: space-between; align-items: center;
  padding: 4px 0; border-bottom: 1px solid #12121a; font-size: .77rem;
}
.irow:last-child { border: none }
.fund-row {
  display: flex; justify-content: space-between;
  padding: 3px 0; border-bottom: 1px solid #14141c; font-size: .71rem;
}
.fund-row:last-child { border: none }

/* ── Nav ── */
.nav {
  display: flex; gap: 2px; margin-bottom: 16px;
  padding-bottom: 10px; border-bottom: 1px solid var(--border);
  overflow-x: auto; white-space: nowrap;
  -ms-overflow-style: none; scrollbar-width: none;
}
.nav::-webkit-scrollbar { display: none }
.nav a {
  color: var(--muted); padding: 4px 10px; border-radius: 5px;
  font-size: .7rem; border: 1px solid transparent;
  min-height: 30px; display: inline-flex; align-items: center;
  transition: color .12s, border-color .12s, background .12s;
}
.nav a:hover { color: var(--text); border-color: var(--border); background: var(--card2) }

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

/* ── Banners ── */
.warn-banner {
  background: #dc262608; border: 1px solid #dc262625; border-radius: 6px;
  padding: 7px 11px; margin-bottom: 8px; font-size: .76rem; color: #f87171;
}
.alert-banner {
  background: #16a34a06; border: 1px solid #16a34a22; border-radius: 8px;
  padding: 11px 14px; margin-bottom: 14px; display: flex; align-items: center; gap: 10px;
}

/* ── Notes ── */
.note-line { padding: 4px 0; border-bottom: 1px solid #12121a; font-size: .73rem; color: var(--muted) }
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
  .container { padding: 8px 10px }
  .section { margin-bottom: 16px; scroll-margin-top: 80px }
  .card { padding: 10px }
  .stat-val { font-size: 1.05rem }
  .stat-label { font-size: .58rem }
  .heatmap-grid { grid-template-columns: repeat(auto-fill, minmax(62px, 1fr)); gap: 3px }
  .hmap-cell { padding: 5px 3px }
  .rec-card { padding: 11px }
  .rec-grid { grid-template-columns: 1fr 1fr }
  .rec-name { font-size: .85rem }
  td, th { padding: 5px 6px; font-size: .72rem }
  .nav a { padding: 3px 8px; font-size: .65rem }
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
  .container { padding: 10px 12px }
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
    mood_color = {"bullish": "var(--green)", "bearish": "var(--red)"}.get(mood, "var(--yellow)")
    mood_label = mood.title()
    trade_badge = (
        '<span class="badge badge-green">Trading Allowed</span>' if trade_ok
        else '<span class="badge badge-red">Trading Paused</span>'
    )
    phase_badge = f'<span class="badge badge-blue">{phase.replace("_", " ").title()}</span>'
    live_dot = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);margin-right:4px;box-shadow:0 0 5px var(--green)"></span>'

    return f"""<div class="header">
  <div class="logo">
    <div class="logo-icon">AI</div>
    <div>
      <div class="logo-text">Deep's NSE AI Tracker</div>
      <div class="logo-sub">Nifty 100 &middot; Self-Learning</div>
    </div>
    {phase_badge}
    <span class="badge badge-cyan" style="font-size:.58rem">Day {day}</span>
  </div>
  <div class="header-right">
    <div class="hdr-stat">
      <div class="hdr-val">{live_dot}Nifty {nifty_str}</div>
      <div class="hdr-lbl">VIX {vix_str} &middot; <span style="color:{mood_color}">{mood_label}</span></div>
    </div>
    {trade_badge}
    <span class="muted" style="font-size:.57rem">{now_utc}</span>
  </div>
</div>"""


def _phase_strip(phase) -> str:
    phase_order = ["exploration", "analysis", "paper_trading", "alerting"]
    phase_names = {
        "exploration":   "Exploration",
        "analysis":      "Analysis",
        "paper_trading": "Paper Trading",
        "alerting":      "Recommendations",
    }
    current_idx = phase_order.index(phase) if phase in phase_order else 0
    parts = []
    for i, p in enumerate(phase_order):
        if i < current_idx:
            cls = "ps-done"
        elif i == current_idx:
            cls = "ps-active"
        else:
            cls = ""
        parts.append(
            f'<div class="ps-item {cls}">'
            f'<div class="ps-dot"></div>'
            f'<span>{phase_names[p]}</span>'
            f'</div>'
        )
        if i < len(phase_order) - 1:
            line_cls = "ps-line-done" if i < current_idx else ""
            parts.append(f'<div class="ps-line {line_cls}"></div>')

    return f'<div class="phase-strip">{"".join(parts)}</div>'


def _nav() -> str:
    return """<nav class="nav">
  <a href="#status">Status</a>
  <a href="#heatmap">Heatmap</a>
  <a href="#portfolio">Portfolio</a>
  <a href="#sectors">Sectors</a>
  <a href="#rankings">Rankings</a>
  <a href="#recommendations">Recommendations</a>
  <a href="#changelog">Changelog</a>
  <a href="#watchlist">Watchlist</a>
  <a href="#trades">Paper Trades</a>
  <a href="#research">Research Log</a>
  <a href="#attribution">Attribution</a>
  <a href="#runlog">Run Log</a>
  <a href="#brain">Brain Insights</a>
</nav>"""


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

    bnifty = market_health.get("banknifty", {})
    bn_val = bnifty.get("value", "")
    bn_chg = bnifty.get("day_change_pct", 0)
    bn_cls = "green" if bn_chg >= 0 else "red"
    bn_str = f"{bn_val:,.0f}" if isinstance(bn_val, (int, float)) else "—"

    warn_html = "".join(f'<div class="warn-banner">&#9888; {w}</div>' for w in warnings)

    n_str = f"{n_val:,.0f}" if isinstance(n_val, (int, float)) else str(n_val)
    mood_color = {"bullish": "var(--green)", "bearish": "var(--red)"}.get(mood, "var(--yellow)")

    return f"""{warn_html}
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
        "exploration":   "Days 1-5",
        "analysis":      "Days 6-15",
        "paper_trading": "Days 16+",
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
        days_left = max(0, 5 - day)
        next_ms = (
            f"Focus stock selection in ~{days_left} day(s)" if days_left > 0
            else "Focus stock selection due at next preclose"
        )
        activity_lines = [
            f"Watching all {stocks_fetched} stocks 3x per day — building price, volume, and indicator baselines",
            "Detecting candlestick patterns on every bar: hammer, engulfing, morning star, doji, and 25+ more",
            "Scoring each stock on: momentum trend, RSI zone, MACD direction, volume strength",
            f"Next milestone: {next_ms} — top 12 by momentum score selected for deep analysis",
        ]
    elif phase == "analysis":
        days_left = max(0, 15 - day)
        activity_lines = [
            f"Deep-analysing {len(focus)} focus stocks — running full pattern detection each session",
            "Building pattern reliability database — learning which patterns work on which stocks",
            "Fetching quarterly fundamentals: P/E, ROE, debt ratio, revenue growth, promoter holding",
            f"Paper trading phase starts in ~{days_left} day(s)",
        ]
    elif phase in ("paper_trading", "alerting"):
        activity_lines = [
            f"Paper trading {len(focus)} focus stocks with virtual capital",
            "Each session: opening new positions, updating stops, closing completed trades",
            "4-layer scoring active: Technical (40) + Fundamental (30) + News (20) + Pattern (10)",
            "Recommendations appear when any stock scores 65/100 with R:R 2:1 or better",
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
        "exploration":   f"Day {min(day+1, 5)}: Add more stocks to analysis",
        "analysis":      f"Day {min(day+1, 15)}: Paper trading begins",
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
    wr_cls  = "green" if stats["win_rate"] >= WIN_RATE_THRESHOLD else "yellow"
    exp_cls = "green" if stats["expectancy"] > 0 else "red"
    wr_pct  = round(stats["win_rate"] * 100, 1)
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
    {sc("Win Rate",          f"{wr_pct:.1f}%", wr_cls)}
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
      <span>{wr_pct:.1f}% / {WIN_RATE_THRESHOLD*100:.0f}%</span>
    </div>
    <div class="progress">
      <div class="progress-fill" style="width:{min(wr_pct/(WIN_RATE_THRESHOLD*100)*100, 100):.1f}%;background:{prog_color}"></div>
    </div>
  </div>
  <div class="grid2">
    <div>{equity_html}</div>
    <div class="card">
      <h3 style="margin-bottom:10px">Trade Stats</h3>
      <div class="irow"><span class="muted">Avg winning trade</span><span class="green">&#8377;{stats['avg_win']:+,.0f}</span></div>
      <div class="irow"><span class="muted">Avg losing trade</span><span class="red">&#8377;{stats['avg_loss']:+,.0f}</span></div>
      <div class="irow"><span class="muted">Win rate</span><span class="{wr_cls}">{wr_pct:.1f}%</span></div>
      <div class="irow"><span class="muted">Expectancy / trade</span><span class="{exp_cls}">&#8377;{stats['expectancy']:+,.0f}</span></div>
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
            f'<div style="flex:1;height:5px;background:#23232e;border-radius:3px">'
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
            f'<div style="flex:1;height:5px;background:#23232e;border-radius:3px">'
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
      <tr style="color:var(--muted);font-size:.72rem;border-bottom:1px solid #23232e">
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


def _section_recommendations(recs) -> str:
    if not recs:
        no_recs = """<div class="card" style="padding:20px;text-align:center">
  <div style="font-size:.8rem;color:var(--muted);line-height:2">
    <div style="font-size:1.6rem;margin-bottom:8px">&#128300;</div>
    <div style="font-weight:700;color:var(--text);margin-bottom:6px">Agent is still learning</div>
    <div>No recommendations yet. The agent needs to:<br>
    1. Complete 5-day exploration &#8594; select 12 focus stocks<br>
    2. Complete 10-day deep analysis on focus stocks<br>
    3. Find a setup scoring &#8805;65/100 across Technical + Fundamental + News + Pattern<br>
    4. Confirm R:R &#8805; 2:1 with a clear stop loss and target<br>
    <br>Check the Watchlist to see which stocks are being scored right now.</div>
  </div>
</div>"""
        return f"""<div class="section" id="recommendations">
  <h2>Stock Recommendations <span>High-confidence setups with full trade details</span></h2>
  {no_recs}
</div>"""

    cards = ""
    for rec in recs:
        signal = rec["signal"]
        cls    = "rec-buy" if signal == "BUY" else "rec-sell"
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

        trade_type     = rec.get("trade_type", "Delivery / Positional")
        entry_window   = rec.get("entry_window", "")
        exit_window    = rec.get("exit_window", "")
        tgt_timeframe  = rec.get("target_timeframe", "")
        action_urgency = rec.get("action_urgency", "")
        is_intraday    = trade_type == "Intraday"
        timing_cls     = "urgency-intraday" if is_intraday else "urgency-delivery"
        trade_type_badge_cls = "badge-orange" if is_intraday else "badge-cyan"

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
  {'<div style="margin-top:6px;padding:5px 8px;border-radius:4px;background:#0f0f16;font-size:.67rem;font-weight:600;color:#fb923c">' + action_urgency + '</div>' if is_intraday and action_urgency else ""}
</div>"""

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
      {f'<span class="badge" style="background:#0e1624;color:#7eb3ff;border:1px solid #1e3050">#{focus_rank} {delta_html}</span>' if focus_rank else ""}
      {'<span class="badge badge-red">STALE</span>' if is_stale else ""}
    </div>
  </div>

  {stale_banner}
  {timing_box}

  <div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap">
    <div style="flex:1;min-width:110px;background:var(--card2);border-radius:6px;padding:7px 9px;border:1px solid var(--border)">
      <div style="font-size:.58rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Success %</div>
      <div style="font-size:1rem;font-weight:700;color:{'#4ade80' if sp_pct>=55 else '#fbbf24'}">{sp_pct}%</div>
      <div style="height:3px;background:#1a1a24;border-radius:2px;margin-top:4px"><div style="width:{sp_pct}%;height:3px;background:{'#4ade80' if sp_pct>=55 else '#fbbf24'};border-radius:2px"></div></div>
    </div>
    <div style="flex:1;min-width:110px;background:var(--card2);border-radius:6px;padding:7px 9px;border:1px solid var(--border)">
      <div style="font-size:.58rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Profit Edge</div>
      <div style="font-size:1rem;font-weight:700;color:{'#4ade80' if profit_prob>0 else '#f87171'}">{pp_pct}%</div>
      <div style="height:3px;background:#1a1a24;border-radius:2px;margin-top:4px"><div style="width:{pp_pct}%;height:3px;background:{'#4ade80' if profit_prob>0 else '#f87171'};border-radius:2px"></div></div>
    </div>
    <div style="flex:1;min-width:110px;background:var(--card2);border-radius:6px;padding:7px 9px;border:1px solid var(--border)">
      <div style="font-size:.58rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">AI Score</div>
      <div style="font-size:1rem;font-weight:700;color:#22d3ee">{composite_sc:.0f}/100</div>
      <div style="height:3px;background:#1a1a24;border-radius:2px;margin-top:4px"><div style="width:{min(100,composite_sc):.0f}%;height:3px;background:#22d3ee;border-radius:2px"></div></div>
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

    return f"""<div class="section" id="recommendations">
  <h2>Stock Recommendations <span>{len(recs)} high-confidence setup(s)</span></h2>
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
        pnl = t.get("pnl", 0)
        rows += (
            f'<tr>'
            f'<td class="muted">{t.get("close_date","?")}</td>'
            f'<td><strong>{t["ticker"].replace(".NS","")}</strong></td>'
            f'<td><span class="pill {"pill-green" if t["action"]=="BUY" else "pill-red"}">{t["action"]}</span></td>'
            f'<td>&#8377;{t["entry"]:.2f}</td>'
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


def _section_runlog(state: dict) -> str:
    import json as _json
    import os as _os
    from agent.config import DAILY_LOG_FILE

    if not _os.path.exists(DAILY_LOG_FILE):
        return ""

    try:
        with open(DAILY_LOG_FILE) as f:
            log = _json.load(f)
    except Exception:
        return ""

    if not log:
        return ""

    rows = ""
    for entry in reversed(log[-30:]):
        date_    = entry.get("date", "")
        sess_    = entry.get("session", "")
        phase_   = entry.get("phase", "")
        day_     = entry.get("day", "")
        stats_   = entry.get("stats", {})
        open_    = entry.get("open", 0)
        wr_      = stats_.get("win_rate", 0)
        total_   = stats_.get("total", 0)
        pnl_     = stats_.get("total_pnl", 0)
        sess_badge = {
            "morning":  '<span class="pill pill-green">Morning</span>',
            "midday":   '<span class="pill pill-blue">Midday</span>',
            "preclose": '<span class="pill pill-yellow">Preclose</span>',
        }.get(sess_, f'<span class="pill">{sess_}</span>')
        rows += (
            f'<tr style="border-bottom:1px solid var(--border)">'
            f'<td style="padding:7px 4px;color:var(--muted);font-size:.75rem">{date_}</td>'
            f'<td style="padding:7px 4px">{sess_badge}</td>'
            f'<td style="padding:7px 4px;font-size:.78rem;color:var(--fg)">{phase_} d{day_}</td>'
            f'<td style="padding:7px 4px;text-align:center;font-size:.78rem">{open_} open</td>'
            f'<td style="padding:7px 4px;text-align:center;font-size:.78rem">{total_} trades · {wr_*100:.0f}% WR</td>'
            f'<td style="padding:7px 4px;text-align:right;font-size:.78rem;color:{"#22c55e" if pnl_>=0 else "#ef4444"}">'
            f'₹{pnl_:+,.0f}</td>'
            f'</tr>'
        )

    return f"""<div class="section" id="runlog">
  <h2>Run Log <span>Each trigger — what time, what session, what happened</span></h2>
  <div class="card" style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:.8rem;min-width:480px">
      <thead><tr style="color:var(--muted);font-size:.72rem">
        <th style="text-align:left;padding:6px 4px">Date</th>
        <th style="text-align:left">Session</th>
        <th style="text-align:left">Phase</th>
        <th style="text-align:center">Positions</th>
        <th style="text-align:center">Trades</th>
        <th style="text-align:right">P&amp;L</th>
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
