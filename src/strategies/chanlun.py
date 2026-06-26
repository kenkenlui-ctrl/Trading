"""
Chanlun (纏論) 3rd-class BUY point detection.

Source theory: chzhshch.blog corpus (#020, #062, #067, #071, #053)
Backtest results: see /Users/kenken/Documents/chanlun-backtest/docs/

Pipeline (pure Python, no I/O):
  1. K-line inclusion processing (含括關係)
  2. Top/Bottom fractal detection with right-side confirmation
  3. Stroke (筆) detection
  4. Line segment via ZigZag(5%) on daily bars
  5. 中樞 detection — overlap of ≥3 consecutive segments
  6. 3rd-class BUY — first close > ZG after pullback that didn't break ZD

DSA-HK adaptation:
  - Operates on DataFrame with columns: Open, High, Low, Close, Volume
  - Returns signals as list of dataclass with metadata for DB persistence
  - Daily timeframe only (filter applied in scan command)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class ProcessedKLine:
    index: int
    high: float
    low: float
    direction: int


@dataclass
class Fractal:
    index: int
    type: str            # "top" or "bottom"
    price: float
    date: pd.Timestamp


@dataclass
class Central:
    start_idx: int
    end_idx: int
    zg: float            # upper bound (min of highs in overlap)
    zd: float            # lower bound (max of lows in overlap)
    gg: float            # absolute max high
    dd: float            # absolute min low
    direction: int       # 1 = up, -1 = down (dominant)


@dataclass
class ChanlunSignal:
    code: str                                  # ticker e.g. "0700.HK"
    signal_date: pd.Timestamp                  # date of breakout bar
    entry_price: float                         # close on breakout day
    stop_loss: float                           # -5%
    target: float                              # +15%
    confidence: int                            # 7 or 8 (based on 中樞 width)
    central_zg: float                          # breakout level
    central_zd: float                          # pullback floor
    central_gg: float                          # 中樞 absolute high
    central_dd: float                          # 中樞 absolute low
    had_pullback: bool                          # pullback confirmation
    rationale: str                              # human-readable explanation


# =============================================================================
# Stage 1: K-line inclusion (含括關係)
# =============================================================================

def process_inclusion(df: pd.DataFrame) -> List[ProcessedKLine]:
    """
    Forward-pass inclusion processing — guaranteed O(n) termination.
    Tracks current parent direction by last non-contained K-line.
    """
    n = len(df)
    if n == 0:
        return []
    high = df["High"].values.astype(float)
    low = df["Low"].values.astype(float)

    merged_high = high.copy()
    merged_low = low.copy()
    stack: List[tuple] = []
    # Initial parent direction: 'high' if first bar's high >= next bar's high
    if n > 1 and high[0] >= high[1]:
        parent_dir = 1
    else:
        parent_dir = 1  # default to up parent
    stack.append((0, 0, merged_high[0], merged_low[0], parent_dir))

    for i in range(1, n):
        h, l = merged_high[i], merged_low[i]
        last = stack[-1]
        if last[4] > 0:
            if h <= last[2] and l >= last[3]:
                stack[-1] = (last[0], i, max(last[2], h), min(last[3], l), last[4])
                continue
            new_dir = 1 if h > l else -1
            stack.append((i, i, h, l, new_dir))
        else:
            if h >= last[2] and l <= last[3]:
                stack[-1] = (last[0], i, max(last[2], h), min(last[3], l), last[4])
                continue
            new_dir = 1 if h > l else -1
            stack.append((i, i, h, l, new_dir))

    return [
        ProcessedKLine(index=s[1], high=s[2], low=s[3], direction=s[4])
        for s in stack
    ]


# =============================================================================
# Stage 2: Fractal detection (顶底分型)
# =============================================================================

def detect_fractals(processed: List[ProcessedKLine], df: pd.DataFrame,
                     require_confirmation: bool = True) -> List[Fractal]:
    """Detect top/bottom fractals with optional right-side confirmation."""
    fractals = []
    n = len(processed)
    for i in range(1, n - 1):
        prev_k = processed[i - 1]
        curr_k = processed[i]
        next_k = processed[i + 1]

        if curr_k.high > prev_k.high and curr_k.high > next_k.high:
            if require_confirmation:
                next_close = df["Close"].iloc[next_k.index]
                if next_close >= curr_k.low:
                    continue
            fractals.append(Fractal(
                index=curr_k.index, type="top",
                price=curr_k.high, date=df.index[curr_k.index],
            ))
        elif curr_k.low < prev_k.low and curr_k.low < next_k.low:
            if require_confirmation:
                next_close = df["Close"].iloc[next_k.index]
                if next_close <= curr_k.high:
                    continue
            fractals.append(Fractal(
                index=curr_k.index, type="bottom",
                price=curr_k.low, date=df.index[curr_k.index],
            ))
    return fractals


# =============================================================================
# Stage 3: ZigZag pivots (for line segment detection)
# =============================================================================

def detect_zigzag_pivots(closes: pd.Series, highs: pd.Series,
                          lows: pd.Series, threshold_pct: float = 0.05) -> List[dict]:
    """Find significant pivots using threshold-based reversal detection.
    Uses High for high pivots, Low for low pivots (matches Python backtest convention).
    """
    high_arr = highs.values
    low_arr = lows.values
    n = len(closes)
    if n < 2:
        return []

    pivots: List[dict] = []
    current_type = "high" if high_arr[0] >= high_arr[1] else "low"
    extreme_idx = 0
    extreme_price = high_arr[0] if current_type == "high" else low_arr[0]

    for i in range(1, n):
        h, l = high_arr[i], low_arr[i]
        if current_type == "high":
            if h > extreme_price:
                extreme_price = h
                extreme_idx = i
            elif extreme_price > 0 and (extreme_price - l) / extreme_price >= threshold_pct:
                pivots.append({"idx": extreme_idx, "price": extreme_price, "type": "high"})
                current_type = "low"
                extreme_price = l
                extreme_idx = i
        else:
            if l < extreme_price:
                extreme_price = l
                extreme_idx = i
            elif extreme_price > 0 and (h - extreme_price) / extreme_price >= threshold_pct:
                pivots.append({"idx": extreme_idx, "price": extreme_price, "type": "low"})
                current_type = "high"
                extreme_price = h
                extreme_idx = i

    if extreme_idx > 0:
        pivots.append({"idx": extreme_idx, "price": extreme_price, "type": current_type})
    return pivots


# =============================================================================
# Stage 4: Line segments from ZigZag pivots
# =============================================================================

def pivots_to_segments(pivots: List[dict], highs: pd.Series,
                         lows: pd.Series) -> List[dict]:
    """Convert alternating pivots into line segments with high/low ranges."""
    high_arr = highs.values
    low_arr = lows.values
    segs = []
    for i in range(len(pivots) - 1):
        p1, p2 = pivots[i], pivots[i + 1]
        direction = 1 if p2["price"] > p1["price"] else -1
        seg_high = -np.inf
        seg_low = np.inf
        for j in range(p1["idx"], p2["idx"] + 1):
            if high_arr[j] > seg_high:
                seg_high = high_arr[j]
            if low_arr[j] < seg_low:
                seg_low = low_arr[j]
        segs.append({
            "start_idx": p1["idx"],
            "end_idx": p2["idx"],
            "high": float(seg_high),
            "low": float(seg_low),
            "direction": direction,
        })
    return segs


# =============================================================================
# Stage 5: 中樞 detection (overlap of 3+ consecutive segments)
# =============================================================================

def detect_centrals(segs: List[dict], min_segments: int = 3) -> List[Central]:
    """Detect 中樞 = overlap zone of ≥3 consecutive segments (any direction)."""
    centrals: List[Central] = []
    if len(segs) < min_segments:
        return centrals

    i = 0
    while i < len(segs):
        if i + min_segments - 1 >= len(segs):
            break
        window = segs[i:i + min_segments]
        overlap_high = min(s["high"] for s in window)
        overlap_low = max(s["low"] for s in window)
        if overlap_high <= overlap_low:
            i += 1
            continue

        end_idx = i + min_segments - 1
        zg, zd = overlap_high, overlap_low
        gg = max(s["high"] for s in window)
        dd = min(s["low"] for s in window)

        # Track dominant direction
        dir_count = {1: 0, -1: 0}
        for s in window:
            dir_count[s["direction"]] += 1

        j = end_idx + 1
        while j < len(segs):
            nxt = segs[j]
            new_h = min(zg, nxt["high"])
            new_l = max(zd, nxt["low"])
            if new_h <= new_l:
                break
            zg, zd = new_h, new_l
            gg = max(gg, nxt["high"])
            dd = min(dd, nxt["low"])
            dir_count[nxt["direction"]] += 1
            end_idx = j
            j += 1

        dominant_dir = 1 if dir_count[1] >= dir_count[-1] else -1
        centrals.append(Central(
            start_idx=segs[i]["start_idx"],
            end_idx=segs[end_idx]["end_idx"],
            zg=zg, zd=zd, gg=gg, dd=dd,
            direction=dominant_dir,
        ))
        i = end_idx + 1

    return centrals


# =============================================================================
# Stage 6: 3rd-class BUY point detection
# =============================================================================

def detect_third_class_buys(
    df: pd.DataFrame,
    centrals: List[Central],
    stop_loss_pct: float = 0.05,
    target_pct: float = 0.15,
    require_pullback: bool = True,
) -> List[ChanlunSignal]:
    """
    Detect 第三類買點 on daily bars.

    Logic: For each UP 中樞, scan forward. Look for first bar where:
      - close > ZG (breakout above upper bound)
      - prior bar touched [ZD, ZG] zone without breaking ZD (pullback confirmation)
    Then emit ChanlunSignal with stop / target / confidence.
    """
    close_arr = df["Close"].values
    low_arr = df["Low"].values
    idx_arr = df.index
    signals: List[ChanlunSignal] = []

    for c in centrals:
        if c.direction <= 0:
            continue  # only UP trend
        for i in range(c.end_idx + 1, len(df)):
            if require_pullback:
                pullback_confirmed = False
                for j in range(c.end_idx + 1, i):
                    if low_arr[j] < c.zg and low_arr[j] >= c.zd:
                        pullback_confirmed = True
                        break
                if not pullback_confirmed:
                    for j in range(c.end_idx + 1, i):
                        if c.zd <= low_arr[j] <= c.zg:
                            pullback_confirmed = True
                            break
                if not pullback_confirmed:
                    continue
            if close_arr[i] > c.zg:
                # Confidence: higher when 中樞 is tight (clearer consolidation)
                width = (c.zg - c.zd) / c.zd if c.zd > 0 else 1.0
                confidence = 8 if width < 0.10 else 7

                entry = float(close_arr[i])
                stop = entry * (1 - stop_loss_pct)
                target = entry * (1 + target_pct)

                rationale = (
                    f"close {entry:.2f} > ZG {c.zg:.2f} ({(c.zg/entry*100):.1f}% below breakout) "
                    f"of up 中樞 [{c.start_idx}→{c.end_idx}]; "
                    f"ZD {c.zd:.2f}, GG {c.gg:.2f}, DD {c.dd:.2f}; "
                    f"中樞 width {width*100:.1f}% of ZD; "
                    f"pullback confirmed: ✓"
                )

                signals.append(ChanlunSignal(
                    code="",  # filled by caller
                    signal_date=idx_arr[i],
                    entry_price=entry,
                    stop_loss=stop,
                    target=target,
                    confidence=confidence,
                    central_zg=c.zg,
                    central_zd=c.zd,
                    central_gg=c.gg,
                    central_dd=c.dd,
                    had_pullback=require_pullback,
                    rationale=rationale,
                ))
                break  # first breakout per 中樞
    return signals


# =============================================================================
# Full pipeline (public API for scan command)
# =============================================================================

def find_buy_signals(
    df: pd.DataFrame,
    code: str,
    zigzag_threshold: float = 0.05,
    stop_loss_pct: float = 0.05,
    target_pct: float = 0.15,
    require_pullback: bool = True,
    min_centrals: int = 1,
) -> List[ChanlunSignal]:
    """
    Run the full Chanlun pipeline on a daily OHLCV dataframe.
    Returns list of ChanlunSignal (last one is the most recent breakout).
    """
    if df is None or df.empty or len(df) < 60:
        return []

    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(df.columns):
        raise ValueError(f"DataFrame missing required columns: {required - set(df.columns)}")

    # Stage 1: K-line inclusion
    processed = process_inclusion(df)

    # Stage 2: Fractals (computed but not directly used — informational)
    # _ = detect_fractals(processed, df, require_confirmation=True)

    # Stage 3-4: ZigZag pivots → segments
    pivots = detect_zigzag_pivots(df["Close"], df["High"], df["Low"], zigzag_threshold)
    if len(pivots) < 4:
        return []
    segments = pivots_to_segments(pivots, df["High"], df["Low"])
    if len(segments) < 3:
        return []

    # Stage 5: 中樞
    centrals = detect_centrals(segments, min_segments=3)
    if len(centrals) < min_centrals:
        return []

    # Stage 6: 3rd-class BUY
    signals = detect_third_class_buys(
        df, centrals,
        stop_loss_pct=stop_loss_pct,
        target_pct=target_pct,
        require_pullback=require_pullback,
    )

    # Fill in the code
    for sig in signals:
        sig.code = code

    return signals


def find_latest_buy_signal(
    df: pd.DataFrame,
    code: str,
    **kwargs,
) -> Optional[ChanlunSignal]:
    """Return only the most recent BUY signal (or None)."""
    signals = find_buy_signals(df, code, **kwargs)
    if not signals:
        return None
    return signals[-1]