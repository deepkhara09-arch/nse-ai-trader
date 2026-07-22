"""
Ranking Engine — scores and ranks all focus stocks every session.

Computes per-stock:
  • success_probability  — Bayesian blend of paper win rate + pattern reliability
  • profit_probability   — expected edge = success_prob × avg_R:R
  • composite_rank_score — weighted blend of all signals (used for ordering)
  • rank_delta           — how many positions changed since last session

Also handles:
  • Promotion  — strong stocks from watchlist promoted into focus
  • Demotion   — persistent underperformers swapped out
  • Rank history — stored in brain/rank_history.json for dashboard trend display
"""

import json
import os
from datetime import date
from typing import Dict, List, Tuple

from agent.config import BRAIN_DIR, FOCUS_STOCK_COUNT, MAX_STOCK_PRICE
from agent.trading_calendar import ist_today

RANK_HISTORY_FILE  = "brain/rank_history.json"
WATCHLIST_FILE     = "brain/watchlist_signals.json"   # tracks near-miss strong signals


# ── Public API ─────────────────────────────────────────────────────────────────

def rank_focus_stocks(
    focus: List[str],
    stock_data: Dict,
    patterns: Dict,
    news_data: Dict,
    fundamentals: Dict,
    book: dict,
    market_health: dict,
    record_history: bool = True,
) -> List[dict]:
    """
    Returns focus stocks sorted best→worst with probability scores.
    Saves rank history for trend display (unless record_history=False — used when
    scoring background-competition challengers, which must NOT pollute the focus
    stocks' rank history or the demotion logic that reads it).
    """
    from agent.fundamentals_fetcher import score_fundamentals
    from agent.brain import get_reliable_patterns_list

    ranked = []
    for ticker in focus:
        entry = stock_data.get(ticker, {})
        if not entry or "latest" not in entry:
            continue
        d       = entry["latest"]
        close   = d.get("close", 0)
        fund    = fundamentals.get(ticker, {})
        news    = news_data.get(ticker, {}).get("latest", {})
        pat_db  = patterns.get(ticker, {})

        # ── Paper trade stats for this ticker ────────────────────────────────
        closed  = [t for t in book.get("closed_trades", []) if t["ticker"] == ticker]
        n_trades = len(closed)
        if n_trades > 0:
            wins     = [t for t in closed if t.get("won")]
            paper_wr = len(wins) / n_trades
            # Compute avg R:R only from trades with a meaningful price move
            valid_rrs = []
            for t in wins:
                price_delta = abs(t.get("entry", 0) - t.get("exit_price", t.get("entry", 0)))
                if price_delta > 0.5:   # at least 50 paise movement to count
                    rr = abs(t.get("pnl", 0)) / (price_delta * t.get("qty", 1))
                    valid_rrs.append(min(rr, 10.0))   # cap individual R:R at 10x to prevent outlier inflation
            avg_rr = sum(valid_rrs) / len(valid_rrs) if valid_rrs else 1.5
        else:
            paper_wr = 0.5   # prior (no data yet)
            avg_rr   = 2.0

        # ── Pattern reliability ───────────────────────────────────────────────
        reliable = get_reliable_patterns_list(ticker, patterns, min_rel=0.55)
        pat_score_raw = min(1.0, len(reliable) / 4)

        # ── Fundamental score (0–1) ───────────────────────────────────────────
        fund_score_raw = score_fundamentals(fund) / 100.0

        # ── Momentum score (0–1) ──────────────────────────────────────────────
        trend_map = {"strong_up": 1.0, "up": 0.75, "sideways": 0.4,
                     "down": 0.2, "strong_down": 0.0}
        momentum = trend_map.get(entry.get("trend_10d", "sideways"), 0.4)
        rsi      = d.get("rsi", 50)
        macd_h   = d.get("macd_hist", 0)
        tech_raw = (
            momentum * 0.4
            + (1.0 if 38 <= rsi <= 62 else 0.4 if 25 <= rsi <= 75 else 0.1) * 0.3
            + (1.0 if macd_h > 0 else 0.3) * 0.3
        )

        # ── News sentiment (0–1) ──────────────────────────────────────────────
        news_raw = min(1.0, max(0.0, (news.get("score", 0) + 1) / 2))

        # ── Regime / quality score (0–1) — uses the deep 2yr history + delivery ─
        # Setups that align with the stock's long-term trend, sit at a constructive
        # 52w position, and show institutional accumulation are statistically more
        # likely to follow through. This is independent of the short-term technical.
        regime_raw   = 0.5   # neutral default when history not yet available
        long_trend   = d.get("hist_long_trend")
        pos52        = d.get("hist_pct_of_52w_range")
        vol_state    = d.get("hist_vol_state")
        delivery_sig = d.get("delivery_signal", "neutral")
        if long_trend in ("strong_uptrend", "uptrend"):
            regime_raw += 0.20
        elif long_trend in ("strong_downtrend", "downtrend"):
            regime_raw -= 0.20
        if pos52 is not None:
            if 35 <= pos52 <= 80:      regime_raw += 0.10   # healthy mid-range room to run
            elif pos52 > 92:           regime_raw += 0.05   # breakout zone (some risk)
            elif pos52 < 10:           regime_raw -= 0.05   # falling-knife risk
        if delivery_sig in ("accumulation", "strong_accumulation"):
            regime_raw += 0.15
        elif delivery_sig == "distribution":
            regime_raw -= 0.15
        if vol_state == "compressed":
            regime_raw += 0.05   # primed for expansion
        # Real FII/DII institutional flow — a market-wide tailwind/headwind that
        # nudges every long setup's odds (institutions move Nifty-100 the most).
        fd_sig = (market_health or {}).get("fii_dii", {}).get("signal", "neutral")
        if fd_sig == "strong_inflow":   regime_raw += 0.08
        elif fd_sig == "inflow":        regime_raw += 0.04
        elif fd_sig == "strong_outflow":regime_raw -= 0.08
        elif fd_sig == "outflow":       regime_raw -= 0.04

        # Options-flow tilt (Nifty PCR + OI structure). Extreme PCR is contrarian;
        # OI-implied floor/lid is a directional lean. Small weight — it's a market-
        # wide backdrop, not a per-stock signal.
        pcr_d  = (market_health or {}).get("pcr", {}) or {}
        pcr_v  = pcr_d.get("pcr", 0) or 0
        if pcr_v >= 1.5:    regime_raw += 0.04   # over-hedged → contrarian bullish
        elif pcr_v <= 0.6 and pcr_v > 0:  regime_raw -= 0.04   # complacent → caution
        oi_bias = pcr_d.get("oi_bias")
        if oi_bias == "put_heavy_support":      regime_raw += 0.03
        elif oi_bias == "call_heavy_resistance":regime_raw -= 0.03

        # Multi-timeframe alignment: a (long) setup whose higher-timeframe trend
        # agrees is genuinely higher quality and should rank above one that fights
        # it. Derived from the SAME history fields the brain's MTF gate uses, so
        # ranking and the gate stay consistent (no contradictory signals).
        htf_lt = d.get("hist_long_trend")
        htf_6m = d.get("hist_ret_6m")
        htf_up   = htf_lt in ("uptrend", "strong_uptrend")   or (htf_6m is not None and htf_6m > 3)
        htf_down = htf_lt in ("downtrend", "strong_downtrend") or (htf_6m is not None and htf_6m < -3)
        if htf_up:     regime_raw += 0.05   # bigger trend supports a long
        elif htf_down: regime_raw -= 0.05   # bigger trend fights a long
        regime_raw = max(0.0, min(1.0, regime_raw))

        # ── Bayesian success probability ──────────────────────────────────────
        # Prior: 0.50. Weight paper data more the more trades we have. The prior
        # blends technical, fundamental, pattern, news AND regime/quality so that
        # genuinely high-quality setups earn a higher base probability.
        paper_weight = min(0.7, n_trades / 20)   # ramps to 0.7 after 20 trades
        prior_weight = 1.0 - paper_weight
        success_prob = round(
            paper_weight * paper_wr
            + prior_weight * (0.28 * tech_raw + 0.25 * fund_score_raw
                              + 0.17 * pat_score_raw + 0.12 * news_raw
                              + 0.18 * regime_raw),
            3
        )

        # ── Profit probability = expected edge ────────────────────────────────
        # How much R we expect per trade if we take this setup
        profit_prob = round(success_prob * avg_rr - (1 - success_prob) * 1.0, 3)

        # ── Composite rank score (0–100) ──────────────────────────────────────
        composite = round(
            success_prob * 38
            + profit_prob * 18
            + fund_score_raw * 18
            + tech_raw * 14
            + regime_raw * 8
            + news_raw * 4,
            2
        ) * 100 / 100   # already in 0-100 range conceptually; cap at 100

        ranked.append({
            "ticker":            ticker,
            "nse_code":          ticker.replace(".NS", ""),
            "close":             round(close, 2),
            "trend":             entry.get("trend_10d", "sideways"),
            "rsi":               round(rsi, 1),
            "success_probability":  success_prob,
            "profit_probability":   profit_prob,
            "composite_score":      round(composite, 2),
            "fund_score_pct":       round(fund_score_raw * 100, 1),
            "tech_score_pct":       round(tech_raw * 100, 1),
            "regime_score_pct":     round(regime_raw * 100, 1),
            "paper_win_rate":       round(paper_wr * 100, 1),
            "paper_trades":         n_trades,
            "reliable_patterns":    reliable,
            "news_score_pct":       round(news_raw * 100, 1),
        })

    ranked.sort(key=lambda x: x["composite_score"], reverse=True)

    # Add rank numbers + deltas
    prev_ranks = _load_prev_ranks()
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
        prev = prev_ranks.get(r["ticker"])
        r["rank_delta"] = (prev - r["rank"]) if prev else 0   # positive = moved up

    if record_history:
        _save_rank_history(ranked)
    return ranked


def evaluate_focus_refresh(
    focus: List[str],
    ranked: List[dict],
    stock_data: Dict,
    patterns: Dict,
    news_data: Dict,
    fundamentals: Dict,
    watchlist_signals: Dict,
    n: int = FOCUS_STOCK_COUNT,
    promotion_pool: List[str] = None,
    held_tickers: List[str] = None,
) -> Tuple[List[str], List[str], List[str]]:
    """
    Decide whether to promote/demote any stocks.

    Returns (new_focus, promoted, demoted).

    held_tickers: stocks with an OPEN paper (or user) position — these are NEVER
    demoted, because the tool must keep managing a live position (opinions, exits,
    trailing) until it actually closes. Demoting a held stock would orphan the
    trade from the analysis loop.

    Rules:
      - A focus stock is demoted if it has been ranked bottom-3 for 3+ consecutive
        sessions AND has composite_score < 30.
      - A watchlist stock is promoted if it has received a strong signal (score > 70)
        in 3 of the last 5 sessions.
    """
    demote_candidates = _get_demotion_candidates(ranked)
    promote_candidates = _get_promotion_candidates(watchlist_signals, stock_data,
                                                   fundamentals, n_slots=len(demote_candidates))
    # Merge background cohort candidates into promotion pool (prioritise them)
    if promotion_pool:
        not_in_focus = [t for t in promotion_pool if t not in focus]
        promote_candidates = list(dict.fromkeys(not_in_focus + promote_candidates))

    # ── Direct competition: challenge mediocre incumbents ──────────────────────
    # The demotion rule above only fires for stocks stuck at the very bottom. But
    # the goal is a perpetual competition: a clearly-stronger candidate should be
    # able to unseat an incumbent that's merely average, not just a bottom-3 one.
    # We score each pooled candidate on the SAME composite scale as the focus
    # stocks, and if it beats the weakest incumbent by a clear margin, we challenge.
    comp_demoted, comp_promoted = _competitive_challenges(
        focus, ranked, promotion_pool or [], stock_data, patterns,
        news_data, fundamentals, watchlist_signals,
    )

    if not demote_candidates and not promote_candidates and not comp_promoted:
        return focus, [], []

    # Existing bottom-3 swaps (need both a demote and a promote candidate)
    n_swaps = min(len(demote_candidates), len(promote_candidates))
    demoted  = demote_candidates[:n_swaps]
    promoted = promote_candidates[:n_swaps]

    # Add the competition-driven challenges (already paired weakest-incumbent →
    # stronger-candidate), avoiding double-counting anything already swapped.
    for inc, cand in zip(comp_demoted, comp_promoted):
        if inc not in demoted and cand not in promoted and cand not in focus:
            demoted.append(inc)
            promoted.append(cand)

    # ── Protect held positions ──────────────────────────────────────────────────
    # Never demote a stock we're currently holding — the trade must stay in the
    # analysis loop until it closes. Drop the corresponding promotion too (keep the
    # swap balanced so we don't overflow the focus count).
    held = set(held_tickers or [])
    if held:
        keep_demoted, keep_promoted = [], []
        for i, dem in enumerate(demoted):
            if dem in held:
                print(f"[ranking] Protected held position {dem} from demotion")
                continue
            keep_demoted.append(dem)
            if i < len(promoted):
                keep_promoted.append(promoted[i])
        # any promotions beyond the paired ones (rare) — keep as slots allow
        demoted, promoted = keep_demoted, keep_promoted

    new_focus = [t for t in focus if t not in demoted] + promoted
    new_focus = list(dict.fromkeys(new_focus))[:n]   # dedup, cap at n

    if demoted or promoted:
        print(f"[ranking] Focus refresh: demoted={demoted}  promoted={promoted}")

    return new_focus, promoted, demoted


def update_watchlist_signals(
    watchlist_signals: Dict,
    stock_data: Dict,
    patterns: Dict,
    news_data: Dict,
    fundamentals: Dict,
    focus: List[str],
) -> Dict:
    """
    Score every non-focus Nifty-100 stock and track how many sessions it has
    shown a strong signal. Used by evaluate_focus_refresh().
    """
    from agent.config import NSE_UNIVERSE
    from agent.fundamentals_fetcher import score_fundamentals
    from agent.brain import get_reliable_patterns_list

    today = ist_today().isoformat()
    non_focus = [t for t in NSE_UNIVERSE if t not in focus]

    for ticker in non_focus:
        entry = stock_data.get(ticker, {})
        if not entry or "latest" not in entry:
            continue
        d     = entry["latest"]
        close = d.get("close", 0)
        if close <= 0 or close > MAX_STOCK_PRICE:
            continue

        fund     = fundamentals.get(ticker, {})
        fund_s   = score_fundamentals(fund)
        reliable = get_reliable_patterns_list(ticker, patterns, min_rel=0.55)
        rsi      = d.get("rsi", 50)
        macd_h   = d.get("macd_hist", 0)
        trend    = entry.get("trend_10d", "sideways")

        score = (
            (1.0 if trend in ("strong_up", "up") else 0.0) * 25
            + (1.0 if 38 <= rsi <= 62 else 0.3) * 20
            + (1.0 if macd_h > 0 else 0.0) * 15
            + fund_s * 0.30
            + min(10, len(reliable) * 2.5)
        )

        rec = watchlist_signals.setdefault(ticker, {"sessions": [], "score_history": []})
        rec["sessions"].append(today)
        rec["score_history"].append(round(score, 1))
        # Keep last 10 sessions
        rec["sessions"]      = rec["sessions"][-10:]
        rec["score_history"] = rec["score_history"][-10:]
        rec["last_score"]    = round(score, 1)

    return watchlist_signals


def load_watchlist_signals() -> Dict:
    from agent.io_safe import load_json_dict
    return load_json_dict(WATCHLIST_FILE)


def save_watchlist_signals(data: Dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_rank_history() -> List[dict]:
    if os.path.exists(RANK_HISTORY_FILE):
        try:
            with open(RANK_HISTORY_FILE) as f:
                data = json.load(f)
            # Must be a list of snapshots; coerce anything else (e.g. an empty {}
            # written by a reset) back to a list so .append() never crashes.
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


# ── Internal helpers ───────────────────────────────────────────────────────────

def _load_prev_ranks() -> Dict[str, int]:
    history = load_rank_history()
    if not history or not isinstance(history[-1], dict):
        return {}
    last = history[-1].get("ranked", [])
    # Defensive: tolerate any malformed snapshot entry from an older format
    out = {}
    for r in last:
        if isinstance(r, dict) and "ticker" in r and "rank" in r:
            out[r["ticker"]] = r["rank"]
    return out


def _save_rank_history(ranked: List[dict]) -> None:
    history = load_rank_history()
    today = ist_today().isoformat()
    snap = {
        "date":   today,
        "ranked": [{"ticker": r["ticker"], "rank": r["rank"],
                    "composite_score": r["composite_score"],
                    "success_probability": r["success_probability"]}
                   for r in ranked],
    }
    # ONE snapshot per date — rankings rebuild every session (6x/day), so replace
    # today's entry instead of appending six near-identical copies a day.
    history = [h for h in history if h.get("date") != today] + [snap]
    history = history[-180:]   # ~180 trading days ≈ 8-9 months of daily snapshots
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(RANK_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# How much higher (composite points) a challenger must score than the incumbent
# it replaces. A margin prevents churn on tiny, noisy differences — only a clearly
# better stock wins a focus slot.
COMPETITION_MARGIN = 8.0
# At most this many competitive swaps per refresh, so the focus list evolves
# steadily rather than lurching wholesale in one session.
MAX_COMPETITIVE_SWAPS = 3


def _competitive_challenges(
    focus: List[str],
    ranked: List[dict],
    pool: List[str],
    stock_data: Dict,
    patterns: Dict,
    news_data: Dict,
    fundamentals: Dict,
    watchlist_signals: Dict,
) -> Tuple[List[str], List[str]]:
    """
    Perpetual competition: score each pooled candidate on the same composite scale
    as the focus stocks and let a clearly-stronger candidate unseat the weakest
    incumbent (by COMPETITION_MARGIN). Returns (incumbents_out, challengers_in),
    paired and ordered weakest-incumbent → strongest-challenger.
    """
    challengers = [t for t in pool if t and t not in focus and t in stock_data]
    if not focus or not challengers:
        return [], []

    # Incumbent composite scores (from the ranking we already computed this run).
    inc_score = {r["ticker"]: r.get("composite_score", 0) for r in ranked}
    # Weakest incumbents first.
    incumbents_sorted = sorted(focus, key=lambda t: inc_score.get(t, 0))

    # Score the challengers with the SAME composite engine, so the comparison is
    # apples-to-apples. Reuse rank_focus_stocks on just the challenger set.
    try:
        from agent.market_health import load_market_health
        mh = load_market_health()
    except Exception:
        mh = {}
    challenger_ranked = rank_focus_stocks(
        challengers, stock_data, patterns, news_data, fundamentals,
        {"closed_trades": []}, mh, record_history=False,
    )
    cand_score = sorted(
        ((r["ticker"], r.get("composite_score", 0)) for r in challenger_ranked),
        key=lambda kv: -kv[1],
    )

    out, in_ = [], []
    used_inc = set()
    for cand, cscore in cand_score:
        if len(out) >= MAX_COMPETITIVE_SWAPS:
            break
        # Find the weakest incumbent not already challenged this round.
        for inc in incumbents_sorted:
            if inc in used_inc:
                continue
            if cscore >= inc_score.get(inc, 0) + COMPETITION_MARGIN:
                out.append(inc); in_.append(cand); used_inc.add(inc)
            break   # only test against the current weakest; stop either way
    return out, in_


def _get_demotion_candidates(ranked: List[dict]) -> List[str]:
    """Stocks in the bottom 3 with low composite scores."""
    if len(ranked) <= 5:
        return []
    bottom = ranked[-3:]
    history = load_rank_history()
    demote = []
    for r in bottom:
        if r["composite_score"] >= 30:
            continue
        # Check how many of the last 3 sessions this stock was in bottom 3
        ticker = r["ticker"]
        bottom_count = 0
        for snap in history[-3:]:
            if not isinstance(snap, dict):
                continue
            snap_ranked = snap.get("ranked", [])
            n = len(snap_ranked)
            for s in snap_ranked[max(0, n-3):]:
                if isinstance(s, dict) and s.get("ticker") == ticker:
                    bottom_count += 1
        if bottom_count >= 3:
            demote.append(ticker)
    return demote


def _get_promotion_candidates(
    watchlist_signals: Dict,
    stock_data: Dict,
    fundamentals: Dict,
    n_slots: int,
) -> List[str]:
    """Stocks with 3+ strong signals in last 5 sessions, not already in focus."""
    from agent.fundamentals_fetcher import score_fundamentals
    candidates = []
    for ticker, rec in watchlist_signals.items():
        scores = rec.get("score_history", [])
        if len(scores) < 3:
            continue
        strong = sum(1 for s in scores[-5:] if s >= 65)
        if strong < 3:
            continue
        entry = stock_data.get(ticker, {})
        close = entry.get("latest", {}).get("close", 0)
        if close <= 0 or close > MAX_STOCK_PRICE:
            continue
        avg_score = sum(scores[-5:]) / min(len(scores), 5)
        candidates.append((ticker, avg_score))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in candidates[:n_slots]]
