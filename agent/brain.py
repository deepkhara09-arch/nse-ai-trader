"""
The Brain — self-improving decision engine.

This is the core intelligence of the agent. It:
1. Analyses every stock independently using all available data
2. Generates a structured "analyst opinion" per stock per session
3. Learns from outcomes — patterns that worked get higher weight, failures get penalised
4. Decides autonomously whether to open/close paper trades
5. Computes a confidence score that gates whether it tells the user anything

Think of this as an autonomous analyst that watches charts, reads news,
and gradually builds conviction through evidence, not gut feel.
"""

import json
import os
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from agent.config import (
    BRAIN_DIR, PATTERN_FILE, BRAIN_DECISIONS_FILE,
    EMA_SHORT, EMA_LONG, EMA_TREND,
    BUY_SIGNAL_MIN_SCORE, SELL_SIGNAL_MIN_SCORE, SIGNAL_SCORE_GAP,
    ATR_STOP_MULTIPLIER, ATR_TARGET_MULTIPLIER,
    FLAT_STOP_PCT, FLAT_TARGET_PCT,
    PATTERN_DECAY_RATE, MIN_PATTERN_SAMPLES, CONFIDENCE_FLOOR,
)


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN LIBRARY — the brain's learned knowledge
# ═══════════════════════════════════════════════════════════════════════════════

def load_patterns() -> Dict:
    if os.path.exists(PATTERN_FILE):
        with open(PATTERN_FILE) as f:
            return json.load(f)
    return {}


def save_patterns(data: Dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(PATTERN_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_decisions() -> List:
    if os.path.exists(BRAIN_DECISIONS_FILE):
        with open(BRAIN_DECISIONS_FILE) as f:
            return json.load(f)
    return []


def save_decisions(decisions: List) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    decisions = decisions[-200:]   # keep last 200
    with open(BRAIN_DECISIONS_FILE, "w") as f:
        json.dump(decisions, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN DETECTION — rule-based, computed entirely from OHLCV
# ═══════════════════════════════════════════════════════════════════════════════

def detect_all_patterns(d: dict, prev: dict = None, prev2: dict = None) -> List[str]:
    """
    Detects candlestick + indicator patterns from a stock's latest data.
    Returns list of pattern name strings.
    """
    patterns = []
    rsi       = d.get("rsi", 50)
    macd_hist = d.get("macd_hist", 0)
    bb_pct    = d.get("bb_pct", 0.5)
    vol_rel   = d.get("vol_rel", 1.0)
    ema_short = d.get("ema_short", 1)
    ema_long  = d.get("ema_long",  1)
    ema_trend = d.get("ema_trend", 1)
    close     = d.get("close", 0)
    open_     = d.get("open",  close)
    high      = d.get("high",  close)
    low       = d.get("low",   close)
    atr_pct   = d.get("atr_pct", 2.0)
    body_pct  = d.get("body_pct", 0.5)
    uw_pct    = d.get("upper_wick_pct", 0.1)
    lw_pct    = d.get("lower_wick_pct", 0.1)
    intra_trend = d.get("intraday_trend", "neutral")
    above_vwap  = d.get("above_vwap", None)

    bullish_candle = close > open_
    bearish_candle = close < open_

    # ── Momentum ─────────────────────────────────────────────────────────────
    if rsi < 32 and macd_hist > 0:
        patterns.append("oversold_macd_reversal")
    if rsi > 68 and macd_hist < 0:
        patterns.append("overbought_macd_divergence")
    if 42 <= rsi <= 58 and macd_hist > 0 and ema_short > ema_long:
        patterns.append("momentum_continuation")
    if rsi < 45 and macd_hist > 0 and ema_short < ema_long:
        patterns.append("early_recovery")

    # ── Trend alignment ───────────────────────────────────────────────────────
    if ema_short > ema_long > ema_trend:
        patterns.append("full_bullish_alignment")
    if ema_short < ema_long < ema_trend:
        patterns.append("full_bearish_alignment")

    # ── EMA crossover (requires prev bar) ────────────────────────────────────
    if prev:
        p_short = prev.get("ema_short", ema_short)
        p_long  = prev.get("ema_long",  ema_long)
        if p_short <= p_long and ema_short > ema_long:
            patterns.append("ema_golden_cross")
        if p_short >= p_long and ema_short < ema_long:
            patterns.append("ema_death_cross")
        if prev.get("macd_hist", 0) <= 0 < macd_hist:
            patterns.append("macd_bullish_crossover")
        if prev.get("macd_hist", 0) >= 0 > macd_hist:
            patterns.append("macd_bearish_crossover")

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    if bb_pct < 0.08:
        patterns.append("bb_lower_bounce")
    if bb_pct > 0.92:
        patterns.append("bb_upper_rejection")
    if 0.48 <= bb_pct <= 0.52 and bullish_candle:
        patterns.append("bb_midline_support")
    if atr_pct < 1.2:
        patterns.append("volatility_squeeze")   # often precedes a breakout

    # ── Volume ───────────────────────────────────────────────────────────────
    if vol_rel >= 2.0 and bullish_candle:
        patterns.append("volume_breakout_bullish")
    if vol_rel >= 2.0 and bearish_candle:
        patterns.append("volume_breakdown_bearish")
    if vol_rel < 0.5:
        patterns.append("low_volume_drift")   # unreliable move

    # ── Candlestick shapes ────────────────────────────────────────────────────
    if body_pct < 0.12:
        patterns.append("doji")
    if lw_pct > 0.55 and body_pct < 0.35 and bullish_candle:
        patterns.append("hammer")
    if uw_pct > 0.55 and body_pct < 0.35 and bearish_candle:
        patterns.append("shooting_star")
    if body_pct > 0.75 and bullish_candle:
        patterns.append("strong_bullish_marubozu")
    if body_pct > 0.75 and bearish_candle:
        patterns.append("strong_bearish_marubozu")

    # Dragonfly doji: very long lower wick, tiny upper wick, body at top
    if body_pct < 0.1 and lw_pct > 0.7 and uw_pct < 0.1:
        patterns.append("dragonfly_doji")

    # Gravestone doji: very long upper wick, tiny lower wick, body at bottom
    if body_pct < 0.1 and uw_pct > 0.7 and lw_pct < 0.1:
        patterns.append("gravestone_doji")

    # Spinning top: both wicks significant, small body
    if body_pct < 0.25 and uw_pct > 0.3 and lw_pct > 0.3:
        patterns.append("spinning_top")

    # Inverted hammer (at bottom of downtrend): small body, long upper wick
    if uw_pct > 0.6 and body_pct < 0.25 and lw_pct < 0.1 and bullish_candle:
        patterns.append("inverted_hammer")

    # Volume climax signals
    if vol_rel > 3.0 and rsi < 35:
        patterns.append("volume_climax_bottom")
    if vol_rel > 3.0 and rsi > 65:
        patterns.append("volume_climax_top")

    # ── RSI divergence (requires price history in d) ──────────────────────────
    # Bullish divergence: price makes lower low but RSI makes higher low
    ph = d.get("price_history", [])   # last N closes stored in latest dict
    rh = d.get("rsi_history", [])
    if len(ph) >= 5 and len(rh) >= 5:
        if ph[-1] < ph[-3] and rh[-1] > rh[-3]:
            patterns.append("rsi_bullish_divergence")
        if ph[-1] > ph[-3] and rh[-1] < rh[-3]:
            patterns.append("rsi_bearish_divergence")

    # ── 52-week proximity ─────────────────────────────────────────────────────
    wk52_high = d.get("week52_high", 0)
    wk52_low  = d.get("week52_low",  0)
    wk52_pos  = d.get("week52_position_pct", 50)
    if wk52_high and close > 0:
        if close >= wk52_high * 0.98:
            patterns.append("near_52w_high")        # potential breakout zone
        if close <= wk52_low * 1.03:
            patterns.append("near_52w_low")         # potential reversal zone
        if close > wk52_high * 1.001 and vol_rel > 1.5:
            patterns.append("52w_breakout")         # strong breakout above annual high

    # ── Intraday alignment (from 5-min data) ─────────────────────────────────
    if intra_trend == "bullish" and above_vwap is True:
        patterns.append("intraday_bullish_vwap")
    if intra_trend == "bearish" and above_vwap is False:
        patterns.append("intraday_bearish_vwap")
    if d.get("intraday_vol_surge") and intra_trend == "bullish":
        patterns.append("intraday_vol_surge_up")

    # ── Multi-candle patterns (require prev bar) ──────────────────────────────
    if prev:
        p_close = prev.get("close", close)
        p_open  = prev.get("open",  p_close)
        p_high  = prev.get("high",  p_close)
        p_low   = prev.get("low",   p_close)
        p_body  = abs(p_close - p_open)
        curr_body = abs(close - open_)

        # Bullish engulfing: prev bearish, current bullish body engulfs prev
        if p_close < p_open and close > open_ and open_ < p_close and close > p_open:
            patterns.append("engulfing_bullish")

        # Bearish engulfing
        if p_close > p_open and close < open_ and open_ > p_close and close < p_open:
            patterns.append("engulfing_bearish")

        # Bullish harami: small bullish inside large bearish
        if p_close < p_open and p_body > 0 and close > open_:
            if open_ > p_close and close < p_open and curr_body < p_body * 0.5:
                patterns.append("bullish_harami")

        # Bearish harami
        if p_close > p_open and p_body > 0 and close < open_:
            if open_ < p_close and close > p_open and curr_body < p_body * 0.5:
                patterns.append("bearish_harami")

        # Tweezer bottom: both lows nearly equal, bullish reversal
        if abs(low - p_low) / (p_low + 1e-9) < 0.002 and close > open_ and p_close < p_open:
            patterns.append("tweezer_bottom")

        # Tweezer top
        if abs(high - p_high) / (p_high + 1e-9) < 0.002 and close < open_ and p_close > p_open:
            patterns.append("tweezer_top")

        # Inside bar (consolidation)
        if high < p_high and low > p_low:
            patterns.append("inside_bar")

        # Outside bar (volatility expansion)
        if high > p_high and low < p_low:
            patterns.append("outside_bar")

        # Piercing line: prev bearish, current opens below prev low, closes above prev midpoint
        if p_close < p_open:
            p_mid = (p_open + p_close) / 2
            if open_ < p_low and close > p_mid and close < p_open:
                patterns.append("piercing_line")

        # Dark cloud cover
        if p_close > p_open:
            p_mid = (p_open + p_close) / 2
            if open_ > p_high and close < p_mid and close > p_close:
                patterns.append("dark_cloud_cover")

    # ── Three-candle patterns (require prev2) ────────────────────────────────
    if prev and prev2:
        p2_close = prev2.get("close", close)
        p2_open  = prev2.get("open",  p2_close)
        p_close2 = prev.get("close", close)
        p_open2  = prev.get("open",  p_close2)

        # Morning star: bearish, small/doji, bullish
        p2_bearish = p2_close < p2_open
        p_small    = abs(p_close2 - p_open2) < abs(p2_close - p2_open) * 0.3
        curr_bull  = close > open_
        p2_mid     = (p2_open + p2_close) / 2
        if p2_bearish and p_small and curr_bull and close > p2_mid:
            patterns.append("morning_star")

        # Evening star: bullish, small/doji, bearish
        p2_bullish = p2_close > p2_open
        curr_bear  = close < open_
        p2_mid2    = (p2_open + p2_close) / 2
        if p2_bullish and p_small and curr_bear and close < p2_mid2:
            patterns.append("evening_star")

        # Three white soldiers: 3 consecutive bullish, each higher close
        if (close > open_ and p_close2 > p_open2 and p2_close > p2_open
                and close > p_close2 > p2_close):
            patterns.append("three_white_soldiers")

        # Three black crows
        if (close < open_ and p_close2 < p_open2 and p2_close < p2_open
                and close < p_close2 < p2_close):
            patterns.append("three_black_crows")

    return list(set(patterns))   # deduplicate


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYST OPINION — per-stock, per-session structured assessment
# ═══════════════════════════════════════════════════════════════════════════════

def analyse_stock(
    ticker: str,
    stock_entry: dict,
    patterns_db: Dict,
    sentiment: dict,
    session: str,
) -> dict:
    """
    Produces a structured analyst opinion for one stock.
    This is what the agent 'thinks' about the stock right now.
    """
    if not stock_entry or "latest" not in stock_entry:
        return {"ticker": ticker, "signal": "NO_DATA", "score": 0}

    d = stock_entry["latest"]
    prev  = stock_entry.get("prev_bar", {})
    prev2 = stock_entry.get("prev2_bar", {})
    patterns = detect_all_patterns(d, prev if prev else None, prev2 if prev2 else None)
    tk_known  = patterns_db.get(ticker, {})
    reliable  = tk_known.get("reliable_patterns", {})

    # ── Score buy vs sell ─────────────────────────────────────────────────────
    buy_score  = 0
    sell_score = 0
    buy_reasons  = []
    sell_reasons = []

    def _score(pattern, side, pts, reason):
        nonlocal buy_score, sell_score
        rel = reliable.get(pattern, {}).get("reliability", 0.5)
        if rel < CONFIDENCE_FLOOR and reliable.get(pattern):
            return   # pattern exists in DB but is unreliable — ignore
        weighted = pts * (0.7 + 0.6 * rel)   # scale by learned reliability
        if side == "buy":
            buy_score  += weighted
            buy_reasons.append(reason)
        else:
            sell_score += weighted
            sell_reasons.append(reason)

    # Base indicator scores
    rsi       = d.get("rsi", 50)
    macd_hist = d.get("macd_hist", 0)
    ema_short = d.get("ema_short", 1)
    ema_long  = d.get("ema_long",  1)
    ema_trend = d.get("ema_trend", 1)
    vol_rel   = d.get("vol_rel", 1)
    bb_pct    = d.get("bb_pct", 0.5)
    atr_pct   = d.get("atr_pct", 2)

    # EMA structure
    if ema_short > ema_long > ema_trend:
        buy_score += 2; buy_reasons.append("All EMAs stacked bullish")
    elif ema_short < ema_long < ema_trend:
        sell_score += 2; sell_reasons.append("All EMAs stacked bearish")
    elif ema_short > ema_long:
        buy_score += 1; buy_reasons.append("Short EMA above long EMA")
    elif ema_short < ema_long:
        sell_score += 1; sell_reasons.append("Short EMA below long EMA")

    # RSI
    if 38 <= rsi <= 55:
        buy_score += 1; buy_reasons.append(f"RSI={rsi:.0f} in healthy buy zone")
    elif rsi < 32:
        buy_score += 1.5; buy_reasons.append(f"RSI={rsi:.0f} oversold — reversal watch")
    elif rsi > 70:
        sell_score += 1.5; sell_reasons.append(f"RSI={rsi:.0f} overbought")
    elif rsi > 62:
        sell_score += 0.5; sell_reasons.append(f"RSI={rsi:.0f} elevated")

    # MACD
    if macd_hist > 0:
        buy_score += 1; buy_reasons.append("MACD histogram positive")
    elif macd_hist < 0:
        sell_score += 1; sell_reasons.append("MACD histogram negative")

    # Bollinger Bands
    if bb_pct < 0.15:
        buy_score += 1; buy_reasons.append("Near lower Bollinger Band")
    elif bb_pct > 0.85:
        sell_score += 1; sell_reasons.append("Near upper Bollinger Band")

    # Volume
    if vol_rel >= 1.4:
        if buy_score > sell_score:
            buy_score += 1; buy_reasons.append(f"High volume confirmation ({vol_rel:.1f}x avg)")
        else:
            sell_score += 1; sell_reasons.append(f"High volume confirmation ({vol_rel:.1f}x avg)")

    # News sentiment — use weighted score if available, else raw
    news_score = sentiment.get("weighted_score",
                               sentiment.get("score", 0)) if sentiment else 0
    news_trend = sentiment.get("trend", "stable") if sentiment else "stable"
    if news_score > 0.15:
        buy_score += 0.5; buy_reasons.append(f"Positive news sentiment ({news_score:.2f})")
        if news_trend == "improving":
            buy_score += 0.3; buy_reasons.append("News sentiment improving across sessions")
    elif news_score < -0.15:
        sell_score += 0.5; sell_reasons.append(f"Negative news sentiment ({news_score:.2f})")
        if news_trend == "worsening":
            sell_score += 0.3; sell_reasons.append("News sentiment worsening across sessions")

    # Learned pattern scores
    for p in patterns:
        if p in reliable:
            rel = reliable[p]["reliability"]
            total = reliable[p]["wins"] + reliable[p]["losses"]
            if total < MIN_PATTERN_SAMPLES:
                continue
            if rel >= 0.55:
                _score(p, "buy", 1.5, f"Learned bullish pattern: {p} (rel={rel:.0%})")
            elif rel <= 0.40:
                _score(p, "sell", 1.5, f"Learned bearish context: {p} (rel={rel:.0%})")

    # Multi-candle bullish patterns — high conviction signals
    BULLISH_MULTI = {"engulfing_bullish", "morning_star", "three_white_soldiers",
                     "bullish_harami", "piercing_line", "tweezer_bottom", "dragonfly_doji", "inverted_hammer"}
    BEARISH_MULTI = {"engulfing_bearish", "evening_star", "three_black_crows",
                     "bearish_harami", "dark_cloud_cover", "tweezer_top", "gravestone_doji"}

    for p in patterns:
        if p in BULLISH_MULTI:
            buy_score += 2.5; buy_reasons.append(f"Candlestick: {p.replace('_', ' ')}")
        elif p in BEARISH_MULTI:
            sell_score += 2.5; sell_reasons.append(f"Candlestick: {p.replace('_', ' ')}")

    if "volume_climax_bottom" in patterns:
        buy_score += 1.5; buy_reasons.append("Volume climax at low — potential capitulation reversal")
    if "volume_climax_top" in patterns:
        sell_score += 1.5; sell_reasons.append("Volume climax at high — potential distribution")

    # ── RSI divergence (strong reversal signals) ──────────────────────────────
    if "rsi_bullish_divergence" in patterns:
        buy_score += 2.0; buy_reasons.append("RSI bullish divergence — price lower but RSI higher")
    if "rsi_bearish_divergence" in patterns:
        sell_score += 2.0; sell_reasons.append("RSI bearish divergence — price higher but RSI lower")

    # ── 52-week position signals ──────────────────────────────────────────────
    if "52w_breakout" in patterns:
        buy_score += 2.5; buy_reasons.append("52-week breakout on high volume — strong momentum")
    if "near_52w_high" in patterns and buy_score > sell_score:
        buy_score += 0.8; buy_reasons.append("Near 52-week high — potential breakout zone")
    if "near_52w_low" in patterns and rsi < 40:
        buy_score += 1.2; buy_reasons.append("Near 52-week low with oversold RSI — reversal setup")

    # ── Earnings risk penalty ─────────────────────────────────────────────────
    days_to_earnings = d.get("days_to_earnings")
    if days_to_earnings is not None and 0 <= days_to_earnings <= 5:
        buy_score  *= 0.6   # reduce conviction near earnings — gap risk
        sell_score *= 0.6
        buy_reasons.append(f"⚠ Earnings in {days_to_earnings}d — position size reduced")

    # ── Sector momentum bonus/penalty ─────────────────────────────────────────
    sector_momentum = d.get("sector_momentum", 0.0)   # -1 to +1, set by sector tracker
    if sector_momentum > 0.3 and buy_score > sell_score:
        buy_score += 0.5; buy_reasons.append(f"Sector tailwind (momentum={sector_momentum:.2f})")
    elif sector_momentum < -0.3 and buy_score > sell_score:
        buy_score -= 0.5; buy_reasons.append(f"Sector headwind (momentum={sector_momentum:.2f})")

    # ── Delivery % bonus/penalty (institutional conviction signal) ─────────────
    delivery_signal = d.get("delivery_signal", "neutral")
    delivery_pct    = d.get("delivery_pct", 0.0)
    if delivery_signal == "strong_accumulation":
        buy_score  += 3.0; buy_reasons.append(f"High delivery {delivery_pct:.0f}% — strong institutional accumulation")
    elif delivery_signal == "accumulation":
        buy_score  += 1.5; buy_reasons.append(f"Delivery {delivery_pct:.0f}% above avg — institutional buying")
    elif delivery_signal == "distribution":
        sell_score += 2.0; sell_reasons.append(f"Low delivery {delivery_pct:.0f}% — institutional distribution / intraday churn")
    elif delivery_signal == "weak":
        if buy_score > sell_score:
            buy_score -= 1.0; buy_reasons.append(f"Delivery {delivery_pct:.0f}% weak — move may be intraday driven")

    buy_score  = round(buy_score,  2)
    sell_score = round(sell_score, 2)
    gap = abs(buy_score - sell_score)

    # ── Determine signal ──────────────────────────────────────────────────────
    if buy_score >= BUY_SIGNAL_MIN_SCORE and buy_score > sell_score and gap >= SIGNAL_SCORE_GAP:
        signal = "BUY"
    elif sell_score >= SELL_SIGNAL_MIN_SCORE and sell_score > buy_score and gap >= SIGNAL_SCORE_GAP:
        signal = "SELL"
    else:
        signal = "WATCH"   # no trade — keep monitoring

    # ── Compute entry / stop / target via ATR ─────────────────────────────────
    close = d.get("close", 0)
    atr   = d.get("atr", close * FLAT_STOP_PCT)
    # Use per-stock learned ATR multiplier if available, else config default
    atr_mult   = tk_known.get("atr_multiplier", ATR_STOP_MULTIPLIER)
    atr_target = ATR_TARGET_MULTIPLIER

    if signal == "BUY":
        entry     = close
        stop_loss = round(close - atr * atr_mult, 2)
        target    = round(close + atr * atr_target, 2)
        style     = tk_known.get("preferred_style", "swing")
    elif signal == "SELL":
        entry     = close
        stop_loss = round(close + atr * atr_mult, 2)
        target    = round(close - atr * atr_target, 2)
        style     = tk_known.get("preferred_style", "swing")
    else:
        entry = stop_loss = target = close
        style = "watch"

    risk_pct   = round(abs(entry - stop_loss) / entry * 100, 2) if entry > 0 else 0
    reward_pct = round(abs(target - entry) / entry * 100, 2) if entry > 0 else 0
    rr_ratio   = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0

    # Overall confidence (0–100)
    max_score = max(buy_score, sell_score)
    confidence = min(round((max_score / 10) * 100, 1), 95)

    return {
        "ticker":       ticker,
        "date":         date.today().isoformat(),
        "session":      session,
        "signal":       signal,
        "buy_score":    buy_score,
        "sell_score":   sell_score,
        "confidence":   confidence,
        "entry":        round(entry, 2),
        "stop_loss":    stop_loss,
        "target":       target,
        "risk_pct":     risk_pct,
        "reward_pct":   reward_pct,
        "rr_ratio":     rr_ratio,
        "style":        style,
        "patterns":     patterns,
        "buy_reasons":  buy_reasons,
        "sell_reasons": sell_reasons,
        "hold_days":    "5–10 days" if style == "swing" else "same day",
        "news_score":   round(news_score, 3),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# OUTCOME LEARNING — update pattern reliability from closed trades
# ═══════════════════════════════════════════════════════════════════════════════

def learn_from_trade(
    ticker: str,
    patterns_at_entry: List[str],
    won: bool,
    style: str,
    patterns_db: Dict,
    **kwargs,
) -> Dict:
    """
    Updates pattern reliability for a ticker based on a closed trade outcome.
    Also adjusts swing vs intraday preference.
    """
    if ticker not in patterns_db:
        patterns_db[ticker] = {
            "reliable_patterns": {},
            "preferred_style":   "swing",
            "swing_wins":        0,
            "swing_losses":      0,
            "intraday_wins":     0,
            "intraday_losses":   0,
            "atr_stop_hits":     0,
            "atr_target_hits":   0,
            "atr_multiplier":    ATR_STOP_MULTIPLIER,   # per-stock tuned value
            "last_updated":      None,
        }

    tk = patterns_db[ticker]
    today = date.today().isoformat()

    # Update each pattern's win/loss record
    for p in patterns_at_entry:
        if p not in tk["reliable_patterns"]:
            tk["reliable_patterns"][p] = {
                "wins": 0, "losses": 0, "reliability": 0.5,
                "last_seen": today,
            }
        pr = tk["reliable_patterns"][p]
        pr["last_seen"] = today
        if won:
            pr["wins"] += 1
        else:
            pr["losses"] += 1
        total = pr["wins"] + pr["losses"]
        # Bayesian-ish smoothing: start at 0.5, drift toward evidence
        prior_weight = max(3 - total, 0)
        pr["reliability"] = round(
            (pr["wins"] + prior_weight * 0.5) / (total + prior_weight), 3
        )

    # Update style preference
    style_key = "swing" if style == "swing" else "intraday"
    if won:
        tk[f"{style_key}_wins"] += 1
    else:
        tk[f"{style_key}_losses"] += 1

    sw  = tk["swing_wins"]    / max(tk["swing_wins"]    + tk["swing_losses"],   1)
    inw = tk["intraday_wins"] / max(tk["intraday_wins"] + tk["intraday_losses"],1)
    tk["preferred_style"] = "swing" if sw >= inw else "intraday"

    # ── Per-stock ATR multiplier auto-tuning ──────────────────────────────────
    # Track how often stop gets hit vs target. If stops hit >50% → widen multiplier.
    # If target hit consistently → tighten stop to lock in profits faster.
    exit_reason = kwargs.get("exit_reason", "")
    if exit_reason == "stop_hit":
        tk["atr_stop_hits"]   = tk.get("atr_stop_hits", 0) + 1
    elif exit_reason == "target_hit":
        tk["atr_target_hits"] = tk.get("atr_target_hits", 0) + 1

    total_exits = tk.get("atr_stop_hits", 0) + tk.get("atr_target_hits", 0)
    if total_exits >= 5:
        stop_rate = tk["atr_stop_hits"] / total_exits
        current_mult = tk.get("atr_multiplier", ATR_STOP_MULTIPLIER)
        if stop_rate > 0.60:
            # Too many stops hit → widen stop
            tk["atr_multiplier"] = min(round(current_mult + 0.1, 2), 3.0)
        elif stop_rate < 0.30:
            # Rarely stopped → can tighten stop
            tk["atr_multiplier"] = max(round(current_mult - 0.05, 2), 1.0)

    tk["last_updated"] = today

    # Decay reliability of patterns not seen recently
    _apply_decay(tk["reliable_patterns"])

    patterns_db[ticker] = tk
    return patterns_db


def _apply_decay(patterns: dict) -> None:
    """Nudge reliability of stale patterns back toward 0.5 (uncertainty)."""
    today = date.today()
    for name, p in patterns.items():
        if not p.get("last_seen"):
            continue
        days_since = (today - date.fromisoformat(p["last_seen"])).days
        if days_since > 7:
            # Pull toward 0.5 at decay rate per day of staleness
            drift = PATTERN_DECAY_RATE * (days_since - 7)
            p["reliability"] = round(
                p["reliability"] + drift * (0.5 - p["reliability"]), 3
            )


def record_decision(decisions: List, opinion: dict, action: str, reason: str) -> List:
    """Append a timestamped decision record to the decisions log."""
    decisions.append({
        "timestamp": datetime.utcnow().isoformat(),
        "date":      date.today().isoformat(),
        "ticker":    opinion.get("ticker"),
        "session":   opinion.get("session"),
        "action":    action,
        "signal":    opinion.get("signal"),
        "confidence":opinion.get("confidence"),
        "entry":     opinion.get("entry"),
        "stop_loss": opinion.get("stop_loss"),
        "target":    opinion.get("target"),
        "reason":    reason,
        "patterns":  opinion.get("patterns", []),
    })
    return decisions


def get_reliable_patterns_list(ticker: str, patterns_db: Dict, min_rel: float = 0.55) -> List[str]:
    tk = patterns_db.get(ticker, {})
    return [
        name for name, p in tk.get("reliable_patterns", {}).items()
        if p["wins"] + p["losses"] >= MIN_PATTERN_SAMPLES and p["reliability"] >= min_rel
    ]
