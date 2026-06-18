"""
Data fetcher — downloads OHLCV using yfinance 0.2.48.

Yahoo Finance blocks GitHub Actions IPs via Cloudflare. The fix:
use a requests.Session with real browser headers injected into yfinance.
This is the approach that works with yfinance 0.2.48 on all platforms.
"""

import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Dict, List

import pandas as pd
import requests
import yfinance as yf

from agent.config import (
    BRAIN_DIR, STOCK_DATA_FILE,
    EMA_SHORT, EMA_LONG, EMA_TREND,
    RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BB_PERIOD, BB_STD, ATR_PERIOD, VOLUME_MA,
)

# ── Browser-spoofed session — bypasses Yahoo's bot detection ──────────────────
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
})


def _download(ticker: str, **kwargs) -> pd.DataFrame:
    """Download via yfinance with our browser-spoofed session."""
    kwargs.setdefault("progress", False)
    kwargs.setdefault("auto_adjust", True)
    # yfinance 0.2.x passes session to its internal downloader
    t = yf.Ticker(ticker, session=_SESSION)
    if "period" in kwargs:
        df = t.history(
            period=kwargs["period"],
            interval=kwargs.get("interval", "1d"),
            auto_adjust=kwargs.get("auto_adjust", True),
        )
    else:
        df = t.history(
            start=kwargs.get("start"),
            end=kwargs.get("end"),
            interval=kwargs.get("interval", "1d"),
            auto_adjust=kwargs.get("auto_adjust", True),
        )
    return df if df is not None else pd.DataFrame()


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_stock_data(tickers: List[str], session: str = "morning") -> Dict:
    result = {}
    for ticker in tickers:
        try:
            entry = _fetch_one(ticker, session)
            if entry:
                result[ticker] = entry
            time.sleep(0.6)
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

    df_daily = _download(
        ticker,
        start=start_daily.isoformat(),
        end=today.isoformat(),
        interval="1d",
    )
    if df_daily is None or df_daily.empty or len(df_daily) < 20:
        return {}

    df_daily      = _compute_indicators(df_daily)
    daily_summary = _summarize_daily(df_daily, ticker)

    intraday_summary = {}
    try:
        df_intra = _download(ticker, period="1d", interval="5m")
        if df_intra is not None and not df_intra.empty and len(df_intra) > 5:
            intraday_summary = _summarize_intraday(df_intra, session)
    except Exception as e:
        print(f"[data] intraday {ticker}: {e}")

    latest = daily_summary["latest"].copy()
    if intraday_summary:
        latest.update({
            "intraday_trend":     intraday_summary.get("trend"),
            "intraday_vwap":      intraday_summary.get("vwap"),
            "intraday_vol_surge": intraday_summary.get("vol_surge"),
            "session_high":       intraday_summary.get("session_high"),
            "session_low":        intraday_summary.get("session_low"),
            "current_price":      intraday_summary.get("current_price", latest.get("close")),
            "above_vwap":         intraday_summary.get("above_vwap"),
        })

    return {
        "ticker":             ticker,
        "fetched_at":         datetime.utcnow().isoformat(),
        "session":            session,
        "latest":             latest,
        "daily":              daily_summary,
        "intraday":           intraday_summary,
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

    body             = (close - open_).abs()
    candle_range     = (high - low).replace(0, 1e-9)
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
        if pct > 4:   return "strong_up"
        if pct > 1:   return "up"
        if pct < -4:  return "strong_down"
        if pct < -1:  return "down"
        return "sideways"

    return {
        "ticker": ticker,
        "date":   str(df.index[-1].date()),
        "latest": {
            "close":          f(l["Close"]),
            "open":           f(l["Open"]),
            "high":           f(l["High"]),
            "low":            f(l["Low"]),
            "volume":         int(l["Volume"]),
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
        "volume_history_20d": [int(v) for v in df["Volume"].squeeze().tail(20).tolist()],
        "avg_volume_20d":     int(df["Volume"].squeeze().tail(20).mean()),
    }


def _summarize_intraday(df: pd.DataFrame, session: str) -> dict:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close  = df["Close"].squeeze()
    high   = df["High"].squeeze()
    low    = df["Low"].squeeze()
    volume = df["Volume"].squeeze()

    tp      = (high + low + close) / 3
    vol_sum = volume.cumsum().iloc[-1]
    vwap    = float((tp * volume).cumsum().iloc[-1] / vol_sum) if vol_sum > 0 else float(close.iloc[-1])

    current  = float(close.iloc[-1])
    vol_avg  = float(volume.mean()) or 1
    vol_last = float(volume.iloc[-3:].mean()) if len(volume) >= 3 else vol_avg
    ema9     = close.ewm(span=9,  adjust=False).mean()
    ema21    = close.ewm(span=21, adjust=False).mean()

    if ema9.iloc[-1] > ema21.iloc[-1] and current > vwap:
        trend = "bullish"
    elif ema9.iloc[-1] < ema21.iloc[-1] and current < vwap:
        trend = "bearish"
    else:
        trend = "neutral"

    return {
        "session":       session,
        "bars":          len(df),
        "current_price": round(current, 2),
        "vwap":          round(vwap, 2),
        "session_high":  round(float(high.max()), 2),
        "session_low":   round(float(low.min()),  2),
        "trend":         trend,
        "vol_surge":     vol_last > vol_avg * 1.5,
        "above_vwap":    current > vwap,
    }
