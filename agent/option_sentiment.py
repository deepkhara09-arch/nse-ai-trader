"""
Option sentiment — Nifty Put-Call Ratio (PCR) from NSE's free option chain.

PCR (total put OI / total call OI) is a widely-watched contrarian sentiment gauge
in Indian markets:
  - very HIGH PCR (>1.5) = heavy put hedging / fear → often a contrarian bullish sign
  - very LOW PCR (<0.6)  = complacency / one-sided calls → caution
  - ~0.8–1.2             = balanced / neutral

Free, no auth (NSE public option-chain endpoint, needs warmed cookies + headers).
Fully graceful: any failure returns last-known (or neutral), tool never breaks.

Output: brain/option_sentiment.json  { date, pcr, total_pe_oi, total_ce_oi }
"""

import json
import os
from datetime import date

from agent.config import BRAIN_DIR
from agent.trading_calendar import ist_today

PCR_FILE = "brain/option_sentiment.json"
# NSE retired the old /api/option-chain-indices path (now 404) and the v3
# replacement returns an empty body without a separate expiry lookup — i.e. there
# is no stable, free, single-call PCR endpoint right now. Rather than ship a
# permanently-failing fetch (404 noise every run), PCR is DISABLED until a stable
# source is available. The wiring (market health + dashboard) already treats a
# neutral PCR as "no signal", so disabling it changes nothing else.
PCR_ENABLED = False
NSE_OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=NIFTY"


def fetch_pcr() -> dict:
    """Fetch Nifty PCR from the NSE option chain. Returns last-known on any error.
    Currently disabled (no stable free endpoint) — returns neutral cleanly."""
    if not PCR_ENABLED:
        return load_pcr()   # neutral; no network call, no log noise
    prev = load_pcr()
    try:
        from agent.data_fetcher import _NSE_SESSION, _warm_nse_session
        _warm_nse_session()
        r = _NSE_SESSION.get(NSE_OPTION_CHAIN_URL, timeout=12)
        if r.status_code != 200:
            print(f"[pcr] HTTP {r.status_code} — keeping last-known")
            return prev
        data = r.json()
        records = (data.get("records", {}) or {}).get("data", []) or []
        total_pe = total_ce = 0
        for row in records:
            ce = row.get("CE") or {}
            pe = row.get("PE") or {}
            total_ce += int(ce.get("openInterest", 0) or 0)
            total_pe += int(pe.get("openInterest", 0) or 0)
        if total_ce <= 0:
            return prev
        pcr = round(total_pe / total_ce, 3)
        snap = {
            "date":       ist_today().isoformat(),
            "pcr":        pcr,
            "total_pe_oi": total_pe,
            "total_ce_oi": total_ce,
            "reading":    _reading(pcr),
        }
        _save(snap)
        print(f"[pcr] Nifty PCR {pcr} ({snap['reading']}) | PE_OI {total_pe:,} CE_OI {total_ce:,}")
        return snap
    except Exception as e:
        print(f"[pcr] fetch failed (non-fatal, keeping last-known): {e}")
        return prev


def _reading(pcr: float) -> str:
    if pcr >= 1.5:  return "very_high_contrarian_bullish"
    if pcr >= 1.1:  return "bullish_bias"
    if pcr <= 0.6:  return "complacent_caution"
    if pcr <= 0.8:  return "bearish_bias"
    return "neutral"


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
    return {"date": None, "pcr": 0, "total_pe_oi": 0, "total_ce_oi": 0, "reading": "neutral"}


def _save(snap: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(PCR_FILE, "w") as f:
        json.dump(snap, f, indent=2)
