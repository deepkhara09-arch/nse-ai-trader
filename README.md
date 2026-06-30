# NSE AI Trader 🤖📊

A **completely free**, self-learning stock market AI agent that acts as your personal investment analyst.
It studies NSE stocks, learns patterns from data, paper-trades to validate its strategy,
and publishes everything to a **live dashboard** — all automatically via GitHub Actions.

**Cost: ₹0. No paid APIs. No servers. Everything runs free on GitHub.**

---

## Live Dashboard

After setup, your dashboard will be live at:
```
https://YOUR_USERNAME.github.io/nse-ai-trader/
```

It shows:
- Agent status & phase timeline
- Paper portfolio value and equity curve
- Today's buy/sell signals (with entry, stop loss, target, risk:reward)
- All monitored stocks with indicator cards
- Full paper trade history with P&L
- Agent's research notes
- Learned patterns per stock

---

## How the Agent Thinks

The agent runs **6 times per trading day** (IST):

| Time (IST) | Session | What happens |
|------------|---------|-------------|
| 08:35 AM | Pre-open | Refresh global/India macro sentiment + dashboard (no trading) |
| 09:35 AM | Morning | Fetch market data, open new positions, analyse all stocks |
| 11:45 AM | Midday | Refresh prices, update unrealised P&L, add swing signals |
| 01:15 PM | Afternoon | Re-assess positions, trail stops, add intraday/swing signals |
| 03:15 PM | Intraday close | Square off intraday positions before the 15:30 close (swing positions ride on) |
| 03:40 PM | Pre-close | Post-close (after 15:30) full-session capture, close trades, record stats, roll the trading day |

### Autonomous learning phases

```
Days 1–7          Days 8–21           Days 22+
────────────      ────────────────    ─────────────────────────
EXPLORATION  →    ANALYSIS       →    PAPER TRADING → SIGNAL READY
Watch all NSE-    Pick 15 focus       Simulate real trades
100 daily         stocks, study them  Learn what works
Score each one    Learn patterns      Win rate ≥ 60% over 25+
                                      trades → alert you
```

### What it analyses (100% computed from raw price data)
- EMA 9 / 21 / 50 alignment
- RSI 14 (overbought/oversold)
- MACD histogram (momentum)
- Bollinger Bands (volatility)
- ATR-based stop loss & position sizing
- Relative volume (confirms breakouts)
- VWAP (intraday bias)
- Candlestick patterns (hammer, doji, marubozu, shooting star)
- News sentiment (free RSS feeds — Moneycontrol, ET, Business Standard)

### How it improves itself
Every closed paper trade teaches the brain:
- Which patterns led to wins → reliability score goes up
- Which patterns failed → reliability score decays
- Swing vs intraday — agent tracks which style works per stock
- Stale patterns automatically decay back toward uncertainty

---

## Setup (5 minutes)

### 1. Fork this repo
Click **Fork** at the top right of this page.

### 2. Enable GitHub Actions
Go to your forked repo → **Actions tab** → click **"I understand my workflows, enable them"**

### 3. Enable GitHub Pages
Go to **Settings → Pages → Source → Deploy from branch**
Select branch: `main`, folder: `/docs`
Click **Save**.

Your dashboard will be live at `https://YOUR_USERNAME.github.io/nse-ai-trader/` within a minute.

### 4. Run it manually (optional, to start immediately)
Go to **Actions → NSE AI Trader → Run workflow** → pick `morning` → Run.

That's it. The agent will now run automatically 3x per weekday.

---

## Reading Signals

When the agent has enough confidence, the dashboard will show signals like:

```
📈 RELIANCE — BUY Signal

Entry price:  ₹2,845
Stop loss:    ₹2,731  (4.0% risk)  ← exit here if wrong
Target:       ₹3,073  (8.0% gain) ← take profit here
Risk:Reward:  1 : 2.0
Style:        Swing (hold 5–10 days)
Confidence:   72%

Reasoning: All EMAs stacked bullish · RSI=52 healthy buy zone ·
           High volume (1.8x avg) · Learned pattern: ema_golden_cross (74%)
```

**Important:** Signals only appear when:
1. The agent has closed at least 12 paper trades
2. Win rate is ≥ 58%
3. Expectancy is positive (average trade makes money)

---

## Risk Rules (hardcoded into the agent)

- Maximum 12% of capital per trade
- Maximum 5 open positions at once
- Stop all new trades if daily paper loss > 3%
- Minimum risk:reward of 2:1 (ATR-based, not fixed %)
- Intraday positions always force-closed at the pre-close run (after the 15:30 close), using the closing price
- Signals only generated when buy/sell score gap ≥ 2 points (no marginal trades)

---

## File Structure

```
nse-ai-trader/
├── agent/
│   ├── main.py             # Daily orchestrator (session-aware)
│   ├── config.py           # All parameters — tune here
│   ├── brain.py            # Core intelligence: pattern detection + analyst opinion
│   ├── data_fetcher.py     # Yahoo Finance: daily + intraday 5-min bars
│   ├── news_fetcher.py     # RSS sentiment (free, no key)
│   ├── stock_scorer.py     # Exploration phase ranking
│   ├── paper_trader.py     # Session-aware paper trading engine
│   ├── dashboard.py        # Generates docs/index.html
│   └── report_generator.py # Generates STRATEGY_REPORT.md
├── brain/                  # Agent memory (auto-committed by bot)
│   ├── state.json          # Phase, day, focus stocks, brain notes
│   ├── stock_data.json     # Latest OHLCV + indicators
│   ├── news_sentiment.json # RSS sentiment history
│   ├── patterns.json       # Learned pattern reliability per stock
│   ├── paper_trades.json   # Trade book + P&L + equity curve
│   ├── decisions.json      # Every analyst decision the agent made
│   └── daily_log.json      # Session-by-session progress log
├── docs/
│   └── index.html          # ← Live dashboard (GitHub Pages)
├── .github/workflows/
│   └── daily_run.yml       # 5x daily schedule (preopen→preclose)
└── requirements.txt        # yfinance, pandas, numpy only
```

---

## Customisation

Edit [agent/config.py](agent/config.py):

| Parameter | Default | What it does |
|-----------|---------|-------------|
| `FOCUS_STOCK_COUNT` | 12 | Stocks to deep-watch |
| `WIN_RATE_THRESHOLD` | 0.57 | Confidence before alerting you |
| `MAX_POSITION_SIZE_PCT` | 0.12 | Max 12% capital per trade |
| `MAX_OPEN_POSITIONS` | 5 | Never hold more than 5 at once |
| `ATR_STOP_MULTIPLIER` | 1.5 | Stop = 1.5x ATR from entry |
| `ATR_TARGET_MULTIPLIER` | 3.0 | Target = 3x ATR from entry |
| `EXPLORATION_DAYS` | 5 | Days to watch all 50 stocks |
| `ANALYSIS_DAYS` | 10 | Days to deep-study selected stocks |
| `NSE_UNIVERSE` | 50 stocks | Add/remove tickers here |

---

## Disclaimer

> This tool is for **educational and research purposes only**.
> Paper trading results do not guarantee real trading profits.
> NSE stocks carry market risk. Always consult a SEBI-registered advisor before investing real money.
> Never invest more than you can afford to lose.
