"""
Delivery % fetcher — NSE publishes daily bhav copy with delivery data.

Delivery % = what fraction of traded volume was actual delivery (not intraday).
High delivery % on a price move = institutional/genuine conviction.
Low delivery % = intraday speculation, less meaningful.

Source: NSE bhavcopy (free, no auth, updated by ~6 PM IST daily).
URL pattern: https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
"""

import json
import os
import time
from datetime import date, timedelta
from typing import Dict, List

import requests

from agent.config import BRAIN_DIR
from agent.trading_calendar import ist_today

DELIVERY_FILE = "brain/delivery_data.json"

_NSE = requests.Session()
_NSE.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.nseindia.com/",
    "Accept":     "text/html,application/xhtml+xml,*/*",
})


def fetch_delivery(tickers: List[str], lookback_days: int = 5) -> Dict:
    """
    Fetch delivery % for all tickers from NSE bhavcopy for the last N trading days.
    Returns dict: ticker → {delivery_pct, delivery_trend, avg_delivery_5d, date}
    """
    # Collect last N trading days of bhavcopy data
    raw: Dict[str, list] = {}   # ticker → list of (date, delivery_pct)
    today = ist_today()

    days_checked = 0
    days_fetched = 0
    offset = 1   # start from yesterday (today's bhavcopy not available until evening)

    while days_fetched < lookback_days and days_checked < 15:
        target = today - timedelta(days=offset)
        offset += 1
        days_checked += 1

        # Skip weekends
        if target.weekday() >= 5:
            continue

        data = _fetch_bhavcopy(target)
        if not data:
            continue

        days_fetched += 1
        date_str = target.isoformat()

        for ticker in tickers:
            symbol = ticker.replace(".NS", "")
            row = data.get(symbol)
            if row is None:
                continue
            if ticker not in raw:
                raw[ticker] = []
            raw[ticker].append((date_str, row["delivery_pct"]))

        time.sleep(0.5)

    # Aggregate per ticker
    result = {}
    for ticker in tickers:
        entries = raw.get(ticker, [])
        if not entries:
            continue
        entries.sort(key=lambda x: x[0])   # oldest → newest
        pcts = [e[1] for e in entries]

        latest_pct   = pcts[-1] if pcts else 0.0
        # Guarded consistently with latest_pct above — the `if not entries` check
        # makes an empty list unreachable today, but an unguarded divide here
        # would become a ZeroDivisionError the moment that guard is ever relaxed.
        avg_5d       = round(sum(pcts) / len(pcts), 1) if pcts else 0.0

        # Trend: compare last 2 sessions
        if len(pcts) >= 2:
            delta = pcts[-1] - pcts[-2]
            trend = "rising" if delta > 5 else "falling" if delta < -5 else "stable"
        else:
            trend = "stable"

        # Signal interpretation
        if latest_pct >= 60:
            signal = "strong_accumulation"
        elif latest_pct >= 45:
            signal = "accumulation"
        elif latest_pct <= 20:
            signal = "distribution"
        elif latest_pct <= 35:
            signal = "weak"
        else:
            signal = "neutral"

        result[ticker] = {
            "date":            entries[-1][0] if entries else today.isoformat(),
            "delivery_pct":    round(latest_pct, 1),
            "avg_delivery_5d": avg_5d,
            "delivery_trend":  trend,
            "delivery_signal": signal,
            "history":         entries[-5:],
        }

    print(f"[delivery] fetched delivery% for {len(result)}/{len(tickers)} tickers")
    return result


def _fetch_bhavcopy(target: date) -> Dict[str, dict]:
    """
    Download NSE full bhavcopy for a given date.
    Returns dict: symbol → {delivery_pct, ...}
    """
    ddmmyyyy = target.strftime("%d%m%Y")
    url = (
        f"https://nsearchives.nseindia.com/products/content/"
        f"sec_bhavdata_full_{ddmmyyyy}.csv"
    )
    try:
        r = _NSE.get(url, timeout=20)
        if r.status_code != 200 or len(r.text) < 100:
            return {}

        import io
        import pandas as pd
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip().upper() for c in df.columns]

        # Expected columns: SYMBOL, SERIES, DELIV_QTY, DELIV_PER, TTL_TRD_QNTY, etc.
        needed = {"SYMBOL", "SERIES", "DELIV_PER"}
        if not needed.issubset(set(df.columns)):
            # Try alternate column names
            col_map = {}
            for c in df.columns:
                if "DELIV" in c and "PER" in c:
                    col_map[c] = "DELIV_PER"
                elif "SYMBOL" in c:
                    col_map[c] = "SYMBOL"
                elif "SERIES" in c:
                    col_map[c] = "SERIES"
            df = df.rename(columns=col_map)

        if "DELIV_PER" not in df.columns or "SYMBOL" not in df.columns:
            return {}

        # Only EQ series (exclude futures, options, bonds)
        if "SERIES" in df.columns:
            df = df[df["SERIES"].str.strip() == "EQ"]

        result = {}
        for _, row in df.iterrows():
            sym = str(row["SYMBOL"]).strip()
            try:
                pct = float(str(row["DELIV_PER"]).replace(",", "").strip())
                if 0 <= pct <= 100:
                    result[sym] = {"delivery_pct": round(pct, 1)}
            except (ValueError, TypeError):
                continue

        return result

    except Exception as e:
        print(f"[delivery] bhavcopy {target}: {e}")
        return {}


def load_delivery() -> Dict:
    from agent.io_safe import load_json_dict
    return load_json_dict(DELIVERY_FILE)


def save_delivery(data: Dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    existing = load_delivery()
    existing.update(data)
    with open(DELIVERY_FILE, "w") as f:
        json.dump(existing, f, indent=2)


def inject_delivery(stock_data: Dict, delivery_data: Dict) -> Dict:
    """Inject delivery % fields into each stock's latest dict."""
    for ticker, entry in stock_data.items():
        d = delivery_data.get(ticker)
        if not d or "latest" not in entry:
            continue
        entry["latest"]["delivery_pct"]    = d.get("delivery_pct", 0.0)
        entry["latest"]["delivery_trend"]  = d.get("delivery_trend", "stable")
        entry["latest"]["delivery_signal"] = d.get("delivery_signal", "neutral")
        entry["latest"]["avg_delivery_5d"] = d.get("avg_delivery_5d", 0.0)
    return stock_data


def score_delivery(delivery_pct: float, signal: str) -> float:
    """
    Returns a bonus/penalty score (-2.0 to +3.0) for use in brain scoring.
    High delivery on a move = institutional conviction = bullish for BUY signals.
    Low delivery = intraday churn = reduce conviction.
    """
    if signal == "strong_accumulation":
        return 3.0
    if signal == "accumulation":
        return 1.5
    if signal == "neutral":
        return 0.0
    if signal == "weak":
        return -1.0
    if signal == "distribution":
        return -2.0
    return 0.0
