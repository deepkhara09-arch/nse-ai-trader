"""
Dashboard generator — writes docs/index.html served via GitHub Pages.
Completely self-contained HTML/CSS/JS — no external CDN dependencies.
"""

import json
import os
from datetime import date, datetime
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
) -> None:
    os.makedirs("docs", exist_ok=True)
    if market_health is None:
        market_health = {}
    if recommendations is None:
        recommendations = []
    if fundamentals is None:
        fundamentals = {}

    stats   = compute_stats(book)
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    phase   = state.get("phase", "exploration")
    day     = state.get("day", 1)
    focus   = state.get("focus_stocks", [])
    alert   = state.get("alert_sent", False)

    # Portfolio math
    open_pos   = book.get("open_positions", [])
    unrealized = sum(p.get("unrealized_pnl", 0) for p in open_pos)
    invested   = sum(p.get("invested", 0) for p in open_pos)
    portfolio  = round(book.get("capital", INITIAL_CAPITAL) + invested + unrealized, 2)
    pnl_total  = round(portfolio - INITIAL_CAPITAL, 2)
    pnl_pct    = round(pnl_total / INITIAL_CAPITAL * 100, 2)

    # Market
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
    fundamentals=None,
):
    fundamentals = fundamentals or {}
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NSE AI Trader</title>
<meta http-equiv="refresh" content="300">
<style>{_css()}</style>
</head>
<body>
{_header(phase, day, now_utc, mood, trade_ok)}
<div class="container">
  {_nav()}
  {_alert_banner(alert, stats) if alert else ""}
  {_market_bar(nifty, vix, mood, mkt_warn, market_health)}
  {_section_heatmap(stock_data)}
  {_section_status(state, phase, day, focus, stock_data)}
  {_section_portfolio(stats, portfolio, pnl_total, pnl_pct, book)}
  {_section_recommendations(recommendations)}
  {_section_watchlist(focus, stock_data, news_data, patterns, fundamentals)}
  {_section_trades(book)}
  {_section_research(state, decisions)}
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
:root{
  --bg:#090c14;--card:#111827;--card2:#1a2035;--card3:#0d1628;
  --green:#10b981;--red:#ef4444;--yellow:#f59e0b;--blue:#6366f1;
  --cyan:#06b6d4;--purple:#a855f7;
  --text:#f1f5f9;--muted:#64748b;--border:#1e293b;
  --font:'Inter',system-ui,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;line-height:1.6;min-height:100vh}
a{color:inherit;text-decoration:none}
h2{font-size:1rem;font-weight:600;color:var(--text);margin-bottom:14px;
   display:flex;align-items:center;gap:8px}
h2 span{font-size:.7rem;color:var(--muted);font-weight:400}
h3{font-size:.88rem;font-weight:600}
.header{background:linear-gradient(135deg,#0f172a,#1e1b4b);
  border-bottom:1px solid var(--border);padding:14px 24px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;
  position:sticky;top:0;z-index:100;backdrop-filter:blur(10px)}
.logo{display:flex;align-items:center;gap:10px}
.logo h1{font-size:1.25rem;font-weight:700;background:linear-gradient(90deg,#818cf8,#34d399);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.badge{padding:2px 9px;border-radius:20px;font-size:.72rem;font-weight:600;white-space:nowrap}
.badge-green{background:#10b98120;color:var(--green);border:1px solid #10b98140}
.badge-yellow{background:#f59e0b20;color:var(--yellow);border:1px solid #f59e0b40}
.badge-blue{background:#6366f120;color:#818cf8;border:1px solid #6366f140}
.badge-red{background:#ef444420;color:var(--red);border:1px solid #ef444440}
.badge-cyan{background:#06b6d420;color:var(--cyan);border:1px solid #06b6d440}
.container{max-width:1440px;margin:0 auto;padding:20px 16px}
.section{margin-bottom:32px;scroll-margin-top:70px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.grid4{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.grid5{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;position:relative}
.card:hover{border-color:#334155}
.stat-card{background:var(--card3);border:1px solid var(--border);border-radius:10px;
  padding:16px;transition:border-color .2s}
.stat-card:hover{border-color:#334155}
.stat-val{font-size:1.65rem;font-weight:700;line-height:1.1;margin-bottom:4px}
.stat-label{font-size:.72rem;color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:.05em}
.green{color:var(--green)} .red{color:var(--red)} .yellow{color:var(--yellow)}
.blue{color:#818cf8} .cyan{color:var(--cyan)} .muted{color:var(--muted)}
table{width:100%;border-collapse:collapse}
th{color:var(--muted);font-size:.72rem;font-weight:500;text-transform:uppercase;
  letter-spacing:.05em;padding:9px 12px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid #0f172a;font-size:.82rem;vertical-align:middle}
tr:last-child td{border:none}
tr:hover td{background:#0f1628}
.pill{display:inline-flex;align-items:center;gap:3px;padding:2px 8px;border-radius:4px;
  font-size:.72rem;font-weight:600;white-space:nowrap}
.pill-green{background:#10b98118;color:var(--green);border:1px solid #10b98130}
.pill-red{background:#ef444418;color:var(--red);border:1px solid #ef444430}
.pill-yellow{background:#f59e0b18;color:var(--yellow);border:1px solid #f59e0b30}
.pill-blue{background:#6366f118;color:#818cf8;border:1px solid #6366f130}
.pill-cyan{background:#06b6d418;color:var(--cyan);border:1px solid #06b6d430}
.irow{display:flex;justify-content:space-between;align-items:center;
  padding:5px 0;border-bottom:1px solid #0f172a;font-size:.82rem}
.irow:last-child{border:none}
.fund-row{display:flex;justify-content:space-between;padding:3px 0;
  border-bottom:1px solid #0f172a22;font-size:.78rem}
.fund-row:last-child{border:none}
.nav{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:22px;padding:10px 0;
  border-bottom:1px solid var(--border)}
.nav a{color:var(--muted);padding:4px 12px;border-radius:6px;font-size:.78rem;
  border:1px solid transparent;transition:all .18s}
.nav a:hover{color:var(--text);border-color:var(--border);background:var(--card)}
.progress{height:4px;border-radius:2px;background:#1e293b;overflow:hidden}
.progress-fill{height:100%;border-radius:2px;transition:width .5s}
.market-bar{background:var(--card3);border:1px solid var(--border);border-radius:10px;
  padding:12px 18px;margin-bottom:20px;display:flex;flex-wrap:wrap;gap:20px;align-items:center}
.mkt-item{display:flex;flex-direction:column;gap:2px}
.mkt-val{font-size:1.05rem;font-weight:700}
.mkt-lbl{font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.rec-card{border-radius:12px;padding:18px;border:1px solid;margin-bottom:14px;position:relative}
.rec-buy{background:#10b98108;border-color:#10b98130}
.rec-sell{background:#ef444408;border-color:#ef444430}
.rec-header{display:flex;justify-content:space-between;align-items:start;margin-bottom:12px;flex-wrap:wrap;gap:8px}
.rec-name{font-size:1.05rem;font-weight:700}
.rec-code{font-size:.78rem;color:var(--muted);margin-top:2px}
.rec-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:12px 0}
.rec-field{background:var(--card);border-radius:8px;padding:10px 12px}
.rec-field-val{font-size:1rem;font-weight:700}
.rec-field-lbl{font-size:.68rem;color:var(--muted);margin-top:2px}
.conf-bar{margin-top:10px}
.reasons-list{margin-top:10px;padding:10px 12px;background:var(--card);border-radius:8px}
.reasons-list li{font-size:.78rem;color:var(--muted);margin-bottom:3px;list-style:none;padding-left:12px;position:relative}
.reasons-list li::before{content:"›";position:absolute;left:0;color:#6366f1}
.stock-card{background:var(--card);border:1px solid var(--border);border-radius:10px;
  padding:14px;transition:border-color .2s}
.stock-card:hover{border-color:#6366f1}
.timeline{display:flex;align-items:center;gap:0;flex-wrap:wrap;margin-top:10px}
.tphase{display:flex;align-items:center;gap:6px;padding:6px 10px;border-radius:6px;font-size:.78rem}
.tphase-done{background:#10b98112;color:var(--green)}
.tphase-active{background:#f59e0b12;color:var(--yellow)}
.tphase-future{color:var(--muted)}
.tarrow{color:var(--muted);margin:0 2px;font-size:.75rem}
.warn-banner{background:#ef444410;border:1px solid #ef444430;border-radius:8px;
  padding:10px 14px;margin-bottom:12px;font-size:.82rem;color:var(--red)}
.alert-banner{background:linear-gradient(135deg,#10b98112,#6366f112);
  border:1px solid #10b98140;border-radius:10px;padding:14px 18px;
  margin-bottom:20px;display:flex;align-items:center;gap:12px}
.note-line{padding:6px 0;border-bottom:1px solid #0f172a;font-size:.78rem;color:var(--muted)}
.note-line:last-child{border:none}
.note-line strong{color:var(--text)}
.heatmap-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:6px;margin-bottom:8px}
.hmap-cell{border:1px solid;border-radius:8px;padding:8px 6px;text-align:center;cursor:default;transition:transform .15s}
.hmap-cell:hover{transform:scale(1.05);z-index:10;position:relative}
.hmap-name{font-size:.72rem;font-weight:700;color:var(--text);margin-bottom:2px}
.hmap-chg{font-size:.85rem;font-weight:700}
.hmap-rsi{font-size:.65rem;color:var(--muted);margin-top:2px}
.phase-progress{display:flex;align-items:flex-start;gap:0;margin:16px 0;overflow-x:auto;padding:8px 0}
.phase-step{display:flex;flex-direction:column;align-items:center;gap:4px;min-width:100px;position:relative}
.phase-step span{font-size:.75rem;font-weight:600;color:var(--muted);text-align:center}
.phase-step small{font-size:.65rem;color:var(--muted)}
.phase-step.active span{color:var(--cyan)}
.phase-step.done span{color:var(--green)}
.phase-dot{width:12px;height:12px;border-radius:50%;background:var(--border);border:2px solid var(--muted)}
.phase-step.active .phase-dot{background:var(--cyan);border-color:var(--cyan);box-shadow:0 0 8px var(--cyan)}
.phase-step.done .phase-dot{background:var(--green);border-color:var(--green)}
.phase-line{flex:1;height:2px;background:var(--border);margin-top:5px;min-width:30px}
@media(max-width:640px){
  .stat-val{font-size:1.2rem}
  .header h1{font-size:1rem}
  td,th{padding:7px 8px}
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────

def _header(phase, day, now_utc, mood, trade_ok) -> str:
    mood_badge = {
        "bullish": '<span class="badge badge-green">📈 Market Bullish</span>',
        "bearish": '<span class="badge badge-red">📉 Market Bearish</span>',
        "neutral": '<span class="badge badge-yellow">↔ Market Neutral</span>',
    }.get(mood, "")
    trade_badge = (
        '<span class="badge badge-green">✓ Trading Allowed</span>' if trade_ok
        else '<span class="badge badge-red">⛔ Trading Paused</span>'
    )
    return f"""<div class="header">
  <div class="logo">
    <h1>📊 NSE AI Trader</h1>
    <span class="badge badge-blue">{phase.replace("_"," ").title()}</span>
    <span class="badge badge-cyan">Day {day}</span>
    {mood_badge}
  </div>
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
    {trade_badge}
    <span class="muted" style="font-size:.72rem">{now_utc} · auto-refresh 5min</span>
  </div>
</div>"""


def _nav() -> str:
    return """<div class="nav">
  <a href="#status">📌 Status</a>
  <a href="#heatmap">🌡 Heatmap</a>
  <a href="#portfolio">💰 Portfolio</a>
  <a href="#recommendations">🎯 Recommendations</a>
  <a href="#watchlist">🔭 Watchlist</a>
  <a href="#trades">📋 Paper Trades</a>
  <a href="#research">📝 Research Log</a>
  <a href="#brain">🧠 Brain Insights</a>
</div>"""


def _alert_banner(alert, stats) -> str:
    return f"""<div class="alert-banner">
  <div style="font-size:1.8rem">🎯</div>
  <div>
    <div style="font-weight:700;font-size:1rem;color:var(--green)">Strategy Confidence Reached!</div>
    <div class="muted" style="font-size:.82rem">
      Win rate: <strong class="green">{stats['win_rate']*100:.1f}%</strong> over
      <strong>{stats['total']}</strong> paper trades |
      Expectancy: <strong class="{'green' if stats['expectancy']>0 else 'red'}">
        ₹{stats['expectancy']:+,.0f}</strong> per trade.
      Check <a href="#recommendations" style="color:var(--cyan)">Recommendations</a> below.
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
    leader_str = " · ".join(leaders[:4]) if leaders else "—"

    warn_html = ""
    for w in warnings:
        warn_html += f'<div class="warn-banner">⚠ {w}</div>'

    return f"""{warn_html}
<div class="market-bar">
  <div class="mkt-item">
    <div class="mkt-val {n_cls}">
      {f"{n_val:,.0f}" if isinstance(n_val, (int,float)) else n_val}
      <span style="font-size:.78rem">({n_chg:+.2f}%)</span>
    </div>
    <div class="mkt-lbl">Nifty 50</div>
  </div>
  <div class="mkt-item">
    <div class="mkt-val {v_cls}">{v_val} <span style="font-size:.72rem">[{v_lvl}]</span></div>
    <div class="mkt-lbl">India VIX</div>
  </div>
  <div class="mkt-item">
    <div class="mkt-val" style="color:{'var(--green)' if mood=='bullish' else ('var(--red)' if mood=='bearish' else 'var(--yellow)')}">
      {mood.title()}
    </div>
    <div class="mkt-lbl">Market Mood</div>
  </div>
  <div class="mkt-item" style="flex:1">
    <div class="mkt-val" style="font-size:.88rem;color:var(--cyan)">{leader_str}</div>
    <div class="mkt-lbl">Leading Sectors</div>
  </div>
</div>"""


def _section_heatmap(stock_data: dict) -> str:
    if not stock_data:
        return ""
    cells = []
    for ticker, entry in sorted(stock_data.items()):
        d = entry.get("latest", {})
        close = d.get("close", 0)
        ph = entry.get("price_history_60d", [])
        if len(ph) >= 2:
            chg = (ph[-1] - ph[-2]) / ph[-2] * 100 if ph[-2] else 0
        else:
            chg = 0
        rsi = d.get("rsi", 50)
        code = ticker.replace(".NS", "")

        # Color: map chg -3..+3 to red..green
        intensity = max(-1, min(1, chg / 3))
        if intensity >= 0:
            r = int(16 + (1 - intensity) * 60)
            g = int(185 - (1 - intensity) * 80)
            b = int(129 - intensity * 100)
        else:
            r = int(239 - (1 + intensity) * 100)
            g = int(68 + (1 + intensity) * 60)
            b = int(68 + (1 + intensity) * 30)
        bg = f"#{r:02x}{g:02x}{b:02x}22"
        border = f"#{r:02x}{g:02x}{b:02x}55"
        chg_color = "var(--green)" if chg >= 0 else "var(--red)"
        sign = "+" if chg >= 0 else ""
        rsi_color = "var(--green)" if rsi < 40 else ("var(--red)" if rsi > 65 else "var(--yellow)")

        cells.append(f'''<div class="hmap-cell" style="background:{bg};border-color:{border}"
          title="{code}: Rs.{close:.2f} | RSI {rsi:.0f}">
          <div class="hmap-name">{code}</div>
          <div class="hmap-chg" style="color:{chg_color}">{sign}{chg:.1f}%</div>
          <div class="hmap-rsi" style="color:{rsi_color}">RSI {rsi:.0f}</div>
        </div>''')

    return f'''<div class="section" id="heatmap">
  <h2>🌡 Market Snapshot <span>all monitored stocks</span></h2>
  <div class="heatmap-grid">{"".join(cells)}</div>
</div>'''


def _section_status(state, phase, day, focus, stock_data=None) -> str:
    stock_data = stock_data or {}
    phase_order = ["exploration", "analysis", "paper_trading", "alerting"]
    phase_labels = {
        "exploration": "1. Explore Universe",
        "analysis": "2. Deep Analysis",
        "paper_trading": "3. Paper Trade",
        "alerting": "4. Signal Ready",
    }
    current_idx = phase_order.index(phase) if phase in phase_order else 0

    timeline_html = ""
    for i, p in enumerate(phase_order):
        label = phase_labels[p]
        if i < current_idx:
            timeline_html += f'<div class="tphase tphase-done">✓ {label}</div>'
        elif i == current_idx:
            timeline_html += f'<div class="tphase tphase-active">▶ {label}</div>'
        else:
            timeline_html += f'<div class="tphase tphase-future">{label}</div>'
        if i < len(phase_order) - 1:
            timeline_html += '<span class="tarrow">→</span>'

    start = state.get("start_date", "—")
    phase_start = state.get("phase_start_date", "—")
    focus_html = ""
    for t in focus:
        focus_html += f'<span class="badge badge-cyan" style="margin:2px">{t.replace(".NS","")}</span>'

    # Phase progress bar
    def _phase_cls(p_name):
        if p_name == phase:
            return "active"
        idx = phase_order.index(p_name) if p_name in phase_order else 0
        if idx < current_idx:
            return "done"
        return ""

    phase_step_html = ""
    phase_descs = {
        "exploration": "Days 1-5",
        "analysis": "Days 6-15",
        "paper_trading": "Days 16+",
        "alerting": "Live",
    }
    phase_names = {
        "exploration": "Exploration",
        "analysis": "Analysis",
        "paper_trading": "Paper Trading",
        "alerting": "Recommendations",
    }
    for i, p_name in enumerate(phase_order):
        cls = _phase_cls(p_name)
        phase_step_html += f'''<div class="phase-step {cls}">
          <div class="phase-dot"></div>
          <span>{phase_names[p_name]}</span>
          <small>{phase_descs[p_name]}</small>
        </div>'''
        if i < len(phase_order) - 1:
            phase_step_html += '<div class="phase-line"></div>'

    # What is the agent actively doing right now?
    if phase == "exploration":
        stocks_fetched = len(stock_data)
        days_left = max(0, 5 - day)
        next_milestone = f"Focus stock selection in ~{days_left} day(s)" if days_left > 0 else "Focus stock selection due at next preclose"
        activity_lines = [
            f"Watching all {stocks_fetched} stocks 3× per day — building price, volume, and indicator baselines",
            "Detecting candlestick patterns on every bar: hammer, engulfing, morning star, doji, and 25+ more",
            "Scoring each stock on: momentum trend, RSI zone, MACD direction, volume strength",
            f"Next milestone: {next_milestone} — top 12 by momentum score will be selected for deep analysis",
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
            f"Paper trading {len(focus)} focus stocks with ₹1,00,000 virtual capital",
            "Each session: opening new positions, updating stops, closing completed trades",
            "4-layer scoring active: Technical (40) + Fundamental (30) + News (20) + Pattern (10)",
            "Recommendations appear when any stock scores ≥65/100 with R:R ≥ 2:1",
        ]
    else:
        activity_lines = ["Agent initialising — check back after first run."]

    activity_html = "".join(
        f'<div style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">'
        f'<span style="color:var(--cyan);margin-top:1px">›</span>'
        f'<span style="font-size:.82rem;color:var(--text)">{line}</span></div>'
        for line in activity_lines
    )

    return f"""<div class="section" id="status">
  <h2>📌 Agent Status <span>What the agent is doing right now</span></h2>
  <div class="card">
    <div class="timeline">{timeline_html}</div>
    <div class="phase-progress">{phase_step_html}</div>
    <div style="margin-top:16px;background:var(--card3);border-radius:8px;padding:12px 14px">
      <div style="font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">Current activity</div>
      {activity_html}
    </div>
    <div style="margin-top:14px;display:flex;flex-wrap:wrap;gap:24px">
      <div><div class="stat-label">Started</div><div style="font-weight:600">{start}</div></div>
      <div><div class="stat-label">Phase since</div><div style="font-weight:600">{phase_start}</div></div>
      <div><div class="stat-label">Total days run</div><div style="font-weight:600">{day}</div></div>
      <div><div class="stat-label">Stocks in database</div><div style="font-weight:600">{len(stock_data)}</div></div>
    </div>
    {f'<div style="margin-top:12px"><div class="stat-label" style="margin-bottom:6px">Focus Stocks</div>{focus_html}</div>' if focus else ""}
  </div>
</div>"""


def _section_portfolio(stats, portfolio, pnl_total, pnl_pct, book) -> str:
    p_cls = "green" if pnl_total >= 0 else "red"
    wr_cls = "green" if stats["win_rate"] >= WIN_RATE_THRESHOLD else "yellow"
    exp_cls = "green" if stats["expectancy"] > 0 else "red"
    wr_pct = round(stats["win_rate"] * 100, 1)
    progress_color = "var(--green)" if wr_pct >= WIN_RATE_THRESHOLD * 100 else "var(--yellow)"

    def sc(label, val, cls=""):
        cls_attr = f' class="{cls}"' if cls else ""
        return f"""<div class="stat-card">
  <div class="stat-val{cls_attr}">{val}</div>
  <div class="stat-label">{label}</div>
</div>"""

    equity_html = _equity_curve(book)
    open_pos_html = _open_positions_table(book)

    return f"""<div class="section" id="portfolio">
  <h2>💰 Paper Portfolio <span>Virtual ₹1,00,000 — no real money</span></h2>
  <div class="grid5" style="margin-bottom:14px">
    {sc("Portfolio Value", f"₹{portfolio:,.0f}", p_cls)}
    {sc("Total P&L", f"₹{pnl_total:+,.0f} ({pnl_pct:+.1f}%)", p_cls)}
    {sc("Win Rate", f"{wr_pct:.1f}%", wr_cls)}
    {sc("Expectancy/Trade", f"₹{stats['expectancy']:+,.0f}", exp_cls)}
    {sc("Total Trades", f"{stats['total']} ({stats['wins']}W / {stats['losses']}L)")}
  </div>
  <div style="margin-bottom:14px">
    <div style="display:flex;justify-content:space-between;font-size:.72rem;color:var(--muted);margin-bottom:4px">
      <span>Win rate progress toward {WIN_RATE_THRESHOLD*100:.0f}% target</span>
      <span>{wr_pct:.1f}% / {WIN_RATE_THRESHOLD*100:.0f}%</span>
    </div>
    <div class="progress">
      <div class="progress-fill" style="width:{min(wr_pct/(WIN_RATE_THRESHOLD*100)*100,100):.1f}%;background:{progress_color}"></div>
    </div>
  </div>
  <div class="grid2">
    <div>{equity_html}</div>
    <div class="card">
      <h3 style="margin-bottom:10px">Trade Stats</h3>
      <div class="irow"><span class="muted">Avg winning trade</span><span class="green">₹{stats['avg_win']:+,.0f}</span></div>
      <div class="irow"><span class="muted">Avg losing trade</span><span class="red">₹{stats['avg_loss']:+,.0f}</span></div>
      <div class="irow"><span class="muted">Win Rate</span><span class="{wr_cls}">{wr_pct:.1f}%</span></div>
      <div class="irow"><span class="muted">Expectancy</span><span class="{exp_cls}">₹{stats['expectancy']:+,.0f}/trade</span></div>
      <div class="irow"><span class="muted">Free capital</span><span>₹{book.get('capital',INITIAL_CAPITAL):,.0f}</span></div>
      <div class="irow"><span class="muted">Open positions</span><span class="blue">{len(book.get('open_positions',[]))}</span></div>
    </div>
  </div>
  {open_pos_html}
</div>"""


def _equity_curve(book) -> str:
    snaps = book.get("daily_snapshots", [])
    if len(snaps) < 2:
        return """<div class="card" style="padding:20px;text-align:center;color:var(--muted)">
  Equity curve will appear after the first trading day.
</div>"""
    vals  = [s["portfolio_value"] for s in snaps]
    dates = [s["date"] for s in snaps]
    color = "#10b981" if vals[-1] >= vals[0] else "#ef4444"
    return f"""<div class="card">
  <div style="display:flex;justify-content:space-between;margin-bottom:8px;font-size:.78rem">
    <span class="muted">Portfolio Equity Curve</span>
    <span class="muted">{dates[0]} → {dates[-1]}</span>
  </div>
  <div data-spark='{json.dumps(vals)}' data-color='{color}' style="height:70px"></div>
  <div style="display:flex;justify-content:space-between;font-size:.72rem;color:var(--muted);margin-top:6px">
    <span>₹{INITIAL_CAPITAL:,} start</span>
    <span>Min ₹{min(vals):,.0f}</span>
    <span>Max ₹{max(vals):,.0f}</span>
    <span class="{'green' if vals[-1]>=vals[0] else 'red'}">Now ₹{vals[-1]:,.0f}</span>
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
        rows += f"""<tr>
  <td><strong>{p['ticker'].replace('.NS','')}</strong></td>
  <td><span class="pill {'pill-green' if p['action']=='BUY' else 'pill-red'}">{p['action']}</span></td>
  <td>₹{p['entry']:.2f}</td>
  <td>₹{p.get('current_price', p['entry']):.2f}</td>
  <td class="{cls}">₹{unr:+.0f} ({pct:+.1f}%)</td>
  <td class="red">₹{p['stop_loss']:.2f}</td>
  <td class="green">₹{p['target']:.2f}</td>
  <td><span class="pill pill-blue">{p.get('style','swing')}</span></td>
  <td class="muted">{p['open_date']}</td>
</tr>"""
    return f"""<div class="card" style="margin-top:14px;overflow-x:auto">
  <h3 style="margin-bottom:10px">Open Positions</h3>
  <table>
    <thead><tr>
      <th>Stock</th><th>Side</th><th>Entry</th><th>Current</th>
      <th>Unrealised P&L</th><th>Stop Loss</th><th>Target</th><th>Style</th><th>Opened</th>
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
    color = "#10b981" if prices[-1] >= prices[0] else "#ef4444"
    path = "M" + " L".join(pts)
    grad_id = f"sg{abs(hash(str(prices[0]))) % 9999}"
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'style="display:block;overflow:visible">'
            f'<defs><linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{color}" stop-opacity="0.3"/>'
            f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/>'
            f'</linearGradient></defs>'
            f'<path d="{path} L{width},{height} L0,{height} Z" fill="url(#{grad_id})"/>'
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.5"/>'
            f'</svg>')


def _rsi_bar(rsi: float) -> str:
    pct = max(0, min(100, rsi))
    color = "#10b981" if rsi < 40 else ("#ef4444" if rsi > 65 else "#f59e0b")
    return (f'<div style="margin-top:6px">'
            f'<div style="display:flex;justify-content:space-between;font-size:.65rem;color:#64748b;margin-bottom:2px">'
            f'<span>RSI</span><span style="color:{color}">{rsi:.0f}</span></div>'
            f'<div style="height:4px;background:#1e293b;border-radius:2px">'
            f'<div style="width:{pct}%;height:100%;background:{color};border-radius:2px;transition:width .3s"></div>'
            f'</div></div>')


def _score_bar(label, score, max_score, color):
    pct = min(100, score / max_score * 100) if max_score > 0 else 0
    return (f'<div style="margin-bottom:4px">'
            f'<div style="display:flex;justify-content:space-between;font-size:.65rem;color:#64748b;margin-bottom:1px">'
            f'<span>{label}</span><span style="color:{color}">{score:.0f}/{max_score}</span></div>'
            f'<div style="height:3px;background:#1e293b;border-radius:2px">'
            f'<div style="width:{pct:.1f}%;height:100%;background:{color};border-radius:2px"></div>'
            f'</div></div>')


def _section_recommendations(recs) -> str:
    if not recs:
        no_recs = """<div class="card" style="padding:20px">
  <div style="color:var(--muted);text-align:center;line-height:2">
    <div style="font-size:2rem;margin-bottom:8px">🔬</div>
    <div style="font-weight:600;color:var(--text);margin-bottom:6px">Agent is still learning</div>
    <div>No recommendations yet. The agent needs to:<br>
    1. Complete 5-day exploration → 12 focus stocks selected<br>
    2. Complete 10-day deep analysis on those stocks<br>
    3. Find a setup scoring ≥65/100 across Technical + Fundamental + News + Pattern<br>
    4. Confirm R:R ≥ 2:1 with clear stop loss and target<br>
    <br>Check the Watchlist below to see which stocks are being scored right now.</div>
  </div>
</div>"""
        return f"""<div class="section" id="recommendations">
  <h2>🎯 Stock Recommendations <span>High-confidence setups with full trade details</span></h2>
  {no_recs}
</div>"""

    cards = ""
    for rec in recs:
        signal  = rec["signal"]
        cls     = "rec-buy" if signal == "BUY" else "rec-sell"
        s_cls   = "green" if signal == "BUY" else "red"
        rr1     = rec.get("rr_target1", 0)
        rr2     = rec.get("rr_target2", 0)
        conf    = rec.get("confidence", 0)
        conf_c  = "green" if conf >= 70 else "yellow"
        pt_wr   = rec.get("paper_win_rate", 0)
        pt_n    = rec.get("paper_trades_on_stock", 0)
        pt_exp  = rec.get("paper_expectancy", 0)

        reasons_html = "".join(f"<li>{r}</li>" for r in rec.get("reasons", [])[:6])
        pat_badges   = "".join(
            f'<span class="badge badge-blue" style="margin:2px;font-size:.65rem">{p.replace("_"," ")}</span>'
            for p in rec.get("reliable_patterns", [])[:5]
        )

        warn_html = ""
        for w in rec.get("market_warning", []):
            warn_html += f'<div class="warn-banner" style="margin-top:8px;font-size:.75rem">⚠ {w}</div>'

        # Score breakdown bars
        tech_score = rec.get("buy_score", rec.get("sell_score", 0))
        fund_score = rec.get("fundamental_score", 0)
        news_score_val = rec.get("news_score_display", 0)
        pat_score = rec.get("pattern_score", 0)
        score_breakdown = (
            _score_bar("Technical", min(tech_score, 40), 40, "#6366f1") +
            _score_bar("Fundamental", min(fund_score, 30), 30, "#10b981") +
            _score_bar("News", min(news_score_val, 20), 20, "#f59e0b") +
            _score_bar("Pattern", min(pat_score, 10), 10, "#06b6d4")
        )

        cards += f"""<div class="rec-card {cls}">
  <div class="rec-header">
    <div>
      <div class="rec-name">{rec.get('company_name','')}</div>
      <div class="rec-code">NSE: <strong>{rec.get('nse_code','')}</strong> &nbsp;|&nbsp; {rec.get('date','')}</div>
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
      <span class="badge {'badge-green' if signal=='BUY' else 'badge-red'}">{signal}</span>
      <span class="badge badge-blue">{rec.get('style','').capitalize()}</span>
      <span class="badge badge-cyan">{rec.get('hold_period','')}</span>
    </div>
  </div>

  <div class="rec-grid">
    <div class="rec-field">
      <div class="rec-field-val">₹{rec.get('cmp',0):,.2f}</div>
      <div class="rec-field-lbl">Current Price (CMP)</div>
    </div>
    <div class="rec-field" style="background:#1a2035">
      <div class="rec-field-val">₹{rec.get('entry_low',0):,.2f} – ₹{rec.get('entry_high',0):,.2f}</div>
      <div class="rec-field-lbl">Entry Zone</div>
    </div>
    <div class="rec-field" style="background:#1a0f0f">
      <div class="rec-field-val red">₹{rec.get('stop_loss',0):,.2f}</div>
      <div class="rec-field-lbl">Stop Loss ({rec.get('risk_pct',0):.1f}% from entry)</div>
    </div>
    <div class="rec-field" style="background:#0f1a10">
      <div class="rec-field-val green">₹{rec.get('target1',0):,.2f}</div>
      <div class="rec-field-lbl">Target 1 (R:R = 1:{rr1:.1f})</div>
    </div>
    <div class="rec-field" style="background:#0f1a14">
      <div class="rec-field-val" style="color:#34d399">₹{rec.get('target2',0):,.2f}</div>
      <div class="rec-field-lbl">Target 2 (R:R = 1:{rr2:.1f})</div>
    </div>
    <div class="rec-field">
      <div class="rec-field-val blue">{rec.get('recommended_qty',0)} shares</div>
      <div class="rec-field-lbl">Qty (2% capital risk = ₹{rec.get('max_loss_if_sl',0):,.0f} max loss)</div>
    </div>
  </div>

  <div style="display:flex;gap:16px;flex-wrap:wrap;margin:10px 0;font-size:.78rem">
    <div><span class="muted">Capital needed:</span> <strong>₹{rec.get('capital_needed',0):,.0f}</strong></div>
    <div><span class="muted">Nearest support:</span> <strong class="red">₹{rec.get('nearest_support',0):,.2f}</strong></div>
    <div><span class="muted">Nearest resistance:</span> <strong class="green">₹{rec.get('nearest_resistance',0):,.2f}</strong></div>
  </div>

  <div class="conf-bar">
    <div style="display:flex;justify-content:space-between;font-size:.72rem;margin-bottom:3px">
      <span class="muted">Confidence</span>
      <span class="{conf_c}">{conf:.0f}%</span>
    </div>
    <div class="progress">
      <div class="progress-fill" style="width:{conf:.0f}%;background:{'var(--green)' if conf>=70 else 'var(--yellow)'}"></div>
    </div>
  </div>

  <div style="margin-top:10px;padding:10px 12px;background:var(--card);border-radius:8px">
    <div style="font-size:.65rem;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em">Score Breakdown</div>
    {score_breakdown}
  </div>

  {f'<div style="margin-top:8px">{pat_badges}</div>' if pat_badges else ""}

  <div class="reasons-list">
    <div style="font-size:.72rem;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em">Why this trade</div>
    <ul>{reasons_html}</ul>
  </div>

  {f'''<div style="margin-top:10px;background:var(--card);border-radius:8px;padding:10px 12px;font-size:.78rem">
    <span class="muted">Paper track record on {rec.get("nse_code","")}: </span>
    <span>{pt_n} trades</span> ·
    <span class="{'green' if pt_wr>=0.55 else 'yellow'}">{pt_wr*100:.0f}% win rate</span> ·
    <span class="{'green' if pt_exp>0 else 'red'}">₹{pt_exp:+,.0f} expectancy</span>
  </div>''' if pt_n > 0 else ""}

  {warn_html}
  <div style="margin-top:10px;font-size:.7rem;color:var(--muted);border-top:1px solid var(--border);padding-top:8px">
    ⚠ Paper trade recommendation only. Not financial advice. Always use your own judgement.
  </div>
</div>"""

    return f"""<div class="section" id="recommendations">
  <h2>🎯 Stock Recommendations <span>{len(recs)} high-confidence setup(s) today</span></h2>
  {cards}
</div>"""


def _fund_table(fund: dict) -> str:
    """Render a compact fundamentals grid for a stock card."""
    if not fund:
        return ""
    rows = []
    def row(label, val, fmt="{}", color=None, suffix=""):
        if val is None:
            return
        try:
            display = fmt.format(val) + suffix
        except Exception:
            display = str(val)
        col = f' style="color:{color}"' if color else ""
        rows.append(f'<div class="fund-row"><span class="muted">{label}</span>'
                    f'<span{col}>{display}</span></div>')

    pe   = fund.get("pe_ratio")
    mktcap = fund.get("market_cap_cr")
    div  = fund.get("dividend_yield_pct")
    np_q = fund.get("np_qtr_cr")
    np_v = fund.get("np_qtr_var_pct")
    sal_q = fund.get("sales_qtr_cr")
    sal_v = fund.get("sales_qtr_var_pct")
    roce = fund.get("roce")
    roe  = fund.get("roe")
    de   = fund.get("debt_equity")
    promo = fund.get("promoter_holding_pct")
    analyst_up = fund.get("analyst_upside_pct")
    et   = fund.get("earnings_trend", "")

    row("P/E",          pe,     "{:.1f}x")
    row("Mkt Cap",      mktcap, "₹{:,.0f} Cr") if mktcap else None
    row("Div Yield",    div,    "{:.2f}%")
    row("NP Qtr",       np_q,   "₹{:,.0f} Cr")
    row("NP Qtr Var",   np_v,   "{:+.1f}%",
        color=("#10b981" if np_v and np_v > 0 else "#ef4444"))
    row("Sales Qtr",    sal_q,  "₹{:,.0f} Cr")
    row("Sales Var",    sal_v,  "{:+.1f}%",
        color=("#10b981" if sal_v and sal_v > 0 else "#ef4444"))
    row("ROCE",         roce,   "{:.1f}%",
        color=("#10b981" if roce and roce > 15 else None))
    row("ROE",          roe,    "{:.1f}%",
        color=("#10b981" if roe and roe > 15 else None))
    row("D/E",          de,     "{:.2f}",
        color=("#10b981" if de is not None and de < 0.5 else
               "#ef4444" if de is not None and de > 1.5 else None))
    row("Promoter",     promo,  "{:.1f}%",
        color=("#10b981" if promo and promo > 55 else None))
    row("Analyst Up",   analyst_up, "{:+.1f}%",
        color=("#10b981" if analyst_up and analyst_up > 10 else None))
    if et and et not in ("unknown", "neutral"):
        et_color = "#10b981" if "beat" in et or et == "improving" else "#ef4444"
        rows.append(f'<div class="fund-row"><span class="muted">Earnings</span>'
                    f'<span style="color:{et_color}">{et.replace("_"," ")}</span></div>')

    if not rows:
        return ""
    return ('<div style="margin-top:10px;background:var(--card3);border-radius:8px;padding:10px 12px">'
            '<div style="font-size:.65rem;color:var(--muted);text-transform:uppercase;'
            'letter-spacing:.05em;margin-bottom:6px">Fundamentals</div>'
            f'{"".join(rows)}</div>')


def _section_watchlist(focus, stock_data, news_data, patterns, fundamentals=None) -> str:
    fundamentals = fundamentals or {}
    # During exploration (no focus stocks yet), show ALL fetched stocks
    display_tickers = focus if focus else sorted(stock_data.keys())
    phase_label = "deep monitoring" if focus else "exploration — all 50 stocks being scored"

    if not display_tickers:
        return f"""<div class="section" id="watchlist">
  <h2>🔭 Watchlist <span>Waiting for first data fetch</span></h2>
  <div class="card" style="padding:20px;text-align:center;color:var(--muted)">
    The agent hasn't fetched any stock data yet. Check back after the next scheduled run.
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
        short  = ticker.replace(".NS","")
        ph     = entry.get("price_history_60d", [])
        t_cls  = {"strong_up":"green","up":"green","sideways":"yellow",
                  "down":"red","strong_down":"red"}.get(trend,"muted")
        n_cls  = "green" if ns > 0.08 else ("red" if ns < -0.08 else "muted")
        r_cls  = "green" if 35 <= rsi <= 62 else "red"
        m_cls  = "green" if macd_h > 0 else "red"
        tk_pat = patterns.get(ticker, {})
        style  = tk_pat.get("preferred_style", "learning")

        hl = news.get("headlines", [])
        news_html = f'<div class="note-line" style="margin-top:6px;font-size:.72rem;color:var(--muted)">{hl[0][:70]}…</div>' if hl else ""

        spark_svg    = _sparkline(ph) if ph else ""
        rsi_bar_html = _rsi_bar(rsi)
        is_focus     = ticker in focus

        # Exploration score: simple momentum proxy visible on card
        expl_score = 0
        if trend in ("strong_up",): expl_score += 3
        elif trend in ("up",):      expl_score += 2
        if rsi > 45 and rsi < 65:   expl_score += 2
        if macd_h > 0:              expl_score += 1
        if vol_r >= 1.3:            expl_score += 2
        expl_score = min(expl_score, 8)
        expl_bar_pct = expl_score / 8 * 100
        expl_color = "#10b981" if expl_score >= 6 else ("#f59e0b" if expl_score >= 3 else "#64748b")

        focus_badge = '<span class="badge badge-cyan" style="font-size:.62rem">FOCUS</span>' if is_focus else ""
        style_badge = f'<span class="pill pill-blue" style="margin-left:4px;font-size:.62rem">{style}</span>' if is_focus else ""

        cards += f"""<div class="stock-card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
    <div style="display:flex;align-items:center;gap:4px;flex-wrap:wrap">
      <strong style="font-size:.95rem">{short}</strong>
      {focus_badge}{style_badge}
    </div>
    <span class="pill {'pill-green' if ns>0.08 else ('pill-red' if ns<-0.08 else 'pill-yellow')}" style="font-size:.62rem">
      news {ns:+.2f}
    </span>
  </div>
  <div style="font-size:1.1rem;font-weight:700;margin-bottom:4px">
    ₹{close:,.2f}
    <span class="{t_cls}" style="font-size:.75rem;font-weight:400"> {trend.replace('_',' ')}</span>
  </div>
  <div style="margin:6px 0;overflow:hidden">{spark_svg}</div>
  {rsi_bar_html}
  <div style="margin-top:6px">
    <div style="display:flex;justify-content:space-between;font-size:.65rem;color:#64748b;margin-bottom:2px">
      <span>Momentum score</span><span style="color:{expl_color}">{expl_score}/8</span>
    </div>
    <div style="height:3px;background:#1e293b;border-radius:2px">
      <div style="width:{expl_bar_pct:.0f}%;height:100%;background:{expl_color};border-radius:2px"></div>
    </div>
  </div>
  <div class="irow" style="margin-top:6px"><span class="muted">MACD hist</span>
    <span class="{m_cls}">{macd_h:+.4f}</span></div>
  <div class="irow"><span class="muted">ATR%</span><span>{atr_p:.2f}%</span></div>
  <div class="irow"><span class="muted">Rel Volume</span>
    <span class="{'green' if vol_r>=1.3 else 'muted'}">{vol_r:.2f}x</span></div>
  {news_html}
  {_fund_table(fundamentals.get(ticker, {})) if is_focus else ""}
</div>"""

    return f"""<div class="section" id="watchlist">
  <h2>🔭 Watchlist <span>{len(display_tickers)} stocks — {phase_label}</span></h2>
  <div class="grid3">{cards}</div>
</div>"""


def _section_trades(book) -> str:
    trades = list(reversed(book.get("closed_trades", [])))
    if not trades:
        return f"""<div class="section" id="trades">
  <h2>📋 Paper Trade History</h2>
  <div class="card" style="padding:20px;text-align:center;color:var(--muted)">
    Paper trades will appear here once the agent enters the trading phase.
  </div>
</div>"""
    rows = ""
    for t in trades[:60]:
        pnl = t.get("pnl", 0)
        rows += f"""<tr>
  <td class="muted">{t.get('close_date','?')}</td>
  <td><strong>{t['ticker'].replace('.NS','')}</strong></td>
  <td><span class="pill {'pill-green' if t['action']=='BUY' else 'pill-red'}">{t['action']}</span></td>
  <td>₹{t['entry']:.2f}</td>
  <td>₹{t.get('exit_price',0):.2f}</td>
  <td class="{'green' if pnl>=0 else 'red'}">₹{pnl:+.0f}</td>
  <td class="{'green' if pnl>=0 else 'red'}">{t.get('pnl_pct',0):+.1f}%</td>
  <td><span class="pill {'pill-green' if t.get('won') else 'pill-red'}">
    {'✓ WIN' if t.get('won') else '✗ LOSS'}</span></td>
  <td class="muted" style="font-size:.72rem">{t.get('exit_reason','?').replace('_',' ')}</td>
  <td><span class="pill pill-blue">{t.get('style','?')}</span></td>
  <td class="muted">{t.get('open_date','?')}</td>
</tr>"""

    return f"""<div class="section" id="trades">
  <h2>📋 Paper Trade History <span>{len(trades)} closed trades</span></h2>
  <div class="card" style="overflow-x:auto">
    <table>
      <thead><tr>
        <th>Date</th><th>Stock</th><th>Side</th><th>Entry</th><th>Exit</th>
        <th>P&L</th><th>P&L%</th><th>Result</th><th>Reason</th><th>Style</th><th>Opened</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>"""


def _section_research(state, decisions) -> str:
    notes = state.get("brain_notes", [])
    notes_html = "".join(
        f'<div class="note-line"><strong>{n.split("]")[0].replace("[","").strip() if "]" in n else ""}</strong> '
        f'{n.split("]",1)[1].strip() if "]" in n else n}</div>'
        for n in reversed(notes[-30:])
    ) or '<div class="muted" style="padding:10px">No brain notes yet.</div>'

    recent_dec = [d for d in reversed(decisions[-50:]) if d.get("signal") in ("BUY","SELL")][:15]
    dec_rows = ""
    for d in recent_dec:
        dec_rows += f"""<tr>
  <td class="muted">{d.get('date','?')}</td>
  <td>{d.get('ticker','').replace('.NS','')}</td>
  <td>{d.get('session','?')}</td>
  <td><span class="pill {'pill-green' if d['signal']=='BUY' else 'pill-red'}">{d['signal']}</span></td>
  <td>₹{d.get('entry',0):.2f}</td>
  <td class="muted" style="font-size:.72rem">{', '.join(d.get('patterns',[])[:3])}</td>
  <td class="muted">{d.get('confidence',0):.0f}%</td>
</tr>"""

    dec_table = f"""<div class="card" style="overflow-x:auto;margin-top:12px">
  <h3 style="margin-bottom:10px">Recent Analyst Decisions</h3>
  <table>
    <thead><tr><th>Date</th><th>Stock</th><th>Session</th><th>Signal</th><th>Price</th><th>Patterns</th><th>Conf</th></tr></thead>
    <tbody>{dec_rows}</tbody>
  </table>
</div>""" if dec_rows else ""

    return f"""<div class="section" id="research">
  <h2>📝 Research Log <span>Agent's autonomous notes and decisions</span></h2>
  <div class="card">{notes_html}</div>
  {dec_table}
</div>"""


def _section_brain(focus, patterns) -> str:
    if not patterns:
        return f"""<div class="section" id="brain">
  <h2>🧠 Brain Insights <span>What the agent has learned</span></h2>
  <div class="card" style="padding:20px;text-align:center;color:var(--muted)">
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

        sorted_pats = sorted(rp.items(), key=lambda x: x[1]["wins"]+x[1]["losses"], reverse=True)[:6]
        pat_rows = ""
        for name, p in sorted_pats:
            total = p["wins"] + p["losses"]
            rel   = p["reliability"]
            r_cls = "green" if rel >= 0.55 else ("red" if rel <= 0.42 else "yellow")
            bar_w = int(rel * 100)
            pat_rows += f"""<div style="margin-bottom:8px">
  <div style="display:flex;justify-content:space-between;font-size:.75rem;margin-bottom:3px">
    <span>{name.replace('_',' ')}</span>
    <span class="{r_cls}">{rel:.0%} ({p['wins']}W/{p['losses']}L / {total} total)</span>
  </div>
  <div class="progress">
    <div class="progress-fill" style="width:{bar_w}%;background:{'var(--green)' if rel>=0.55 else ('var(--red)' if rel<=0.42 else 'var(--yellow)')}"></div>
  </div>
</div>"""

        cards += f"""<div class="card">
  <div style="display:flex;justify-content:space-between;margin-bottom:10px">
    <strong>{ticker.replace('.NS','')}</strong>
    <span class="pill pill-blue">{style}</span>
  </div>
  <div style="display:flex;gap:16px;font-size:.75rem;color:var(--muted);margin-bottom:10px">
    <span>Swing: {sw}W/{sl_}L</span>
    <span>Intraday: {iw}W/{il}L</span>
  </div>
  {pat_rows or '<div class="muted" style="font-size:.75rem">No resolved patterns yet.</div>'}
</div>"""

    return f"""<div class="section" id="brain">
  <h2>🧠 Brain Insights <span>Learned pattern reliability per stock</span></h2>
  <div class="grid2">{cards}</div>
</div>"""


# ─────────────────────────────────────────────────────────────────────────────
# JavaScript — sparkline renderer
# ─────────────────────────────────────────────────────────────────────────────

def _scripts() -> str:
    return """<script>
document.querySelectorAll('[data-spark]').forEach(el => {
  const vals = JSON.parse(el.dataset.spark || '[]');
  if (!vals.length) return;
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = mx - mn || 1;
  const W = el.offsetWidth || 280, H = parseInt(el.style.height) || 50;
  const pts = vals.map((v, i) =>
    `${(i / (vals.length - 1) * W).toFixed(1)},${(H - ((v - mn) / rng) * (H - 4) - 2).toFixed(1)}`
  ).join(' ');
  const col = el.dataset.color || (vals[vals.length-1] >= vals[0] ? '#10b981' : '#ef4444');
  const gradId = 'g' + Math.random().toString(36).slice(2,7);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:${H}px;display:block">
    <defs>
      <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${col}" stop-opacity="0.2"/>
        <stop offset="100%" stop-color="${col}" stop-opacity="0"/>
      </linearGradient>
    </defs>
    <polygon points="${pts} ${W},${H} 0,${H}" fill="url(#${gradId})"/>
    <polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.8" stroke-linejoin="round"/>
  </svg>`;
});
</script>"""
