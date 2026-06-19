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


# Ticker → company name fragments for article matching (full Nifty-100 coverage)
_NAMES: Dict[str, List[str]] = {
    # ── Nifty 50 ──────────────────────────────────────────────────────────────
    "RELIANCE.NS":   ["reliance", "ril", "reliance industries"],
    "TCS.NS":        ["tcs", "tata consultancy"],
    "INFY.NS":       ["infosys", "infy"],
    "HDFCBANK.NS":   ["hdfc bank", "hdfcbank"],
    "ICICIBANK.NS":  ["icici bank", "icicibank"],
    "HINDUNILVR.NS": ["hindustan unilever", "hul", "hindusthan unilever"],
    "ITC.NS":        ["itc limited", " itc ", "itc ltd"],
    "SBIN.NS":       ["sbi", "state bank of india", "state bank"],
    "BHARTIARTL.NS": ["airtel", "bharti airtel", "bharti enterprises"],
    "KOTAKBANK.NS":  ["kotak bank", "kotak mahindra"],
    "LT.NS":         ["larsen", "l&t", "larsen & toubro", "larsen and toubro"],
    "AXISBANK.NS":   ["axis bank", "axisbank"],
    "ASIANPAINT.NS": ["asian paints", "asian paint"],
    "MARUTI.NS":     ["maruti", "maruti suzuki"],
    "BAJFINANCE.NS": ["bajaj finance"],
    "HCLTECH.NS":    ["hcl tech", "hcltech", "hcl technologies"],
    "WIPRO.NS":      ["wipro"],
    "ULTRACEMCO.NS": ["ultratech cement", "ultratech"],
    "TITAN.NS":      ["titan company", "tanishq", "titan industries"],
    "SUNPHARMA.NS":  ["sun pharma", "sun pharmaceutical"],
    "NESTLEIND.NS":  ["nestle india", "nestle"],
    "POWERGRID.NS":  ["power grid", "pgcil", "power grid corporation"],
    "NTPC.NS":       ["ntpc", "national thermal power"],
    "ONGC.NS":       ["ongc", "oil and natural gas", "oil & natural gas"],
    "COALINDIA.NS":  ["coal india", "coalindia"],
    "TATAMOTORS.NS": ["tata motors", "tatamotors", "jaguar", "jlr"],
    "TATASTEEL.NS":  ["tata steel", "tatasteel"],
    "JSWSTEEL.NS":   ["jsw steel", "jsw group"],
    "ADANIENT.NS":   ["adani enterprises", "adani group"],
    "ADANIPORTS.NS": ["adani ports", "mundra port"],
    "TECHM.NS":      ["tech mahindra", "techmahindra"],
    "DRREDDY.NS":    ["dr reddy", "dr. reddy", "drreddys"],
    "DIVISLAB.NS":   ["divi's lab", "divis lab", "divi laboratories"],
    "CIPLA.NS":      ["cipla"],
    "BAJAJFINSV.NS": ["bajaj finserv"],
    "EICHERMOT.NS":  ["eicher", "royal enfield", "eicher motors"],
    "HEROMOTOCO.NS": ["hero motocorp", "hero moto", "hero honda"],
    "APOLLOHOSP.NS": ["apollo hospital", "apollo health", "apollo hospitals"],
    "TATACONSUM.NS": ["tata consumer", "tata tea", "tata beverages"],
    "BRITANNIA.NS":  ["britannia"],
    "PIDILITIND.NS": ["pidilite", "fevicol"],
    "GRASIM.NS":     ["grasim"],
    "BEL.NS":        ["bel", "bharat electronics"],
    "BPCL.NS":       ["bpcl", "bharat petroleum"],
    "TRENT.NS":      ["trent", "westside", "zudio"],
    "SHRIRAMFIN.NS": ["shriram finance", "shriram transport"],
    "HINDALCO.NS":   ["hindalco", "novelis"],
    "SBILIFE.NS":    ["sbi life", "sbi life insurance"],
    "HDFCLIFE.NS":   ["hdfc life", "hdfc life insurance"],
    "INDUSINDBK.NS": ["indusind bank", "indusind"],
    # ── Nifty Next 50 ─────────────────────────────────────────────────────────
    "SIEMENS.NS":    ["siemens india", "siemens"],
    "HAVELLS.NS":    ["havells"],
    "DABUR.NS":      ["dabur"],
    "MARICO.NS":     ["marico", "parachute"],
    "GODREJCP.NS":   ["godrej consumer", "godrej cp", "godrej"],
    "MUTHOOTFIN.NS": ["muthoot", "muthoot finance"],
    "IDFCFIRSTB.NS": ["idfc first", "idfc first bank"],
    "BANDHANBNK.NS": ["bandhan bank", "bandhan"],
    "PNB.NS":        ["pnb", "punjab national bank"],
    "CANBK.NS":      ["canara bank", "canbk"],
    "BANKBARODA.NS": ["bank of baroda", "bob"],
    "UNIONBANK.NS":  ["union bank", "union bank of india"],
    "IRCTC.NS":      ["irctc", "indian railway catering"],
    "IRFC.NS":       ["irfc", "indian railway finance"],
    "HAL.NS":        ["hal", "hindustan aeronautics"],
    "ZOMATO.NS":     ["zomato"],
    "NYKAA.NS":      ["nykaa", "fss"],
    "PAYTM.NS":      ["paytm", "one97"],
    "POLICYBZR.NS":  ["policybazaar", "pb fintech"],
    "DMART.NS":      ["dmart", "avenue supermarts", "d-mart"],
    "ABCAPITAL.NS":  ["aditya birla capital", "ab capital"],
    "MFSL.NS":       ["max financial", "max life"],
    "LICI.NS":       ["lic", "life insurance corporation"],
    "GICRE.NS":      ["gic re", "general insurance corporation"],
    "NIACL.NS":      ["new india assurance", "new india"],
    "ICICIGI.NS":    ["icici lombard", "icici general"],
    "HDFCAMC.NS":    ["hdfc amc", "hdfc mutual fund"],
    "NIPPONLIFE.NS": ["nippon life", "nippon india", "reliance nippon"],
    "CHOLAFIN.NS":   ["cholamandalam", "chola finance"],
    "BAJAJHLDNG.NS": ["bajaj holdings", "bajaj auto holdings"],
    "COLPAL.NS":     ["colgate", "colgate palmolive"],
    "EMAMILTD.NS":   ["emami"],
    "VBL.NS":        ["varun beverages", "pepsi varun"],
    "TATAPOWER.NS":  ["tata power"],
    "ADANIGREEN.NS": ["adani green", "adani green energy"],
    "NHPC.NS":       ["nhpc", "national hydroelectric"],
    "SJVN.NS":       ["sjvn", "satluj jal vidyut"],
    "RECLTD.NS":     ["rec ltd", "rural electrification"],
    "PFC.NS":        ["pfc", "power finance corporation"],
    "GAIL.NS":       ["gail", "gail india"],
    "IOC.NS":        ["ioc", "indian oil", "indian oil corporation"],
    "HINDPETRO.NS":  ["hpcl", "hindustan petroleum"],
    "APLAPOLLO.NS":  ["apl apollo", "apl apollo tubes"],
    "POLYCAB.NS":    ["polycab"],
    "CUMMINSIND.NS": ["cummins india", "cummins"],
    "AAVAS.NS":      ["aavas financiers", "aavas"],
    "HOMEFIRST.NS":  ["home first finance", "home first"],
    "M&MFIN.NS":     ["mahindra finance", "m&m financial", "mahindra financial"],
    "PGHH.NS":       ["p&g", "procter & gamble", "procter and gamble"],
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
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; NSENewsBot/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })
        with urlopen(req, timeout=15) as r:
            raw = r.read()
    except Exception as e:
        print(f"[news] {url}: {e}")
        return []
    try:
        # Strip any BOM and fix common encoding issues before parsing
        raw = raw.lstrip(b"\xef\xbb\xbf")
        text = raw.decode("utf-8", errors="replace")
        # Remove control chars that break XML parsers
        text = "".join(ch for ch in text if ch >= " " or ch in "\n\r\t")
        root = ET.fromstring(text)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        out = []
        for item in items[:40]:   # cap per feed to avoid huge parsing
            title = (item.findtext("title") or
                     item.findtext("atom:title", namespaces=ns) or "").strip()
            desc  = (item.findtext("description") or
                     item.findtext("content") or
                     item.findtext("atom:summary", namespaces=ns) or "").strip()
            # Strip HTML tags from description
            import re
            desc = re.sub(r"<[^>]+>", " ", desc)
            if title:
                out.append({"title": title, "text": (title + " " + desc).lower()})
        return out
    except ET.ParseError as e:
        print(f"[news] XML parse error {url}: {e}")
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
        if denom == 0:
            continue
        # Raw score per article, clamped to [-1, 1]
        raw = (pos - neg) / max(denom, 1)
        # Amplify: articles with many keywords are higher conviction
        conviction = min(denom / 5, 1.0)   # 5+ keywords = full conviction
        total += raw * (0.6 + 0.4 * conviction)
    return round(max(-1.0, min(1.0, total / len(articles))), 3)


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
        history = (existing[ticker].get("history", []) +
                   [{"date": today, "score": info["score"], "count": info["count"]}])[-10:]
        existing[ticker]["history"] = history

        # Sentiment momentum: recent 3 vs prior 3 sessions
        scores = [h["score"] for h in history]
        if len(scores) >= 6:
            recent = sum(scores[-3:]) / 3
            prior  = sum(scores[-6:-3]) / 3
            momentum = round(recent - prior, 3)
        elif len(scores) >= 2:
            momentum = round(scores[-1] - scores[-2], 3)
        else:
            momentum = 0.0

        # Weighted score: most recent session weighted most
        if len(scores) >= 3:
            weighted = scores[-1] * 0.5 + scores[-2] * 0.3 + scores[-3] * 0.2
        else:
            weighted = scores[-1] if scores else 0.0

        info["momentum"]       = momentum
        info["weighted_score"] = round(weighted, 3)
        info["trend"] = ("improving" if momentum > 0.05 else
                         "worsening" if momentum < -0.05 else "stable")
        existing[ticker]["latest"] = info

    with open(NEWS_FILE, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"[news] saved sentiment for {len(data)} tickers")
