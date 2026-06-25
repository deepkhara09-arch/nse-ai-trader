"""
Market Health Monitor — checks Nifty, VIX, Bank Nifty, sector strength,
advance-decline ratio, and intraday regime each session.
"""

import json
import os
import time
from datetime import date, timedelta
from typing import Dict

import pandas as pd

from agent.config import BRAIN_DIR
from agent.trading_calendar import ist_today

VIX_NORMAL  = 15.0
VIX_CAUTION = 20.0
VIX_DANGER  = 25.0

MARKET_STATE_FILE = "brain/market_health.json"

SECTOR_PROXIES = {
    "IT":      "ITBEES.NS",
    "Pharma":  "PHARMABEES.NS",
    "Metal":   "METALBEEES.NS",
    "Bank":    "BANKBEES.NS",
    "Energy":  "ENERGYBEES.NS",
    "Infra":   "INFRABEES.NS",
}


def _log_health(component: str, detail: str, message: str, session: str) -> None:
    """Record a non-fatal data-source failure to the System Health panel.
    Never raises — health logging must never break market assessment."""
    try:
        from agent.run_health import record_issue
        record_issue(component, detail, message, session)
    except Exception:
        pass


def assess_market(session: str = "morning") -> dict:
    from agent.data_fetcher import _download_daily, _fetch_intraday_candles

    today = ist_today()
    start = (today - timedelta(days=60)).isoformat()   # extended to 60d for VIX percentile
    end   = today.isoformat()

    nifty      = _fetch_index("^NSEI",    start, end, _download_daily)
    bank_nifty = _fetch_index("^NSEBANK", start, end, _download_daily)
    vix_data   = _fetch_vix_with_percentile(_download_daily)
    sectors    = _sector_strength(start, end, _download_daily)

    # Surface core index-data failures on the dashboard health panel.
    if not nifty:
        _log_health("market_index", "Nifty 50", "index data unavailable — using neutral", session)
    if not bank_nifty:
        _log_health("market_index", "Bank Nifty", "index data unavailable", session)
    if not sectors:
        _log_health("market_index", "sectors", "sector ETF data unavailable", session)

    health = {
        "date":          today.isoformat(),
        "session":       session,
        "nifty":         nifty,
        "vix":           vix_data,
        "bank_nifty":    bank_nifty,
        "sectors":       sectors,
        "market_mood":   "neutral",
        "trade_allowed": True,
        "warnings":      [],
        "leading_sectors": [],
        "market_regime": "normal",   # normal / caution / danger / sideways
    }

    vix_val    = vix_data.get("value", 15)
    vix_pct    = vix_data.get("percentile_30d", 50)   # how extreme is today's VIX?
    n_trend    = nifty.get("trend_5d", "sideways")
    n_chg      = nifty.get("day_change_pct", 0)
    bn_trend   = bank_nifty.get("trend_5d", "sideways")
    bn_chg     = bank_nifty.get("day_change_pct", 0)
    warnings   = []
    trade_ok   = True

    # ── VIX regime ────────────────────────────────────────────────────────────
    if vix_val >= VIX_DANGER:
        warnings.append(f"India VIX={vix_val:.1f} DANGER (top {100-vix_pct:.0f}% of last 30d) — no new trades")
        trade_ok = False
        health["market_regime"] = "danger"
    elif vix_val >= VIX_CAUTION:
        warnings.append(f"India VIX={vix_val:.1f} elevated — reduce position size by 25%")
        health["market_regime"] = "caution"
    elif vix_pct >= 80:
        warnings.append(f"VIX={vix_val:.1f} at {vix_pct:.0f}th percentile vs last 30d — unusually high stress")

    # ── Nifty trend ───────────────────────────────────────────────────────────
    if n_trend in ("strong_down", "down"):
        warnings.append(f"Nifty in downtrend ({n_trend}) — only high-confidence BUY signals")
    if n_chg < -1.5:
        warnings.append(f"Nifty down {n_chg:.1f}% today — likely institutional selling")
    if n_chg > 1.5:
        health["nifty"]["intraday_surge"] = True   # flag strong up day

    # ── Bank Nifty divergence (Indian market barometer) ───────────────────────
    # Bank Nifty diverging from Nifty = unreliable broad market signal
    bn_diverging = False
    if n_trend in ("up", "strong_up") and bn_trend in ("down", "strong_down"):
        warnings.append("Bank Nifty diverging DOWN while Nifty up — rally may be narrow/unreliable")
        bn_diverging = True
    elif n_trend in ("down", "strong_down") and bn_trend in ("up", "strong_up"):
        warnings.append("Bank Nifty outperforming while Nifty down — banking sector resilient")
        bn_diverging = True
    # Large Bank Nifty move amplifies overall market stress
    if abs(bn_chg) > abs(n_chg) * 1.5 and abs(bn_chg) > 1.5:
        warnings.append(f"Bank Nifty move ({bn_chg:+.1f}%) outpacing Nifty — high banking sector volatility")
    health["bank_nifty"]["diverging"] = bn_diverging

    # ── Sector breadth (how many sectors are participating) ───────────────────
    up_sectors   = [s for s, d in sectors.items() if d.get("trend_5d") in ("up",   "strong_up")]
    down_sectors = [s for s, d in sectors.items() if d.get("trend_5d") in ("down", "strong_down")]
    breadth = len(up_sectors) / max(len(sectors), 1)
    health["sector_breadth"]   = round(breadth, 2)
    health["leading_sectors"]  = up_sectors
    health["lagging_sectors"]  = down_sectors

    if n_trend in ("up", "strong_up") and breadth < 0.4:
        warnings.append(f"Narrow rally — only {len(up_sectors)}/{len(sectors)} sectors up. Fragile move.")
    if breadth >= 0.75:
        health["broad_market_strength"] = True   # wide participation = strong

    # ── Intraday regime from live Nifty candles ────────────────────────────────
    try:
        nifty_candles = _fetch_intraday_candles("^NSEI")
        if nifty_candles and len(nifty_candles) >= 3:
            regime = _intraday_regime(nifty_candles)
            health["intraday_regime"] = regime
            if regime == "trending_up":
                health["market_mood"] = "bullish"
            elif regime == "trending_down":
                warnings.append("Nifty in intraday downtrend — avoid new longs this session")
            elif regime == "choppy":
                warnings.append("Nifty choppy intraday — wait for cleaner setups")
    except Exception:
        health["intraday_regime"] = "unknown"

    # ── Global + India macro sentiment ─────────────────────────────────────────
    # Refresh the macro snapshot at the pre-open / midday sweeps (heavier: feeds +
    # one LLM call). Other sessions reuse the most recent snapshot so the mood is
    # always present without re-fetching every run.
    try:
        from agent.macro_sentiment import assess_macro_sentiment, load_macro_sentiment
        if session in ("preopen", "midday"):
            macro = assess_macro_sentiment(session=session, use_llm=True)
        else:
            macro = load_macro_sentiment() or assess_macro_sentiment(session=session, use_llm=False)
    except Exception as e:
        print(f"[market] macro sentiment failed (non-fatal): {e}")
        try:
            from agent.run_health import record_issue
            record_issue("macro_sentiment", "macro pass", str(e), session)
        except Exception:
            pass
        macro = {}

    macro_mood  = macro.get("mood", "neutral")
    macro_score = macro.get("overall_score", 0.0)
    health["macro"] = macro   # full snapshot for dashboard + scoring

    # Macro influences risk: strongly negative global mood tightens conditions.
    macro_risk_factor = 1.0
    if macro_mood == "risk_off":
        warnings.append(
            f"Global macro risk-off ({macro_score:+.2f}) — "
            f"{macro.get('summary','')[:120]}"
        )
        macro_risk_factor = 0.7   # consumed by paper_trader sizing
        # Only an EXTREME macro reading blocks new trades outright (rare).
        if macro_score <= -0.55:
            warnings.append("Severe global risk-off — blocking new trades this session")
            trade_ok = False
    elif macro_mood == "risk_on" and macro_score >= 0.25:
        health["macro_tailwind"] = True
    health["macro_risk_factor"] = macro_risk_factor

    # ── Real FII/DII institutional flows (biggest Nifty driver) ─────────────────
    # Published once daily by NSE; fetch fresh at preopen/preclose, reuse otherwise.
    # A failed LIVE fetch (only on the fetch sessions) is logged to System Health so
    # you can SEE if it stops working, while the tool keeps using last-known data.
    try:
        from agent.fii_dii_fetcher import fetch_fii_dii, load_fii_dii
        if session in ("preopen", "preclose"):
            flows = fetch_fii_dii()
            if not flows.get("ok"):
                _log_health("fii_dii", "NSE FII/DII", flows.get("error") or "fetch returned no fresh data", session)
        else:
            flows = load_fii_dii()
    except Exception as e:
        print(f"[market] FII/DII fetch failed (non-fatal): {e}")
        _log_health("fii_dii", "NSE FII/DII", str(e), session)
        flows = {}
    health["fii_dii"] = flows
    fd_sig = flows.get("signal", "neutral")
    if fd_sig == "strong_outflow":
        warnings.append(f"Heavy FII/DII outflow (₹{flows.get('combined_net_cr',0):+,.0f} Cr) — institutions selling")
        macro_risk_factor = min(macro_risk_factor, 0.75)
    elif fd_sig == "outflow":
        warnings.append(f"Net institutional outflow (₹{flows.get('combined_net_cr',0):+,.0f} Cr)")
    elif fd_sig == "strong_inflow":
        health["flow_tailwind"] = True
    health["macro_risk_factor"] = macro_risk_factor

    # ── Nifty Put-Call Ratio (contrarian option-sentiment gauge) ────────────────
    try:
        from agent.option_sentiment import fetch_pcr, load_pcr
        pcr = fetch_pcr() if session in ("preopen", "preclose") else load_pcr()
    except Exception as e:
        print(f"[market] PCR fetch failed (non-fatal): {e}")
        pcr = {}
    health["pcr"] = pcr
    pcr_val = pcr.get("pcr", 0)
    # Extreme PCR is contrarian: very high = over-hedged (bullish), very low = complacent (bearish)
    if pcr_val and pcr_val >= 1.5:
        health["pcr_note"] = "PCR very high — excessive put hedging, often a contrarian bullish sign"
    elif pcr_val and pcr_val <= 0.6 and pcr_val > 0:
        warnings.append(f"PCR low ({pcr_val:.2f}) — market complacent, caution")

    # Options-flow extras (max-pain pull near expiry, OI-implied support/resistance)
    mp_dist = pcr.get("max_pain_dist_pct", 0) or 0
    if abs(mp_dist) >= 1.0:
        health["max_pain_note"] = (
            f"Spot {abs(mp_dist):.1f}% {'above' if mp_dist > 0 else 'below'} max-pain "
            f"({pcr.get('max_pain')}) — option writers pull toward it into expiry")
    oi_bias = pcr.get("oi_bias")
    if oi_bias == "put_heavy_support":
        health["oi_note"] = "Heavy put OI below spot — option writers see a floor (bullish lean)"
    elif oi_bias == "call_heavy_resistance":
        health["oi_note"] = "Heavy call OI above spot — option writers see a lid (bearish lean)"
    # Surface a recurring options-flow outage on System Health
    if pcr.get("ok") is False and pcr.get("error"):
        _log_health("options_flow", "NIFTY option chain", pcr.get("error"), session)

    # ── Overall mood ──────────────────────────────────────────────────────────
    if not warnings and n_trend in ("up", "strong_up") and vix_val < VIX_CAUTION and breadth >= 0.5 and macro_mood != "risk_off":
        health["market_mood"] = "bullish"
    elif len(warnings) >= 2 or not trade_ok or macro_mood == "risk_off":
        health["market_mood"] = "bearish"
    elif health["market_regime"] == "sideways" or "choppy" in health.get("intraday_regime", ""):
        health["market_mood"] = "neutral"

    health["trade_allowed"] = trade_ok
    health["warnings"]      = warnings

    _save(health)
    _print_health(health)
    return health


def _intraday_regime(candles: list) -> str:
    """Classify today's Nifty intraday price action from 5-min candles."""
    if len(candles) < 4:
        return "unknown"
    closes    = [c["close"] for c in candles]
    highs     = [c["high"]  for c in candles]
    lows      = [c["low"]   for c in candles]
    first_c   = closes[0]
    last_c    = closes[-1]
    total_rng = max(highs) - min(lows)
    net_move  = abs(last_c - first_c)
    # Direction ratio: how much of the range is net move?
    directional = net_move / (total_rng + 1e-9)

    # Count up vs down candles in last 6 bars
    recent = candles[-6:]
    up_bars   = sum(1 for c in recent if c["close"] > c["open"])
    down_bars = sum(1 for c in recent if c["close"] < c["open"])

    if directional > 0.55 and up_bars >= 4 and last_c > first_c:
        return "trending_up"
    if directional > 0.55 and down_bars >= 4 and last_c < first_c:
        return "trending_down"
    if directional < 0.3:
        return "choppy"
    return "mixed"


def load_market_health() -> dict:
    if os.path.exists(MARKET_STATE_FILE):
        try:
            with open(MARKET_STATE_FILE) as f:
                d = json.load(f)
            if isinstance(d, dict):
                # Ensure newer keys always exist even on an older saved file
                d.setdefault("macro", {})
                d.setdefault("macro_risk_factor", 1.0)
                return d
        except Exception:
            pass
    return {
        "trade_allowed": True, "market_mood": "neutral", "warnings": [],
        "nifty": {}, "vix": {"value": 15.0, "level": "normal"},
        "bank_nifty": {}, "sectors": {}, "leading_sectors": [],
        "market_regime": "normal", "intraday_regime": "unknown",
        "macro": {}, "macro_risk_factor": 1.0,
    }


def _fetch_index(symbol: str, start: str, end: str, downloader) -> dict:
    try:
        df = downloader(symbol, start=start, end=end)
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

        # ATH proximity
        high_60d = float(df["High"].tail(60).max()) if "High" in df.columns else today_c
        ath_pct  = round((today_c - high_60d) / high_60d * 100, 2)

        return {
            "symbol":         symbol,
            "value":          round(today_c, 2),
            "day_change_pct": round(day_chg, 2),
            "trend_5d":       trend(5),
            "trend_20d":      trend(20),
            "ath_pct_60d":    ath_pct,   # how far from 60d high
        }
    except Exception as e:
        print(f"[market] {symbol}: {e}")
        return {}


def _fetch_vix_with_percentile(downloader) -> dict:
    """Fetch VIX with 30-day percentile context to judge if current level is extreme."""
    try:
        today = ist_today()
        df = downloader("^INDIAVIX", start=(today - timedelta(days=45)).isoformat(), end=today.isoformat())
        if df is None or df.empty:
            return {"value": 15.0, "level": "normal", "percentile_30d": 50}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        closes = df["Close"].squeeze()
        val    = float(closes.iloc[-1])
        level  = "danger" if val >= VIX_DANGER else ("caution" if val >= VIX_CAUTION else "normal")

        # 30-day percentile: what % of last 30 days had VIX below today's?
        last30 = closes.tail(30).tolist()
        pct    = round(sum(1 for v in last30 if v < val) / max(len(last30), 1) * 100, 0)

        return {
            "value":         round(val, 2),
            "level":         level,
            "percentile_30d": pct,
            "avg_30d":       round(sum(last30) / len(last30), 2) if last30 else val,
        }
    except Exception:
        return {"value": 15.0, "level": "normal", "percentile_30d": 50}


def _sector_strength(start: str, end: str, downloader) -> Dict:
    result = {}
    for name, sym in SECTOR_PROXIES.items():
        d = _fetch_index(sym, start, end, downloader)
        if d:
            result[name] = d
        time.sleep(0.2)
    return result


def _save(health: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(MARKET_STATE_FILE, "w") as f:
        json.dump(health, f, indent=2)


def _print_health(h: dict) -> None:
    nifty = h.get("nifty", {})
    bn    = h.get("bank_nifty", {})
    vix   = h.get("vix", {})
    print(f"[market] Nifty={nifty.get('value','?')} ({nifty.get('day_change_pct',0):+.2f}%) "
          f"| BankNifty={bn.get('value','?')} ({bn.get('day_change_pct',0):+.2f}%) "
          f"| VIX={vix.get('value','?')} [{vix.get('level','?')} {vix.get('percentile_30d',50):.0f}th pct] "
          f"| Mood={h.get('market_mood')} | Regime={h.get('intraday_regime','?')} "
          f"| Trade={'YES' if h.get('trade_allowed') else 'NO'}")
    for w in h.get("warnings", []):
        print(f"  [!] {w}")
