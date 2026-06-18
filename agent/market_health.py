"""
Market Health Monitor — checks Nifty, VIX, and sector strength each session.
"""

import json
import os
import time
from datetime import date, timedelta
from typing import Dict

import pandas as pd
import requests
import yfinance as yf

from agent.config import BRAIN_DIR

# Browser-spoofed session — same approach as data_fetcher
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

SECTOR_PROXIES = {
    "IT":     "ITBEES.NS",
    "Pharma": "PHARMABEES.NS",
    "Metal":  "METALBEEES.NS",
}

VIX_NORMAL  = 15.0
VIX_CAUTION = 20.0
VIX_DANGER  = 25.0

MARKET_STATE_FILE = "brain/market_health.json"


def assess_market(session: str = "morning") -> dict:
    health = {
        "date":          date.today().isoformat(),
        "session":       session,
        "nifty":         _fetch_index("^NSEI"),
        "vix":           _fetch_vix(),
        "bank_nifty":    _fetch_index("^NSEBANK"),
        "sectors":       _sector_strength(),
        "market_mood":   "neutral",
        "trade_allowed": True,
        "warnings":      [],
        "leading_sectors": [],
    }

    vix_val = health["vix"].get("value", 15)
    nifty   = health["nifty"]
    n_trend = nifty.get("trend_5d", "sideways")
    n_chg   = nifty.get("day_change_pct", 0)
    warnings = []
    trade_ok = True

    if vix_val >= VIX_DANGER:
        warnings.append(f"India VIX={vix_val:.1f} DANGER — no new trades today")
        trade_ok = False
    elif vix_val >= VIX_CAUTION:
        warnings.append(f"India VIX={vix_val:.1f} elevated — reduce position size")

    if n_trend in ("strong_down", "down"):
        warnings.append(f"Nifty in downtrend ({n_trend}) — only high-confidence BUY signals")
    if n_chg < -1.5:
        warnings.append(f"Nifty down {n_chg:.1f}% today — likely institutional selling")

    leading = [s for s, d in health["sectors"].items()
               if d.get("trend_5d") in ("up", "strong_up")]
    health["leading_sectors"] = leading

    if not warnings and n_trend in ("up", "strong_up") and vix_val < VIX_CAUTION:
        health["market_mood"] = "bullish"
    elif len(warnings) >= 2 or not trade_ok:
        health["market_mood"] = "bearish"

    health["trade_allowed"] = trade_ok
    health["warnings"] = warnings

    _save(health)
    _print_health(health)
    return health


def load_market_health() -> dict:
    if os.path.exists(MARKET_STATE_FILE):
        with open(MARKET_STATE_FILE) as f:
            return json.load(f)
    return {"trade_allowed": True, "market_mood": "neutral", "warnings": [],
            "nifty": {}, "vix": {}, "sectors": {}, "leading_sectors": []}


def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol, session=_SESSION)


def _fetch_index(symbol: str) -> dict:
    try:
        today = date.today()
        t = _ticker(symbol)
        df = t.history(
            start=(today - timedelta(days=20)).isoformat(),
            end=today.isoformat(),
            interval="1d",
            auto_adjust=True,
        )
        if df is None or df.empty or len(df) < 2:
            return {}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close   = df["Close"].squeeze()
        today_c = float(close.iloc[-1])
        prev_c  = float(close.iloc[-2])
        day_chg = (today_c - prev_c) / prev_c * 100

        def trend(n):
            if len(close) < n + 1: return "unknown"
            p = (close.iloc[-1] - close.iloc[-n]) / close.iloc[-n] * 100
            if p > 3:    return "strong_up"
            if p > 0.8:  return "up"
            if p < -3:   return "strong_down"
            if p < -0.8: return "down"
            return "sideways"

        return {
            "symbol":         symbol,
            "value":          round(today_c, 2),
            "day_change_pct": round(day_chg, 2),
            "trend_5d":       trend(5),
            "trend_20d":      trend(20),
        }
    except Exception as e:
        print(f"[market] {symbol}: {e}")
        return {}


def _fetch_vix() -> dict:
    try:
        t  = _ticker("^INDIAVIX")
        df = t.history(period="5d", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return {"value": 15.0, "level": "normal"}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        val   = float(df["Close"].squeeze().iloc[-1])
        level = "danger" if val >= VIX_DANGER else ("caution" if val >= VIX_CAUTION else "normal")
        return {"value": round(val, 2), "level": level}
    except Exception as e:
        print(f"[market] VIX: {e}")
        return {"value": 15.0, "level": "normal"}


def _sector_strength() -> Dict:
    result = {}
    for name, sym in SECTOR_PROXIES.items():
        d = _fetch_index(sym)
        if d:
            result[name] = d
        time.sleep(0.5)
    return result


def _save(health: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(MARKET_STATE_FILE, "w") as f:
        json.dump(health, f, indent=2)


def _print_health(h: dict) -> None:
    nifty = h.get("nifty", {})
    vix   = h.get("vix", {})
    print(f"[market] Nifty={nifty.get('value','?')} ({nifty.get('day_change_pct',0):+.2f}%) "
          f"| VIX={vix.get('value','?')} [{vix.get('level','?')}] "
          f"| Mood={h.get('market_mood')} | Trade={'YES' if h.get('trade_allowed') else 'NO'}")
    for w in h.get("warnings", []):
        print(f"  [!] {w}")
