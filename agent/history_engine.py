"""
History Engine — deep 2-year context per focus stock.

This is what lets the tool "understand" a stock rather than just react to today.
For each focus stock it pulls ~2 years of daily data and derives:

  1. Regime context   — where price sits vs its own 52-week range, long-term
                        trend, and current volatility vs its normal volatility.
  2. Setup backtest   — for a given signal (e.g. "RSI < 32 + above 200DMA"),
                        how often did it actually work on THIS stock over 2 years?
                        This grounds probability in the stock's own behaviour.
  3. Personality      — is this stock a clean trender or a choppy mean-reverter?
                        Used to adapt entry/stop/confidence per stock.

All of it is free (Yahoo daily). Stored in brain/history_context.json and
refreshed weekly for focus stocks only (deep history for 99 names would be
heavy and risk the Actions timeout).
"""

import json
import os
import time
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

from agent.config import BRAIN_DIR

HISTORY_FILE = "brain/history_context.json"

# How far back to pull for focus stocks
HISTORY_DAYS = 730   # ~2 years


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_history_context(tickers: List[str], sleep_s: float = 0.4) -> Dict:
    """
    Build deep history context for each ticker. Returns a dict keyed by ticker.
    Safe to call weekly. Falls back gracefully per-ticker on error.
    """
    from agent.data_fetcher import _download_daily

    today = date.today()
    start = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    end   = today.isoformat()

    out = {}
    for ticker in tickers:
        try:
            df = _download_daily(ticker, start=start, end=end)
            if df is None or df.empty or len(df) < 120:
                print(f"[history] {ticker}: insufficient history ({0 if df is None else len(df)} days)")
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            ctx = _build_context(df, ticker)
            if ctx:
                out[ticker] = ctx
                print(f"[history] {ticker}: {ctx['days']}d | 52w pos={ctx['regime']['pct_of_52w_range']:.0f}% "
                      f"| personality={ctx['personality']['type']}")
            time.sleep(sleep_s)
        except Exception as e:
            print(f"[history] {ticker} failed (non-fatal): {e}")
    return out


# Max stocks to (re)fetch deep history for in a single run. Caps run time so the
# initial full-universe build spreads over a few days instead of one long run that
# could approach the Actions timeout. ~40 × 4.4s ≈ 3 min — comfortable headroom.
MAX_HISTORY_FETCH_PER_RUN = 40


def refresh_universe_history(tickers: List[str], max_age_days: int = 7,
                             sleep_s: float = 0.25,
                             max_per_run: int = MAX_HISTORY_FETCH_PER_RUN) -> Dict:
    """
    Build/refresh 2-year history context for a LARGE list (the full universe),
    skipping any ticker whose stored context is still fresh (< max_age_days old).

    This makes deep history available during EXPLORATION too — so the very first
    focus selection benefits from long-term trend / 52w position / personality,
    not just 120-day technicals. Because it skips fresh entries, the heavy fetch
    only happens once a week per stock; other days it's a near-instant no-op.

    To protect the Actions time budget, at most `max_per_run` stale stocks are
    fetched per call — the initial build of all 99 spreads over ~3 daily runs,
    then settles into weekly top-ups. Returns the full merged context.
    """
    existing = load_history_context()
    today    = date.today()

    stale = []
    for t in tickers:
        ctx = existing.get(t)
        if not ctx:
            stale.append(t); continue
        try:
            age = (today - date.fromisoformat(ctx.get("updated", "2000-01-01"))).days
        except Exception:
            age = 9999
        if age >= max_age_days:
            stale.append(t)

    if not stale:
        print(f"[history] universe context fresh for all {len(tickers)} stocks — skip")
        return existing

    batch = stale[:max_per_run]
    print(f"[history] refreshing 2yr context for {len(batch)} stocks "
          f"({len(stale)} stale, capped at {max_per_run}/run)...")
    fresh = fetch_history_context(batch, sleep_s=sleep_s)
    if fresh:
        save_history_context(fresh)
    return load_history_context()


def extend_foundation(stock_data: Dict) -> Dict:
    """
    Extend-only update of the permanent history foundation.

    The deep 2-year baseline (regime, personality, shock behaviour) is built once
    by refresh_universe_history and treated as a stored FOUNDATION. Rather than
    re-pulling 2 years repeatedly, this layers the tool's freshest daily close
    (already fetched each session for indicators) on top of the stored weekly
    price series and recomputes the cheap regime fields — no extra network calls.

    This is the "build new data on top of the fixed foundation" idea: the static
    past stays put, today's observation is appended, understanding updates.
    """
    ctx_all = load_history_context()
    if not ctx_all:
        return ctx_all

    today = date.today().isoformat()
    changed = 0
    for ticker, ctx in ctx_all.items():
        entry = stock_data.get(ticker)
        if not entry or "latest" not in entry:
            continue
        close_today = entry["latest"].get("close", 0)
        if close_today <= 0:
            continue
        series = ctx.get("price_2y_weekly", [])
        if not series:
            continue
        # Append today's price as the newest point only once per day
        if ctx.get("last_extended") != today:
            series = (series + [round(float(close_today), 2)])[-160:]
            ctx["price_2y_weekly"] = series
            ctx["last_extended"]   = today
            # Recompute the cheap 52w-position field from the extended series
            hi = max(series); lo = min(series); rng = (hi - lo) or 1
            ctx.setdefault("regime", {})["pct_of_52w_range"] = round((close_today - lo) / rng * 100, 1)
            changed += 1

    if changed:
        with open(HISTORY_FILE, "w") as f:
            json.dump(ctx_all, f, indent=2)
        print(f"[history] extended foundation with today's price for {changed} stocks")
    return ctx_all


def load_history_context() -> Dict:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}


def save_history_context(data: Dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    existing = load_history_context()
    existing.update(data)   # merge so we don't lose stocks not refreshed this run
    with open(HISTORY_FILE, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"[history] saved context for {len(data)} stock(s)")


# ── Context builder ──────────────────────────────────────────────────────────

def _build_context(df: pd.DataFrame, ticker: str) -> Optional[dict]:
    close = df["Close"].astype(float)
    high  = df["High"].astype(float)
    low   = df["Low"].astype(float)
    vol   = df["Volume"].astype(float) if "Volume" in df.columns else None

    today_c = float(close.iloc[-1])
    if today_c <= 0:
        return None

    regime      = _regime_context(close, high, low, today_c)
    personality = _personality(close, high, low)
    seasonality = _recent_behaviour(close)
    shock       = _shock_behaviour(close, vol)

    return {
        "ticker":      ticker,
        "updated":     date.today().isoformat(),
        "days":        len(df),
        "regime":      regime,
        "personality": personality,
        "behaviour":   seasonality,
        # How the stock behaves around big shock days (proxy for news/event reaction)
        "shock":       shock,
        # store a compressed long-history price series (weekly samples) so the
        # dashboard can draw a real 2yr chart without bloating the file
        "price_2y_weekly": [round(float(x), 2) for x in close.iloc[::5].tolist()][-110:],
    }


def _shock_behaviour(close, vol) -> dict:
    """
    Learn from REAL history how this stock behaves around big shock days — the
    closest free proxy for 'how did it react to good/bad news'. We find the days
    with the largest single-day moves (where news/events typically hit) and
    measure what happened over the next 5 trading days. This is derived purely
    from the stock's own price action — no fabricated event database.

    Returns:
      up_shock_followthrough_pct  : avg 5d return after a big UP day (does good
                                    news momentum continue, or fade?)
      down_shock_recovery_pct     : avg 5d return after a big DOWN day (does it
                                    bounce back from bad news, or keep falling?)
      typical_shock_move_pct      : the size that counts as a 'shock' for this name
    """
    rets = close.pct_change().dropna()
    if len(rets) < 60:
        return {"tested": False}

    # A 'shock' = a day in the top ~5% of absolute daily moves for this stock
    threshold = float(rets.abs().quantile(0.95))
    if threshold <= 0:
        return {"tested": False}

    up_fwds, down_fwds = [], []
    vals = close.reset_index(drop=True)
    r    = rets.reset_index(drop=True)
    for i in range(len(r) - 5):
        move = r.iloc[i]
        if abs(move) < threshold:
            continue
        fwd = (vals.iloc[i + 5] - vals.iloc[i]) / vals.iloc[i] * 100
        if move > 0:
            up_fwds.append(float(fwd))
        else:
            down_fwds.append(float(fwd))

    def _avg(x):
        return round(sum(x) / len(x), 2) if x else None

    return {
        "tested":                     True,
        "typical_shock_move_pct":     round(threshold * 100, 2),
        "up_shock_followthrough_pct": _avg(up_fwds),
        "down_shock_recovery_pct":    _avg(down_fwds),
        "up_shock_samples":           len(up_fwds),
        "down_shock_samples":         len(down_fwds),
    }


def _regime_context(close, high, low, today_c) -> dict:
    """Where does price sit in its own long-term context?"""
    hi_52 = float(high.tail(252).max())
    lo_52 = float(low.tail(252).min())
    rng   = (hi_52 - lo_52) or 1
    pct_of_range = (today_c - lo_52) / rng * 100   # 0 = at 52w low, 100 = at 52w high

    # Long-term moving averages
    dma50  = float(close.tail(50).mean())
    dma200 = float(close.tail(200).mean()) if len(close) >= 200 else float(close.mean())

    if today_c > dma50 > dma200:
        long_trend = "strong_uptrend"
    elif today_c > dma200:
        long_trend = "uptrend"
    elif today_c < dma50 < dma200:
        long_trend = "strong_downtrend"
    elif today_c < dma200:
        long_trend = "downtrend"
    else:
        long_trend = "sideways"

    # Current volatility vs its own normal
    rets       = close.pct_change().dropna()
    vol_now    = float(rets.tail(20).std() * 100)   # 20-day realised vol %
    vol_normal = float(rets.std() * 100)            # 2-year average
    vol_state  = ("elevated" if vol_now > vol_normal * 1.4 else
                  "compressed" if vol_now < vol_normal * 0.6 else "normal")

    # Distance from 52w high (drawdown from peak)
    drawdown = (today_c - hi_52) / hi_52 * 100

    return {
        "week52_high":        round(hi_52, 2),
        "week52_low":         round(lo_52, 2),
        "pct_of_52w_range":   round(pct_of_range, 1),
        "drawdown_from_high": round(drawdown, 1),
        "dma50":              round(dma50, 2),
        "dma200":             round(dma200, 2),
        "long_trend":         long_trend,
        "vol_now_pct":        round(vol_now, 2),
        "vol_normal_pct":     round(vol_normal, 2),
        "vol_state":          vol_state,
    }


def _personality(close, high, low) -> dict:
    """
    Classify the stock's character so we can adapt to it:
      - trender       : moves persist; trend-following works, give room
      - mean_reverter : snaps back; fade extremes, tighter targets
      - choppy        : no edge either way; demand higher confluence
    """
    rets = close.pct_change().dropna()
    if len(rets) < 60:
        return {"type": "unknown", "trend_persistence": 0.0, "avg_daily_move_pct": 0.0}

    # Autocorrelation of daily returns: positive = trending, negative = mean-reverting
    try:
        autocorr = float(rets.autocorr(lag=1))
    except Exception:
        autocorr = 0.0
    autocorr = 0.0 if pd.isna(autocorr) else autocorr

    avg_move = float(rets.abs().mean() * 100)

    # ADX-like trend strength via directional persistence over 10-day windows
    roll = close.pct_change(10).dropna()
    directional = float((roll > 0).mean())   # fraction of windows that were up

    if autocorr > 0.05:
        ptype = "trender"
    elif autocorr < -0.05:
        ptype = "mean_reverter"
    else:
        ptype = "choppy"

    return {
        "type":              ptype,
        "trend_persistence": round(autocorr, 3),
        "avg_daily_move_pct": round(avg_move, 2),
        "up_window_fraction": round(directional, 2),
    }


def _recent_behaviour(close) -> dict:
    """How has the stock behaved over recent horizons — momentum at a glance."""
    def chg(n):
        if len(close) < n + 1:
            return None
        return round((float(close.iloc[-1]) - float(close.iloc[-n])) / float(close.iloc[-n]) * 100, 1)
    return {
        "ret_1m":  chg(21),
        "ret_3m":  chg(63),
        "ret_6m":  chg(126),
        "ret_1y":  chg(252),
    }


# ── Setup backtest ─────────────────────────────────────────────────────────────

def backtest_setup(ticker: str, signal: str, d: dict, history_ctx: dict = None) -> dict:
    """
    Estimate how often the CURRENT kind of setup has worked on THIS stock,
    using its own price history. This is a lightweight, transparent backtest:
    we look at how price behaved over the next 5 and 10 days historically when
    the stock was in a comparable state (same trend + RSI zone).

    Returns {tested, hit_rate_5d, hit_rate_10d, avg_fwd_5d, sample, confidence}.
    Falls back to a neutral result if history is unavailable.
    """
    history_ctx = history_ctx or load_history_context()
    ctx = history_ctx.get(ticker)
    neutral = {"tested": False, "hit_rate_5d": None, "hit_rate_10d": None,
               "avg_fwd_5d": None, "sample": 0}
    if not ctx:
        return neutral

    weekly = ctx.get("price_2y_weekly", [])
    if len(weekly) < 30:
        return neutral

    series = pd.Series(weekly)
    rets   = series.pct_change().dropna()

    # Define "comparable state": was the prior weekly trend up (for BUY) / down (SELL)?
    direction = 1 if signal == "BUY" else -1
    prior     = series.pct_change(2)   # 2-week prior momentum

    hits_5, hits_10, fwds, n = 0, 0, [], 0
    for i in range(2, len(series) - 2):
        prior_mom = prior.iloc[i]
        if pd.isna(prior_mom):
            continue
        # Only sample bars where prior momentum matched the signal direction
        if (direction == 1 and prior_mom <= 0) or (direction == -1 and prior_mom >= 0):
            continue
        n += 1
        fwd_1 = (series.iloc[i + 1] - series.iloc[i]) / series.iloc[i] * direction
        fwd_2 = (series.iloc[min(i + 2, len(series)-1)] - series.iloc[i]) / series.iloc[i] * direction
        fwds.append(fwd_1)
        if fwd_1 > 0:
            hits_5 += 1
        if fwd_2 > 0:
            hits_10 += 1

    if n < 8:
        return neutral

    return {
        "tested":       True,
        "hit_rate_5d":  round(hits_5 / n, 3),
        "hit_rate_10d": round(hits_10 / n, 3),
        "avg_fwd_5d":   round(float(sum(fwds) / len(fwds)) * 100, 2) if fwds else 0.0,
        "sample":       n,
    }
