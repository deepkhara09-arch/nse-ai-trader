"""
News fetcher — free RSS feeds + rule-based sentiment scoring.
No API key. No paid service. Runs once daily (preclose).

Two layers: (1) market-wide RSS feeds (Moneycontrol/ET/BS/Mint) matched to each
stock by company name; (2) per-stock Google News RSS as a fallback when a stock
isn't mentioned in the market feeds — closes the coverage gap for smaller names.
"""

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import Dict, List
from urllib.request import urlopen, Request

from agent.config import BRAIN_DIR, NEWS_FILE, NEWS_FEEDS, POSITIVE_WORDS, NEGATIVE_WORDS
from agent.trading_calendar import ist_today


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
    # ── User-added stocks (June 2026) ──────────────────────────────────────────
    "ANANTRAJ.NS":   ["anant raj", "anantraj"],
    "ETERNAL.NS":    ["eternal", "zomato", "blinkit"],          # Zomato renamed to Eternal
    "JIOFIN.NS":     ["jio financial", "jio finance", "jiofin"],
    "ASHOKLEY.NS":   ["ashok leyland", "ashok leylnd"],
    "TMPV.NS":       ["tata motors passenger", "tmpv", "tata motors pv"],
    "FEDERALBNK.NS": ["federal bank", "federalbank"],
    "SWIGGY.NS":     ["swiggy"],
    "BAJAJFL.NS":    ["bajaj finance"],                          # Bajaj Finance
    "BHEL.NS":       ["bhel", "bharat heavy electricals"],
}


# Analyst-call / recommendation pieces are NOT news. We study what is HAPPENING
# to the company and its industry (orders, results, deals, probes, capacity…) —
# not what some brokerage tells people to do. These markers drop an article
# entirely: it never reaches sentiment scoring, the dashboard headlines, or the
# news-outcome learner.
_ANALYST_CALL_MARKERS = [
    "buy call", "sell call", "hold call", "target price", "price target",
    "brokerage", "initiates coverage", "upgrades", "downgrades", "maintains",
    "rating", "recommend", "top picks", "stock picks", "stocks to buy",
    "stocks to sell", "stocks to watch", "multibagger", "analyst",
    "overweight", "underweight", "outperform", "underperform",
    "should you buy", "technical pick", "trading strategy", "share price target",
    # price-tracker pages and weekly technical-outlook pieces are noise, not news
    "live nse:", "price & chart", "stock price & chart", "outlook for the week",
    "support and resistance", "technical outlook",
]


def _is_analyst_piece(text: str) -> bool:
    return any(m in text for m in _ANALYST_CALL_MARKERS)


# Google News RSS — free, no key. A per-stock query closes the coverage gap where
# a stock simply isn't mentioned in the 4 market-wide feeds on a given day.
_GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}+stock+when:7d&hl=en-IN&gl=IN&ceid=IN:en"
# Cap how many stocks we hit per run so we never hammer Google (news runs once/day
# at preclose; after exploration it's only the focus stocks anyway).
_GOOGLE_NEWS_MAX = 30


def fetch_news(tickers: List[str]) -> Dict:
    articles = []
    for url in NEWS_FEEDS:
        fetched = _fetch_rss(url)
        articles.extend(fetched)
        time.sleep(1)
    print(f"[news] {len(articles)} articles fetched from {len(NEWS_FEEDS)} feeds")

    result = {}
    for i, ticker in enumerate(tickers):
        frags = _NAMES.get(ticker, [ticker.replace(".NS", "").lower()])
        matched = [a for a in articles if any(f in a["text"] for f in frags)]

        # If the market-wide feeds gave little/nothing for this stock, ask Google
        # News directly for it. Only for a bounded number of stocks per run.
        if len(matched) < 2 and i < _GOOGLE_NEWS_MAX:
            g = _fetch_google_news(ticker, frags)
            if g:
                # de-dupe by title against what we already matched
                seen = {a["title"] for a in matched}
                matched.extend(a for a in g if a["title"] not in seen)
            time.sleep(0.5)   # be polite to Google

        # Keep only REAL company/industry news — drop analyst-call/recommendation
        # pieces from scoring, headlines and the news-outcome learner alike.
        matched = [a for a in matched if not _is_analyst_piece(a["text"])]

        score = _sentiment(matched)
        result[ticker] = {
            "date":       ist_today().isoformat(),
            "count":      len(matched),
            "score":      score,
            "headlines":  [a["title"] for a in matched[:6]],
        }
    return result


def _fetch_google_news(ticker: str, frags: List[str]) -> List[dict]:
    """Per-stock Google News RSS. Returns [] on any failure (never raises)."""
    name = frags[0].replace(" ", "+") if frags else ticker.replace(".NS", "")
    url = _GOOGLE_NEWS.format(q=name)
    try:
        items = _fetch_rss(url)
    except Exception:
        return []
    # Keep only items that actually mention the company (Google search can be loose)
    return [a for a in items if any(f in a["text"] for f in frags)]


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
    from agent.io_safe import load_json_dict
    return load_json_dict(NEWS_FILE)


def save_news(data: Dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    existing = load_news()
    today = ist_today().isoformat()
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
