"""
Sector Rotation Tracker — groups stocks by sector, scores sector momentum.

Each session:
  1. Computes average trend/RSI/MACD for each sector from current stock data
  2. Produces a sector_momentum score (-1 to +1) per sector
  3. Injects sector_momentum into each stock's latest dict before brain analysis
  4. Persists sector scores to brain/sector_scores.json for dashboard display

Sector groupings are based on NSE GICS classifications for Nifty 100.
"""

import json
import os
from datetime import date
from typing import Dict, List

from agent.config import BRAIN_DIR

SECTOR_FILE = "brain/sector_scores.json"

# Stock → sector mapping for the full Nifty 100 universe
SECTOR_MAP: Dict[str, str] = {
    # Technology
    "TCS.NS":        "IT",
    "INFY.NS":       "IT",
    "HCLTECH.NS":    "IT",
    "WIPRO.NS":      "IT",
    "TECHM.NS":      "IT",

    # Banking & Finance
    "HDFCBANK.NS":   "Banking",
    "ICICIBANK.NS":  "Banking",
    "SBIN.NS":       "Banking",
    "KOTAKBANK.NS":  "Banking",
    "AXISBANK.NS":   "Banking",
    "INDUSINDBK.NS": "Banking",
    "BANDHANBNK.NS": "Banking",
    "IDFCFIRSTB.NS": "Banking",
    "PNB.NS":        "Banking",
    "CANBK.NS":      "Banking",
    "BANKBARODA.NS": "Banking",
    "UNIONBANK.NS":  "Banking",

    # Financial Services / NBFC
    "BAJFINANCE.NS": "NBFC",
    "BAJAJFINSV.NS": "NBFC",
    "MUTHOOTFIN.NS": "NBFC",
    "CHOLAFIN.NS":   "NBFC",
    "M&MFIN.NS":     "NBFC",
    "SHRIRAMFIN.NS": "NBFC",

    # Insurance
    "SBILIFE.NS":    "Insurance",
    "HDFCLIFE.NS":   "Insurance",
    "ICICIGI.NS":    "Insurance",
    "LICI.NS":       "Insurance",
    "GICRE.NS":      "Insurance",
    "NIACL.NS":      "Insurance",

    # Asset Management
    "HDFCAMC.NS":    "Asset Management",
    "NIPPONLIFE.NS": "Asset Management",
    "ABCAPITAL.NS":  "Asset Management",
    "MFSL.NS":       "Asset Management",

    # Energy & Oil
    "RELIANCE.NS":   "Energy",
    "ONGC.NS":       "Energy",
    "BPCL.NS":       "Energy",
    "IOC.NS":        "Energy",
    "HINDPETRO.NS":  "Energy",
    "GAIL.NS":       "Energy",
    "TATAPOWER.NS":  "Energy",
    "ADANIGREEN.NS": "Energy",
    "COALINDIA.NS":  "Energy",
    "NTPC.NS":       "Energy",
    "POWERGRID.NS":  "Energy",
    "NHPC.NS":       "Energy",
    "SJVN.NS":       "Energy",

    # Infrastructure / Capital Goods
    "LT.NS":         "Infra",
    "ADANIENT.NS":   "Infra",
    "ADANIPORTS.NS": "Infra",
    "BEL.NS":        "Infra",
    "HAL.NS":        "Infra",
    "SIEMENS.NS":    "Infra",
    "HAVELLS.NS":    "Infra",
    "POLYCAB.NS":    "Infra",
    "APLAPOLLO.NS":  "Infra",
    "CUMMINSIND.NS": "Infra",

    # Metals & Mining
    "TATASTEEL.NS":  "Metals",
    "JSWSTEEL.NS":   "Metals",
    "HINDALCO.NS":   "Metals",
    "GRASIM.NS":     "Metals",

    # Cement
    "ULTRACEMCO.NS": "Cement",

    # Automobile
    "MARUTI.NS":     "Auto",
    "TATAMOTORS.NS": "Auto",
    "EICHERMOT.NS":  "Auto",
    "HEROMOTOCO.NS": "Auto",

    # FMCG / Consumer
    "HINDUNILVR.NS": "FMCG",
    "ITC.NS":        "FMCG",
    "NESTLEIND.NS":  "FMCG",
    "BRITANNIA.NS":  "FMCG",
    "DABUR.NS":      "FMCG",
    "MARICO.NS":     "FMCG",
    "GODREJCP.NS":   "FMCG",
    "TATACONSUM.NS": "FMCG",
    "EMAMILTD.NS":   "FMCG",
    "COLPAL.NS":     "FMCG",
    "VBL.NS":        "FMCG",

    # Pharma & Healthcare
    "SUNPHARMA.NS":  "Pharma",
    "DRREDDY.NS":    "Pharma",
    "CIPLA.NS":      "Pharma",
    "DIVISLAB.NS":   "Pharma",
    "APOLLOHOSP.NS": "Pharma",

    # Consumer Discretionary / Retail
    "TITAN.NS":      "Consumer",
    "ASIANPAINT.NS": "Consumer",
    "PIDILITIND.NS": "Consumer",
    "DMART.NS":      "Consumer",
    "TRENT.NS":      "Consumer",
    "BAJAJHLDNG.NS": "Consumer",
    "PGHH.NS":       "Consumer",

    # Telecom
    "BHARTIARTL.NS": "Telecom",

    # New-age / Digital
    "ZOMATO.NS":     "Digital",
    "NYKAA.NS":      "Digital",
    "PAYTM.NS":      "Digital",
    "POLICYBZR.NS":  "Digital",
    "IRCTC.NS":      "Digital",

    # Financials / Lending (infra finance)
    "RECLTD.NS":     "Infra Finance",
    "PFC.NS":        "Infra Finance",
    "IRFC.NS":       "Infra Finance",
    "AAVAS.NS":      "Infra Finance",
    "HOMEFIRST.NS":  "Infra Finance",
}


def compute_sector_scores(stock_data: Dict) -> Dict[str, dict]:
    """
    Compute momentum score per sector from current stock data.
    Returns dict: sector_name → {score, trend, stocks, avg_rsi, avg_macd_hist}
    """
    sector_buckets: Dict[str, List[dict]] = {}

    for ticker, entry in stock_data.items():
        sector = SECTOR_MAP.get(ticker)
        if not sector or not entry or "latest" not in entry:
            continue
        d = entry["latest"]
        sector_buckets.setdefault(sector, []).append({
            "ticker":    ticker,
            "rsi":       d.get("rsi", 50),
            "macd_hist": d.get("macd_hist", 0),
            "trend":     entry.get("trend_10d", "sideways"),
            "vol_rel":   d.get("vol_rel", 1.0),
            "bb_pct":    d.get("bb_pct", 0.5),
        })

    trend_score_map = {
        "strong_up": 1.0, "up": 0.6, "sideways": 0.0,
        "down": -0.6, "strong_down": -1.0, "unknown": 0.0,
    }

    results = {}
    for sector, stocks in sector_buckets.items():
        n = len(stocks)
        if n == 0:
            continue

        avg_rsi      = sum(s["rsi"] for s in stocks) / n
        avg_macd     = sum(s["macd_hist"] for s in stocks) / n
        avg_vol      = sum(s["vol_rel"] for s in stocks) / n
        avg_trend    = sum(trend_score_map.get(s["trend"], 0) for s in stocks) / n
        avg_bb       = sum(s["bb_pct"] for s in stocks) / n

        # RSI component: 0 at RSI=50, positive when RSI 50-65, negative when <45
        rsi_score = (avg_rsi - 50) / 25   # maps 25→-1, 50→0, 75→+1

        # MACD component
        macd_score = 1.0 if avg_macd > 0 else -1.0

        # Volume component: high vol = conviction
        vol_score = min(1.0, (avg_vol - 1.0) * 2)   # 1.0x→0, 1.5x→1.0

        # Composite sector momentum (-1 to +1)
        momentum = round(
            avg_trend * 0.40
            + rsi_score * 0.25
            + macd_score * 0.20
            + vol_score * 0.10
            + (avg_bb - 0.5) * 0.10,   # BB position
            3
        )
        momentum = max(-1.0, min(1.0, momentum))

        results[sector] = {
            "momentum":    momentum,
            "avg_rsi":     round(avg_rsi, 1),
            "avg_macd":    round(avg_macd, 4),
            "avg_vol_rel": round(avg_vol, 2),
            "stock_count": n,
            "stocks":      [s["ticker"].replace(".NS", "") for s in stocks],
            "date":        date.today().isoformat(),
        }

    return results


def inject_sector_momentum(stock_data: Dict, sector_scores: Dict) -> Dict:
    """
    Injects sector_momentum and sector fields into each stock's latest dict.
    Called before brain analysis so analyse_stock can use the sector context.
    """
    for ticker, entry in stock_data.items():
        sector = SECTOR_MAP.get(ticker, "Other")
        if "latest" in entry:
            entry["latest"]["sector"] = sector
            entry["latest"]["sector_momentum"] = sector_scores.get(
                sector, {}
            ).get("momentum", 0.0)
    return stock_data


def get_stock_sector(ticker: str) -> str:
    return SECTOR_MAP.get(ticker, "Other")


def load_sector_scores() -> Dict:
    if os.path.exists(SECTOR_FILE):
        with open(SECTOR_FILE) as f:
            return json.load(f)
    return {}


def save_sector_scores(data: Dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    existing = load_sector_scores()
    # Keep history: append today's scores per sector
    today = date.today().isoformat()
    for sector, info in data.items():
        if sector not in existing:
            existing[sector] = {"history": [], "latest": {}}
        existing[sector]["history"] = (
            existing[sector].get("history", [])[-29:]
            + [{"date": today, "momentum": info["momentum"]}]
        )
        existing[sector]["latest"] = info
    with open(SECTOR_FILE, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"[sector] scored {len(data)} sectors")
