"""
Central configuration — all tunable parameters in one place.
"""

# ── Trading sessions (IST) ────────────────────────────────────────────────────
# The agent runs 3x per day. Each session has a role:
SESSIONS = {
    "morning":   {"utc_hour": 4,  "utc_min": 0,  "ist": "09:30"},   # market open
    "midday":    {"utc_hour": 6,  "utc_min": 30, "ist": "12:00"},   # midday review
    "preclose":  {"utc_hour": 9,  "utc_min": 30, "ist": "15:00"},   # pre-close decisions
}

# ── Phase durations (calendar days) ──────────────────────────────────────────
EXPLORATION_DAYS   = 5    # watch full universe
ANALYSIS_DAYS      = 10   # deep watch selected stocks
PAPER_TRADING_DAYS = 20   # paper trade & learn
# After paper trading, agent cycles back to keep signals fresh

# ── Universe — top 50 NSE stocks via yfinance ─────────────────────────────────
NSE_UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
    "LT.NS", "AXISBANK.NS", "ASIANPAINT.NS", "MARUTI.NS", "BAJFINANCE.NS",
    "HCLTECH.NS", "WIPRO.NS", "ULTRACEMCO.NS", "TITAN.NS", "SUNPHARMA.NS",
    "NESTLEIND.NS", "POWERGRID.NS", "NTPC.NS", "ONGC.NS", "COALINDIA.NS",
    "TATAMOTORS.NS", "TATASTEEL.NS", "JSWSTEEL.NS", "ADANIENT.NS", "ADANIPORTS.NS",
    "TECHM.NS", "DRREDDY.NS", "DIVISLAB.NS", "CIPLA.NS", "BAJAJFINSV.NS",
    "EICHERMOT.NS", "HEROMOTOCO.NS", "APOLLOHOSP.NS", "TATACONSUM.NS", "BRITANNIA.NS",
    "PIDILITIND.NS", "DABUR.NS", "MARICO.NS", "GODREJCP.NS", "MUTHOOTFIN.NS",
    "INDUSINDBK.NS", "BANDHANBNK.NS", "IDFCFIRSTB.NS", "GRASIM.NS", "SHREECEM.NS",
]

FOCUS_STOCK_COUNT = 12   # stocks to deeply monitor after exploration

# ── Capital & risk ────────────────────────────────────────────────────────────
INITIAL_CAPITAL        = 100_000   # virtual INR
MAX_POSITION_SIZE_PCT  = 0.12      # max 12% per trade
MAX_OPEN_POSITIONS     = 5         # never hold more than 5 at once
MAX_DAILY_LOSS_PCT     = 0.03      # stop all trading if day loss > 3%

# ATR-based stop/target (overrides flat % when ATR is available)
ATR_STOP_MULTIPLIER    = 1.5       # stop = entry ± 1.5x ATR
ATR_TARGET_MULTIPLIER  = 3.0       # target = entry ± 3.0x ATR (2:1 R:R minimum)
FLAT_STOP_PCT          = 0.035     # fallback if ATR unavailable
FLAT_TARGET_PCT        = 0.07

# Win rate needed before alerting you
WIN_RATE_THRESHOLD     = 0.57
MIN_TRADES_FOR_SIGNAL  = 12

# ── Technical indicator parameters ───────────────────────────────────────────
EMA_SHORT    = 9
EMA_LONG     = 21
EMA_TREND    = 50
RSI_PERIOD   = 14
MACD_FAST    = 12
MACD_SLOW    = 26
MACD_SIGNAL  = 9
BB_PERIOD    = 20
BB_STD       = 2
ATR_PERIOD   = 14
VOLUME_MA    = 20
VWAP_ENABLED = True    # computed from intraday ticks

# ── Brain self-improvement ────────────────────────────────────────────────────
# Pattern reliability decays over time if it hasn't been tested recently
PATTERN_DECAY_RATE     = 0.02      # reliability shrinks by this if untested > 7 days
MIN_PATTERN_SAMPLES    = 3         # minimum trades before trusting a pattern
CONFIDENCE_FLOOR       = 0.40      # patterns below this are ignored

# Signal scoring thresholds
BUY_SIGNAL_MIN_SCORE   = 5         # out of ~10 possible points
SELL_SIGNAL_MIN_SCORE  = 5
SIGNAL_SCORE_GAP       = 2         # buy and sell scores must differ by at least this

# ── News RSS feeds ────────────────────────────────────────────────────────────
NEWS_FEEDS = [
    "https://www.moneycontrol.com/rss/latestnews.xml",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021557.cms",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.livemint.com/rss/markets",
]

POSITIVE_WORDS = [
    "surge", "rally", "gain", "profit", "beat", "upgrade", "buy", "bullish",
    "record", "growth", "strong", "positive", "rise", "soar", "breakout",
    "outperform", "expansion", "revenue", "dividend", "acquisition", "partnership",
    "order", "wins", "approval", "launch", "robust", "recovery",
]
NEGATIVE_WORDS = [
    "fall", "drop", "loss", "miss", "downgrade", "sell", "bearish", "weak",
    "decline", "crash", "concern", "risk", "debt", "cut", "layoff",
    "underperform", "contraction", "penalty", "probe", "fraud", "recall",
    "resign", "investigation", "default", "write-off", "slowdown",
]

# ── File paths ────────────────────────────────────────────────────────────────
BRAIN_DIR           = "brain"
STATE_FILE          = "brain/state.json"
STOCK_DATA_FILE     = "brain/stock_data.json"
NEWS_FILE           = "brain/news_sentiment.json"
PATTERN_FILE        = "brain/patterns.json"
PAPER_TRADES_FILE   = "brain/paper_trades.json"
BRAIN_DECISIONS_FILE= "brain/decisions.json"
DAILY_LOG_FILE      = "brain/daily_log.json"
DASHBOARD_FILE        = "docs/index.html"
STRATEGY_REPORT       = "STRATEGY_REPORT.md"
RECOMMENDATIONS_FILE  = "brain/recommendations.json"
