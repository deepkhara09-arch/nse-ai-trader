"""
Context Coach — FREE, deterministic contextual learning from closed trades.

Per-pattern reliability answers "does this pattern work?" — but not "does it work
IN THIS REGIME?". A breakout that wins in a trending market can bleed in a choppy
one. This coach mines closed trades for CONTEXT-CONDITIONED lessons — combinations
of (setup, market regime, VIX band, mood) that have a clearly good or bad record —
and writes them as the same `lessons` structure the LLM coach uses, so they feed
back into scoring via brain.get_lessons_for(). No API key, always runs.

Lessons are only emitted once a context has enough samples (MIN_SAMPLES) and a
clearly skewed win-rate, so we never "learn" from noise. Recency isn't decayed
here (small samples), but stale contexts naturally get overwritten as new trades
arrive and old closed_trades roll off the bounded store.

Output merges into brain/coach_memory.json under "lessons":
  { "<ticker>|<pattern>": [ {what_to_watch, what_happened, context, samples,
                             win_rate, source:"context_coach"} ] }
"""

from typing import Dict, List
from collections import defaultdict

MIN_SAMPLES = 4          # need at least this many trades in a context to trust it
STRONG_WR   = 0.65       # win-rate at/above this in a context = a "works here" lesson
WEAK_WR     = 0.35       # win-rate at/below this = a "fails here" caution


def _vix_band(vix) -> str:
    try:
        v = float(vix)
    except Exception:
        return "vix_unknown"
    return "vix_low" if v < 13 else ("vix_high" if v >= 20 else "vix_normal")


def run_context_coach(closed_trades: List[dict], memory: dict) -> int:
    """Mine closed trades for context-conditioned lessons; merge into memory.
    Returns the number of lessons written. Pure/deterministic, never raises."""
    try:
        if not closed_trades:
            return 0
        # Group outcomes by (ticker, pattern, context). Context uses the market
        # snapshot captured AT ENTRY (paper_trader stores entry_market).
        buckets: Dict[tuple, List[bool]] = defaultdict(list)
        for t in closed_trades:
            won = bool(t.get("won", t.get("pnl", 0) > 0))
            em  = t.get("entry_market", {}) or {}
            ctx_parts = []
            nt = em.get("nifty_trend")
            if nt:
                ctx_parts.append(f"nifty_{nt}")
            ctx_parts.append(_vix_band(em.get("vix")))
            mood = em.get("mood") or em.get("regime")
            if mood:
                ctx_parts.append(str(mood))
            context = " · ".join(ctx_parts) if ctx_parts else "any_regime"
            ticker  = t.get("ticker", "?")
            for p in (t.get("patterns") or ["general"]):
                buckets[(ticker, p, context)].append(won)

        lessons_by_key: Dict[str, list] = defaultdict(list)
        written = 0
        for (ticker, pattern, context), outcomes in buckets.items():
            n = len(outcomes)
            if n < MIN_SAMPLES:
                continue
            wr = sum(outcomes) / n
            if wr >= STRONG_WR:
                what = (f"This setup has WORKED in this regime "
                        f"({wr*100:.0f}% over {n} trades) — favourable context.")
                watch = f"{pattern.replace('_',' ')} tends to work when {context}"
            elif wr <= WEAK_WR:
                what = (f"This setup has FAILED in this regime "
                        f"({wr*100:.0f}% over {n} trades) — be cautious / demand more.")
                watch = f"{pattern.replace('_',' ')} tends to fail when {context}"
            else:
                continue
            # Key format MUST match llm_coach.get_lessons_for lookups: ticker::setup.
            key = f"{ticker}::{pattern}"
            lessons_by_key[key].append({
                "what_to_watch": watch,
                "what_happened": what,
                "context":       context,
                "samples":       n,
                "win_rate":      round(wr, 2),
                "source":        "context_coach",
            })
            written += 1

        # Replace prior context-coach lessons (keep any LLM lessons untouched).
        mem_lessons = memory.setdefault("lessons", {})
        for key in list(mem_lessons.keys()):
            mem_lessons[key] = [l for l in mem_lessons[key]
                                if l.get("source") != "context_coach"]
            if not mem_lessons[key]:
                del mem_lessons[key]
        for key, ls in lessons_by_key.items():
            mem_lessons.setdefault(key, []).extend(ls)

        # ── Also feed the dashboard's flat feed ────────────────────────────────
        # The Coach panel reads `recent_lessons` (written by the Gemini path).
        # Without this the free coach's lessons would be learned but INVISIBLE —
        # the panel would say "Coach hasn't run yet" forever with no API key.
        from agent.trading_calendar import ist_today
        today = ist_today().isoformat()
        recent = [l for l in memory.get("recent_lessons", [])
                  if l.get("source") != "context_coach"]        # drop stale ones
        for key, ls in lessons_by_key.items():
            tk, _, pat = key.partition("::")
            for l in ls:
                recent.append({**l, "date": today,
                               "ticker": tk.replace(".NS", ""), "setup": pat})
        memory["recent_lessons"] = sorted(
            recent, key=lambda x: x.get("date", ""), reverse=True)[:30]

        if written:
            print(f"[context-coach] {written} context lesson(s) from {len(closed_trades)} trades (free, no LLM)")
        return written
    except Exception as e:
        print(f"[context-coach] non-fatal: {e}")
        return 0
