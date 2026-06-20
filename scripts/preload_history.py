"""
One-time preload of the deep historical foundation for the FULL universe.

Run locally (not in GitHub Actions) to fetch and store everything the data
sources actually provide, so the tool ships with the foundation already baked in
and never has to fetch 2-year history itself — it just builds on top from live data.

What this populates (all from REAL free sources, nothing fabricated):
  - brain/history_context.json : 2yr regime, personality, shock-reaction behaviour,
                                 weekly price series, per stock
  - brain/fundamentals.json    : current fundamentals snapshot per stock

Usage:  python -m scripts.preload_history
"""

import json
import os
import sys
import time
from datetime import date, timedelta

# Ensure repo root on path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from agent.config import NSE_UNIVERSE, BRAIN_DIR
from agent.history_engine import _build_context, HISTORY_FILE, HISTORY_DAYS
from agent.data_fetcher import _download_daily, _warm_nse_session


def preload_history(tickers):
    today = date.today()
    start = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    end   = today.isoformat()

    out = {}
    ok, fail = 0, 0
    for i, ticker in enumerate(tickers, 1):
        try:
            df = _download_daily(ticker, start=start, end=end)
            if df is None or df.empty or len(df) < 120:
                print(f"[{i:3d}/{len(tickers)}] {ticker:16s} SKIP (only "
                      f"{0 if df is None else len(df)} rows)")
                fail += 1
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            ctx = _build_context(df, ticker)
            if not ctx:
                print(f"[{i:3d}/{len(tickers)}] {ticker:16s} SKIP (no context)")
                fail += 1
                continue
            ctx["preloaded"] = True   # mark as a permanent foundation entry
            out[ticker] = ctx
            rg = ctx["regime"]; pr = ctx["personality"]; sh = ctx.get("shock", {})
            print(f"[{i:3d}/{len(tickers)}] {ticker:16s} {ctx['days']}d | "
                  f"{rg['long_trend']:16s} | 52w {rg['pct_of_52w_range']:5.1f}% | "
                  f"{pr['type']:13s} | shock↑ {sh.get('up_shock_followthrough_pct')}")
            ok += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"[{i:3d}/{len(tickers)}] {ticker:16s} ERROR {e}")
            fail += 1
    print(f"\n[history] preloaded {ok} stocks, {fail} failed/skipped")
    return out


def preload_fundamentals(tickers):
    from agent.fundamentals_fetcher import fetch_fundamentals
    print("\n[fundamentals] fetching for full universe (this is the slow part)...")
    try:
        return fetch_fundamentals(tickers)
    except Exception as e:
        print(f"[fundamentals] batch fetch error: {e}")
        return {}


def main():
    os.makedirs(BRAIN_DIR, exist_ok=True)
    _warm_nse_session()

    print(f"=== Preloading deep foundation for {len(NSE_UNIVERSE)} stocks ===\n")

    hist = preload_history(NSE_UNIVERSE)
    if hist:
        # Merge with any existing (preserve, don't clobber)
        existing = {}
        if os.path.exists(HISTORY_FILE):
            try:
                existing = json.load(open(HISTORY_FILE))
            except Exception:
                existing = {}
        existing.update(hist)
        json.dump(existing, open(HISTORY_FILE, "w"), indent=2)
        print(f"[history] saved {len(existing)} stocks -> {HISTORY_FILE}")

    funds = preload_fundamentals(NSE_UNIVERSE)
    if funds:
        from agent.fundamentals_fetcher import save_fundamentals
        save_fundamentals(funds)
        print(f"[fundamentals] saved {len(funds)} stocks")

    print("\n=== Preload complete ===")


if __name__ == "__main__":
    main()
