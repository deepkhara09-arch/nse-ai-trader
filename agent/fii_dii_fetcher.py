"""
FII / DII flow fetcher — real institutional cash-market flows from NSE.

Foreign (FII) and Domestic (DII) institutional net buy/sell is the single biggest
driver of Nifty-100 direction. This replaces the previous sector-ETF *proxy* with
NSE's actual published daily figures.

Free, no auth (NSE public endpoint, needs browser-like headers + cookie warmup).
Fully graceful: any failure returns the last-known value (or neutral), so the tool
never breaks — it just keeps using the sector proxy as before.

Output: brain/fii_dii.json
  { date, fii_net_cr, dii_net_cr, combined_net_cr, signal, history:[...] }
  signal: strong_inflow / inflow / neutral / outflow / strong_outflow
"""

import json
import os
from datetime import date
from typing import Dict

from agent.config import BRAIN_DIR

FII_DII_FILE = "brain/fii_dii.json"

# NSE publishes FII/DII daily; this is the public reports endpoint used by the site.
NSE_FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"


def fetch_fii_dii() -> dict:
    """
    Fetch today's FII + DII net cash figures (in ₹ crore). Returns a dict; on any
    failure returns the last-known snapshot (or a neutral default) so callers never
    have to handle errors.
    """
    prev = load_fii_dii()
    try:
        from agent.data_fetcher import _NSE_SESSION, _warm_nse_session
        _warm_nse_session()
        r = _NSE_SESSION.get(NSE_FII_DII_URL, timeout=12)
        if r.status_code != 200:
            print(f"[fii-dii] HTTP {r.status_code} — keeping last-known")
            return prev
        data = r.json()
        # Response is a list of two dicts: one FII, one DII (each with netValue).
        fii_net = dii_net = 0.0
        for row in data if isinstance(data, list) else []:
            cat = str(row.get("category", "")).upper()
            net = _to_float(row.get("netValue") or row.get("net") or 0)
            if "FII" in cat or "FPI" in cat:
                fii_net = net
            elif "DII" in cat:
                dii_net = net
        combined = round(fii_net + dii_net, 2)
        snap = {
            "date":            date.today().isoformat(),
            "fii_net_cr":      round(fii_net, 2),
            "dii_net_cr":      round(dii_net, 2),
            "combined_net_cr": combined,
            "signal":          _classify(combined, fii_net),
        }
        _save(snap)
        print(f"[fii-dii] FII {fii_net:+,.0f} Cr | DII {dii_net:+,.0f} Cr "
              f"| combined {combined:+,.0f} Cr | {snap['signal']}")
        return snap
    except Exception as e:
        print(f"[fii-dii] fetch failed (non-fatal, keeping last-known): {e}")
        return prev


def _classify(combined: float, fii: float) -> str:
    """Classify net institutional flow into a directional signal."""
    # FII is the more market-moving leg, so weight it; thresholds in ₹ crore.
    score = combined + fii * 0.5
    if score >= 3000:   return "strong_inflow"
    if score >= 800:    return "inflow"
    if score <= -3000:  return "strong_outflow"
    if score <= -800:   return "outflow"
    return "neutral"


def _to_float(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def load_fii_dii() -> dict:
    if os.path.exists(FII_DII_FILE):
        try:
            with open(FII_DII_FILE) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else _neutral()
        except Exception:
            return _neutral()
    return _neutral()


def _neutral() -> dict:
    return {"date": None, "fii_net_cr": 0.0, "dii_net_cr": 0.0,
            "combined_net_cr": 0.0, "signal": "neutral"}


def _save(snap: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    # keep a short rolling history for the dashboard
    existing = load_fii_dii()
    hist = existing.get("history", []) if isinstance(existing, dict) else []
    hist = [h for h in hist if h.get("date") != snap["date"]]  # dedupe today
    hist.append({"date": snap["date"], "fii": snap["fii_net_cr"],
                 "dii": snap["dii_net_cr"], "combined": snap["combined_net_cr"]})
    snap["history"] = hist[-30:]
    with open(FII_DII_FILE, "w") as f:
        json.dump(snap, f, indent=2)
