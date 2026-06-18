"""
Market Health Monitor — checks Nifty, VIX, and sector strength each session.
Uses Stooq for indices (no IP blocking). VIX falls back to a fixed default
since Stooq doesn't carry India VIX.
"""

import json
import os
import time
from datetime import date, timedelta
from io import StringIO
from typing import Dict

import pandas as pd
import requests

from agent.config import BRAIN_DIR

_STOOQ = requests.Session()
_STOOQ.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; NSEBot/1.0)",
    "Accept":     "text/html,application/xhtml+xml,*/*",
})

# Stooq tickers for NSE indices and sector ETFs
# Stooq carries Nifty 50 as ^NF and BankNifty as ^BNF
# Sector ETFs: use their .IN stooq format
INDICES = {
    "nifty":      "^NF",
    "bank_nifty": "^BNF",
}
SECTOR_PROXIES = {
    "IT":     "ITBEES.IN",
    "Pharma": "PHARMABEES.IN",
    "Metal":  "METALBEEES.IN",
}

VIX_NORMAL  = 15.0
VIX_CAUTION = 20.0
VIX_DANGER  = 25.0

MARKET_STATE_FILE = "brain/market_health.json"


def assess_market(session: str = "morning") -> dict:
    health = {
        "date":          date.today().isoformat(),
        "session":       session,
        "nifty":         _fetch_index("^NF", "^NSEI"),
        "vix":           {"value": 15.0, "level": "normal"},   # Stooq has no India VIX
        "bank_nifty":    _fetch_index("^BNF", "^NSEBANK"),
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
            "nifty": {}, "vix": {"value": 15.0, "level": "normal"},
            "sectors": {}, "leading_sectors": []}


def _stooq_fetch(stooq_sym: str, days: int = 25) -> pd.DataFrame:
    today = date.today()
    start = (today - timedelta(days=days)).strftime("%Y%m%d")
    end   = today.strftime("%Y%m%d")
    url   = (
        f"https://stooq.com/q/d/l/"
        f"?s={stooq_sym.lower()}"
        f"&d1={start}&d2={end}&i=d"
    )
    try:
        r = _STOOQ.get(url, timeout=15)
        if r.status_code != 200 or len(r.text) < 50:
            return pd.DataFrame()
        df = pd.read_csv(StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        date_col = next((c for c in df.columns if c.lower() == "date"), None)
        if date_col is None:
            return pd.DataFrame()
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.set_index(date_col).sort_index()
        df.columns = [c.strip().title() for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()


def _fetch_index(stooq_sym: str, label: str = "") -> dict:
    try:
        df = _stooq_fetch(stooq_sym)
        if df is None or df.empty or len(df) < 2:
            return {}
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
            "symbol":         label or stooq_sym,
            "value":          round(today_c, 2),
            "day_change_pct": round(day_chg, 2),
            "trend_5d":       trend(5),
            "trend_20d":      trend(20),
        }
    except Exception as e:
        print(f"[market] {stooq_sym}: {e}")
        return {}


def _sector_strength() -> Dict:
    result = {}
    for name, sym in SECTOR_PROXIES.items():
        d = _fetch_index(sym, name)
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
    print(f"[market] Nifty={nifty.get('value','?')} ({nifty.get('day_change_pct',0):+.2f}%) "
          f"| VIX={vix.get('value','?')} [{vix.get('level','?')}] "
          f"| Mood={h.get('market_mood')} | Trade={'YES' if h.get('trade_allowed') else 'NO'}")
    for w in h.get("warnings", []):
        print(f"  [!] {w}")
