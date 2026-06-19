"""
Main orchestrator — runs 3x per trading day via GitHub Actions.
SESSION env var: morning | midday | preclose
"""

import json
import os
from datetime import date

from agent.migrations import run_migrations, check_schema_health
from agent.config import (
    NSE_UNIVERSE, FOCUS_STOCK_COUNT,
    EXPLORATION_DAYS, ANALYSIS_DAYS,
    BRAIN_DIR, DAILY_LOG_FILE,
)
from agent.state_manager import (
    load_state, save_state, set_phase, advance_session, add_brain_note,
)
from agent.data_fetcher import (
    fetch_stock_data, load_stock_data, save_stock_data, merge_stock_data,
)
from agent.news_fetcher   import fetch_news, load_news, save_news
from agent.market_health  import assess_market, load_market_health
from agent.stock_scorer   import select_focus_stocks
from agent.brain          import (
    load_patterns, save_patterns, load_decisions, save_decisions,
    analyse_stock, record_decision,
)
from agent.paper_trader   import (
    load_book, save_book,
    morning_session, midday_session, preclose_session,
    compute_stats, is_ready_to_alert,
)
from agent.recommendations import generate_recommendations, load_recommendations
from agent.fundamentals_fetcher import fetch_fundamentals, load_fundamentals, save_fundamentals
from agent.dashboard       import build_dashboard
from agent.report_generator import generate_report
from agent.ranking_engine import (
    rank_focus_stocks, evaluate_focus_refresh,
    update_watchlist_signals, load_watchlist_signals, save_watchlist_signals,
)
from agent.sector_tracker import (
    compute_sector_scores, inject_sector_momentum,
    save_sector_scores, load_sector_scores,
)
from agent.rec_changelog import compute_changes, save_changelog, load_changelog
from agent.delivery_fetcher import fetch_delivery, save_delivery, inject_delivery
from agent.attribution import update_attribution, aggregate_attribution


def run():
    session = os.environ.get("SESSION", "morning").lower()
    print(f"\n{'='*60}")
    print(f"NSE AI Trader  {date.today()}  session={session}")
    print(f"{'='*60}\n")

    # ── Auto-migrate brain data before anything else ───────────────────────────
    # Reads GitHub-committed brain files, upgrades structure if needed, writes back.
    # Zero data loss on structural code changes.
    run_migrations()
    health = check_schema_health()
    print(f"[schema] v{health['schema_version']} | phase={health['phase']} day={health['day']} "
          f"stocks={health['stocks_tracked']} trades={health['closed_trades']}\n")

    state = load_state()
    phase = state["phase"]
    day   = state["day"]
    focus = state.get("focus_stocks", [])
    print(f"Phase={phase}  Day={day}  FocusStocks={focus}\n")

    # ── Market health check (every session) ────────────────────────────────────
    market_health = assess_market(session)

    # ── EXPLORATION ────────────────────────────────────────────────────────────
    if phase == "exploration":
        tickers = NSE_UNIVERSE
        fresh   = fetch_stock_data(tickers, session=session)
        merged_expl = merge_stock_data(load_stock_data(), fresh)
        # Inject sector momentum so scores are sector-aware from day 1
        sector_scores = compute_sector_scores(merged_expl)
        save_sector_scores(sector_scores)
        merged_expl = inject_sector_momentum(merged_expl, sector_scores)
        save_stock_data(merged_expl)

        if session == "preclose":
            news = fetch_news(tickers)
            save_news(news)
            state = advance_session(state, session)

            if state["day"] > EXPLORATION_DAYS:
                sd   = load_stock_data()
                sent = {t: v.get("latest", {}) for t, v in load_news().items()}
                top  = select_focus_stocks(sd, sent, FOCUS_STOCK_COUNT)
                state["focus_stocks"] = [t for t, _ in top]
                scores_str = " | ".join(f"{t.replace('.NS','')}={s:.0f}" for t, s in top)
                note = f"Selected {len(top)} focus stocks. Scores: {scores_str}"
                state = add_brain_note(state, note)
                # Fetch fundamentals for the selected focus stocks
                print("[main] Fetching fundamentals for focus stocks...")
                fund_data = fetch_fundamentals([t for t, _ in top])
                save_fundamentals(fund_data)
                state = set_phase(state, "analysis", note)
                # Kick off background cohort exploration immediately
                state["background_cohort"] = {
                    "day":        1,
                    "start_date": date.today().isoformat(),
                    "ready":      False,
                    "candidates": [],
                }
        else:
            state = advance_session(state, session) if session == "midday" else state

        save_state(state)

    # ── ANALYSIS ───────────────────────────────────────────────────────────────
    elif phase == "analysis":
        fresh = fetch_stock_data(focus, session=session)
        merged_anal = merge_stock_data(load_stock_data(), fresh)
        sector_scores = compute_sector_scores(merged_anal)
        save_sector_scores(sector_scores)
        merged_anal = inject_sector_momentum(merged_anal, sector_scores)
        # Inject earnings days_to_earnings from fundamentals into latest dict
        fund_anal = load_fundamentals()
        merged_anal = _inject_fund_context(merged_anal, fund_anal)
        save_stock_data(merged_anal)

        if session == "preclose":
            news = fetch_news(focus)
            save_news(news)
            # Refresh fundamentals weekly (every 5 preclose sessions in analysis)
            if state.get("day", 1) % 5 == 0:
                fund_data = fetch_fundamentals(focus)
                save_fundamentals(fund_data)
            # Fetch and inject delivery % data into stock latest dicts
            try:
                delivery_data = fetch_delivery(focus)
                save_delivery(delivery_data)
                merged_anal = inject_delivery(merged_anal, delivery_data)
                save_stock_data(merged_anal)
            except Exception as e:
                print(f"[delivery] fetch failed (non-fatal): {e}")

            patterns  = load_patterns()
            decisions = load_decisions()
            sd        = load_stock_data()
            nd        = load_news()

            for ticker in focus:
                sent = nd.get(ticker, {}).get("latest", {})
                op   = analyse_stock(ticker, sd.get(ticker, {}), patterns, sent, session)
                decisions = record_decision(decisions, op, "ANALYSE", f"Analysis day {day}")
                if op["signal"] != "WATCH":
                    print(f"  [{ticker.replace('.NS','')}] {op['signal']} score={op['confidence']:.0f}%")

            save_patterns(patterns)
            save_decisions(decisions)
            state = advance_session(state, session)

            # Tick background cohort during analysis too
            _tick_background_cohorts(state, session)

            if state["day"] > EXPLORATION_DAYS + ANALYSIS_DAYS:
                state = set_phase(state, "paper_trading", "Analysis done — starting paper trades")

        save_state(state)

    # ── PAPER TRADING ──────────────────────────────────────────────────────────
    elif phase in ("paper_trading", "alerting"):
        # Fetch data for ALL focus stocks every session for full paper trading
        fresh  = fetch_stock_data(focus, session=session)
        merged = merge_stock_data(load_stock_data(), fresh)
        sector_scores = compute_sector_scores(merged)
        save_sector_scores(sector_scores)
        merged = inject_sector_momentum(merged, sector_scores)
        fund   = load_fundamentals()
        merged = _inject_fund_context(merged, fund)
        save_stock_data(merged)

        if session == "preclose":
            news = fetch_news(focus)
            save_news(news)
            # Fetch and inject delivery % data
            try:
                delivery_data = fetch_delivery(focus)
                save_delivery(delivery_data)
                merged = inject_delivery(merged, delivery_data)
                save_stock_data(merged)
            except Exception as e:
                print(f"[delivery] fetch failed (non-fatal): {e}")

        patterns  = load_patterns()
        decisions = load_decisions()
        nd        = load_news()
        book      = load_book()
        fund      = load_fundamentals()

        # Build analyst opinions for ALL 15 focus stocks (paper trade all of them)
        opinions = []
        for ticker in focus:
            sent = nd.get(ticker, {}).get("latest", {})
            op   = analyse_stock(ticker, merged.get(ticker, {}), patterns, sent, session)
            opinions.append(op)
            if op["signal"] != "WATCH":
                decisions = record_decision(decisions, op, op["signal"], f"paper {session}")
        print(f"[paper] Analysed {len(opinions)} focus stocks this {session} session")

        # Gate trading on market health
        if not market_health.get("trade_allowed", True):
            print("[paper] Market conditions unfavourable — skipping new trades this session")
            tradeable_opinions = []
        else:
            tradeable_opinions = opinions

        # Session trading
        if session == "morning":
            book, patterns = morning_session(book, tradeable_opinions, patterns, market_health=market_health)
        elif session in ("midday", "afternoon"):
            book, patterns = midday_session(book, tradeable_opinions, merged, patterns, market_health=market_health)
        elif session == "preclose":
            book, patterns = preclose_session(book, tradeable_opinions, merged, patterns, market_health=market_health)

        # Update win rate attribution after each session's trade activity
        if session == "preclose":
            patterns = update_attribution(patterns, book.get("closed_trades", []))

        save_patterns(patterns)
        save_decisions(decisions)
        save_book(book)

        stats = compute_stats(book)
        state["paper_trade_stats"] = stats
        print(f"\n[stats] Trades={stats['total']} | WR={stats['win_rate']*100:.1f}% "
              f"| PnL=₹{stats['total_pnl']:+,.0f} | Exp=₹{stats['expectancy']:+,.0f}/trade")

        if session == "preclose":
            state = advance_session(state, session)

            # ── Dynamic focus refresh: promote/demote stocks ──────────────────
            _maybe_refresh_focus(state, merged, patterns, nd, fund, book, market_health)
            # Reload state after potential focus update
            state = load_state()
            focus = state.get("focus_stocks", focus)

            # ── Refresh fundamentals weekly ───────────────────────────────────
            if state.get("day", 1) % 5 == 0:
                fund_data = fetch_fundamentals(focus)
                save_fundamentals(fund_data)

            # ── Parallel background cohort exploration ────────────────────────
            _tick_background_cohorts(state, session)

            # ── Alert when paper trading is validated ─────────────────────────
            if is_ready_to_alert(stats, book) and not state.get("alert_sent"):
                state["alert_sent"] = True
                state = add_brain_note(
                    state,
                    f"ALERT: Win rate {stats['win_rate']*100:.1f}% "
                    f"over {stats['total']} trades — user recommendations ready"
                )
                state = set_phase(state, "alerting")

            if phase == "alerting":
                # Keep running indefinitely — never stop after alerting
                state = add_brain_note(state, "Continuing live paper trading post-alert (perpetual mode)")

        save_state(state)

    # ── Always: generate recommendations + rebuild dashboard ──────────────────
    _refresh_outputs(state, market_health, session)
    _append_log(state, session)
    print(f"\n[done] {session} complete. Phase={state['phase']} Day={state['day']}\n")


def _tick_background_cohorts(state: dict, session: str) -> None:
    """
    Perpetual parallel exploration pipeline.

    Design: the moment batch N completes its first day of exploration, batch N+1
    starts immediately. Multiple batches can be in-flight simultaneously.
    Each batch independently explores the full NSE universe for EXPLORATION_DAYS,
    then scores and marks itself ready.  When a focus refresh happens, the most
    recently completed batch's candidates feed the promotion pool.

    State stores batches as a list:  state["background_batches"] = [
        {"id": 1, "day": 5, "start_date": "...", "ready": True,  "candidates": [...]},
        {"id": 2, "day": 2, "start_date": "...", "ready": False, "candidates": []},
    ]
    """
    if session != "preclose":
        return

    focus     = state.get("focus_stocks", [])
    batches   = state.setdefault("background_batches", [])

    # ── Start a new batch if: no batches exist, OR the newest batch finished day 1
    should_start_new = (
        not batches or
        batches[-1].get("day", 0) >= 1   # newest batch has passed day 1 → spawn next
    )
    # But don't spawn another if newest is still on day 0 (just created)
    if batches and batches[-1].get("day", 0) == 0:
        should_start_new = False
    # Cap at 3 concurrent in-flight batches to avoid overloading the runner
    active_count = sum(1 for b in batches if not b.get("ready"))
    if active_count >= 3:
        should_start_new = False

    if should_start_new:
        new_id = (batches[-1]["id"] + 1) if batches else 1
        batches.append({
            "id":         new_id,
            "day":        0,
            "start_date": date.today().isoformat(),
            "ready":      False,
            "candidates": [],
        })
        print(f"[cohort] Started background batch #{new_id}")

    # ── Tick every non-ready batch ────────────────────────────────────────────
    for batch in batches:
        if batch.get("ready"):
            continue

        batch_day = batch.get("day", 0)
        batch_id  = batch.get("id", "?")
        print(f"[cohort] Batch #{batch_id} exploration day {batch_day}/{EXPLORATION_DAYS}")

        try:
            fresh_bg  = fetch_stock_data(NSE_UNIVERSE, session="preclose")
            existing  = load_stock_data()
            merged_bg = merge_stock_data(existing, fresh_bg)
            for ticker in NSE_UNIVERSE:
                if ticker not in focus and ticker in fresh_bg:
                    existing[ticker] = merged_bg[ticker]
            save_stock_data(existing)
        except Exception as e:
            print(f"[cohort] Batch #{batch_id} fetch error (non-fatal): {e}")
            batch["day"] = batch_day + 1
            continue

        batch_day += 1
        batch["day"] = batch_day

        if batch_day >= EXPLORATION_DAYS:
            try:
                sd   = load_stock_data()
                nd   = load_news()
                sent = {t: v.get("latest", {}) for t, v in nd.items()}
                top  = select_focus_stocks(sd, sent, FOCUS_STOCK_COUNT * 2)
                batch["candidates"]  = [t for t, _ in top]
                batch["scored_date"] = date.today().isoformat()
                batch["ready"]       = True
                print(f"[cohort] Batch #{batch_id} ready — {len(top)} candidates scored")
                state = add_brain_note(state, f"Cohort batch #{batch_id} complete — {len(top)} candidates ready")
            except Exception as e:
                print(f"[cohort] Batch #{batch_id} scoring error: {e}")

    # Keep only last 5 batches (older ones are stale)
    state["background_batches"] = batches[-5:]
    # Legacy compat: expose most-recent ready batch as background_cohort
    ready = [b for b in batches if b.get("ready")]
    state["background_cohort"] = ready[-1] if ready else None


def _maybe_refresh_focus(state, stock_data, patterns, news_data, fund, book, market_health):
    """Evaluate whether any focus stocks should be promoted/demoted. Runs every preclose."""
    focus = state.get("focus_stocks", [])
    if not focus:
        return

    # Only refresh after first 3 paper trading days to have enough data
    paper_days = state.get("day", 1) - (
        state.get("exploration_days_used", 5) + state.get("analysis_days_used", 10)
    )
    if paper_days < 3:
        return

    ranked = rank_focus_stocks(focus, stock_data, patterns, news_data, fund, book, market_health)

    # Update watchlist scores for non-focus stocks
    wl = load_watchlist_signals()
    wl = update_watchlist_signals(wl, stock_data, patterns, news_data, fund, focus)
    save_watchlist_signals(wl)

    # Use background cohort candidates as the promotion candidate pool if ready
    cohort = state.get("background_cohort", {})
    cohort_candidates = cohort.get("candidates", []) if cohort.get("ready") else []
    if cohort_candidates:
        print(f"[cohort] Using {len(cohort_candidates)} background candidates for focus refresh")

    new_focus, promoted, demoted = evaluate_focus_refresh(
        focus, ranked, stock_data, patterns, news_data, fund, wl,
        promotion_pool=cohort_candidates or None,
    )

    if promoted or demoted:
        state["focus_stocks"] = new_focus
        prev = state.get("dropped_stocks", [])
        state["dropped_stocks"] = list(set(prev + demoted))[-30:]
        note = f"Focus refresh — promoted: {[t.replace('.NS','') for t in promoted]} | " \
               f"demoted: {[t.replace('.NS','') for t in demoted]}"
        state = add_brain_note(state, note)
        # Fetch fundamentals for any newly promoted stocks
        if promoted:
            new_fund = fetch_fundamentals(promoted)
            save_fundamentals(new_fund)
        save_state(state)


def _inject_fund_context(stock_data: dict, fund: dict) -> dict:
    """Inject days_to_earnings and analyst fields from fundamentals into each stock's latest dict."""
    for ticker, entry in stock_data.items():
        f = fund.get(ticker, {})
        if not f or "latest" not in entry:
            continue
        d = entry["latest"]
        if "days_to_earnings" in f:
            d["days_to_earnings"] = f["days_to_earnings"]
        if "week52_high" in f and d.get("week52_high", 0) == 0:
            d["week52_high"] = f.get("week52_high", 0)
        if "week52_low" in f and d.get("week52_low", 0) == 0:
            d["week52_low"] = f.get("week52_low", 0)
    return stock_data


def _refresh_outputs(state: dict, market_health: dict, session: str) -> None:
    try:
        sd      = load_stock_data()
        book    = load_book()
        pats    = load_patterns()
        decs    = load_decisions()
        nd      = load_news()
        fund    = load_fundamentals()
        sectors = load_sector_scores()
        clog    = load_changelog()

        # Regenerate recommendations every preclose session or on first run
        if session == "preclose" or not os.path.exists("brain/recommendations.json"):
            prev_recs = load_recommendations()
            recs = generate_recommendations(state, sd, pats, nd, book, market_health, fund, session=session)
            # Compute and persist what changed vs previous recommendations
            changes = compute_changes(prev_recs, recs)
            if changes:
                save_changelog(changes)
                clog = load_changelog()
                for c in changes:
                    print(f"[changelog] {c['type'].upper()} {c['nse_code']}: {c['detail']}")
            generate_report(state, sd, pats, nd, book)
        else:
            recs = load_recommendations()

        # Always rebuild ranking (runs fast, no API calls)
        focus  = state.get("focus_stocks", [])
        ranked = rank_focus_stocks(focus, sd, pats, nd, fund, book, market_health) if focus else []

        attr_summary = aggregate_attribution(pats)
        build_dashboard(state, sd, book, pats, decs, nd, market_health,
                        recs, fund, ranked, sectors, clog, attr_summary)

    except Exception as e:
        import traceback
        print(f"[output] Error: {e}")
        traceback.print_exc()


def _append_log(state: dict, session: str) -> None:
    book  = load_book()
    stats = compute_stats(book)
    entry = {
        "date":    date.today().isoformat(),
        "session": session,
        "phase":   state["phase"],
        "day":     state["day"],
        "stats":   stats,
        "open":    len(book.get("open_positions", [])),
    }
    os.makedirs(BRAIN_DIR, exist_ok=True)
    log = []
    if os.path.exists(DAILY_LOG_FILE):
        with open(DAILY_LOG_FILE) as f:
            try:
                log = json.load(f)
            except Exception:
                log = []
    log.append(entry)
    log = log[-270:]
    with open(DAILY_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


if __name__ == "__main__":
    run()
