"""
Data fetcher — uses Stooq as primary source (no IP blocking, no auth needed).

Stooq ticker format for NSE: RELIANCE.NS → RELIANCE.IN
Indices: ^NSEI → ^NF (Nifty 50 futures proxy), but we use
         pandas_datareader with stooq reader which handles NSE tickers.

Fallback: direct Yahoo Finance v8/v11 API with crumb cookie handshake.
"""

import json
import os
import time
from datetime import date, datetime, timedelta
from io import StringIO
from typing import Dict, List

import pandas as pd
import requests

from agent.config import (
    BRAIN_DIR, STOCK_DATA_FILE,
    EMA_SHORT, EMA_LONG, EMA_TREND,
    RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BB_PERIOD, BB_STD, ATR_PERIOD, VOLUME_MA,
)
from agent.trading_calendar import ist_today

# ── Stooq HTTP session (no auth, works from any IP) ──────────────────────────
_STOOQ = requests.Session()
_STOOQ.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; NSEBot/1.0)",
    "Accept":     "text/html,application/xhtml+xml,*/*",
})

# ── Yahoo Finance crumb session (fallback) ────────────────────────────────────
_YF = requests.Session()
_YF.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
})
_YF_CRUMB: str = ""


def _stooq_ticker(yf_ticker: str) -> str:
    """Convert yfinance NSE ticker to Stooq format."""
    if yf_ticker.startswith("^"):
        # Index mapping
        mapping = {
            "^NSEI":    "^NF",     # Nifty 50 futures (closest proxy on stooq)
            "^NSEBANK": "^BNF",    # BankNifty futures
            "^INDIAVIX": None,     # not on stooq — use yf fallback
        }
        return mapping.get(yf_ticker)
    # Stock: RELIANCE.NS → RELIANCE.IN
    return yf_ticker.replace(".NS", ".IN")


def _stooq_download(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Download daily OHLCV from Stooq CSV endpoint."""
    stooq_t = _stooq_ticker(ticker)
    if not stooq_t:
        return pd.DataFrame()
    url = (
        f"https://stooq.com/q/d/l/"
        f"?s={stooq_t.lower()}"
        f"&d1={start.replace('-', '')}"
        f"&d2={end.replace('-', '')}"
        f"&i=d"
    )
    try:
        r = _STOOQ.get(url, timeout=15)
        if r.status_code != 200 or len(r.text) < 50:
            return pd.DataFrame()
        df = pd.read_csv(StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        # Stooq uses "Date" but sometimes returns an error HTML page
        date_col = next((c for c in df.columns if c.lower() == "date"), None)
        if date_col is None:
            return pd.DataFrame()
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.set_index(date_col).sort_index()
        df.columns = [c.strip().title() for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()


def _yf_crumb() -> str:
    """Get Yahoo Finance crumb for API calls."""
    global _YF_CRUMB
    if _YF_CRUMB:
        return _YF_CRUMB
    try:
        # Step 1: visit finance page to get cookies
        r = _YF.get("https://finance.yahoo.com", timeout=10)
        # Step 2: fetch crumb
        r2 = _YF.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10)
        if r2.status_code == 200 and r2.text.strip():
            _YF_CRUMB = r2.text.strip()
    except Exception:
        pass
    return _YF_CRUMB


def _yf_download(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Download from Yahoo Finance v8 API with crumb."""
    crumb = _yf_crumb()
    if not crumb:
        return pd.DataFrame()
    try:
        import time as _time
        p1 = int(_time.mktime(datetime.strptime(start, "%Y-%m-%d").timetuple()))
        p2 = int(_time.mktime(datetime.strptime(end,   "%Y-%m-%d").timetuple()))
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?period1={p1}&period2={p2}&interval=1d&events=history"
            f"&crumb={crumb}"
        )
        r = _YF.get(url, timeout=15)
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return pd.DataFrame()
        res       = result[0]
        timestamps = res.get("timestamp", [])
        q         = res.get("indicators", {}).get("quote", [{}])[0]
        adj       = res.get("indicators", {}).get("adjclose", [{}])
        adj_close = adj[0].get("adjclose", []) if adj else []
        df = pd.DataFrame({
            "Open":   q.get("open", []),
            "High":   q.get("high", []),
            "Low":    q.get("low",  []),
            "Close":  adj_close if adj_close else q.get("close", []),
            "Volume": q.get("volume", []),
        }, index=pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Kolkata"))
        df.index = df.index.date
        df.index = pd.DatetimeIndex(df.index)
        df = df.dropna(subset=["Open", "Close"])
        return df
    except Exception as e:
        print(f"[yf-api] {ticker}: {e}")
        return pd.DataFrame()


def _download_daily(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Primary: Stooq. Fallback: Yahoo Finance v8 API."""
    df = _stooq_download(ticker, start, end)
    if df is not None and not df.empty and len(df) >= 5:
        return df
    # fallback
    return _yf_download(ticker, start, end)


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_stock_data(tickers: List[str], session: str = "morning") -> Dict:
    # Warm NSE session once so live quote API calls have valid cookies
    _warm_nse_session()

    result = {}
    for ticker in tickers:
        try:
            entry = _fetch_one(ticker, session)
            if entry:
                result[ticker] = entry
            time.sleep(0.3)
        except Exception as e:
            print(f"[data] {ticker}: {e}")
    print(f"[data] fetched {len(result)}/{len(tickers)} tickers  session={session}")
    return result


def load_stock_data() -> Dict:
    from agent.io_safe import load_json_dict
    return load_json_dict(STOCK_DATA_FILE)


def save_stock_data(data: Dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(STOCK_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def merge_stock_data(existing: Dict, fresh: Dict) -> Dict:
    merged = dict(existing)
    for ticker, new_entry in fresh.items():
        if ticker in merged:
            old_snaps = merged[ticker].get("intraday_snapshots", [])
            old_snap  = merged[ticker].get("intraday")
            if old_snap:
                old_snaps = (old_snaps + [old_snap])[-6:]
            new_entry["intraday_snapshots"] = old_snaps
        merged[ticker] = new_entry
    return merged


# ── Internal ──────────────────────────────────────────────────────────────────

def _fetch_intraday_candles(ticker: str) -> list:
    """
    Fetch today's 5-minute OHLCV candles from Yahoo Finance v8 API.
    Returns a list of dicts [{time, open, high, low, close, volume}, ...]
    ordered oldest → newest, covering today's session so far.
    Free, no extra auth beyond the crumb already used for daily data.
    """
    crumb = _yf_crumb()
    if not crumb:
        return []
    try:
        import time as _time
        # Fetch last 1 day at 5-minute resolution — gives today's candles
        now_ts  = int(_time.time())
        ago_ts  = now_ts - 86400   # 24h back is enough to cover today's session
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?period1={ago_ts}&period2={now_ts}&interval=5m"
            f"&crumb={crumb}"
        )
        r = _YF.get(url, timeout=15)
        if r.status_code != 200:
            return []
        data   = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return []
        res        = result[0]
        timestamps = res.get("timestamp", [])
        q          = res.get("indicators", {}).get("quote", [{}])[0]
        opens   = q.get("open",   [])
        highs   = q.get("high",   [])
        lows    = q.get("low",    [])
        closes  = q.get("close",  [])
        volumes = q.get("volume", [])

        # The 24h window can reach into the PREVIOUS trading day's session. We only
        # want "today's session so far", so keep candles whose IST date matches the
        # most recent candle's IST date (= the current/just-closed session).
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        _IST = _tz(_td(hours=5, minutes=30))
        if not timestamps:
            return []
        session_date = _dt.fromtimestamp(timestamps[-1], _IST).date()

        candles = []
        for i, ts in enumerate(timestamps):
            o = opens[i]  if i < len(opens)   else None
            h = highs[i]  if i < len(highs)   else None
            l = lows[i]   if i < len(lows)    else None
            c = closes[i] if i < len(closes)  else None
            v = volumes[i]if i < len(volumes) else 0
            if None in (o, h, l, c):
                continue
            # Skip bars from an earlier session that leaked into the 24h window
            if _dt.fromtimestamp(ts, _IST).date() != session_date:
                continue
            candles.append({
                "time":   ts,           # unix timestamp
                "open":   round(float(o), 2),
                "high":   round(float(h), 2),
                "low":    round(float(l), 2),
                "close":  round(float(c), 2),
                "volume": int(v or 0),
            })
        return candles
    except Exception as e:
        print(f"[intraday] {ticker} candles failed: {e}")
        return []


def _summarise_intraday_candles(candles: list) -> dict:
    """
    From a list of 5-min candles, derive:
    - current_price  : last candle's close
    - day_open       : first candle's open
    - day_high       : max of all highs
    - day_low        : min of all lows
    - candle_sequence: list of (high, low) in order — used to determine
                       which level (target or stop) was touched first
    """
    if not candles:
        return {}
    return {
        "current_price":   candles[-1]["close"],
        "day_open":        candles[0]["open"],
        "day_high":        max(c["high"] for c in candles),
        "day_low":         min(c["low"]  for c in candles),
        "candle_sequence": [(c["high"], c["low"]) for c in candles],
        "candle_count":    len(candles),
    }


def _fetch_intraday_quote(symbol: str) -> dict:
    """
    Fetch live intraday quote from NSE's free quote API.
    Returns {current_price, day_high, day_low, day_open, prev_close, volume}
    or empty dict on failure.

    NSE quote API is free, no auth, but requires the Referer header.
    Symbol format: RELIANCE (no .NS suffix).
    """
    url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
    try:
        r = _NSE_SESSION.get(url, timeout=10)
        if r.status_code != 200:
            return {}
        data = r.json()
        pd_  = data.get("priceInfo", {})
        if not pd_:
            return {}
        return {
            "current_price": float(pd_.get("lastPrice",   0) or 0),
            "day_high":      float(pd_.get("intraDayHighLow", {}).get("max", 0) or pd_.get("high", 0) or 0),
            "day_low":       float(pd_.get("intraDayHighLow", {}).get("min", 0) or pd_.get("low",  0) or 0),
            "day_open":      float(pd_.get("open",  0) or 0),
            "prev_close":    float(pd_.get("previousClose", 0) or 0),
            "volume":        int(data.get("marketDeptOrderBook", {}).get("tradeInfo", {}).get("totalTradedVolume", 0) or 0),
        }
    except Exception:
        return {}


# Shared NSE session with required headers for live API
_NSE_SESSION = requests.Session()
_NSE_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":    "https://www.nseindia.com/",
    "Accept":     "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
})

def _warm_nse_session() -> bool:
    """Visit NSE homepage to get session cookies before hitting the API."""
    try:
        _NSE_SESSION.get("https://www.nseindia.com", timeout=10)
        return True
    except Exception:
        return False


def _fetch_one(ticker: str, session: str) -> dict:
    today       = ist_today()
    start_daily = today - timedelta(days=120)

    df_daily = _download_daily(
        ticker,
        start=start_daily.isoformat(),
        end=today.isoformat(),
    )
    if df_daily is None or df_daily.empty or len(df_daily) < 20:
        return {}

    df_daily      = _compute_indicators(df_daily)
    daily_summary = _summarize_daily(df_daily, ticker)
    if not daily_summary:
        return {}

    latest    = daily_summary["latest"].copy()
    prev_bar  = _bar_dict(df_daily, -2)
    prev2_bar = _bar_dict(df_daily, -3)

    # ── Live intraday candles: today's 5-min bars from Yahoo Finance ─────────────
    # Stooq/YF daily bars only include completed days. During market hours,
    # latest.high/low = yesterday's values. We need today's actual candle data
    # to know if target or stop was touched, and in what order.
    candles  = _fetch_intraday_candles(ticker)
    intraday = _summarise_intraday_candles(candles)

    if intraday and intraday.get("current_price", 0) > 0:
        latest["current_price"]    = intraday["current_price"]
        latest["session_high"]     = intraday["day_high"]
        latest["day_high"]         = intraday["day_high"]
        latest["session_low"]      = intraday["day_low"]
        latest["day_low"]          = intraday["day_low"]
        latest["day_open"]         = intraday["day_open"]
        # candle_sequence: list of (high, low) per 5-min bar in chronological order.
        # _check_exits uses this to determine which level — target or stop — was
        # touched first when both are breached in the same session.
        latest["candle_sequence"]  = intraday["candle_sequence"]
        latest["intraday_fetched"] = True
        # ── Keep "close" current ─────────────────────────────────────────────
        # Free daily feeds (Stooq/Yahoo) post the final daily bar with a lag, so
        # right after a session their last bar is YESTERDAY's close. The live
        # 5-min candle capture already has today's real price (and after close,
        # its last bar IS today's closing price). Preserve the true daily-bar
        # close separately, then make "close" reflect the freshest price so the
        # whole system (scoring, recs, dashboard) is in sync with reality.
        latest["daily_bar_close"]  = latest.get("close", 0)
        latest["close"]            = intraday["current_price"]
        latest["price_is_live"]    = True
        print(f"[intraday] {ticker}: {intraday['candle_count']} candles "
              f"| H={intraday['day_high']} L={intraday['day_low']} "
              f"last={intraday['current_price']} (daily_bar={latest['daily_bar_close']})")
    else:
        # Market closed or candles unavailable — fall back to NSE quote for
        # at least current price + day H/L, with no sequence info
        symbol   = ticker.replace(".NS", "")
        quote    = _fetch_intraday_quote(symbol)
        if quote and quote.get("current_price", 0) > 0:
            latest["current_price"] = quote["current_price"]
            if quote.get("day_high", 0) > 0:
                latest["session_high"] = quote["day_high"]
                latest["day_high"]     = quote["day_high"]
            if quote.get("day_low", 0) > 0:
                latest["session_low"] = quote["day_low"]
                latest["day_low"]     = quote["day_low"]
            if quote.get("day_open", 0) > 0:
                latest["day_open"]   = quote["day_open"]
            # NSE quote is fresher than a lagging daily bar — sync "close" to it
            latest["daily_bar_close"] = latest.get("close", 0)
            latest["close"]           = quote["current_price"]
            latest["price_is_live"]   = True
        else:
            latest["current_price"] = latest.get("close", 0)
            latest["price_is_live"] = False
        latest["candle_sequence"]  = []
        latest["intraday_fetched"] = False

    return {
        "ticker":             ticker,
        "fetched_at":         datetime.utcnow().isoformat(),
        "session":            session,
        "latest":             latest,
        "daily":              daily_summary,
        "intraday":           intraday,
        "intraday_candles":   candles[-20:],   # keep last 20 bars for dashboard sparkline
        "price_history_60d":  daily_summary.get("price_history_60d", []),
        "volume_history_20d": daily_summary.get("volume_history_20d", []),
        "prev_bar":           prev_bar,
        "prev2_bar":          prev2_bar,
    }


def _bar_dict(df: pd.DataFrame, idx: int) -> dict:
    """Extract one bar's indicator values as a lowercase-keyed dict."""
    if df is None or df.empty or len(df) < abs(idx):
        return {}
    row = df.iloc[idx]
    name_map = {
        'Close': 'close', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Volume': 'volume',
        'ema_short': 'ema_short', 'ema_long': 'ema_long', 'ema_trend': 'ema_trend',
        'rsi': 'rsi', 'macd': 'macd', 'macd_signal': 'macd_signal', 'macd_hist': 'macd_hist',
        'bb_pct': 'bb_pct', 'atr': 'atr', 'atr_pct': 'atr_pct', 'vol_rel': 'vol_rel',
        'body_pct': 'body_pct', 'upper_wick_pct': 'upper_wick_pct', 'lower_wick_pct': 'lower_wick_pct',
    }
    result = {}
    for src, dst in name_map.items():
        if src in row.index:
            try:
                result[dst] = round(float(row[src]), 4)
            except (TypeError, ValueError):
                pass
    return result


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close  = df["Close"].squeeze()
    high   = df["High"].squeeze()
    low    = df["Low"].squeeze()
    volume = df["Volume"].squeeze()
    open_  = df["Open"].squeeze()

    df["ema_short"] = close.ewm(span=EMA_SHORT, adjust=False).mean()
    df["ema_long"]  = close.ewm(span=EMA_LONG,  adjust=False).mean()
    df["ema_trend"] = close.ewm(span=EMA_TREND, adjust=False).mean()

    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))

    ema_f             = close.ewm(span=MACD_FAST,   adjust=False).mean()
    ema_s             = close.ewm(span=MACD_SLOW,   adjust=False).mean()
    df["macd"]        = ema_f - ema_s
    df["macd_signal"] = df["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    sma            = close.rolling(BB_PERIOD).mean()
    std            = close.rolling(BB_PERIOD).std()
    df["bb_upper"] = sma + BB_STD * std
    df["bb_lower"] = sma - BB_STD * std
    df["bb_pct"]   = (close - (sma - BB_STD * std)) / (2 * BB_STD * std + 1e-9)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"]     = tr.rolling(ATR_PERIOD).mean()
    df["atr_pct"] = df["atr"] / close * 100

    # ── ADX (Average Directional Index) — trend STRENGTH, not direction ──────────
    # Reuses the True Range above (Wilder's method). ADX answers a question none of
    # the other indicators do: "is this a real trend or just chop?" High ADX (>25)
    # = strong trend worth trading; low ADX (<20) = choppy/range-bound, where
    # breakout signals are unreliable. Lets the brain stand down in chop.
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm   = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm  = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr_w     = tr.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean()
    plus_di   = 100 * plus_dm.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean() / atr_w.replace(0, 1e-9)
    minus_di  = 100 * minus_dm.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean() / atr_w.replace(0, 1e-9)
    dx        = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
    df["adx"]      = dx.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean()
    df["plus_di"]  = plus_di
    df["minus_di"] = minus_di

    df["vol_ma"]  = volume.rolling(VOLUME_MA).mean()
    df["vol_rel"] = volume / df["vol_ma"].replace(0, 1)

    df["daily_ret"]      = close.pct_change() * 100
    df["volatility_10d"] = df["daily_ret"].rolling(10).std()

    body         = (close - open_).abs()
    candle_range = (high - low).replace(0, 1e-9)
    df["body_pct"]       = body / candle_range
    df["upper_wick_pct"] = (high - pd.concat([close, open_], axis=1).max(axis=1)) / candle_range
    df["lower_wick_pct"] = (pd.concat([close, open_], axis=1).min(axis=1) - low) / candle_range

    return df


def _summarize_daily(df: pd.DataFrame, ticker: str) -> dict:
    df = df.dropna(subset=["rsi", "atr"])
    if df.empty:
        return {}
    l = df.iloc[-1]

    def f(x):
        try:
            return round(float(x), 4)
        except Exception:
            return 0.0

    close = df["Close"].squeeze()

    def trend(n):
        if len(close) < n + 1:
            return "unknown"
        pct = (close.iloc[-1] - close.iloc[-n]) / close.iloc[-n] * 100
        if pct > 4:  return "strong_up"
        if pct > 1:  return "up"
        if pct < -4: return "strong_down"
        if pct < -1: return "down"
        return "sideways"

    price_hist = [round(float(p), 2) for p in close.tail(60).tolist()]
    rsi_hist   = [round(float(r), 2) for r in df["rsi"].tail(60).tolist()
                  if pd.notna(r)]

    return {
        "ticker": ticker,
        "date":   str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1])[:10],
        "latest": {
            "close":          f(l["Close"]),
            "open":           f(l["Open"]),
            "high":           f(l["High"]),
            "low":            f(l["Low"]),
            "volume":         int(l["Volume"]) if pd.notna(l["Volume"]) else 0,
            "ema_short":      f(l["ema_short"]),
            "ema_long":       f(l["ema_long"]),
            "ema_trend":      f(l["ema_trend"]),
            "rsi":            f(l["rsi"]),
            "macd":           f(l["macd"]),
            "macd_signal":    f(l["macd_signal"]),
            "macd_hist":      f(l["macd_hist"]),
            "bb_upper":       f(l["bb_upper"]),
            "bb_lower":       f(l["bb_lower"]),
            "bb_pct":         f(l["bb_pct"]),
            "atr":            f(l["atr"]),
            "atr_pct":        f(l["atr_pct"]),
            "adx":            f(l["adx"]),
            "plus_di":        f(l["plus_di"]),
            "minus_di":       f(l["minus_di"]),
            "trend_strength": ("strong" if f(l["adx"]) >= 25 else
                               "weak"   if f(l["adx"]) <  20 else "moderate"),
            "vol_rel":        f(l["vol_rel"]),
            "volatility_10d": f(l["volatility_10d"]),
            "body_pct":       f(l["body_pct"]),
            "upper_wick_pct": f(l["upper_wick_pct"]),
            "lower_wick_pct": f(l["lower_wick_pct"]),
            # Short histories for divergence detection in brain.py
            "price_history":  price_hist[-10:],
            "rsi_history":    rsi_hist[-10:],
            # 52-week computed from the fetched window (~120 days, best available)
            "week52_high":    round(float(df["High"].tail(252).max()), 2),
            "week52_low":     round(float(df["Low"].tail(252).min()), 2),
            "week52_position_pct": round(
                (float(l["Close"]) - float(df["Low"].tail(252).min())) /
                max(float(df["High"].tail(252).max()) - float(df["Low"].tail(252).min()), 0.01)
                * 100, 1
            ),
        },
        "trend_30d":          trend(30),
        "trend_10d":          trend(10),
        "trend_5d":           trend(5),
        "price_history_60d":  price_hist,
        "volume_history_20d": [int(v) if pd.notna(v) else 0 for v in df["Volume"].squeeze().tail(20).tolist()],
        "avg_volume_20d":     int(df["Volume"].squeeze().tail(20).mean()),
    }
