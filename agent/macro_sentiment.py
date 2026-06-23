"""
Macro Sentiment Watcher — global + India market mood, before/around the open.

Purpose: keep the tool aware of the BIG PICTURE that moves Nifty as a whole —
overnight US close, Asian markets, crude, USD/INR, and global/India macro
headlines — so it can size and bias trades wisely instead of reacting only to
single-stock signals.

Free + quota-friendly:
  - Pulls a set of global + India macro RSS feeds (no keys).
  - Scores sentiment with the existing keyword lexicon (cheap, deterministic).
  - Reads overnight global index/crude/INR moves from the data layer.
  - Optionally asks the LLM coach (Gemini) ONCE per run for a short mood summary —
    only when there's enough fresh news to be worth it (keeps API usage tiny).

Output: brain/macro_sentiment.json
  {
    "date", "session",
    "global_score", "india_score", "overall_score",   # -1..+1
    "mood",                                            # risk_on / neutral / risk_off
    "drivers": [...headlines...],
    "global_cues": {sgx_nifty, dow, nasdaq, crude, usdinr, ...},
    "summary": "one-paragraph plain-English read (LLM or rule-based)",
    "playbook": [...event-reaction notes that matched today...],
  }

Consumed by market_health.assess_market() so every phase/session sees it.
"""

import json
import os
import re
import time
from datetime import date, datetime
from typing import Dict, List
from urllib.request import urlopen, Request

from agent.config import BRAIN_DIR, POSITIVE_WORDS, NEGATIVE_WORDS
from agent.trading_calendar import ist_today

MACRO_FILE = "brain/macro_sentiment.json"

# Global + India macro feeds (free RSS, no auth). Kept small & reliable.
MACRO_FEEDS = [
    # Global / world markets
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",      # MarketWatch top
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",           # CNBC top news
    "https://www.cnbc.com/id/15839135/device/rss/rss.html",            # CNBC world markets
    # India macro / economy
    "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
    "https://www.business-standard.com/rss/economy-policy-103.rss",
]

# Words that signal global RISK-OFF / RISK-ON specifically at the macro level
# (layered on top of the generic POSITIVE/NEGATIVE lexicon).
MACRO_RISK_OFF = [
    "war", "conflict", "tariff", "sanction", "recession", "inflation", "rate hike",
    "hawkish", "crash", "selloff", "sell-off", "plunge", "crisis", "default",
    "downgrade", "geopolitical", "oil spike", "yield surge", "fed tightening",
    "slump", "fear", "volatility spike", "contagion", "meltdown",
]
MACRO_RISK_ON = [
    "rally", "rate cut", "dovish", "stimulus", "easing", "record high", "soft landing",
    "cooling inflation", "strong jobs", "rebound", "recovery", "optimism", "risk-on",
    "surge", "gains", "upbeat", "boost",
]


# ── Event-reaction playbook ──────────────────────────────────────────────────
# Fixed prior knowledge of how Indian markets have TYPICALLY reacted to recurring
# event types. This is general, well-established market behaviour — not fabricated
# per-stock data. Each entry: keywords that detect the event, the usual direction,
# and a short note. Used to add context, never as a hard rule.
EVENT_PLAYBOOK = [
    {"name": "US Fed rate cut / dovish",
     "keywords": ["rate cut", "dovish", "fed cuts", "easing cycle"],
     "bias": "risk_on",
     "note": "Dovish Fed usually lifts EM equities incl. Nifty; FIIs tend to buy."},
    {"name": "US Fed rate hike / hawkish",
     "keywords": ["rate hike", "hawkish", "fed raises", "tightening"],
     "bias": "risk_off",
     "note": "Hawkish Fed pressures Nifty via FII outflows and stronger USD."},
    {"name": "Crude oil spike",
     "keywords": ["oil spike", "crude surges", "oil jumps", "brent surges"],
     "bias": "risk_off",
     "note": "India imports most of its oil — crude spikes hurt rupee, inflation, OMCs."},
    {"name": "Crude oil crash",
     "keywords": ["oil plunges", "crude falls", "oil slumps", "brent falls"],
     "bias": "risk_on",
     "note": "Falling crude eases India's import bill & inflation — positive for Nifty."},
    {"name": "Geopolitical conflict / war",
     "keywords": ["war", "conflict", "military", "attack", "invasion", "geopolitical"],
     "bias": "risk_off",
     "note": "Geopolitical shocks trigger global risk-off; gold up, equities down initially."},
    {"name": "Strong US markets overnight",
     "keywords": ["wall street rallies", "dow jumps", "nasdaq surges", "s&p record"],
     "bias": "risk_on",
     "note": "Strong US close usually gives Nifty a positive opening cue via SGX/GIFT."},
    {"name": "US markets sell off overnight",
     "keywords": ["wall street falls", "dow plunges", "nasdaq tumbles", "us stocks slump"],
     "bias": "risk_off",
     "note": "Weak US close typically drags Nifty lower at open — expect gap-down risk."},
    {"name": "Tariffs / trade war",
     "keywords": ["tariff", "trade war", "import duty", "trade tension"],
     "bias": "risk_off",
     "note": "Trade-war headlines spook global markets; IT & export names react most."},
]


# ── Public API ───────────────────────────────────────────────────────────────

def assess_macro_sentiment(session: str = "preopen", use_llm: bool = True) -> dict:
    """
    Build the macro sentiment snapshot. Called by market_health each session.
    Heaviest work (feeds + optional one LLM summary) but still light.
    """
    articles = _fetch_macro_feeds()
    global_cues = _fetch_global_cues()

    g_score = _score_articles(articles, scope="global")
    i_score = _score_articles(articles, scope="india")
    # Overnight cues nudge the global score (real price action > headlines)
    cue_adj = _cue_adjustment(global_cues)
    global_score = max(-1.0, min(1.0, g_score + cue_adj))
    india_score  = max(-1.0, min(1.0, i_score))
    # Overall: global drives the open, India macro modulates it
    overall = round(0.55 * global_score + 0.45 * india_score, 3)

    mood = ("risk_off" if overall <= -0.20 else
            "risk_on"  if overall >=  0.20 else "neutral")

    playbook = _match_playbook(articles)
    drivers  = [a["title"] for a in articles[:8]]

    summary = ""
    if use_llm and articles:
        summary = _llm_summary(articles, global_cues, overall, mood, playbook)
    if not summary:
        summary = _rule_summary(overall, mood, global_cues, playbook)

    snap = {
        "date":          ist_today().isoformat(),
        "session":       session,
        "generated_at":  datetime.utcnow().isoformat(),
        "global_score":  round(global_score, 3),
        "india_score":   round(india_score, 3),
        "overall_score": overall,
        "mood":          mood,
        "drivers":       drivers,
        "global_cues":   global_cues,
        "summary":       summary,
        "playbook":      [p["note"] for p in playbook],
        "playbook_bias": _playbook_net_bias(playbook),
        "article_count": len(articles),
    }
    _save(snap)
    print(f"[macro] {session} | mood={mood} overall={overall:+.2f} "
          f"(global={global_score:+.2f} india={india_score:+.2f}) | {len(articles)} articles")
    for p in playbook:
        print(f"  [playbook] {p['name']} ({p['bias']}): {p['note']}")
    return snap


def load_macro_sentiment() -> dict:
    if os.path.exists(MACRO_FILE):
        try:
            with open(MACRO_FILE) as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


# ── Feeds + scoring ──────────────────────────────────────────────────────────

def _fetch_macro_feeds() -> List[dict]:
    out = []
    for url in MACRO_FEEDS:
        try:
            req = Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; NSEMacroBot/1.0)",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            })
            with urlopen(req, timeout=12) as r:
                raw = r.read()
        except Exception as e:
            print(f"[macro] feed {url[:40]}...: {e}")
            continue
        out.extend(_parse_rss(raw))
        time.sleep(0.5)
    # De-dup by title
    seen, uniq = set(), []
    for a in out:
        key = a["title"][:80].lower()
        if key and key not in seen:
            seen.add(key); uniq.append(a)
    return uniq[:60]


def _parse_rss(raw: bytes) -> List[dict]:
    import xml.etree.ElementTree as ET
    try:
        raw = raw.lstrip(b"\xef\xbb\xbf")
        text = raw.decode("utf-8", errors="replace")
        text = "".join(ch for ch in text if ch >= " " or ch in "\n\r\t")
        root = ET.fromstring(text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        out = []
        for item in items[:30]:
            title = (item.findtext("title") or
                     item.findtext("atom:title", namespaces=ns) or "").strip()
            desc = (item.findtext("description") or
                    item.findtext("atom:summary", namespaces=ns) or "")
            desc = re.sub(r"<[^>]+>", " ", desc)
            if title:
                out.append({"title": title, "text": (title + " " + desc).lower()})
        return out
    except Exception:
        return []


def _score_articles(articles: List[dict], scope: str) -> float:
    """Keyword sentiment over macro articles. scope filters India-specific terms."""
    if not articles:
        return 0.0
    india_terms = ("india", "nifty", "sensex", "rbi", "rupee", "fii", "dii", "sebi", "modi")
    total, n = 0.0, 0
    for a in articles:
        t = a["text"]
        if scope == "india" and not any(w in t for w in india_terms):
            continue
        if scope == "global" and any(w in t for w in india_terms):
            # still allow — global stories often mention India; just don't exclude
            pass
        pos = sum(1 for w in POSITIVE_WORDS if w in t) + sum(2 for w in MACRO_RISK_ON if w in t)
        neg = sum(1 for w in NEGATIVE_WORDS if w in t) + sum(2 for w in MACRO_RISK_OFF if w in t)
        denom = pos + neg
        if denom == 0:
            continue
        total += (pos - neg) / denom
        n += 1
    return round(total / n, 3) if n else 0.0


def _fetch_global_cues() -> dict:
    """
    Overnight/early global price cues that pre-empt the Nifty open. Uses the
    existing daily downloader on global tickers. All free via Yahoo/Stooq.
    """
    from agent.data_fetcher import _download_daily
    from datetime import timedelta
    cues = {}
    symbols = {
        "dow":     "^DJI",
        "nasdaq":  "^IXIC",
        "sp500":   "^GSPC",
        "nikkei":  "^N225",
        "crude":   "CL=F",
        "usdinr":  "INR=X",
        "gold":    "GC=F",
    }
    start = (ist_today() - timedelta(days=7)).isoformat()
    end   = ist_today().isoformat()
    for name, sym in symbols.items():
        try:
            df = _download_daily(sym, start=start, end=end)
            if df is None or df.empty or len(df) < 2:
                continue
            import pandas as pd
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            c = df["Close"].astype(float)
            chg = (c.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100
            cues[name] = {"last": round(float(c.iloc[-1]), 2), "chg_pct": round(float(chg), 2)}
            time.sleep(0.2)
        except Exception:
            continue
    return cues


def _cue_adjustment(cues: dict) -> float:
    """Translate overnight price cues into a small sentiment nudge (-0.3..+0.3)."""
    adj = 0.0
    def _chg(name):   # safe accessor — never KeyErrors on a partial cue dict
        return (cues.get(name) or {}).get("chg_pct", 0.0)
    # US markets are the strongest pre-open cue for Nifty
    for k, w in (("dow", 0.04), ("nasdaq", 0.04), ("sp500", 0.04), ("nikkei", 0.02)):
        adj += max(-1.5, min(1.5, _chg(k))) / 1.5 * w
    # Crude UP is bad for India; USD/INR UP (rupee weak) is bad
    adj -= max(-2.0, min(2.0, _chg("crude")))  / 2.0 * 0.05
    adj -= max(-1.0, min(1.0, _chg("usdinr"))) / 1.0 * 0.05
    return round(max(-0.30, min(0.30, adj)), 3)


def _match_playbook(articles: List[dict]) -> List[dict]:
    """Find which known event types are present in today's macro news."""
    blob = " ".join(a["text"] for a in articles)
    matched = []
    for ev in EVENT_PLAYBOOK:
        if any(kw in blob for kw in ev["keywords"]):
            matched.append(ev)
    return matched


def _playbook_net_bias(playbook: List[dict]) -> str:
    if not playbook:
        return "neutral"
    on  = sum(1 for p in playbook if p["bias"] == "risk_on")
    off = sum(1 for p in playbook if p["bias"] == "risk_off")
    if off > on:  return "risk_off"
    if on > off:  return "risk_on"
    return "mixed"


# ── Summaries ────────────────────────────────────────────────────────────────

def _rule_summary(overall: float, mood: str, cues: dict, playbook: List[dict]) -> str:
    bits = [f"Overall macro mood is {mood.replace('_', '-')} ({overall:+.2f})."]
    if "dow" in cues:
        bits.append(f"US Dow {cues['dow']['chg_pct']:+.1f}% overnight.")
    if "crude" in cues:
        bits.append(f"Crude {cues['crude']['chg_pct']:+.1f}%.")
    if "usdinr" in cues:
        bits.append(f"USD/INR {cues['usdinr']['chg_pct']:+.1f}%.")
    if playbook:
        bits.append("Notable: " + "; ".join(p["name"] for p in playbook) + ".")
    return " ".join(bits)


def _llm_summary(articles, cues, overall, mood, playbook) -> str:
    """One short Gemini call summarising the macro backdrop. Quota-frugal:
    a single request per session, only when there's news to read."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return ""
    heads = "\n".join(f"- {a['title']}" for a in articles[:14])
    cue_txt = ", ".join(f"{k} {v['chg_pct']:+.1f}%" for k, v in cues.items())
    prompt = f"""You are a markets desk analyst briefing an automated NSE (India) trading tool
before the session. Based ONLY on the inputs, give a 3-4 sentence read of the
macro backdrop for Indian equities today.

Overnight global cues: {cue_txt or 'n/a'}
Computed mood: {mood} ({overall:+.2f})
Top headlines:
{heads}

Cover: global risk tone, the likely cue for Nifty's open, and one risk to watch.
Be concise and factual. No disclaimers, no bullet points — just the briefing."""
    try:
        from agent.llm_coach import _call_gemini
        out = _call_gemini(prompt, api_key, use_search=False)
        return (out or "").strip()[:600]
    except Exception as e:
        print(f"[macro] LLM summary skipped: {e}")
        return ""


def _save(snap: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(MACRO_FILE, "w") as f:
        json.dump(snap, f, indent=2)
