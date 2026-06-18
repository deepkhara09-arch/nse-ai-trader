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
    if os.path.exists(STOCK_DATA_FILE):
        with open(STOCK_DATA_FILE) as f:
            return json.load(f)
    return {}


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

def _fetch_one(ticker: str, session: str) -> dict:
    today       = date.today()
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

    latest = daily_summary["latest"].copy()

    return {
        "ticker":             ticker,
        "fetched_at":         datetime.utcnow().isoformat(),
        "session":            session,
        "latest":             latest,
        "daily":              daily_summary,
        "intraday":           {},
        "price_history_60d":  daily_summary.get("price_history_60d", []),
        "volume_history_20d": daily_summary.get("volume_history_20d", []),
    }


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
            "vol_rel":        f(l["vol_rel"]),
            "volatility_10d": f(l["volatility_10d"]),
            "body_pct":       f(l["body_pct"]),
            "upper_wick_pct": f(l["upper_wick_pct"]),
            "lower_wick_pct": f(l["lower_wick_pct"]),
        },
        "trend_30d":          trend(30),
        "trend_10d":          trend(10),
        "trend_5d":           trend(5),
        "price_history_60d":  [round(float(p), 2) for p in close.tail(60).tolist()],
        "volume_history_20d": [int(v) if pd.notna(v) else 0 for v in df["Volume"].squeeze().tail(20).tolist()],
        "avg_volume_20d":     int(df["Volume"].squeeze().tail(20).mean()),
    }
