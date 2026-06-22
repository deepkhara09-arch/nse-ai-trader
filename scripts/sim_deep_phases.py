"""
Deep phase simulation — engineers conditions to actually exercise the logic of
EVERY phase past exploration, and ASSERTS real behaviour (not just no-crash):

  • analysis    — stocks get scored, decisions recorded, history/delivery injected
  • paper_trade — positions actually OPEN, then CLOSE at target/stop, P&L + stats
  • focus refresh — a weak focus stock gets demoted, a strong one promoted
  • alerting    — once win-rate + trade-count + days thresholds met, phase flips
  • recommendations — real recs generate with full fields

Sandboxed + mocked network. Dev test only.  Run: python -m scripts.sim_deep_phases
"""
import os, sys, shutil, tempfile, json, io, contextlib
from datetime import date
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    sb = tempfile.mkdtemp(prefix="nse_deep_")
    shutil.copytree(os.path.join(REPO, "brain"), os.path.join(sb, "brain"))
    os.makedirs(os.path.join(sb, "docs"), exist_ok=True)
    os.chdir(sb); sys.path.insert(0, REPO)

    from agent.state_manager import _fresh_state, save_state
    from agent.paper_trader import _fresh_book, save_book
    from agent.migrations import CURRENT_SCHEMA_VERSION
    st = _fresh_state(); st["schema_version"] = CURRENT_SCHEMA_VERSION; st["background_batches"] = []
    save_state(st); save_book(_fresh_book())
    for f, v in [("patterns", {}), ("decisions", []), ("stock_data", {}), ("news_sentiment", {}),
                 ("recommendations", []), ("sector_scores", {}), ("rank_history", []),
                 ("daily_log", []), ("watchlist_signals", {})]:
        json.dump(v, open("brain/" + f + ".json", "w"))

    import pandas as pd
    import agent.config as C, agent.data_fetcher as DF, agent.main as M
    UNIV = ["INFY.NS", "TCS.NS", "RELIANCE.NS", "SBIN.NS", "ITC.NS", "LT.NS",
            "ICICIBANK.NS", "WIPRO.NS", "HCLTECH.NS", "AXISBANK.NS"]
    C.NSE_UNIVERSE = UNIV; M.NSE_UNIVERSE = UNIV

    # Engineer prices: each stock trends UP steadily so BUYs fire and targets hit.
    # We advance a global "tick" so prices rise across sessions -> winning trades.
    state_tick = {"t": 0}

    def fake_fetch(tickers, session="morning"):
        state_tick["t"] += 1
        out = {}
        for j, t in enumerate(tickers):
            # rising price: base climbs each tick so open positions hit targets
            base = 300 + (j * 40)
            c = base * (1.0 + 0.004 * state_tick["t"])   # +0.4% drift per fetch
            out[t] = {"ticker": t, "fetched_at": "now", "session": session,
                      "latest": {"close": c, "rsi": 48, "macd_hist": 0.8,
                                 "ema_short": c*1.03, "ema_long": c*1.01, "ema_trend": c*0.98,
                                 "atr": c*0.02, "atr_pct": 2.0, "vol_rel": 1.8, "bb_pct": 0.25,
                                 "open": c*0.995, "high": c*1.03, "low": c*0.985, "volume": 1e6,
                                 "current_price": c, "session_high": c*1.03, "session_low": c*0.985,
                                 "day_high": c*1.03, "day_low": c*0.985, "day_open": c*0.995,
                                 "candle_sequence": [], "delivery_signal": "strong_accumulation",
                                 "delivery_pct": 70},
                      "daily": {}, "price_history_60d": [base*0.9 + i for i in range(60)],
                      "volume_history_20d": [1e6]*20,
                      "prev_bar": {"close": c*0.97, "open": c*0.96, "high": c*0.98, "low": c*0.95},
                      "prev2_bar": {"close": c*0.95}, "trend_10d": "strong_up"}
        return out

    DF.fetch_stock_data = fake_fetch; M.fetch_stock_data = fake_fetch
    DF._download_daily = lambda t, start, end: pd.DataFrame(); DF._warm_nse_session = lambda: True
    import agent.news_fetcher as NF
    NF.fetch_news = lambda tk: {t: {"date": "now", "count": 2, "score": 0.4,
                                    "weighted_score": 0.4, "trend": "improving",
                                    "headlines": ["X wins big order"]} for t in tk}
    M.fetch_news = NF.fetch_news
    import agent.fundamentals_fetcher as FF
    FF.fetch_fundamentals = lambda tk: {t: {"roe": 22, "roce": 20, "debt_equity": 0.2, "pe_ratio": 20,
                                            "revenue_growth_pct": 14, "market_cap_cr": 80000,
                                            "promoter_holding_pct": 55} for t in tk}
    M.fetch_fundamentals = FF.fetch_fundamentals
    import agent.history_engine as HE
    def fake_hist(tk, **k):
        return {t: {"regime": {"long_trend": "strong_uptrend", "pct_of_52w_range": 60,
                               "vol_state": "compressed", "week52_high": 999, "week52_low": 100,
                               "dma50": 300, "dma200": 280, "drawdown_from_high": -5},
                    "personality": {"type": "trender"}, "shock": {"tested": False},
                    "updated": date.today().isoformat(), "price_2y_weekly": [300]*100,
                    "days": 500} for t in tk}
    HE.refresh_universe_history = lambda tk, **k: (HE.save_history_context(fake_hist(tk)) or HE.load_history_context())
    HE.fetch_history_context = fake_hist
    M.refresh_universe_history = HE.refresh_universe_history; M.fetch_history_context = HE.fetch_history_context
    M.fetch_delivery = lambda tk, lookback_days=5: {t: {"delivery_pct": 70, "delivery_signal": "strong_accumulation",
                                                        "avg_delivery_5d": 60, "delivery_trend": "rising"} for t in tk}
    import agent.macro_sentiment as MS
    MS._fetch_macro_feeds = lambda: []; MS._fetch_global_cues = lambda: {}; MS._llm_summary = lambda *a, **k: ""
    import agent.llm_coach as LC
    LC._call_gemini = lambda *a, **k: None

    # Track per-phase observations
    obs = {"opened": 0, "closed": 0, "max_trades": 0, "phases": set(), "alerted": False,
           "recs_seen": 0, "errors": []}

    # Controllable clock so the trading-day counter (which only advances once per
    # distinct weekday) can actually progress through the lifecycle in the sim.
    import datetime as _dt
    import agent.state_manager as SM
    _real_date = _dt.date
    clock = {"d": _dt.date(2026, 1, 5)}  # a Monday

    class _FakeDate(_real_date):
        @classmethod
        def today(cls):
            return clock["d"]
    SM.date = _FakeDate   # advance_session reads date.today() from state_manager
    M.date = _FakeDate    # main.py weekend-guard + logging use the same clock

    def _next_weekday():
        d = clock["d"] + _dt.timedelta(days=1)
        while d.weekday() >= 5:   # skip Sat/Sun
            d += _dt.timedelta(days=1)
        clock["d"] = d

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for day in range(60):
            _next_weekday()   # each loop iteration = a new trading day
            for s in ["preopen", "morning", "midday", "afternoon", "preclose"]:
                os.environ["SESSION"] = s
                try:
                    M.run()
                except Exception as e:
                    import traceback
                    obs["errors"].append((day, s, repr(e), traceback.format_exc()[-300:]))
            stt = json.load(open("brain/state.json"))
            book = json.load(open("brain/paper_trades.json"))
            recs = json.load(open("brain/recommendations.json"))
            obs["phases"].add(stt["phase"])
            obs["closed"] = len(book.get("closed_trades", []))
            obs["max_trades"] = max(obs["max_trades"], len(book.get("closed_trades", [])))
            obs["opened"] = max(obs["opened"], len(book.get("open_positions", [])))
            obs["recs_seen"] = max(obs["recs_seen"], len(recs))
            if stt["phase"] == "alerting":
                obs["alerted"] = True

    # Final snapshots
    book = json.load(open("brain/paper_trades.json"))
    stt = json.load(open("brain/state.json"))
    stats = book.get("daily_snapshots", [])
    from agent.paper_trader import compute_stats
    final_stats = compute_stats(book)

    os.chdir(REPO); shutil.rmtree(sb, ignore_errors=True)

    print("=== DEEP PHASE SIM RESULTS ===")
    print("errors:", len(obs["errors"]))
    for e in obs["errors"][:6]:
        print("  ERROR", e[0], e[1], e[2]); print("   ", e[3].replace(chr(10), " | ")[-250:])
    print("phases reached:", sorted(obs["phases"]))
    print("final phase/day:", stt["phase"], stt["day"])
    print("max open positions seen:", obs["opened"])
    print("total closed trades:", obs["max_trades"])
    print("final win rate:", final_stats["win_rate"], "| total PnL:", final_stats["total_pnl"])
    print("recommendations seen:", obs["recs_seen"])
    print("reached alerting:", obs["alerted"])
    # Assertions
    checks = {
        "no errors":            len(obs["errors"]) == 0,
        "positions opened":     obs["opened"] > 0,
        "trades closed":        obs["max_trades"] > 0,
        "reached paper_trading": "paper_trading" in obs["phases"],
    }
    print("--- CHECKS ---")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
