"""
Lifecycle simulation harness — drives run() through ~30 trading days in an
isolated sandbox with mocked network, to catch state-machine / phase-transition
bugs that only emerge over time. NOT shipped to Actions; a dev test only.

Run: python -m scripts.sim_lifecycle
"""
import os, sys, shutil, tempfile, json, io, contextlib
from datetime import date
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    sb = tempfile.mkdtemp(prefix="nse_sim_")
    shutil.copytree(os.path.join(REPO, "brain"), os.path.join(sb, "brain"))
    os.makedirs(os.path.join(sb, "docs"), exist_ok=True)
    os.chdir(sb)
    sys.path.insert(0, REPO)

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
    import agent.config as C
    import agent.data_fetcher as DF
    import agent.main as M
    SMALL = ["INFY.NS", "TCS.NS", "RELIANCE.NS", "SBIN.NS", "ITC.NS", "LT.NS", "ICICIBANK.NS"]
    C.NSE_UNIVERSE = SMALL
    M.NSE_UNIVERSE = SMALL

    def fake_fetch(tickers, session="morning"):
        out = {}
        for t in tickers:
            rng = np.random.RandomState(abs(hash(t + session)) % 99999)
            c = 100 + rng.rand() * 500
            out[t] = {"ticker": t, "fetched_at": "now", "session": session,
                      "latest": {"close": c, "rsi": 50, "macd_hist": 0.2, "ema_short": c*1.01,
                                 "ema_long": c*1.005, "ema_trend": c*0.99, "atr": c*0.02,
                                 "atr_pct": 2.0, "vol_rel": 1.3, "bb_pct": 0.4, "open": c*0.99,
                                 "high": c*1.02, "low": c*0.98, "volume": 1e6, "current_price": c},
                      "daily": {}, "price_history_60d": [c]*60, "volume_history_20d": [1e6]*20,
                      "intraday_candles": [], "prev_bar": {"close": c*0.99, "open": c*0.98},
                      "prev2_bar": {"close": c*0.98}}
        return out

    DF.fetch_stock_data = fake_fetch; M.fetch_stock_data = fake_fetch
    DF._download_daily = lambda t, start, end: pd.DataFrame()
    DF._warm_nse_session = lambda: True
    import agent.news_fetcher as NF
    NF.fetch_news = lambda tk: {t: {"date": "now", "count": 1, "score": 0.1, "headlines": ["x"]} for t in tk}
    M.fetch_news = NF.fetch_news
    import agent.fundamentals_fetcher as FF
    FF.fetch_fundamentals = lambda tk: {t: {"roe": 18, "roce": 16, "debt_equity": 0.3, "pe_ratio": 22,
                                            "revenue_growth_pct": 10, "market_cap_cr": 50000} for t in tk}
    M.fetch_fundamentals = FF.fetch_fundamentals
    import agent.history_engine as HE
    HE.refresh_universe_history = lambda tk, **k: HE.load_history_context()
    HE.fetch_history_context = lambda tk, **k: {}
    M.refresh_universe_history = HE.refresh_universe_history
    M.fetch_history_context = HE.fetch_history_context
    M.fetch_delivery = lambda tk, lookback_days=5: {}
    import agent.macro_sentiment as MS
    MS._fetch_macro_feeds = lambda: []; MS._fetch_global_cues = lambda: {}; MS._llm_summary = lambda *a, **k: ""
    import agent.llm_coach as LC
    LC._call_gemini = lambda *a, **k: None

    errors, plog = [], []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for day in range(30):
            for s in ["preopen", "morning", "midday", "afternoon", "preclose"]:
                os.environ["SESSION"] = s
                try:
                    M.run()
                except Exception as e:
                    import traceback
                    errors.append((day, s, repr(e), traceback.format_exc()[-400:]))
            stt = json.load(open("brain/state.json"))
            plog.append((stt["day"], stt["phase"]))

    os.chdir(REPO)
    shutil.rmtree(sb, ignore_errors=True)

    print("runs:", 30 * 5, "| errors:", len(errors))
    for e in errors[:10]:
        print("  ERROR day", e[0], e[1], "->", e[2])
        print("   ", e[3].replace("\n", " | ")[-300:])
    print("phases reached:", sorted(set(p for _, p in plog)))
    print("progression (day7, day20, final):", plog[6], plog[19], plog[-1])
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
