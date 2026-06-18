"""
Main orchestrator — runs 3x per trading day via GitHub Actions.
SESSION env var: morning | midday | preclose
"""

import json
import os
from datetime import date

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


def run():
    session = os.environ.get("SESSION", "morning").lower()
    print(f"\n{'='*60}")
    print(f"NSE AI Trader  {date.today()}  session={session}")
    print(f"{'='*60}\n")

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
        save_stock_data(merge_stock_data(load_stock_data(), fresh))

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
        else:
            state = advance_session(state, session) if session == "midday" else state

        save_state(state)

    # ── ANALYSIS ───────────────────────────────────────────────────────────────
    elif phase == "analysis":
        fresh = fetch_stock_data(focus, session=session)
        save_stock_data(merge_stock_data(load_stock_data(), fresh))

        if session == "preclose":
            news = fetch_news(focus)
            save_news(news)
            # Refresh fundamentals weekly (every 5 preclose sessions in analysis)
            if state.get("day", 1) % 5 == 0:
                fund_data = fetch_fundamentals(focus)
                save_fundamentals(fund_data)

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

            if state["day"] > EXPLORATION_DAYS + ANALYSIS_DAYS:
                state = set_phase(state, "paper_trading", "Analysis done — starting paper trades")

        save_state(state)

    # ── PAPER TRADING ──────────────────────────────────────────────────────────
    elif phase in ("paper_trading", "alerting"):
        fresh  = fetch_stock_data(focus, session=session)
        merged = merge_stock_data(load_stock_data(), fresh)
        save_stock_data(merged)

        if session == "preclose":
            news = fetch_news(focus)
            save_news(news)

        patterns  = load_patterns()
        decisions = load_decisions()
        nd        = load_news()
        book      = load_book()

        # Build analyst opinions
        opinions = []
        for ticker in focus:
            sent = nd.get(ticker, {}).get("latest", {})
            op   = analyse_stock(ticker, merged.get(ticker, {}), patterns, sent, session)
            opinions.append(op)
            if op["signal"] != "WATCH":
                decisions = record_decision(decisions, op, op["signal"], f"paper {session}")

        # Gate trading on market health
        if not market_health.get("trade_allowed", True):
            print("[paper] Market conditions unfavourable — skipping new trades this session")
            tradeable_opinions = []
        else:
            tradeable_opinions = opinions

        # Session trading
        if session == "morning":
            book, patterns = morning_session(book, tradeable_opinions, patterns)
        elif session == "midday":
            book, patterns = midday_session(book, tradeable_opinions, merged, patterns)
        elif session == "preclose":
            book, patterns = preclose_session(book, tradeable_opinions, merged, patterns)

        save_patterns(patterns)
        save_decisions(decisions)
        save_book(book)

        stats = compute_stats(book)
        state["paper_trade_stats"] = stats
        print(f"\n[stats] Trades={stats['total']} | WR={stats['win_rate']*100:.1f}% "
              f"| PnL=₹{stats['total_pnl']:+,.0f} | Exp=₹{stats['expectancy']:+,.0f}/trade")

        if session == "preclose":
            state = advance_session(state, session)
            if is_ready_to_alert(stats) and not state.get("alert_sent"):
                state["alert_sent"] = True
                state = add_brain_note(
                    state,
                    f"ALERT: Win rate {stats['win_rate']*100:.1f}% "
                    f"over {stats['total']} trades — user recommendations ready"
                )
                state = set_phase(state, "alerting")
            if phase == "alerting":
                state = set_phase(state, "paper_trading", "Continuing post-alert")

        save_state(state)

    # ── Always: generate recommendations + rebuild dashboard ──────────────────
    _refresh_outputs(state, market_health, session)
    _append_log(state, session)
    print(f"\n[done] {session} complete. Phase={state['phase']} Day={state['day']}\n")


def _refresh_outputs(state: dict, market_health: dict, session: str) -> None:
    try:
        sd   = load_stock_data()
        book = load_book()
        pats = load_patterns()
        decs = load_decisions()
        nd   = load_news()

        # Regenerate recommendations every preclose session
        if session == "preclose" or not os.path.exists("brain/recommendations.json"):
            fund = load_fundamentals()
            recs = generate_recommendations(state, sd, pats, nd, book, market_health, fund)
            generate_report(state, sd, pats, nd, book)
        else:
            recs = load_recommendations()

        build_dashboard(state, sd, book, pats, decs, nd, market_health, recs, fund)

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
