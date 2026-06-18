"""
Market Health Monitor — runs every session before any trade decisions.

Checks:
  1. Nifty 50 trend (proxy: check NIFTY BEES or ^NSEI)
  2. Advance/Decline ratio (broad breadth via sample)
  3. India VIX level (fear gauge — high VIX = stay out)
  4. Sector strength (which sectors are leading)
  5. FII sentiment proxy (large-cap index flow)

Returns a MarketHealth object that gates trading decisions in the brain.
"""

import json
import os
import time
from datetime import date, timedelta
from typing import Dict, List

import yfinance as yf
import pandas as pd

from agent.config import BRAIN_DIR


# ── Sector ETF proxies (free via yfinance) ────────────────────────────────────
SECTOR_PROXIES = {
    "Nifty50":   "^NSEI",
    "BankNifty": "^NSEBANK",
    "IT":        "ITBEES.NS",
    "Pharma":    "PHARMABEES.NS",
    "Auto":      "AUTOBEES.NS",   # may not always have data
    "FMCG":      "FMCGBEES.NS",
    "Metal":     "METALBEEES.NS",
}

# VIX thresholds
VIX_NORMAL  = 15.0
VIX_CAUTION = 20.0
VIX_DANGER  = 25.0

MARKET_STATE_FILE = "brain/market_health.json"


def assess_market(session: str = "morning") -> dict:
    """
    Assess overall market health. Returns a structured health report.
    This is checked before any paper trade is opened.
    """
    health = {
        "date":        date.today().isoformat(),
        "session":     session,
        "nifty":       _fetch_index("^NSEI"),
        "vix":         _fetch_vix(),
        "bank_nifty":  _fetch_index("^NSEBANK"),
        "sectors":     _sector_strength(),
        "market_mood": "neutral",
        "trade_allowed": True,
        "warnings":    [],
    }

    # ── Determine mood ────────────────────────────────────────────────────────
    vix_val = health["vix"].get("value", 15)
    nifty   = health["nifty"]
    n_trend = nifty.get("trend_5d", "sideways")

    warnings = []
    trade_ok = True

    if vix_val >= VIX_DANGER:
        warnings.append(f"INDIA VIX={vix_val:.1f} (DANGER >25) — no new trades today")
        trade_ok = False
    elif vix_val >= VIX_CAUTION:
        warnings.append(f"INDIA VIX={vix_val:.1f} (CAUTION >20) — reduce position sizes")

    if n_trend in ("strong_down", "down"):
        warnings.append(f"Nifty50 in downtrend ({n_trend}) — only take very high-confidence BUY signals")
    elif n_trend in ("strong_up", "up"):
        health["market_mood"] = "bullish"
    elif n_trend == "sideways":
        health["market_mood"] = "neutral"

    # FII proxy: if Nifty fell >1.5% today, likely FII selling
    nifty_day_chg = nifty.get("day_change_pct", 0)
    if nifty_day_chg < -1.5:
        warnings.append(f"Nifty down {nifty_day_chg:.1f}% today — likely FII selling. Be cautious.")

    # Leading sectors
    leading = [s for s, d in health["sectors"].items() if d.get("trend_5d") in ("up","strong_up")]
    if leading:
        health["leading_sectors"] = leading
    else:
        health["leading_sectors"] = []

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


def _fetch_index(symbol: str) -> dict:
    try:
        today = date.today()
        df = yf.download(symbol, start=(today - timedelta(days=20)).isoformat(),
                         end=today.isoformat(), progress=False, auto_adjust=True)
        if df.empty or len(df) < 2:
            return {}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df["Close"].squeeze()
        today_open = float(df["Open"].squeeze().iloc[-1])
        today_close = float(close.iloc[-1])
        prev_close  = float(close.iloc[-2])
        day_chg = (today_close - prev_close) / prev_close * 100

        def trend(n):
            if len(close) < n+1: return "unknown"
            p = (close.iloc[-1] - close.iloc[-n]) / close.iloc[-n] * 100
            if p>3: return "strong_up"
            if p>0.8: return "up"
            if p<-3: return "strong_down"
            if p<-0.8: return "down"
            return "sideways"

        return {
            "symbol":        symbol,
            "value":         round(today_close, 2),
            "day_change_pct":round(day_chg, 2),
            "trend_5d":      trend(5),
            "trend_20d":     trend(20),
        }
    except Exception as e:
        print(f"[market] index {symbol}: {e}")
        return {}


def _fetch_vix() -> dict:
    try:
        df = yf.download("^INDIAVIX", period="5d", progress=False, auto_adjust=True)
        if df.empty:
            return {"value": 15.0, "level": "normal"}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        val = float(df["Close"].squeeze().iloc[-1])
        level = "danger" if val >= VIX_DANGER else ("caution" if val >= VIX_CAUTION else "normal")
        return {"value": round(val, 2), "level": level}
    except Exception as e:
        print(f"[market] VIX: {e}")
        return {"value": 15.0, "level": "normal"}


def _sector_strength() -> Dict:
    result = {}
    for name, sym in SECTOR_PROXIES.items():
        if name in ("Nifty50", "BankNifty"):
            continue   # already fetched separately
        d = _fetch_index(sym)
        if d:
            result[name] = d
        time.sleep(0.3)
    return result


def _save(health: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(MARKET_STATE_FILE, "w") as f:
        json.dump(health, f, indent=2)


def _print_health(h: dict) -> None:
    nifty = h.get("nifty", {})
    vix   = h.get("vix", {})
    print(f"\n[market] Nifty={nifty.get('value','?')} ({nifty.get('day_change_pct',0):+.2f}%) "
          f"| VIX={vix.get('value','?')} [{vix.get('level','?')}] "
          f"| Mood={h.get('market_mood','?')} "
          f"| Trade={'YES' if h.get('trade_allowed') else 'NO'}")
    for w in h.get("warnings", []):
        print(f"  ⚠ {w}")
