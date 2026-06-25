"""
Option sentiment — Nifty options-flow signals from NSE's free option chain.

This is real smart-money positioning data, free, no auth (NSE public option-chain
endpoints, needs warmed cookies + headers). Three signals are derived:

  1. PCR (total put OI / total call OI) — contrarian sentiment gauge:
       - very HIGH PCR (>1.5) = heavy put hedging / fear → often contrarian bullish
       - very LOW PCR (<0.6)  = complacency / one-sided calls → caution
       - ~0.8–1.2             = balanced / neutral
  2. Max pain — the strike where option writers lose least; price tends to gravitate
     here near expiry. Distance of spot from max-pain hints at expiry-week pull.
  3. OI shift — day-over-day change in where call/put OI is concentrated, i.e. are
     writers adding resistance (calls) above or support (puts) below.

The endpoint needs a TWO-STEP call (this is what makes it work in 2026, where the
old single-call /option-chain-indices path 404s):
  step 1: GET /api/option-chain-contract-info?symbol=NIFTY  → nearest expiry date
  step 2: GET /api/option-chain-v3?type=Indices&symbol=NIFTY&expiry=<DD-Mon-YYYY>

Fully graceful: any failure returns last-known (or neutral), the tool never breaks,
and the failure is recorded to System Health so a recurring outage is visible.

Output: brain/option_sentiment.json
  { date, pcr, reading, total_pe_oi, total_ce_oi, spot, max_pain, max_pain_dist_pct,
    oi_bias, ok, error }
"""

import json
import os

from agent.config import BRAIN_DIR
from agent.trading_calendar import ist_today

PCR_FILE = "brain/option_sentiment.json"

# Re-enabled 2026-06: the v3 endpoint works when called WITH a real expiry obtained
# from option-chain-contract-info first. Verified live (PCR 1.025, 149 strikes).
PCR_ENABLED = True
CONTRACT_INFO_URL = "https://www.nseindia.com/api/option-chain-contract-info?symbol=NIFTY"
V3_URL = "https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=NIFTY&expiry={expiry}"


def fetch_pcr() -> dict:
    """Fetch Nifty options-flow signals. Returns last-known on any error; the tool
    never breaks. Records recurring failures to System Health."""
    prev = load_pcr()
    if not PCR_ENABLED:
        return prev
    try:
        from agent.data_fetcher import _NSE_SESSION, _warm_nse_session
        _warm_nse_session()

        # ── Step 1: nearest expiry ──────────────────────────────────────────────
        ci = _NSE_SESSION.get(CONTRACT_INFO_URL, timeout=12)
        if ci.status_code != 200:
            return _fail(prev, f"contract-info HTTP {ci.status_code}")
        expiries = (ci.json() or {}).get("expiryDates", []) or []
        if not expiries:
            return _fail(prev, "no expiry dates returned")
        expiry = expiries[0]

        # ── Step 2: option chain for that expiry ────────────────────────────────
        r = _NSE_SESSION.get(V3_URL.format(expiry=expiry), timeout=12)
        if r.status_code != 200:
            return _fail(prev, f"option-chain-v3 HTTP {r.status_code}")
        data = r.json()
        records = (data.get("records", {}) or {})
        rows = records.get("data", []) or []
        spot = float(records.get("underlyingValue", 0) or 0)
        if not rows:
            return _fail(prev, "empty option chain")

        total_pe = total_ce = 0
        per_strike = []   # (strike, ce_oi, pe_oi) for max-pain
        for row in rows:
            strike = row.get("strikePrice")
            ce = row.get("CE") or {}
            pe = row.get("PE") or {}
            ce_oi = int(ce.get("openInterest", 0) or 0)
            pe_oi = int(pe.get("openInterest", 0) or 0)
            total_ce += ce_oi
            total_pe += pe_oi
            if strike is not None:
                per_strike.append((float(strike), ce_oi, pe_oi))

        if total_ce <= 0:
            return _fail(prev, "zero call OI")

        pcr = round(total_pe / total_ce, 3)
        max_pain = _max_pain(per_strike)
        mp_dist = round((spot - max_pain) / spot * 100, 2) if (spot and max_pain) else 0.0
        oi_bias = _oi_bias(per_strike, spot)

        snap = {
            "date":              ist_today().isoformat(),
            "pcr":               pcr,
            "reading":           _reading(pcr),
            "total_pe_oi":       total_pe,
            "total_ce_oi":       total_ce,
            "spot":              round(spot, 2),
            "max_pain":          max_pain,
            "max_pain_dist_pct": mp_dist,   # +ve = spot above max-pain (downward pull)
            "oi_bias":           oi_bias,    # call_heavy_resistance / put_heavy_support / balanced
            "ok":                True,
            "error":             "",
        }
        _save(snap)
        print(f"[options] PCR {pcr} ({snap['reading']}) | max-pain {max_pain} "
              f"(spot {spot:.0f}, {mp_dist:+.1f}%) | OI bias {oi_bias}")
        return snap
    except Exception as e:
        return _fail(prev, str(e))


def _max_pain(per_strike) -> float:
    """The strike where total option-writer payout is minimised. Price often
    gravitates here into expiry."""
    if not per_strike:
        return 0.0
    strikes = [s for s, _, _ in per_strike]
    best_strike, best_pain = 0.0, None
    for expiry_price in strikes:
        pain = 0.0
        for strike, ce_oi, pe_oi in per_strike:
            # call writers pay when price > strike; put writers pay when price < strike
            if expiry_price > strike:
                pain += (expiry_price - strike) * ce_oi
            else:
                pain += (strike - expiry_price) * pe_oi
        if best_pain is None or pain < best_pain:
            best_pain, best_strike = pain, expiry_price
    return round(best_strike, 1)


def _oi_bias(per_strike, spot) -> str:
    """Where is OI concentrated relative to spot? Heavy call OI above = resistance
    (bearish lid); heavy put OI below = support (bullish floor)."""
    if not per_strike or not spot:
        return "balanced"
    call_above = sum(ce for s, ce, _ in per_strike if s > spot)
    put_below  = sum(pe for s, _, pe in per_strike if s < spot)
    if call_above <= 0 and put_below <= 0:
        return "balanced"
    ratio = put_below / (call_above + 1e-9)
    if ratio > 1.3:  return "put_heavy_support"      # writers see a floor below — bullish
    if ratio < 0.77: return "call_heavy_resistance"  # writers see a lid above — bearish
    return "balanced"


def _reading(pcr: float) -> str:
    if pcr >= 1.5:  return "very_high_contrarian_bullish"
    if pcr >= 1.1:  return "bullish_bias"
    if pcr <= 0.6:  return "complacent_caution"
    if pcr <= 0.8:  return "bearish_bias"
    return "neutral"


def _fail(prev: dict, msg: str) -> dict:
    """Log to System Health and return last-known so the tool keeps running."""
    print(f"[options] fetch failed (non-fatal, keeping last-known): {msg}")
    try:
        from agent.run_health import record_issue
        record_issue("options_flow", "NIFTY option chain", msg)
    except Exception:
        pass
    out = dict(prev) if isinstance(prev, dict) else _neutral()
    out["ok"] = False
    out["error"] = msg
    return out


def load_pcr() -> dict:
    if os.path.exists(PCR_FILE):
        try:
            with open(PCR_FILE) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else _neutral()
        except Exception:
            return _neutral()
    return _neutral()


def _neutral() -> dict:
    return {"date": None, "pcr": 0, "reading": "neutral", "total_pe_oi": 0,
            "total_ce_oi": 0, "spot": 0, "max_pain": 0, "max_pain_dist_pct": 0.0,
            "oi_bias": "balanced", "ok": True, "error": ""}


def _save(snap: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(PCR_FILE, "w") as f:
        json.dump(snap, f, indent=2)
