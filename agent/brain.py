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
    BUY_SIGNAL_MIN_SCORE, SELL_SIGNAL_MIN_SCORE, SIGNAL_SCORE_GAP,
    ATR_STOP_MULTIPLIER, ATR_TARGET_MULTIPLIER,
    FLAT_STOP_PCT,
    PATTERN_DECAY_RATE, MIN_PATTERN_SAMPLES, CONFIDENCE_FLOOR,
)


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN LIBRARY — the brain's learned knowledge
# ═══════════════════════════════════════════════════════════════════════════════

def load_patterns() -> Dict:
    from agent.io_safe import load_json_dict
    return load_json_dict(PATTERN_FILE)


def save_patterns(data: Dict) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    with open(PATTERN_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_decisions() -> List:
    from agent.io_safe import load_json_list
    return load_json_list(BRAIN_DECISIONS_FILE)


def save_decisions(decisions: List) -> None:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    decisions = decisions[-1000:]   # research/audit trail (display only, not learning)
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

    # ── Supertrend (ATR-based trend filter — most used in Indian retail trading) ─
    # Supertrend = close ± (multiplier × ATR). Bull when close > upper band.
    atr_val    = d.get("atr", close * 0.02)
    st_mult    = 3.0
    st_upper   = d.get("supertrend_upper", close + st_mult * atr_val)
    st_lower   = d.get("supertrend_lower", close - st_mult * atr_val)
    st_bull    = d.get("supertrend_bull")   # pre-computed if available
    if st_bull is None:
        st_bull = close > (close - st_mult * atr_val)   # approximate
    if st_bull and bullish_candle:
        patterns.append("supertrend_bullish")
    elif not st_bull and bearish_candle:
        patterns.append("supertrend_bearish")

    # ── Pivot Points (daily — standard S1/S2/R1/R2 that every Indian trader uses) ─
    # Classic pivot: P = (H+L+C)/3, R1 = 2P-L, S1 = 2P-L (prev day bar)
    if prev:
        p_h = prev.get("high",  close)
        p_l = prev.get("low",   close)
        p_c = prev.get("close", close)
        pivot  = (p_h + p_l + p_c) / 3
        r1 = 2 * pivot - p_l
        r2 = pivot + (p_h - p_l)
        s1 = 2 * pivot - p_h
        s2 = pivot - (p_h - p_l)
        tol = atr_val * 0.3   # within 30% ATR = "near" the level

        if close > r1 - tol and vol_rel > 1.2:
            patterns.append("pivot_r1_breakout")
        if close > r2 - tol and vol_rel > 1.5:
            patterns.append("pivot_r2_breakout")
        if abs(close - s1) < tol and bullish_candle:
            patterns.append("pivot_s1_bounce")
        if abs(close - s2) < tol and bullish_candle:
            patterns.append("pivot_s2_bounce")
        if abs(close - pivot) < tol:
            patterns.append("pivot_point_test")
        if close < s1 + tol and bearish_candle:
            patterns.append("pivot_s1_breakdown")

    # ── ADX — trend strength (avoids false signals in sideways markets) ──────────
    # ADX > 25 = trending, < 20 = ranging. We proxy from price volatility.
    adx_proxy = d.get("adx", None)
    if adx_proxy is None:
        # Use 10-day volatility as proxy: high vol + directional = trending
        vol_10d = d.get("volatility_10d", 1.5)
        adx_proxy = min(50, vol_10d * 15)   # rough proxy
    if adx_proxy > 25 and bullish_candle:
        patterns.append("adx_strong_trend_up")
    elif adx_proxy > 25 and bearish_candle:
        patterns.append("adx_strong_trend_down")
    elif adx_proxy < 18:
        patterns.append("adx_ranging_market")   # reduce conviction in all signals

    # ── Stochastic RSI (faster RSI-of-RSI — catches turns earlier) ───────────────
    stoch_rsi = d.get("stoch_rsi", None)
    if stoch_rsi is None and len(rh := d.get("rsi_history", [])) >= 5:
        # Compute %K from RSI history: (current - min) / (max - min)
        rsi_min = min(rh[-5:])
        rsi_max = max(rh[-5:])
        stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min + 1e-9) * 100
    if stoch_rsi is not None:
        if stoch_rsi < 20 and bullish_candle:
            patterns.append("stoch_rsi_oversold_reversal")
        elif stoch_rsi > 80 and bearish_candle:
            patterns.append("stoch_rsi_overbought_reversal")
        if prev:
            prev_rh = prev.get("rsi_history", [])
            if prev_rh and len(prev_rh) >= 5:
                prev_srsi = (prev.get("rsi", 50) - min(prev_rh[-5:])) / (max(prev_rh[-5:]) - min(prev_rh[-5:]) + 1e-9) * 100
                if prev_srsi < 20 and stoch_rsi >= 20:
                    patterns.append("stoch_rsi_bullish_cross")
                elif prev_srsi > 80 and stoch_rsi <= 80:
                    patterns.append("stoch_rsi_bearish_cross")

    # ── MACD divergence (separate from crossover — reversal signal) ───────────────
    macd_h    = d.get("macd_hist", 0)
    ph_list   = d.get("price_history", [])
    macd_hist_h = d.get("macd_hist_history", [])
    if len(ph_list) >= 5 and len(macd_hist_h) >= 5:
        price_falling = ph_list[-1] < ph_list[-3]
        macd_rising   = macd_hist_h[-1] > macd_hist_h[-3]
        price_rising  = ph_list[-1] > ph_list[-3]
        macd_falling  = macd_hist_h[-1] < macd_hist_h[-3]
        if price_falling and macd_rising and macd_h < 0:
            patterns.append("macd_bullish_divergence")   # strong buy
        if price_rising and macd_falling and macd_h > 0:
            patterns.append("macd_bearish_divergence")   # strong sell

    # ── Ichimoku Cloud signals (Kumo) ─────────────────────────────────────────────
    # We proxy with a simple tenkan/kijun from price history if full Ichimoku not computed
    if len(ph_list) >= 26:
        tenkan  = (max(ph_list[-9:])  + min(ph_list[-9:]))  / 2   # 9-period midpoint
        kijun   = (max(ph_list[-26:]) + min(ph_list[-26:])) / 2   # 26-period midpoint
        senkou_a = (tenkan + kijun) / 2                             # cloud top/bottom
        senkou_b = (max(ph_list[-52:]) + min(ph_list[-52:])) / 2 if len(ph_list) >= 52 else kijun
        cloud_top    = max(senkou_a, senkou_b)
        cloud_bottom = min(senkou_a, senkou_b)
        if close > cloud_top and tenkan > kijun:
            patterns.append("ichimoku_bullish_kumo")   # price above cloud, TK bullish
        elif close < cloud_bottom and tenkan < kijun:
            patterns.append("ichimoku_bearish_kumo")   # price below cloud, TK bearish
        elif cloud_bottom <= close <= cloud_top:
            patterns.append("ichimoku_inside_kumo")    # inside cloud = uncertainty
        if prev and close > tenkan and prev.get("close", close) <= tenkan:
            patterns.append("ichimoku_tk_cross_bullish")
        if prev and close < tenkan and prev.get("close", close) >= tenkan:
            patterns.append("ichimoku_tk_cross_bearish")

    # ── Price-Volume Trend (PVT) proxy ────────────────────────────────────────────
    # PVT = cumulative (close_change_pct × volume). Rising PVT with price = confirmed.
    vol_hist = d.get("volume_history_20d", [])
    if len(ph_list) >= 5 and len(vol_hist) >= 5:
        pvt_changes = []
        for i in range(1, min(5, len(ph_list), len(vol_hist))):
            pct = (ph_list[-i] - ph_list[-i-1]) / (ph_list[-i-1] + 1e-9)
            pvt_changes.append(pct * vol_hist[-i])
        pvt_trend = sum(pvt_changes)
        if pvt_trend > 0 and bullish_candle:
            patterns.append("pvt_accumulation")
        elif pvt_trend < 0 and bearish_candle:
            patterns.append("pvt_distribution")

    # ── VWAP deviation (institutional reference — critical for Indian markets) ────
    vwap      = d.get("vwap", None)
    if vwap and vwap > 0 and close > 0:
        vwap_dev_pct = (close - vwap) / vwap * 100
        if vwap_dev_pct > 0.5 and bullish_candle:
            patterns.append("above_vwap_strong")
        elif vwap_dev_pct < -0.5 and bearish_candle:
            patterns.append("below_vwap_strong")
        elif abs(vwap_dev_pct) < 0.15:
            patterns.append("vwap_magnet")   # price hugging VWAP = indecision

    # ── Gap analysis (overnight gaps are critical in Indian markets) ───────────────
    if prev:
        prev_close = prev.get("close", close)
        if prev_close > 0:
            gap_pct = (open_ - prev_close) / prev_close * 100
            if gap_pct > 1.5 and bullish_candle:
                patterns.append("gap_up_continuation")
            elif gap_pct < -1.5 and bearish_candle:
                patterns.append("gap_down_continuation")
            elif gap_pct > 1.0 and bearish_candle:
                patterns.append("gap_up_reversal")   # fade the gap
            elif gap_pct < -1.0 and bullish_candle:
                patterns.append("gap_down_reversal")  # buy the gap-down

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
        info  = reliable.get(pattern, {})
        rel   = info.get("reliability", 0.5)
        wins  = info.get("wins", 0)
        losses= info.get("losses", 0)
        sample = wins + losses

        if rel < CONFIDENCE_FLOOR and info:
            return   # pattern exists in DB but is proven unreliable — ignore

        # ── Outcome-weighted scoring ───────────────────────────────────────────
        # Lean HARDER on a pattern's real win-rate, but only as the sample grows —
        # so 1-2 lucky/unlucky trades don't swing it, while a genuinely proven
        # pattern (many trades) gets a strong boost and a proven loser gets cut.
        #   confidence ramps 0→1 over the first ~10 trades on this pattern.
        if sample <= 0:
            mult = 1.0   # never traded — use the base points as-is
        else:
            confidence = min(1.0, sample / 10.0)
            # base gentle scaling (as before) blended toward an aggressive,
            # win-rate-driven multiplier the more evidence we have.
            gentle     = 0.7 + 0.6 * rel                # ~0.94–1.30x
            aggressive = 0.4 + 1.4 * rel                # ~0.4x (loser) – 1.8x (winner)
            mult = gentle * (1 - confidence) + aggressive * confidence
        weighted = pts * mult
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

    # ── Supertrend ────────────────────────────────────────────────────────────────
    if "supertrend_bullish" in patterns:
        buy_score += 1.5; buy_reasons.append("Supertrend bullish — price above trend band")
    if "supertrend_bearish" in patterns:
        sell_score += 1.5; sell_reasons.append("Supertrend bearish — price below trend band")

    # ── ADX trend strength filter ─────────────────────────────────────────────────
    if "adx_ranging_market" in patterns:
        # Reduce conviction when market is sideways — all signals less reliable
        buy_score  *= 0.75; sell_score *= 0.75
        buy_reasons.append("ADX low — market ranging, signals less reliable")
    if "adx_strong_trend_up" in patterns:
        buy_score += 1.0; buy_reasons.append("ADX strong trend — directional move confirmed")
    if "adx_strong_trend_down" in patterns:
        sell_score += 1.0; sell_reasons.append("ADX strong trend — downside momentum confirmed")

    # ── Pivot Points ──────────────────────────────────────────────────────────────
    if "pivot_r1_breakout" in patterns:
        buy_score += 1.5; buy_reasons.append("Broke above Pivot R1 with volume — bullish")
    if "pivot_r2_breakout" in patterns:
        buy_score += 2.0; buy_reasons.append("Broke above Pivot R2 — strong momentum")
    if "pivot_s1_bounce" in patterns:
        buy_score += 1.5; buy_reasons.append("Bounced off Pivot S1 support")
    if "pivot_s2_bounce" in patterns:
        buy_score += 2.0; buy_reasons.append("Bounced off Pivot S2 — strong support held")
    if "pivot_s1_breakdown" in patterns:
        sell_score += 1.5; sell_reasons.append("Broke below Pivot S1 support — bearish")
    if "pivot_point_test" in patterns and buy_score > sell_score:
        buy_score += 0.5; buy_reasons.append("Testing pivot point — decision zone")

    # ── Stochastic RSI ────────────────────────────────────────────────────────────
    if "stoch_rsi_bullish_cross" in patterns:
        buy_score += 1.5; buy_reasons.append("StochRSI crossed above oversold — early buy signal")
    if "stoch_rsi_bearish_cross" in patterns:
        sell_score += 1.5; sell_reasons.append("StochRSI crossed below overbought — early sell signal")
    if "stoch_rsi_oversold_reversal" in patterns:
        buy_score += 1.0; buy_reasons.append("StochRSI oversold with bullish candle — reversal")
    if "stoch_rsi_overbought_reversal" in patterns:
        sell_score += 1.0; sell_reasons.append("StochRSI overbought with bearish candle — reversal")

    # ── MACD divergence ───────────────────────────────────────────────────────────
    if "macd_bullish_divergence" in patterns:
        buy_score += 2.0; buy_reasons.append("MACD bullish divergence — momentum reversing despite lower price")
    if "macd_bearish_divergence" in patterns:
        sell_score += 2.0; sell_reasons.append("MACD bearish divergence — momentum fading despite higher price")

    # ── Ichimoku Cloud ────────────────────────────────────────────────────────────
    if "ichimoku_bullish_kumo" in patterns:
        buy_score += 2.0; buy_reasons.append("Above Ichimoku cloud with bullish TK cross — strong uptrend")
    if "ichimoku_bearish_kumo" in patterns:
        sell_score += 2.0; sell_reasons.append("Below Ichimoku cloud — strong downtrend confirmed")
    if "ichimoku_tk_cross_bullish" in patterns:
        buy_score += 1.5; buy_reasons.append("Ichimoku TK bullish cross — momentum turning up")
    if "ichimoku_tk_cross_bearish" in patterns:
        sell_score += 1.5; sell_reasons.append("Ichimoku TK bearish cross — momentum turning down")
    if "ichimoku_inside_kumo" in patterns:
        buy_score  *= 0.85; sell_score *= 0.85   # inside cloud = uncertainty, reduce all conviction

    # ── Price-Volume Trend ────────────────────────────────────────────────────────
    if "pvt_accumulation" in patterns:
        buy_score += 1.0; buy_reasons.append("PVT accumulation — volume-weighted buying pressure building")
    if "pvt_distribution" in patterns:
        sell_score += 1.0; sell_reasons.append("PVT distribution — volume-weighted selling pressure")

    # ── VWAP deviation ────────────────────────────────────────────────────────────
    if "above_vwap_strong" in patterns:
        buy_score += 1.0; buy_reasons.append("Holding above VWAP — institutional support")
    if "below_vwap_strong" in patterns:
        sell_score += 1.0; sell_reasons.append("Below VWAP — institutional selling pressure")

    # ── Gap analysis (Indian market specific — gaps set intraday sentiment) ────────
    if "gap_up_continuation" in patterns:
        buy_score += 1.5; buy_reasons.append("Gap up with bullish follow-through — momentum")
    if "gap_down_reversal" in patterns:
        buy_score += 1.0; buy_reasons.append("Gap down reversal — buyers stepping in at lower open")
    if "gap_down_continuation" in patterns:
        sell_score += 1.5; sell_reasons.append("Gap down continuation — selling pressure from open")
    if "gap_up_reversal" in patterns:
        sell_score += 1.0; sell_reasons.append("Gap up fade — selling into strength")

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

    # ── Historical regime context (from 2-year history engine) ─────────────────
    # These fields are injected into latest{} by main.py from history_context.json.
    # They let the brain reason about WHERE in its long-term life a stock is, and
    # adapt to the stock's own personality.
    hist_long_trend = d.get("hist_long_trend")        # strong_uptrend / uptrend / ...
    hist_52w_pos    = d.get("hist_pct_of_52w_range")  # 0=at 52w low, 100=at 52w high
    hist_vol_state  = d.get("hist_vol_state")         # elevated / normal / compressed
    hist_drawdown   = d.get("hist_drawdown_from_high")
    personality     = d.get("hist_personality")       # trender / mean_reverter / choppy

    if hist_long_trend in ("strong_uptrend", "uptrend"):
        if buy_score > sell_score:
            buy_score += 1.0; buy_reasons.append(f"In a long-term {hist_long_trend.replace('_',' ')} (2yr) — trend on our side")
    elif hist_long_trend in ("strong_downtrend", "downtrend"):
        if sell_score > buy_score:
            sell_score += 1.0; sell_reasons.append(f"In a long-term {hist_long_trend.replace('_',' ')} (2yr)")
        elif buy_score > sell_score:
            buy_score -= 0.8; buy_reasons.append("⚠ Buying against a long-term downtrend — lower conviction")

    if hist_52w_pos is not None:
        if hist_52w_pos >= 92 and buy_score > sell_score:
            buy_score += 0.8; buy_reasons.append(f"Near 52-week high ({hist_52w_pos:.0f}% of range) — breakout zone")
        elif hist_52w_pos <= 12 and rsi < 40:
            buy_score += 1.0; buy_reasons.append(f"Near 52-week low ({hist_52w_pos:.0f}% of range) + oversold — value reversal setup")

    # Compressed volatility historically precedes expansion — favour breakouts
    if hist_vol_state == "compressed" and buy_score > sell_score and macd_hist > 0:
        buy_score += 0.5; buy_reasons.append("Volatility compressed vs its norm — primed for an expansion move")
    if hist_vol_state == "elevated":
        # In abnormally high vol, demand more — fade conviction slightly both ways
        buy_score *= 0.92; sell_score *= 0.92

    # Adapt to the stock's personality
    if personality == "mean_reverter":
        # extremes snap back — boost reversal logic, trim trend-chasing
        if rsi < 35 and buy_score > sell_score:
            buy_score += 0.6; buy_reasons.append("Mean-reverting stock at oversold extreme — fade the dip")
        if hist_52w_pos is not None and hist_52w_pos >= 90 and buy_score > sell_score:
            buy_score -= 0.5   # chasing a mean-reverter at highs is risky
    elif personality == "trender":
        # trends persist — reward momentum alignment
        if hist_long_trend in ("uptrend", "strong_uptrend") and buy_score > sell_score:
            buy_score += 0.5; buy_reasons.append("Clean trender aligned with its long-term uptrend")

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

    # ── Coach memory — read lessons this tool has already learned ─────────────
    # If the coach flagged caution on this ticker+setup, reduce confidence slightly.
    # If coach flagged it as reliable, add a small boost.
    # This is how the tool accumulates institutional memory across sessions.
    coach_notes  = []
    coach_caution = False
    try:
        from agent.llm_coach import get_lessons_for, load_coach_memory
        _coach_mem = load_coach_memory()
        # Pick top pattern as setup key (most important signal that fired)
        top_pattern = patterns[0] if patterns else "general"
        lessons = get_lessons_for(ticker, top_pattern, _coach_mem)
        for lesson in lessons:
            watch = lesson.get("what_to_watch", "")
            happened = lesson.get("what_happened", "")
            if watch:
                coach_notes.append(f"Coach: {watch}")
            # If coach lesson is about a loss on this setup, add slight caution
            if "fail" in happened.lower() or "stop" in happened.lower() or "loss" in happened.lower():
                coach_caution = True
        if coach_caution and signal != "WATCH":
            confidence = max(confidence - 5, 30)   # slight reduction — not a veto
            if coach_notes:
                buy_reasons.append(f"⚠ {coach_notes[0]}")
    except Exception:
        pass   # coach is always optional — never block trading logic

    # ── Queue a question if a rare signal combination appeared ────────────────
    # The tool asks the coach about setups it hasn't seen much of yet.
    try:
        _rare_patterns = [p for p in patterns if p not in (tk_known.get("reliable_patterns") or {})]
        if len(_rare_patterns) >= 2 and signal != "WATCH":
            from agent.llm_coach import queue_question
            q = (
                f"In Indian NSE markets, what does the combination of "
                f"{', '.join(_rare_patterns[:3])} mean for a {style} trade on "
                f"{ticker.replace('.NS','')}? "
                f"Current RSI={rsi:.0f}, MACD hist={macd_hist:+.4f}, "
                f"Vol={vol_rel:.1f}x average. Signal is {signal}."
            )
            queue_question(q, {
                "ticker":   ticker,
                "signal":   signal,
                "patterns": ", ".join(_rare_patterns[:3]),
                "rsi":      round(rsi, 1),
            })
    except Exception:
        pass

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
        "coach_notes":  coach_notes,
        "days_to_earnings": days_to_earnings,   # for earnings-aware sizing downstream
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
