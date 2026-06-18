"""
News fetcher — free RSS feeds + rule-based sentiment scoring.
No API key. No paid service. Runs 3x daily.
"""

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import Dict, List
from urllib.request import urlopen, Request
from urllib.error import URLError

from agent.config import BRAIN_DIR, NEWS_FILE, NEWS_FEEDS, POSITIVE_WORDS, NEGATIVE_WORDS


# Ticker → company name fragments for article matching
_NAMES: Dict[str, List[str]] = {
    "RELIANCE.NS":   ["reliance", "ril"],
    "TCS.NS":        ["tcs", "tata consultancy"],
    "INFY.NS":       ["infosys", "infy"],
    "HDFCBANK.NS":   ["hdfc bank", "hdfcbank"],
    "ICICIBANK.NS":  ["icici bank", "icicibank"],
    "HINDUNILVR.NS": ["hindustan unilever", "hul"],
    "ITC.NS":        ["itc limited", " itc "],
    "SBIN.NS":       ["sbi", "state bank of india"],
    "BHARTIARTL.NS": ["airtel", "bharti airtel"],
    "KOTAKBANK.NS":  ["kotak bank", "kotak mahindra"],
    "LT.NS":         ["larsen", "l&t", "larsen & toubro"],
    "AXISBANK.NS":   ["axis bank"],
    "ASIANPAINT.NS": ["asian paints"],
    "MARUTI.NS":     ["maruti", "suzuki"],
    "BAJFINANCE.NS": ["bajaj finance"],
    "HCLTECH.NS":    ["hcl tech", "hcltech"],
    "WIPRO.NS":      ["wipro"],
    "ULTRACEMCO.NS": ["ultratech cement"],
    "TITAN.NS":      ["titan company", "tanishq"],
    "SUNPHARMA.NS":  ["sun pharma", "sun pharmaceutical"],
    "NESTLEIND.NS":  ["nestle india"],
    "POWERGRID.NS":  ["power grid"],
    "NTPC.NS":       ["ntpc"],
    "ONGC.NS":       ["ongc", "oil and natural gas"],
    "COALINDIA.NS":  ["coal india"],
    "TATAMOTORS.NS": ["tata motors"],
    "TATASTEEL.NS":  ["tata steel"],
    "JSWSTEEL.NS":   ["jsw steel"],
    "ADANIENT.NS":   ["adani enterprises"],
    "ADANIPORTS.NS": ["adani ports"],
    "TECHM.NS":      ["tech mahindra"],
    "DRREDDY.NS":    ["dr reddy", "dr. reddy"],
    "DIVISLAB.NS":   ["divi's lab", "divis lab"],
    "CIPLA.NS":      ["cipla"],
    "BAJAJFINSV.NS": ["bajaj finserv"],
    "EICHERMOT.NS":  ["eicher", "royal enfield"],
    "HEROMOTOCO.NS": ["hero motocorp"],
    "APOLLOHOSP.NS": ["apollo hospital", "apollo health"],
    "TATACONSUM.NS": ["tata consumer"],
    "BRITANNIA.NS":  ["britannia"],
    "PIDILITIND.NS": ["pidilite", "fevicol"],
    "DABUR.NS":      ["dabur"],
    "MARICO.NS":     ["marico", "parachute"],
    "GODREJCP.NS":   ["godrej consumer"],
    "MUTHOOTFIN.NS": ["muthoot"],
    "INDUSINDBK.NS": ["indusind bank"],
    "BANDHANBNK.NS": ["bandhan bank"],
    "IDFCFIRSTB.NS": ["idfc first"],
    "GRASIM.NS":     ["grasim"],
    "SHREECEM.NS":   ["shree cement"],
}


def fetch_news(tickers: List[str]) -> Dict:
    articles = []
    for url in NEWS_FEEDS:
        fetched = _fetch_rss(url)
        articles.extend(fetched)
        time.sleep(1)
    print(f"[news] {len(articles)} articles fetched from {len(NEWS_FEEDS)} feeds")

    result = {}
    for ticker in tickers:
        frags = _NAMES.get(ticker, [ticker.replace(".NS", "").lower()])
        matched = [a for a in articles if any(f in a["text"] for f in frags)]
        score = _sentiment(matched)
        result[ticker] = {
            "date":       date.today().isoformat(),
            "count":      len(matched),
            "score":      score,
            "headlines":  [a["title"] for a in matched[:6]],
        }
    return result


def _fetch_rss(url: str) -> List[dict]:
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=12) as r:
            raw = r.read()
    except URLError as e:
        print(f"[news] {url}: {e}")
        return []
    try:
        root = ET.fromstring(raw)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        out = []
        for item in items:
            title = (item.findtext("title") or
                     item.findtext("atom:title", namespaces=ns) or "").strip()
            desc  = (item.findtext("description") or
                     item.findtext("atom:summary", namespaces=ns) or "").strip()
            if title:
                out.append({"title": title, "text": (title + " " + desc).lower()})
        return out
    except ET.ParseError:
        return []


def _sentiment(articles: List[dict]) -> float:
    if not articles:
        return 0.0
    total = 0.0
    for a in articles:
        t = a["text"]
        pos = sum(1 for w in POSITIVE_WORDS if w in t)
        neg = sum(1 for w in NEGATIVE_WORDS if w in t)
        denom = pos + neg
        total += (pos - neg) / denom if denom else 0
    return round(total / len(articles), 3)


def load_news() -> Dict:
    if os.path.exists(NEWS_FILE):
        with open(NEWS_FILE) as f:
            return json.load(f)
    return {}


def save_news(data: Dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    existing = load_news()
    today = date.today().isoformat()
    for ticker, info in data.items():
        if ticker not in existing:
            existing[ticker] = {"history": [], "latest": {}}
        existing[ticker]["history"] = (
            existing[ticker].get("history", [])[-6:] +
            [{"date": today, "score": info["score"], "count": info["count"]}]
        )
        existing[ticker]["latest"] = info
    with open(NEWS_FILE, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"[news] saved sentiment for {len(data)} tickers")
