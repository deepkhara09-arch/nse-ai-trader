"""
Central configuration — all tunable parameters in one place.
"""

# ── UTF-8 safety (imported by ~every module, so this runs everywhere) ──────────
# Log lines use unicode glyphs (₹ → — · ✅ ❌). On a non-UTF-8 console (Windows
# cp1252) a bare print() of those crashes the process. GitHub Actions is UTF-8 so
# prod is fine, but this makes the tool crash-proof for ANY entry point (tests,
# local runs, imported use) — not just main.py.
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ── Trading sessions (IST) ────────────────────────────────────────────────────
# The agent runs 5x per trading day; each session has a distinct role:
SESSIONS = {
    "preopen":   {"utc_hour": 3,  "utc_min": 5,  "ist": "08:35"},   # pre-open macro sweep (no trading)
    "morning":   {"utc_hour": 4,  "utc_min": 5,  "ist": "09:35"},   # market open
    "midday":    {"utc_hour": 6,  "utc_min": 15, "ist": "11:45"},   # midday review
    "afternoon": {"utc_hour": 7,  "utc_min": 45, "ist": "13:15"},   # afternoon positioning
    "intraday_close": {"utc_hour": 9, "utc_min": 45, "ist": "15:15"},  # square off intraday before 3:30 close
    "preclose":  {"utc_hour": 10, "utc_min": 10, "ist": "15:40"},   # post-close: captures full session incl 3:30 close + day rollover
}

# ── Phase durations (trading sessions) ───────────────────────────────────────
# Longer phases = more data = better decisions. We'd rather wait.
EXPLORATION_DAYS   = 7    # raised from 5 → 7 days watching full universe
ANALYSIS_DAYS      = 14   # raised from 10 → 14 days deep watching selected stocks
PAPER_TRADING_DAYS = 30   # raised from 20 → 30 days paper trading before alerting
# After paper trading, agent cycles back to keep signals fresh

# ── Universe — Nifty 100 stocks (CMP filter ≤ ₹5000 applied at runtime) ───────
# Stocks above ₹5000/share are excluded automatically during exploration scoring
# so you never get stuck in a high-priced name with poor position sizing.
MAX_STOCK_PRICE = 5000   # filter applied after first data fetch

NSE_UNIVERSE = [
    # ── Nifty 50 ──────────────────────────────────────────────────────────────
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "BHARTIARTL.NS", "ICICIBANK.NS",
    "INFY.NS", "SBIN.NS", "HINDUNILVR.NS", "ITC.NS", "LT.NS",
    "BAJFINANCE.NS", "HCLTECH.NS", "KOTAKBANK.NS", "AXISBANK.NS", "WIPRO.NS",
    "ASIANPAINT.NS", "MARUTI.NS", "NTPC.NS", "ONGC.NS", "TITAN.NS",
    "ULTRACEMCO.NS", "SUNPHARMA.NS", "POWERGRID.NS", "TATASTEEL.NS",
    "ADANIENT.NS", "ADANIPORTS.NS", "JSWSTEEL.NS", "COALINDIA.NS", "NESTLEIND.NS",
    "TECHM.NS", "GRASIM.NS", "TATACONSUM.NS", "BAJAJFINSV.NS", "CIPLA.NS",
    "DRREDDY.NS", "DIVISLAB.NS", "EICHERMOT.NS", "HEROMOTOCO.NS", "APOLLOHOSP.NS",
    "INDUSINDBK.NS", "BRITANNIA.NS", "BPCL.NS", "TRENT.NS", "BEL.NS",
    "SHRIRAMFIN.NS", "HINDALCO.NS", "SBILIFE.NS", "HDFCLIFE.NS", "PIDILITIND.NS",

    # ── Nifty Next 50 (51–100) ────────────────────────────────────────────────
    "SIEMENS.NS", "HAVELLS.NS", "DABUR.NS", "MARICO.NS", "GODREJCP.NS",
    "MUTHOOTFIN.NS", "IDFCFIRSTB.NS", "BANDHANBNK.NS", "PNB.NS", "CANBK.NS",
    "BANKBARODA.NS", "UNIONBANK.NS", "IRCTC.NS", "IRFC.NS", "HAL.NS",
    "ZOMATO.NS", "NYKAA.NS", "PAYTM.NS", "POLICYBZR.NS", "DMART.NS",
    "ABCAPITAL.NS", "MFSL.NS", "LICI.NS", "GICRE.NS", "NIACL.NS",
    "ICICIGI.NS", "HDFCAMC.NS", "NIPPONLIFE.NS", "AAVAS.NS", "HOMEFIRST.NS",
    "CHOLAFIN.NS", "M&MFIN.NS", "BAJAJHLDNG.NS", "PGHH.NS", "COLPAL.NS",
    "EMAMILTD.NS", "VBL.NS", "TATAPOWER.NS", "ADANIGREEN.NS", "ADANIPORTS.NS",
    "NHPC.NS", "SJVN.NS", "RECLTD.NS", "PFC.NS", "GAIL.NS",
    "IOC.NS", "HINDPETRO.NS", "APLAPOLLO.NS", "POLYCAB.NS", "CUMMINSIND.NS",

    # ── User-added stocks (June 2026) — treated identically across all phases ──
    "ANANTRAJ.NS", "ETERNAL.NS", "JIOFIN.NS", "ASHOKLEY.NS", "TMPV.NS",
    "FEDERALBNK.NS", "SWIGGY.NS", "BAJAJFL.NS", "BHEL.NS",
    # (ITC, TATASTEEL, TATAPOWER, PAYTM, BEL already present above)
]
# Deduplicate (ADANIPORTS appears in both halves)
NSE_UNIVERSE = list(dict.fromkeys(NSE_UNIVERSE))

FOCUS_STOCK_COUNT = 15   # increased from 12 → wider coverage across sectors

# Concurrent background exploration batches. Each is an independent full-universe
# scan that feeds the perpetual focus-competition pool. More batches = a fresher,
# more diverse candidate pool, at the cost of more scoring CPU per preclose. They
# reuse already-fetched data (no extra network), so 5 stays well under the runner
# timeout. This is the engine that keeps recommendations from ever going stale.
CONCURRENT_BATCH_CAP = 5

# ── Capital & risk ────────────────────────────────────────────────────────────
INITIAL_CAPITAL        = 100_000   # virtual INR
MAX_POSITION_SIZE_PCT  = 0.12      # max 12% per trade (normal market)
MAX_OPEN_POSITIONS     = 5         # never hold more than 5 at once
MAX_DAILY_LOSS_PCT     = 0.03      # stop all trading if day loss > 3%
MAX_SECTOR_POSITIONS   = 2         # max open positions from the same sector simultaneously

# Max holding period (trading days) per style before a time-based exit. Intraday
# is squared off same day; swing rides a couple of weeks; long-term is a
# fundamentally-driven hold of a couple of months. These make the paper trade's
# behaviour MATCH the horizon the recommendation shows the user.
MAX_HELD_DAYS = {
    "intraday":  1,
    "swing":     10,
    "long_term": 30,   # ~6 trading weeks — a positional hold, force-exit at day 30
}

# ATR-based stop/target (overrides flat % when ATR is available)
ATR_STOP_MULTIPLIER    = 1.5       # stop = entry ± 1.5x ATR
ATR_TARGET_MULTIPLIER  = 3.0       # target = entry ± 3.0x ATR (2:1 R:R minimum)
FLAT_STOP_PCT          = 0.035     # fallback if ATR unavailable
FLAT_TARGET_PCT        = 0.07

# ── Volatility regime scaling ─────────────────────────────────────────────────
# Position size and stop width scale with VIX so dollar risk stays constant.
# Normal (VIX < 15): full size. Caution (VIX 15-20): 75%. Danger (VIX >= 20): 50%.
VOL_REGIME_NORMAL_MAX_PCT   = 0.12   # 12% position at normal VIX
VOL_REGIME_CAUTION_MAX_PCT  = 0.09   # 9%  position when VIX 15-20
VOL_REGIME_DANGER_MAX_PCT   = 0.06   # 6%  position when VIX >= 20 (but trade_allowed)
# ATR stop multiplier also widens in high vol (give trades more room to breathe)
VOL_REGIME_CAUTION_ATR_MULT = 1.75   # wider stop when VIX 15-20
VOL_REGIME_DANGER_ATR_MULT  = 2.25   # even wider when VIX 20-25

# Win rate needed before alerting you
# Conservative thresholds — we'd rather wait longer and be right than rush
WIN_RATE_THRESHOLD     = 0.60      # raised from 0.57 — need 60%+ win rate

# ── Post-validation drift monitor ─────────────────────────────────────────────
# Validation must be EARNED CONTINUOUSLY, not once. In the alerting phase the
# rolling win-rate over the last DRIFT_WINDOW_TRADES closed trades is re-checked
# every preclose; if it decays below DRIFT_WR_FLOOR the tool demotes itself back
# to paper_trading (alert_sent=False) — recommendations automatically revert to
# the honest 'practice / not-yet-validated' framing until the edge is re-proven.
DRIFT_WINDOW_TRADES = 20
DRIFT_WR_FLOOR      = 0.45
MIN_TRADES_FOR_SIGNAL  = 25        # raised from 12 — need 25 closed paper trades

# Recommendation validity — how long a rec stays "fresh" after generation
# If price moves more than this % away from entry zone, rec is flagged as STALE
REC_STALE_PRICE_MOVE_PCT = 0.8    # flag if CMP has moved >0.8% from entry midpoint
# Session validity windows (IST) — what each session's recs are valid until
# (i.e. until the NEXT session that regenerates them). Matches the 5-session day.
REC_SESSION_VALID_UNTIL = {
    "preopen":   "09:35 IST",   # pre-open backdrop valid until the morning run
    "morning":   "11:45 IST",   # morning recs valid until midday run
    "midday":    "13:15 IST",   # midday recs valid until afternoon run
    "afternoon": "15:15 IST",   # afternoon recs valid until intraday-close run
    "intraday_close": "15:40 IST",  # intraday square-off valid until preclose run
    "preclose":  "09:35 IST",   # preclose recs valid until next morning (next trading day)
}

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
PATTERN_DECAY_RATE     = 0.02      # (legacy) kept for back-compat; superseded by half-life
# Recency decay half-life for learned pattern evidence (in days). The accumulated
# wins/losses behind a pattern's reliability halve over this span, so RECENT trade
# outcomes dominate and a pattern that stops working loses its edge instead of
# coasting on old wins. Markets are non-stationary — this keeps learning adaptive.
# ~120 days ≈ 6 months: fast enough to adapt, slow enough to ignore short noise.
PATTERN_DECAY_HALFLIFE_DAYS = 120
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
