"""
Main orchestrator — runs 5x per trading day via GitHub Actions.
SESSION env var: preopen | morning | midday | afternoon | intraday_close | preclose
"""

import json
import os
import sys
from datetime import date

# Force UTF-8 stdout/stderr so the unicode glyphs used in log lines (→ — · ✅ ❌)
# never crash a run on a non-UTF-8 console (e.g. Windows cp1252). On Linux/Actions
# this is already UTF-8; this just makes the tool portable and crash-proof.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from agent.trading_calendar import ist_today, ist_now
from agent.migrations import run_migrations, check_schema_health
from agent.config import (
    NSE_UNIVERSE, FOCUS_STOCK_COUNT,
    EXPLORATION_DAYS, ANALYSIS_DAYS, CONCURRENT_BATCH_CAP,
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
    analyse_stock, record_decision, evaluate_dry_decisions,
)
from agent.paper_trader   import (
    load_book, save_book,
    morning_session, midday_session, preclose_session, intraday_close_session,
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
from agent.llm_coach import run_coach, load_coach_memory
from agent.history_engine import (
    fetch_history_context, save_history_context, load_history_context,
    refresh_universe_history, extend_foundation,
)
from agent.run_health import record_issue, start_run, finish_run


def run():
    raw_session = os.environ.get("SESSION", "").strip().lower()

    # ── TEST mode ───────────────────────────────────────────────────────────────
    # A safe dry-run for checking the pipeline / dashboard without side effects.
    # It does NOT advance the day, does NOT open/close trades, does NOT fetch fresh
    # market data — it only re-runs market-health (read-only) and rebuilds the
    # dashboard from the EXISTING brain data. Use it to confirm everything works
    # after a code change, any time, any day.
    if raw_session == "test":
        print(f"\n{'='*60}\nNSE AI Trader  {ist_today()}  session=TEST (dry run, no side effects)\n{'='*60}\n")
        run_migrations()
        start_run("test")
        try:
            state = load_state()
            market_health = assess_market("test")
            # read_only=True -> rebuild dashboard from existing data ONLY; no recs
            # regeneration, no report, no history extend, no file writes beyond the
            # dashboard + market_health snapshot. True zero-side-effect dry run.
            # Log THIS run first so the dashboard we then build includes it.
            _append_log(state, "test")
            _refresh_outputs(state, market_health, "test", read_only=True)
            print(f"[test] Dashboard rebuilt from existing data. "
                  f"phase={state['phase']} day={state['day']} (UNCHANGED). No trades, no day advance.")
        except Exception as e:
            import traceback
            print(f"[test] error: {e}"); traceback.print_exc()
            record_issue("test_run", str(ist_today()), str(e), "test")
        finish_run("test")
        print(f"\n[done] TEST run complete — nothing was changed.\n")
        return

    # SESSION must come from the workflow. If it's missing/blank (e.g. a manual
    # trigger where the dropdown didn't register), fail loud rather than silently
    # pretending it's "morning" — a wrong session pollutes the trading record.
    valid_sessions = {"preopen", "morning", "midday", "afternoon", "intraday_close", "preclose"}
    if raw_session not in valid_sessions:
        print(f"[run] SESSION='{raw_session}' invalid/empty — defaulting to 'preopen' "
              f"(safe: no trading). Valid: {sorted(valid_sessions)}")
        session = "preopen"
    else:
        session = raw_session

    print(f"\n{'='*60}")
    print(f"NSE AI Trader  {ist_today()}  session={session}")
    print(f"{'='*60}\n")

    # ── Non-trading-day guard (weekend OR NSE holiday) ──────────────────────────
    # NSE trades Mon–Fri excluding holidays. A run on any closed day would fetch +
    # "trade" on a non-trading day and pollute the record. On closed days we still
    # refresh the dashboard (so you can view it) but skip all data fetch / trading.
    # Set ALLOW_WEEKEND=1 to override (e.g. forced testing).
    from agent.trading_calendar import is_trading_day, reason_not_trading
    if not is_trading_day(ist_today()) and os.environ.get("ALLOW_WEEKEND", "") != "1":
        why = reason_not_trading(ist_today())
        print(f"[run] {ist_today()} is a {why} — NSE is closed. Refreshing dashboard "
              f"only; skipping data fetch & trading. (Set ALLOW_WEEKEND=1 to force.)")
        start_run(session)
        try:
            state = load_state()
            state["session"] = session
            today_iso = ist_today().isoformat()
            # Same per-session dedup as a trading day: a backup trigger that fires
            # after this session already refreshed today must NOT re-log. Without
            # this, every backup fire on a closed day appends a duplicate entry.
            if state.get("sessions_done", {}).get(session) == today_iso:
                print(f"[run] '{session}' already refreshed today ({today_iso}) on a "
                      f"closed day — duplicate trigger, dashboard-only, no re-log.")
                mh = assess_market(session)
                _refresh_outputs(state, mh, session, read_only=True)
                finish_run(session)
                return
            market_health = assess_market(session)
            _mark_session_done(state, session)
            save_state(state)
            _append_log(state, session, closed_day=why)   # honest "market closed" wording
            _refresh_outputs(state, market_health, session)
        except Exception as e:
            print(f"[run] weekend dashboard refresh failed (non-fatal): {e}")
            record_issue("weekend_refresh", str(ist_today()), str(e), session)
        finish_run(session)
        print(f"\n[done] {why} no-op complete ({session}).\n")
        return

    # Begin run-health tracking so any non-fatal failure this run is visible on
    # the dashboard's System Health panel.
    start_run(session)

    # ── Auto-migrate brain data before anything else ───────────────────────────
    # Reads GitHub-committed brain files, upgrades structure if needed, writes back.
    # Zero data loss on structural code changes.
    run_migrations()
    health = check_schema_health()
    print(f"[schema] v{health['schema_version']} | phase={health['phase']} day={health['day']} "
          f"stocks={health['stocks_tracked']} trades={health['closed_trades']}\n")

    state = load_state()
    # The cron-provided SESSION env var is the source of truth for which session
    # is running. Record it so the dashboard always shows the actual current session
    # (the day counter is still only incremented at preclose via advance_session).
    state["session"] = session
    phase = state["phase"]
    day   = state["day"]
    focus = state.get("focus_stocks", [])
    print(f"Phase={phase}  Day={day}  FocusStocks={focus}\n")

    # ── Session-level duplicate guard ───────────────────────────────────────────
    # Prevents the same session running its full work twice in one day (e.g. when a
    # backup trigger fires after the primary already succeeded). If THIS session
    # already completed today, do a lightweight dashboard-only refresh and stop —
    # no re-fetch, no duplicate trade attempts, no duplicate log spam. This is what
    # makes a backup trigger safe: a redundant fire becomes a harmless no-op.
    done_today = state.get("sessions_done", {})
    today_iso  = ist_today().isoformat()
    if done_today.get(session) == today_iso:
        print(f"[run] '{session}' already completed today ({today_iso}) — "
              f"duplicate trigger, refreshing dashboard only (no re-run).")
        start_run(session)
        try:
            mh = assess_market(session)
            _refresh_outputs(state, mh, session, read_only=True)
        except Exception as e:
            print(f"[run] duplicate-guard refresh failed (non-fatal): {e}")
        finish_run(session)
        return

    # ── Market health check (every session) ────────────────────────────────────
    # For the pre-open sweep this also runs the global+India macro sentiment pass.
    market_health = assess_market(session)

    # ── PRE-OPEN sweep ──────────────────────────────────────────────────────────
    # Runs ~08:35 IST before the market opens. Its job is purely to refresh the
    # global/India macro sentiment (done inside assess_market) and rebuild the
    # dashboard so you see the day's backdrop BEFORE the open. No data fetch, no
    # trading — the market isn't open yet. The session pointer is NOT advanced and
    # the day counter is untouched (preclose still owns the day rollover).
    if session == "preopen":
        macro = market_health.get("macro", {})
        note  = (f"Pre-open macro: {macro.get('mood','neutral')} "
                 f"({macro.get('overall_score',0):+.2f})")
        state = add_brain_note(state, note)
        _mark_session_done(state, session)
        save_state(state)
        _append_log(state, session)   # log first so the dashboard includes this run
        _refresh_outputs(state, market_health, session)
        finish_run(session)
        print(f"\n[done] pre-open sweep complete. {note}\n")
        return

    # ── Phase processing (wrapped so one failure never kills the whole run) ─────
    # If anything in the phase body throws unexpectedly, we log it but STILL fall
    # through to refresh the dashboard + write the run log from whatever data
    # exists. A self-running tool must never silently produce a dead run.
    try:
        state = _run_phase(state, phase, session, market_health, day, focus)
    except Exception as e:
        import traceback
        print(f"[run] Phase processing error (non-fatal, continuing to outputs): {e}")
        traceback.print_exc()
        record_issue("phase", f"{phase} phase", str(e), session)
        # Reload the last good state from disk so outputs reflect a consistent view
        state = load_state()

    # Mark this session complete for today so a later backup/duplicate trigger
    # no-ops instead of re-running. Persist it (the phase already saved state, so
    # we re-save with the completion mark).
    _mark_session_done(state, session)
    save_state(state)

    # ── Always: write this run's log entry, THEN rebuild outputs ──────────────
    # Order matters: _append_log must run BEFORE _refresh_outputs so the dashboard
    # it builds includes the CURRENT run in the Run Log (otherwise the dashboard is
    # always one run behind — e.g. morning's run wouldn't appear until the next run).
    _append_log(state, session)
    _refresh_outputs(state, market_health, session)
    finish_run(session)
    print(f"\n[done] {session} complete. Phase={state['phase']} Day={state['day']}\n")


def _run_phase(state, phase, session, market_health, day, focus):
    """All phase-specific processing. Extracted so run() can guard it and always
    still refresh outputs even if a phase step fails unexpectedly.
    Returns the (possibly updated) state so run() always has the latest view."""
    # ── intraday_close is a LIGHTWEIGHT close-only session ──────────────────────
    # It only has work to do once paper trading is live (intraday positions can
    # exist). In exploration/analysis there are no positions, so doing a full
    # universe fetch here would be wasteful (and slow). No-op cleanly in that case.
    if session == "intraday_close" and phase not in ("paper_trading", "alerting"):
        print("[intraday_close] No live positions in this phase — nothing to square off.")
        return state

    # ── EXPLORATION ────────────────────────────────────────────────────────────
    if phase == "exploration":
        tickers = NSE_UNIVERSE
        fresh   = fetch_stock_data(tickers, session=session)
        if not fresh:
            record_issue("data_fetch", "full universe", "all price sources returned no data — using last-known", session)
        merged_expl = merge_stock_data(load_stock_data(), fresh)
        # Inject sector momentum so scores are sector-aware from day 1
        sector_scores = compute_sector_scores(merged_expl)
        save_sector_scores(sector_scores)
        merged_expl = inject_sector_momentum(merged_expl, sector_scores)
        save_stock_data(merged_expl)

        if session == "preclose":
            news = fetch_news(tickers)
            save_news(news)

            # ── Deep 2-year history for the WHOLE universe (weekly refresh) ────
            # Built during exploration so the very first focus selection benefits
            # from long-term trend / 52w position / personality — not just 120d
            # technicals. refresh_universe_history skips stocks already fresh
            # (<7 days old), so the heavy fetch runs ~once a week; other days it's
            # a near-instant no-op. Inject into stock data so the scorer sees it.
            print("[main] Refreshing 2-year history for full universe...")
            uni_hist    = refresh_universe_history(NSE_UNIVERSE)
            merged_expl = _inject_history_context(load_stock_data(), uni_hist)
            save_stock_data(merged_expl)

            # ── Perpetual batches from exploration day 2 ──────────────────────
            # Spin up overlapping background batches early, as you asked. They
            # reuse the universe data the primary run just saved (skip_fetch) so
            # there's no duplicate fetching — each batch is an independent scoring
            # pass that seeds an early candidate pool for later focus promotion.
            _tick_background_cohorts(state, session, skip_fetch=True)

            state = advance_session(state, session)

            if state["day"] > EXPLORATION_DAYS:
                sd   = load_stock_data()
                sent = {t: v.get("latest", {}) for t, v in load_news().items()}
                # Focus selection now uses full-universe 2yr regime context too
                top  = select_focus_stocks(sd, sent, FOCUS_STOCK_COUNT)
                state["focus_stocks"] = [t for t, _ in top]
                scores_str = " | ".join(f"{t.replace('.NS','')}={s:.0f}" for t, s in top)
                note = f"Selected {len(top)} focus stocks. Scores: {scores_str}"
                state = add_brain_note(state, note)
                # Fetch fundamentals for the selected focus stocks
                print("[main] Fetching fundamentals for focus stocks...")
                fund_data = fetch_fundamentals([t for t, _ in top])
                save_fundamentals(fund_data)
                # History for focus stocks is already in uni_hist (fetched above)
                state = set_phase(state, "analysis", note)
                # Background batch pipeline is auto-seeded by _tick_background_cohorts()
                # on the first analysis preclose (it creates batch #1 when the list is
                # empty), so no manual init is needed here.
                state.setdefault("background_batches", [])
        # Non-preclose sessions: nothing to advance — the day counter only bumps at
        # preclose, and the session pointer is already set from the SESSION env var
        # at the top of run().

        save_state(state)

    # ── ANALYSIS ───────────────────────────────────────────────────────────────
    elif phase == "analysis":
        fresh = fetch_stock_data(focus, session=session)
        if not fresh:
            record_issue("data_fetch", "focus stocks", "all price sources returned no data — using last-known", session)
        merged_anal = merge_stock_data(load_stock_data(), fresh)
        sector_scores = compute_sector_scores(merged_anal)
        save_sector_scores(sector_scores)
        merged_anal = inject_sector_momentum(merged_anal, sector_scores)
        # Inject earnings days_to_earnings from fundamentals into latest dict
        fund_anal = load_fundamentals()
        merged_anal = _inject_fund_context(merged_anal, fund_anal)
        merged_anal = _inject_history_context(merged_anal, load_history_context())
        save_stock_data(merged_anal)

        if session == "preclose":
            news = fetch_news(focus)
            save_news(news)
            # Refresh fundamentals weekly (every 5 preclose sessions) OR self-heal if
            # they're missing/empty — e.g. the one-time fetch at focus selection
            # failed. Without this, empty fundamentals would block long-term
            # classification and weaken rec quality for up to 5 days.
            fund_missing = not load_fundamentals()
            if state.get("day", 1) % 5 == 0 or fund_missing:
                if fund_missing:
                    print("[main] Fundamentals empty — self-healing fetch for focus stocks")
                fund_data = fetch_fundamentals(focus)
                if fund_data:
                    save_fundamentals(fund_data)
                save_history_context(fetch_history_context(focus))   # refresh 2yr context weekly
            # Fetch and inject delivery % data into stock latest dicts
            try:
                delivery_data = fetch_delivery(focus)
                save_delivery(delivery_data)
                merged_anal = inject_delivery(merged_anal, delivery_data)
                save_stock_data(merged_anal)
            except Exception as e:
                print(f"[delivery] fetch failed (non-fatal): {e}")
                record_issue("delivery", "NSE bhavcopy", str(e), session)

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

            # ── Forward-test earlier dry calls against the real price since ────
            # This is the analysis phase's actual LEARNING: each past "I would
            # BUY/SELL here" is scored days later against what happened, seeding
            # pattern reliability before a single paper trade is placed.
            decisions, patterns, _ = evaluate_dry_decisions(decisions, sd, patterns)

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
        if not fresh:
            record_issue("data_fetch", "focus stocks", "all price sources returned no data — using last-known", session)
        merged = merge_stock_data(load_stock_data(), fresh)
        # At the 3:15 square-off, stamp the live price as the 3:15 reference so the
        # intraday exit books THIS price even if the close runs a few minutes late.
        if session == "intraday_close":
            for _t, _e in merged.items():
                _lat = _e.get("latest") if isinstance(_e, dict) else None
                if _lat and _lat.get("price_is_live") and _lat.get("current_price"):
                    _lat["price_315"] = _lat["current_price"]
        sector_scores = compute_sector_scores(merged)
        save_sector_scores(sector_scores)
        merged = inject_sector_momentum(merged, sector_scores)
        fund   = load_fundamentals()
        merged = _inject_fund_context(merged, fund)
        merged = _inject_history_context(merged, load_history_context())
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
                record_issue("delivery", "NSE bhavcopy", str(e), session)

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
            book, patterns = morning_session(book, tradeable_opinions, patterns, market_health=market_health, stock_data=merged)
        elif session in ("midday", "afternoon"):
            book, patterns = midday_session(book, tradeable_opinions, merged, patterns, market_health=market_health)
        elif session == "intraday_close":
            # 3:15 square-off: close ONLY intraday positions at the ~3:15 price.
            # Opens nothing new, leaves swing positions to ride to stop/target, and
            # does NOT advance the day (preclose still owns the rollover).
            book, patterns = intraday_close_session(book, merged, patterns)
        elif session == "preclose":
            book, patterns = preclose_session(book, tradeable_opinions, merged, patterns, market_health=market_health)

        # Update win rate attribution after each session's trade activity
        if session == "preclose":
            patterns = update_attribution(patterns, book.get("closed_trades", []))
            # Finish forward-testing any dry ANALYSE calls from late in the
            # analysis phase that were still inside their proving window.
            decisions, patterns, _ = evaluate_dry_decisions(decisions, merged, patterns)

        save_patterns(patterns)
        save_decisions(decisions)
        save_book(book)

        stats = compute_stats(book)
        state["paper_trade_stats"] = stats
        print(f"\n[stats] Trades={stats['total']} | WR={stats['win_rate']*100:.1f}% "
              f"| PnL=₹{stats['total_pnl']:+,.0f} | Exp=₹{stats['expectancy']:+,.0f}/trade")

        if session == "preclose":
            state = advance_session(state, session)

            # ── LLM Coach — end-of-day learning session ───────────────────────
            # Runs after all trades are settled. Reviews outcomes, answers queued
            # questions, and generates structural suggestions. Non-blocking.
            try:
                run_coach(book.get("closed_trades", []), patterns, market_health)
                print("[coach] Coach session complete")
            except Exception as e:
                print(f"[coach] Session failed (non-fatal): {e}")
                record_issue("coach", "Gemini coach", str(e), session)

            # ── Dynamic focus refresh: promote/demote stocks ──────────────────
            # _maybe_refresh_focus mutates `state` in place (and persists if it
            # changed focus). We must NOT reload from disk here: advance_session()
            # above bumped the day only in memory, and a disk reload would discard
            # that increment whenever no focus change occurred. Just re-read focus
            # from the already-mutated state.
            _maybe_refresh_focus(state, merged, patterns, nd, fund, book, market_health)
            focus = state.get("focus_stocks", focus)

            # ── Refresh fundamentals + 2yr history weekly (or self-heal if empty) ──
            if state.get("day", 1) % 5 == 0 or not load_fundamentals():
                fund_data = fetch_fundamentals(focus)
                if fund_data:
                    save_fundamentals(fund_data)
                save_history_context(fetch_history_context(focus))

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

            # Check the CURRENT phase (state), not the stale local `phase` captured
            # at run() start — set_phase above may have just flipped it to alerting.
            if state.get("phase") == "alerting":
                # Keep running indefinitely — never stop after alerting
                state = add_brain_note(state, "Continuing live paper trading post-alert (perpetual mode)")

                # ── Drift monitor: validation must stay earned ─────────────────
                # The 60% gate proved the edge ONCE; markets change. Re-check the
                # rolling win-rate over the most recent trades every preclose. If
                # it has decayed below the floor, demote back to paper_trading —
                # alert_sent=False automatically flips recommendations back to the
                # honest 'practice' framing until the edge is re-proven through
                # the full validation gate again.
                from agent.config import DRIFT_WINDOW_TRADES, DRIFT_WR_FLOOR
                recent = book.get("closed_trades", [])[-DRIFT_WINDOW_TRADES:]
                if len(recent) >= DRIFT_WINDOW_TRADES:
                    wins_r = sum(1 for t in recent
                                 if t.get("won", t.get("pnl", 0) > 0))
                    roll_wr = wins_r / len(recent)
                    if roll_wr < DRIFT_WR_FLOOR:
                        state["alert_sent"] = False
                        note = (f"DRIFT: rolling win-rate {roll_wr*100:.0f}% over last "
                                f"{len(recent)} trades fell below {DRIFT_WR_FLOOR*100:.0f}% — "
                                f"validation revoked, back to paper trading until re-proven")
                        state = add_brain_note(state, note)
                        state = set_phase(state, "paper_trading", note)
                        record_issue("strategy_drift", "rolling win-rate",
                                     note, session)
                        print(f"[drift] {note}")

        save_state(state)

    return state


def _tick_background_cohorts(state: dict, session: str, skip_fetch: bool = False) -> None:
    """
    Perpetual parallel exploration pipeline.

    Design: the moment batch N completes its first day of exploration, batch N+1
    starts immediately. Multiple batches can be in-flight simultaneously.
    Each batch independently explores the full NSE universe for EXPLORATION_DAYS,
    then scores and marks itself ready.  When a focus refresh happens, the most
    recently completed batch's candidates feed the promotion pool.

    skip_fetch=True is used during exploration, where the primary run has ALREADY
    fetched & saved the full universe this session — so batches reuse that data
    with zero extra network cost (this is what lets batches run from day 2 of
    exploration without duplicating fetches).

    State stores batches as a list:  state["background_batches"] = [
        {"id": 1, "day": 5, "start_date": "...", "ready": True,  "candidates": [...]},
        {"id": 2, "day": 2, "start_date": "...", "ready": False, "candidates": []},
    ]
    """
    if session != "preclose":
        return

    # Only tick batches on a real trading day, and only ONCE per day — mirrors the
    # main day-counter so duplicate/overlapping precloses or weekend/holiday runs
    # don't make batches "age" multiple steps or on non-trading days.
    from agent.trading_calendar import is_trading_day
    today_str = ist_today().isoformat()
    if not is_trading_day(ist_today()):
        print("[cohort] Not a trading day — batches not advanced.")
        return
    if state.get("cohort_last_tick_date") == today_str:
        print("[cohort] Batches already advanced today — skipping (duplicate preclose).")
        return
    state["cohort_last_tick_date"] = today_str

    focus     = state.get("focus_stocks", [])
    batches   = state.setdefault("background_batches", [])

    # ── Decide whether to start a new batch ────────────────────────────────────
    # Goal: keep a staggered pipeline of in-flight batches so the competition pool
    # is continuously refreshed (a new batch's candidates can challenge incumbents
    # every few days). Spawn when the newest batch has passed day 1 (normal
    # staggering) OR when the in-flight pool has fallen below the cap — the latter
    # is SELF-HEALING: if a state hiccup or run gap ever loses batches, the pool
    # refills itself instead of leaving a permanent gap. (Prior logic could leave
    # the pool under-filled after a missed/duplicate run; this repairs it.)
    active_count = sum(1 for b in batches if not b.get("ready"))
    newest_day   = batches[-1].get("day", 0) if batches else 99
    should_start_new = (
        not batches
        or newest_day >= 1                       # normal staggering
        or active_count < CONCURRENT_BATCH_CAP    # self-heal: top the pool back up
    )
    # Never spawn a second brand-new batch in the same tick (newest still day 0)
    # unless we're below the cap and genuinely need to refill.
    if batches and newest_day == 0 and active_count >= CONCURRENT_BATCH_CAP:
        should_start_new = False
    # Hard cap on concurrent in-flight batches to keep each preclose under the
    # Actions timeout (batches reuse already-fetched data, so cost is scoring CPU).
    if active_count >= CONCURRENT_BATCH_CAP:
        should_start_new = False

    if should_start_new:
        new_id = (batches[-1]["id"] + 1) if batches else 1
        batches.append({
            "id":         new_id,
            "day":        0,
            "start_date": ist_today().isoformat(),
            "ready":      False,
            "candidates": [],
        })
        print(f"[cohort] Started background batch #{new_id}")

    active_batches = [b for b in batches if not b.get("ready")]
    if not active_batches:
        # Nothing to tick — still refresh the legacy pointer below
        state["background_batches"] = batches[-(CONCURRENT_BATCH_CAP + 3):]
        ready = [b for b in batches if b.get("ready")]
        state["background_cohort"] = ready[-1] if ready else None
        return

    # ── Fetch the full universe ONCE and share it across all active batches ────
    # All batches explore the same data; they differ only by their own day counter
    # and scoring window. Fetching per-batch would mean 99 stocks × N batches of
    # HTTP + intraday calls every preclose — enough to blow the Actions timeout.
    # During exploration (skip_fetch) the primary run already saved fresh universe
    # data this session, so we reuse it with zero extra network cost.
    fetch_ok = True
    if skip_fetch:
        print("[cohort] Reusing this session's universe data (exploration) — no extra fetch")
    else:
        try:
            fresh_bg  = fetch_stock_data(NSE_UNIVERSE, session="preclose")
            existing  = load_stock_data()
            merged_bg = merge_stock_data(existing, fresh_bg)
            for ticker in NSE_UNIVERSE:
                if ticker not in focus and ticker in fresh_bg:
                    existing[ticker] = merged_bg[ticker]
            save_stock_data(existing)
        except Exception as e:
            print(f"[cohort] Shared universe fetch error (non-fatal): {e}")
            fetch_ok = False

    # ── Advance every non-ready batch's day counter ───────────────────────────
    for batch in active_batches:
        batch_day = batch.get("day", 0)
        batch_id  = batch.get("id", "?")
        print(f"[cohort] Batch #{batch_id} exploration day {batch_day}/{EXPLORATION_DAYS}"
              f"{' (fetch failed)' if not fetch_ok else ''}")

        batch_day += 1
        batch["day"] = batch_day

        if batch_day >= EXPLORATION_DAYS:
            try:
                sd   = load_stock_data()
                nd   = load_news()
                sent = {t: v.get("latest", {}) for t, v in nd.items()}
                top  = select_focus_stocks(sd, sent, FOCUS_STOCK_COUNT * 2, load_fundamentals())
                batch["candidates"]  = [t for t, _ in top]
                batch["scored_date"] = ist_today().isoformat()
                batch["ready"]       = True
                print(f"[cohort] Batch #{batch_id} ready — {len(top)} candidates scored")
                state = add_brain_note(state, f"Cohort batch #{batch_id} complete — {len(top)} candidates ready")
            except Exception as e:
                print(f"[cohort] Batch #{batch_id} scoring error: {e}")

    # Retain active + a few recently-graduated batches so their candidates still
    # feed the competition pool before being rotated out.
    state["background_batches"] = batches[-(CONCURRENT_BATCH_CAP + 3):]
    # Legacy compat: expose most-recent ready batch as background_cohort
    ready = [b for b in batches if b.get("ready")]
    state["background_cohort"] = ready[-1] if ready else None


def _maybe_refresh_focus(state, stock_data, patterns, news_data, fund, book, market_health):
    """Evaluate whether any focus stocks should be promoted/demoted. Runs every preclose."""
    focus = state.get("focus_stocks", [])
    if not focus:
        return

    # Only refresh after first 3 paper trading days to have enough data.
    # Phase boundaries come from config (EXPLORATION_DAYS + ANALYSIS_DAYS), not
    # hardcoded values, so this stays correct when those are tuned.
    paper_days = state.get("day", 1) - (EXPLORATION_DAYS + ANALYSIS_DAYS)
    if paper_days < 3:
        return

    ranked = rank_focus_stocks(focus, stock_data, patterns, news_data, fund, book, market_health)

    # Update watchlist scores for non-focus stocks
    wl = load_watchlist_signals()
    wl = update_watchlist_signals(wl, stock_data, patterns, news_data, fund, focus)
    save_watchlist_signals(wl)

    # ── Competition pool: ALL ready batches, not just the latest ────────────────
    # Each background batch is an independent full-universe scan. Pooling every
    # ready batch's candidates means the focus list competes against the combined
    # findings of multiple staggered scans — so a genuinely strong stock that shows
    # up across batches gets surfaced, and incumbents are continually challenged.
    batches = state.get("background_batches", [])
    pool_counts = {}
    for b in batches:
        if b.get("ready"):
            for t in b.get("candidates", []):
                pool_counts[t] = pool_counts.get(t, 0) + 1
    # Rank the pool by how many independent batches surfaced each stock (consensus
    # = stronger signal), preserving order for ties.
    cohort_candidates = [t for t, _ in sorted(pool_counts.items(),
                                              key=lambda kv: -kv[1])]
    # Back-compat: also fold in the legacy single-cohort pointer if present.
    legacy = state.get("background_cohort", {}) or {}
    if legacy.get("ready"):
        for t in legacy.get("candidates", []):
            if t not in cohort_candidates:
                cohort_candidates.append(t)
    if cohort_candidates:
        n_ready = sum(1 for b in batches if b.get("ready"))
        print(f"[cohort] Competition pool: {len(cohort_candidates)} candidates from "
              f"{n_ready} ready batch(es) for focus refresh")

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
        # Honest, visible competition log: record every swap + why (with scores).
        try:
            from agent.focus_competition import record_focus_changes
            scores = {r["ticker"]: r.get("composite_score", 0) for r in ranked}
            record_focus_changes(promoted, demoted, scores=scores, driver="competition")
        except Exception as e:
            print(f"[competition] could not log focus changes (non-fatal): {e}")
        # Fetch fundamentals + 2yr history for any newly promoted stocks
        if promoted:
            new_fund = fetch_fundamentals(promoted)
            save_fundamentals(new_fund)
            save_history_context(fetch_history_context(promoted))
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
        # Fundamental strength so the brain can classify a LONG-TERM style
        # consistently with how recommendations classify the horizon.
        try:
            from agent.fundamentals_fetcher import score_fundamentals
            d["fund_strength"] = score_fundamentals(f) if f else 0
        except Exception:
            d["fund_strength"] = 0
        d["roe"]            = f.get("roe") or 0
        d["roce"]           = f.get("roce") or 0
        d["debt_equity"]    = f.get("debt_equity")
        d["revenue_growth_pct"] = f.get("revenue_growth_pct") or 0
    return stock_data


def _inject_history_context(stock_data: dict, history_ctx: dict) -> dict:
    """
    Flatten the 2-year history regime/personality fields into each stock's
    latest{} so the brain can read them via d.get(...) without a signature change.
    """
    for ticker, entry in stock_data.items():
        ctx = history_ctx.get(ticker)
        if not ctx or "latest" not in entry:
            continue
        d  = entry["latest"]
        rg = ctx.get("regime", {})
        pr = ctx.get("personality", {})
        bh = ctx.get("behaviour", {})
        d["hist_long_trend"]          = rg.get("long_trend")
        d["hist_pct_of_52w_range"]    = rg.get("pct_of_52w_range")
        d["hist_vol_state"]           = rg.get("vol_state")
        d["hist_drawdown_from_high"]  = rg.get("drawdown_from_high")
        d["hist_personality"]         = pr.get("type")
        d["hist_ret_6m"]              = bh.get("ret_6m")   # higher-timeframe momentum
        d["hist_ret_3m"]              = bh.get("ret_3m")
        # Promote real 52w levels from deep history (more accurate than 120d)
        if rg.get("week52_high"):
            d["week52_high"] = rg["week52_high"]
        if rg.get("week52_low"):
            d["week52_low"] = rg["week52_low"]
    return stock_data


def _refresh_outputs(state: dict, market_health: dict, session: str, read_only: bool = False) -> None:
    """Rebuild recommendations + dashboard. When read_only=True (test mode) it
    does NOT regenerate/persist recommendations, changelog, report, or extend the
    history foundation — it only rebuilds the dashboard from EXISTING data, so a
    test run truly has zero side effects."""
    try:
        sd      = load_stock_data()
        # Layer today's fresh price onto the permanent 2yr history foundation
        # (extend-only — no re-fetch). Skipped in read-only/test mode.
        if not read_only:
            try:
                extend_foundation(sd)
            except Exception as e:
                print(f"[history] foundation extend failed (non-fatal): {e}")
        book    = load_book()
        pats    = load_patterns()
        decs    = load_decisions()
        nd      = load_news()
        fund    = load_fundamentals()
        sectors = load_sector_scores()
        clog    = load_changelog()

        # Regenerate recommendations EVERY session once we have focus stocks.
        # Recs use live prices, so a morning run must produce fresh morning recs —
        # otherwise the dashboard would show yesterday's preclose recs (stale, and
        # useless for intraday calls which expire the same day). During exploration
        # there are no focus stocks yet, so nothing is generated.
        # In read-only/test mode we skip regeneration entirely and just load existing.
        focus = state.get("focus_stocks", [])
        if read_only:
            recs = load_recommendations()
        elif focus or not os.path.exists("brain/recommendations.json"):
            prev_recs = load_recommendations()
            recs = generate_recommendations(state, sd, pats, nd, book, market_health, fund, session=session)
            # Compute and persist what changed vs previous recommendations
            changes = compute_changes(prev_recs, recs)
            if changes:
                save_changelog(changes)
                clog = load_changelog()
                for c in changes:
                    print(f"[changelog] {c['type'].upper()} {c['nse_code']}: {c['detail']}")
            # Full strategy report only needs refreshing at preclose (heavier write)
            if session == "preclose":
                generate_report(state, sd, pats, nd, book)
                # ── Snapshot today's recs for the TRACK RECORD ─────────────────
                # One RECOMMEND decision per ticker per day (preclose = the day's
                # settled view). These get forward-tested against the real price by
                # evaluate_dry_decisions, so the dashboard can show honestly how
                # past recommendations actually played out.
                existing_keys = {(x.get("ticker"), x.get("date"))
                                 for x in decs if x.get("action") == "RECOMMEND"}
                today_iso = ist_today().isoformat()
                added = 0
                for r in recs:
                    key = (r.get("ticker"), today_iso)
                    if key in existing_keys or not r.get("ticker"):
                        continue
                    entry_mid = round(((r.get("entry_low") or 0) + (r.get("entry_high") or 0)) / 2, 2)
                    decs.append({
                        "timestamp": ist_now().isoformat(),
                        "date":      today_iso,
                        "ticker":    r["ticker"],
                        "session":   session,
                        "action":    "RECOMMEND",
                        "signal":    r.get("direction_short") or r.get("signal"),
                        "confidence": r.get("confidence"),
                        "entry":     entry_mid or r.get("cmp"),
                        "stop_loss": r.get("stop_loss"),
                        "target":    r.get("target1"),
                        "reason":    f"rec snapshot ({r.get('trade_type_key','')})",
                        "patterns":  r.get("patterns_seen", []) or [],
                    })
                    added += 1
                if added:
                    save_decisions(decs)
                    decs = load_decisions()
        else:
            recs = load_recommendations()

        # Always rebuild ranking (runs fast, no API calls)
        ranked = rank_focus_stocks(focus, sd, pats, nd, fund, book, market_health) if focus else []

        # ── Manage the USER's real positions every session ─────────────────────
        # Mark-to-market with the live price, trail their stops with the same
        # rules as paper trades, and flag EXIT NOW when stop/target is hit.
        # Never auto-closes (the tool can't know their real fill) — the user
        # confirms via the 'My Trades' workflow. Read-only in test mode.
        from agent.my_trades import manage_positions
        my_pos = manage_positions(sd, save=not read_only)

        attr_summary = aggregate_attribution(pats)
        coach_mem    = load_coach_memory()
        from agent.run_health import load_run_health
        build_dashboard(state, sd, book, pats, decs, nd, market_health,
                        recs, fund, ranked, sectors, clog, attr_summary,
                        coach_memory=coach_mem, run_health=load_run_health(),
                        my_positions=my_pos)

    except Exception as e:
        import traceback
        print(f"[output] Error: {e}")
        traceback.print_exc()
        record_issue("dashboard", "refresh outputs", str(e), session)


def _mark_session_done(state: dict, session: str) -> None:
    """Record that `session` finished its real work today, so a later duplicate or
    backup trigger for the same session no-ops. Keeps only today's marks (auto-
    resets each new day)."""
    today = ist_today().isoformat()
    done = state.get("sessions_done", {})
    # Drop any marks from previous days, then set today's.
    done = {s: d for s, d in done.items() if d == today}
    done[session] = today
    state["sessions_done"] = done


def _append_log(state: dict, session: str, closed_day: str = "") -> None:
    from agent.trading_calendar import ist_now
    now_ist = ist_now()

    book   = load_book()
    stats  = compute_stats(book)
    open_n = len(book.get("open_positions", []))
    phase_label = {"exploration": "Exploration", "analysis": "Analysis",
                   "paper_trading": "Paper-trading", "alerting": "Live"}.get(state["phase"], state["phase"])

    # A brief human-readable summary of what this run actually did.
    # "day N" = the Nth TRADING day of the CURRENT phase (weekends/holidays don't
    # count), not a calendar day — wording kept explicit to avoid confusion.
    # state['day'] is the GLOBAL trading-day counter, so subtract the days spent in
    # earlier phases to get a phase-relative number (else "Analysis trading-day 8").
    from agent.config import EXPLORATION_DAYS, ANALYSIS_DAYS
    _phase_offset = {"exploration": 0,
                     "analysis": EXPLORATION_DAYS,
                     "paper_trading": EXPLORATION_DAYS + ANALYSIS_DAYS,
                     "alerting": EXPLORATION_DAYS + ANALYSIS_DAYS}.get(state["phase"], 0)
    phase_day = max(1, state["day"] - _phase_offset)
    recs = load_recommendations()
    mood = load_market_health().get("market_mood", "?")
    if closed_day:
        # Market is closed (weekend / NSE holiday): be explicit, and do NOT imply a
        # trading day happened. The day counter is untouched on closed days.
        summary = (f"{session.title()} — NSE closed ({closed_day}); dashboard refreshed, "
                   f"no trading, day unchanged.")
    elif session == "preopen":
        summary = f"Pre-open sweep — macro mood {mood}; dashboard refreshed (no trading)."
    elif session == "intraday_close":
        summary = (f"Intraday square-off (3:15) · {phase_label} trading-day {phase_day} · "
                   f"{open_n} open · {stats['total']} closed ({stats['win_rate']*100:.0f}% WR).")
    elif session == "test":
        summary = "Test dry-run — dashboard rebuilt, no trading, day unchanged."
    else:
        summary = (f"{session.title()} run · {phase_label} trading-day {phase_day} · "
                   f"{open_n} open · {stats['total']} closed ({stats['win_rate']*100:.0f}% WR) · "
                   f"{len(recs)} live rec(s).")

    entry = {
        "date":         ist_today().isoformat(),
        "triggered_at": now_ist.strftime("%Y-%m-%d %H:%M IST"),  # when the run fired
        "session":      session,
        "phase":        state["phase"],
        "day":          state["day"],
        "stats":        stats,
        "open":         open_n,
        "recs":         len(recs),
        "summary":      summary,
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
    log = log[-750:]   # ~3 years of run-activity history (display feed, not learning)
    with open(DAILY_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


if __name__ == "__main__":
    run()
