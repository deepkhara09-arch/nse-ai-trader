"""
LLM Coach — Gemini Flash 2.0 as a teacher for the trading tool.

The coach is called once per day (preclose) and does three things:
1. Reviews every closed trade from today and writes a structured lesson.
2. Answers technical questions the tool has queued up during the session.
3. Suggests structural improvements to the tool itself (shown on dashboard).

The coach NEVER touches scores, positions, or trade decisions.
All output is stored as read-only lessons in brain/coach_memory.json.
The tool reads these lessons before analysis and uses them as context.

If Gemini is unavailable, everything fails gracefully — the tool runs as normal.
"""

import json
import os
import time
from datetime import date, datetime
from typing import Dict, List, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

from agent.config import BRAIN_DIR
from agent.trading_calendar import ist_today

COACH_MEMORY_FILE  = "brain/coach_memory.json"
COACH_QUESTIONS_FILE = "brain/coach_questions.json"   # questions queued during the day

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)

# How many lessons to keep per (stock, setup) key before rotating oldest
MAX_LESSONS_PER_KEY = 5
# Max lessons in a single Gemini call to avoid huge prompts
MAX_TRADES_PER_CALL = 6


# ── Public API ─────────────────────────────────────────────────────────────────

def run_coach(closed_trades: List[dict], patterns: dict, market_health: dict) -> dict:
    """
    Main entry point. Called at preclose.
    Returns the coach memory dict (also saved to disk).
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("[coach] No GEMINI_API_KEY — skipping coach session")
        return load_coach_memory()

    memory   = load_coach_memory()
    today    = ist_today().isoformat()
    lessons  = []

    # ── 1. Review today's closed trades ──────────────────────────────────────
    # Trade records use "close_date" (set in paper_trader._check_exits).
    today_trades = [t for t in closed_trades if t.get("close_date") == today]
    if today_trades:
        print(f"[coach] Reviewing {len(today_trades)} trade(s) closed today")
        for batch in _batch(today_trades, MAX_TRADES_PER_CALL):
            batch_lessons = _review_trades(batch, market_health, api_key)
            lessons.extend(batch_lessons)
            time.sleep(1)   # be polite to the free tier

    # ── 2. Answer queued technical questions ──────────────────────────────────
    questions = _load_questions()
    if questions:
        print(f"[coach] Answering {len(questions)} queued question(s)")
        qa_lessons = _answer_questions(questions, patterns, market_health, api_key)
        lessons.extend(qa_lessons)
        _clear_questions()

    # ── 3. Structural suggestions (once per week — every 5 preclose sessions) ─
    session_count = memory.get("session_count", 0) + 1
    memory["session_count"] = session_count
    if session_count % 5 == 0 and closed_trades:
        print("[coach] Generating weekly structural suggestions")
        suggestions = _structural_suggestions(closed_trades, patterns, market_health, api_key)
        memory["structural_suggestions"] = suggestions
        memory["suggestions_date"] = today

    # ── Store lessons in memory ───────────────────────────────────────────────
    if lessons:
        _store_lessons(memory, lessons, today)
        print(f"[coach] Stored {len(lessons)} lesson(s) in coach memory")

    memory["last_run"] = today
    save_coach_memory(memory)
    return memory


def queue_question(question: str, context: dict) -> None:
    """
    Queue a technical question for the coach to answer at end of day.
    Called by brain.py or paper_trader.py when the tool encounters something
    it doesn't have data about or wants explained.
    """
    questions = _load_questions()
    questions.append({
        "question": question,
        "context":  context,
        "queued_at": datetime.utcnow().isoformat(),
    })
    # Keep at most 10 queued questions (oldest dropped)
    questions = questions[-10:]
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(COACH_QUESTIONS_FILE, "w") as f:
        json.dump(questions, f, indent=2)


def get_lessons_for(ticker: str, setup_type: str, memory: dict = None) -> List[dict]:
    """
    Retrieve coach lessons relevant to a specific ticker + setup combination.
    Called by brain.py before scoring to inject learned context.
    """
    if memory is None:
        memory = load_coach_memory()
    lessons = memory.get("lessons", {})

    # Check exact key first, then setup-only key, then ticker-only
    exact  = lessons.get(f"{ticker}::{setup_type}", [])
    setup  = lessons.get(f"ANY::{setup_type}", [])
    ticker_any = lessons.get(f"{ticker}::ANY", [])

    combined = exact + setup + ticker_any
    # Sort by date desc, return most recent 3
    combined.sort(key=lambda x: x.get("date", ""), reverse=True)
    return combined[:3]


def get_structural_suggestions(memory: dict = None) -> List[str]:
    """Return the latest structural suggestions for the dashboard."""
    if memory is None:
        memory = load_coach_memory()
    return memory.get("structural_suggestions", [])


def load_coach_memory() -> dict:
    if os.path.exists(COACH_MEMORY_FILE):
        with open(COACH_MEMORY_FILE) as f:
            try:
                return json.load(f)
            except Exception:
                pass
    return {
        "lessons":                {},
        "structural_suggestions": [],
        "session_count":          0,
        "last_run":               None,
        "suggestions_date":       None,
    }


def save_coach_memory(memory: dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(COACH_MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)


# ── Trade review ───────────────────────────────────────────────────────────────

def _review_trades(trades: List[dict], market_health: dict, api_key: str) -> List[dict]:
    """Ask Gemini to explain what happened in a batch of trades."""
    mood    = market_health.get("market_mood", "neutral")
    vix     = market_health.get("vix", {}).get("value", 15)
    nifty_t = market_health.get("nifty", {}).get("trend_5d", "sideways")

    trade_text = ""
    for i, t in enumerate(trades, 1):
        ticker   = t.get("ticker", "?").replace(".NS", "")
        outcome  = "WIN" if t.get("pnl", 0) > 0 else "LOSS"
        pnl_pct  = t.get("pnl_pct", 0)
        entry    = t.get("entry", 0)
        exit_p   = t.get("exit_price", 0)
        reason   = t.get("exit_reason", "unknown")
        patterns = t.get("patterns", [])
        style    = t.get("style", "intraday")
        session  = t.get("open_session", t.get("close_session", "morning"))

        em       = t.get("entry_market", {})
        entry_ctx = ""
        if em:
            entry_ctx = (
                f"  Market when opened: Nifty {em.get('nifty_trend','?')}, "
                f"VIX {em.get('vix','?')}, mood {em.get('mood','?')}, "
                f"regime {em.get('regime','?')}\n"
            )
        open_days = t.get("open_days", "?")
        trade_text += (
            f"\nTrade {i}: {ticker} | {outcome} {pnl_pct:+.2f}%\n"
            f"  Style: {style} | Session: {session} | Held: {open_days} day(s)\n"
            f"  Entry: ₹{entry:.2f} → Exit: ₹{exit_p:.2f} | Reason: {reason}\n"
            f"{entry_ctx}"
            f"  Signals at entry: {', '.join(patterns[:6]) if patterns else 'none recorded'}\n"
        )

    prompt = f"""You are a senior Indian stock market analyst and trading coach.
An automated NSE paper trading tool made these trades today on Nifty-100 stocks.
Market context: Nifty trend={nifty_t}, VIX={vix:.1f}, mood={mood}.

{trade_text}

For each trade, provide a SHORT structured lesson (3-4 lines max per trade):
1. What likely caused the outcome (be specific to Indian market dynamics)
2. What signal combination worked or failed and why
3. One concrete thing to watch for next time this setup appears
4. If market conditions (VIX, Nifty trend) influenced the outcome, say so

Format your response as JSON array like this:
[
  {{
    "trade_ref": "TICKER outcome",
    "setup_key": "signal_combination_short_name",
    "what_happened": "...",
    "what_to_watch": "...",
    "confidence": "high|medium|low",
    "applies_to": "ticker|setup|both"
  }}
]
Only return valid JSON. No markdown, no explanation outside the JSON."""

    response = _call_gemini(prompt, api_key, use_search=True)
    if not response:
        return []

    try:
        lessons_raw = json.loads(response)
        if not isinstance(lessons_raw, list):
            return []
        lessons = []
        for raw in lessons_raw:
            if not isinstance(raw, dict):
                continue
            # Match back to original trade to get ticker + setup
            ref    = raw.get("trade_ref", "")
            ticker = next((t.get("ticker", "") for t in trades
                           if t.get("ticker", "").replace(".NS", "") in ref), "")
            lessons.append({
                "date":          ist_today().isoformat(),
                "ticker":        ticker,
                "setup_key":     raw.get("setup_key", "unknown"),
                "what_happened": raw.get("what_happened", ""),
                "what_to_watch": raw.get("what_to_watch", ""),
                "confidence":    raw.get("confidence", "medium"),
                "applies_to":    raw.get("applies_to", "both"),
                "source":        "trade_review",
            })
        return lessons
    except (json.JSONDecodeError, Exception) as e:
        print(f"[coach] Trade review parse error: {e}")
        return []


# ── Technical Q&A ──────────────────────────────────────────────────────────────

def _answer_questions(questions: List[dict], patterns: dict, market_health: dict, api_key: str) -> List[dict]:
    """Ask Gemini to answer technical questions the tool has queued."""
    lessons = []
    for q in questions:
        question = q.get("question", "")
        context  = q.get("context", {})
        if not question:
            continue

        ctx_text = ""
        if context:
            ctx_text = "Context: " + ", ".join(f"{k}={v}" for k, v in context.items() if v) + "\n"

        prompt = f"""You are a senior Indian stock market analyst.
An automated NSE trading tool is asking you a technical question to help it learn.

{ctx_text}Question: {question}

Answer concisely (4-6 lines max). Focus on:
- What this means in the context of Indian markets (NSE/Nifty stocks)
- What the tool should look for next time
- Any caveats specific to Indian market microstructure (FII flows, F&O expiry,
  circuit breakers, NSE circuit limits)

Return as JSON:
{{
  "answer": "...",
  "key_takeaway": "one sentence the tool should remember",
  "setup_key": "short_label_for_this_lesson",
  "applies_to_ticker": "TICKER.NS or empty string if general"
}}
Only return valid JSON."""

        response = _call_gemini(prompt, api_key, use_search=True)
        if not response:
            continue

        try:
            ans = json.loads(response)
            lessons.append({
                "date":          ist_today().isoformat(),
                "ticker":        ans.get("applies_to_ticker", ""),
                "setup_key":     ans.get("setup_key", "qa_lesson"),
                "what_happened": ans.get("answer", ""),
                "what_to_watch": ans.get("key_takeaway", ""),
                "confidence":    "medium",
                "applies_to":    "ticker" if ans.get("applies_to_ticker") else "setup",
                "source":        "qa",
                "original_question": question,
            })
        except Exception as e:
            print(f"[coach] Q&A parse error: {e}")
        time.sleep(0.8)

    return lessons


# ── Structural suggestions ─────────────────────────────────────────────────────

def _structural_suggestions(closed_trades: List[dict], patterns: dict, market_health: dict, api_key: str) -> List[str]:
    """
    Ask Gemini to look at overall performance and suggest structural improvements
    to the tool. These are shown on the dashboard for the user to review.
    """
    total  = len(closed_trades)
    wins   = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
    wr     = wins / total * 100 if total else 0
    avg_pnl = sum(t.get("pnl_pct", 0) for t in closed_trades) / max(total, 1)

    # Summarise pattern performance
    pat_summary = {}
    for t in closed_trades:
        for p in t.get("patterns", []):
            if p not in pat_summary:
                pat_summary[p] = {"wins": 0, "total": 0}
            pat_summary[p]["total"] += 1
            if t.get("pnl", 0) > 0:
                pat_summary[p]["wins"] += 1

    pat_text = ""
    for p, d in sorted(pat_summary.items(), key=lambda x: -x[1]["total"])[:10]:
        wr_p = d["wins"] / d["total"] * 100 if d["total"] else 0
        pat_text += f"  {p}: {d['total']} trades, {wr_p:.0f}% win rate\n"

    prompt = f"""You are a quant analyst reviewing an automated NSE paper trading system.

Overall stats ({total} closed paper trades):
- Win rate: {wr:.1f}%
- Avg PnL per trade: {avg_pnl:+.2f}%
- Market today: mood={market_health.get('market_mood','?')}, VIX={market_health.get('vix',{}).get('value',15):.1f}

Signal performance:
{pat_text if pat_text else "  No pattern data yet"}

The system currently uses: Supertrend, Pivot Points, Ichimoku, MACD divergence,
StochRSI, ADX, VWAP, PVT, gap analysis, delivery %, FII/DII proxy via sector ETFs,
VIX percentile, Bank Nifty divergence, intraday 5-min candle sequence.

Based on the performance data and your knowledge of Indian market dynamics,
suggest 3-5 concrete structural improvements the system could make.
Focus on: signals it might be missing, Indian market-specific factors not yet used,
risk management improvements, data sources it could use.

Return as JSON array of strings, each a concrete suggestion:
["suggestion 1", "suggestion 2", ...]
Only return valid JSON. Be specific, not generic."""

    response = _call_gemini(prompt, api_key, use_search=True)
    if not response:
        return []

    try:
        suggestions = json.loads(response)
        if isinstance(suggestions, list):
            return [str(s) for s in suggestions[:5]]
    except Exception as e:
        print(f"[coach] Suggestions parse error: {e}")
    return []


# ── Gemini API call ────────────────────────────────────────────────────────────

def _call_gemini(prompt: str, api_key: str, use_search: bool = True) -> Optional[str]:
    """
    Call Gemini Flash 2.0. Returns the text content or None on failure.
    Uses Google Search grounding so Gemini can pull current market info.
    """
    url     = f"{GEMINI_API_URL}?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature":     0.3,    # lower = more factual, less creative
            "maxOutputTokens": 1024,
            "topP":            0.8,
        },
    }
    # Search grounding — lets Gemini look things up on the internet
    if use_search:
        payload["tools"] = [{"google_search": {}}]

    body = json.dumps(payload).encode("utf-8")
    req  = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        candidates = data.get("candidates", [])
        if not candidates:
            print("[coach] Gemini returned no candidates")
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        text  = "".join(p.get("text", "") for p in parts).strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return text.strip()
    except URLError as e:
        print(f"[coach] Gemini network error: {e}")
    except Exception as e:
        print(f"[coach] Gemini call error: {e}")
    return None


# ── Lesson storage ─────────────────────────────────────────────────────────────

def _store_lessons(memory: dict, lessons: List[dict], today: str) -> None:
    """
    Store lessons into memory dict, keyed by ticker::setup_key.
    Keeps MAX_LESSONS_PER_KEY most recent per key.
    Also maintains a flat recent_lessons list for the dashboard.
    """
    stored = memory.setdefault("lessons", {})
    recent = memory.setdefault("recent_lessons", [])

    for lesson in lessons:
        ticker    = lesson.get("ticker", "")
        setup_key = lesson.get("setup_key", "general")
        applies   = lesson.get("applies_to", "both")

        # Determine which keys to store under
        keys = []
        if applies in ("both", "ticker") and ticker:
            keys.append(f"{ticker}::{setup_key}")
        if applies in ("both", "setup"):
            keys.append(f"ANY::{setup_key}")
        if not keys:
            keys.append(f"ANY::{setup_key}")

        for key in keys:
            bucket = stored.setdefault(key, [])
            bucket.append(lesson)
            # Keep only most recent MAX_LESSONS_PER_KEY
            stored[key] = sorted(bucket, key=lambda x: x.get("date", ""), reverse=True)[:MAX_LESSONS_PER_KEY]

        recent.append(lesson)

    # Keep only last 30 lessons in the flat list for dashboard display
    memory["recent_lessons"] = sorted(recent, key=lambda x: x.get("date", ""), reverse=True)[:30]


# ── Question queue helpers ─────────────────────────────────────────────────────

def _load_questions() -> List[dict]:
    if os.path.exists(COACH_QUESTIONS_FILE):
        with open(COACH_QUESTIONS_FILE) as f:
            try:
                return json.load(f)
            except Exception:
                pass
    return []


def _clear_questions() -> None:
    if os.path.exists(COACH_QUESTIONS_FILE):
        os.remove(COACH_QUESTIONS_FILE)


def _batch(items: list, size: int) -> List[list]:
    return [items[i:i+size] for i in range(0, len(items), size)]
