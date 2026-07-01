"""Backtest snapshot fetcher.
Uses yfinance 2y history, filters to <= as_of_date, simulates "today's close" as of that date.
Skips Tencent/Futu (live only — not available for historical dates).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import yfinance as yf

from src.data_fetcher import _hk_code_yfinance  # reuse

logger = logging.getLogger(__name__)

# Disk cache: /tmp/snap_cache/{ticker}/{date}.json
CACHE_DIR = "/tmp/snap_cache"


def _cache_path(code: str, as_of_date: str) -> str:
    safe = code.replace("/", "_").replace("\\", "_")
    d = os.path.join(CACHE_DIR, safe)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{as_of_date}.json")


def _load_cache(code: str, as_of_date: str) -> Optional[dict]:
    p = _cache_path(code, as_of_date)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            return None
    return None


def _save_cache(code: str, as_of_date: str, snap: dict) -> None:
    p = _cache_path(code, as_of_date)
    try:
        json.dump(snap, open(p, "w"), ensure_ascii=False, default=str)
    except Exception as e:
        logger.debug(f"cache save failed: {e}")


# Global rate-limit state: when yfinance returns 429, sleep before next call
RATE_LIMIT_SLEEP = 60  # seconds
_rate_limited_until = [0.0]


def _history_with_timeout(t: yf.Ticker, timeout: int = 25, max_retries: int = 2) -> Optional[pd.DataFrame]:
    """Fetch 2y history with thread-safe timeout + retry on rate-limit."""
    global _rate_limited_until
    for attempt in range(max_retries):
        # Honor global rate-limit window
        now = time.time()
        if _rate_limited_until[0] > now:
            time.sleep(_rate_limited_until[0] - now)
        result = [None]
        def _call():
            try:
                result[0] = t.history(period="2y", auto_adjust=False)
            except Exception as e:
                err = str(e)
                if "Too Many Requests" in err or "Rate limited" in err:
                    _rate_limited_until[0] = time.time() + RATE_LIMIT_SLEEP
                logger.warning(f"yfinance history error (attempt {attempt+1}): {e}")
                result[0] = None
        th = threading.Thread(target=_call)
        th.start()
        th.join(timeout=timeout)
        if th.is_alive():
            logger.warning(f"yfinance timeout for ticker (attempt {attempt+1})")
            continue
        hist = result[0]
        if hist is None or len(hist) == 0:
            if attempt < max_retries - 1:
                time.sleep(5)
                continue
            return None
        return hist
    return None


def _compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-diff)
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _ma(closes: list[float], window: int) -> Optional[float]:
    if len(closes) < window:
        return None
    return round(sum(closes[-window:]) / window, 4)


def fetch_historical_snapshot(code: str, as_of_date: str) -> Optional[dict]:
    """Fetch snapshot as of a historical date.
    Uses yfinance 2y history, filters to <= as_of_date.
    Caches to disk by (code, date). Returns same dict structure as fetch_snapshot.
    """
    # Try cache first
    cached = _load_cache(code, as_of_date)
    if cached is not None:
        return cached

    yf_code = _hk_code_yfinance(code)
    yf_code = _hk_code_yfinance(code)
    t = yf.Ticker(yf_code)
    hist = _history_with_timeout(t)
    if hist is None or len(hist) == 0:
        return None

    # Filter to <= as_of_date (handle tz-aware yfinance index)
    target_naive = pd.Timestamp(as_of_date)
    # Make target tz-aware to match yfinance index (US/Eastern for US, Asia/Hong_Kong for HK)
    if hist.index.tz is not None:
        target = target_naive.tz_localize(hist.index.tz)
    else:
        target = target_naive
    hist_filtered = hist[hist.index <= target]
    if len(hist_filtered) < 1:
        return None
    # Drop NaN close rows
    closes_df = hist_filtered["Close"].dropna()
    if len(closes_df) < 1:
        return None
    closes = closes_df.astype(float).tolist()

    # Use the last close <= as_of_date as "last_price"
    last_price = closes[-1]
    if len(closes) >= 2:
        prev_close = closes[-2]
    else:
        prev_close = last_price
    change_pct = round((last_price - prev_close) / prev_close * 100, 2) if prev_close else 0.0

    # Build kline_30d (last 30 days up to as_of_date)
    kline_30d = []
    for idx, row in hist_filtered.tail(30).iterrows():
        kline_30d.append({
            "date": idx.strftime("%Y-%m-%d"),
            "close": round(float(row["Close"]), 4) if not pd.isna(row["Close"]) else None,
            "high": round(float(row["High"]), 4) if not pd.isna(row["High"]) else None,
            "low": round(float(row["Low"]), 4) if not pd.isna(row["Low"]) else None,
            "volume": int(row["Volume"]) if not pd.isna(row["Volume"]) else 0,
        })

    # MAs and RSI
    ma20 = _ma(closes, 20)
    ma50 = _ma(closes, 50)
    ma100 = _ma(closes, 100)
    ma200 = _ma(closes, 200)
    rsi14 = _compute_rsi(closes, 14)

    # 52w high/low
    last_252 = closes[-252:] if len(closes) >= 252 else closes
    w52_high = round(max(last_252), 4) if last_252 else None
    w52_low = round(min(last_252), 4) if last_252 else None

    # YTD change
    ytd_open = None
    for c in closes:
        # crude: first close of calendar year
        pass
    # approximate YTD: first close of same year
    year_start_idx = 0
    target_year = pd.Timestamp(as_of_date).year
    for i, c in enumerate(closes):
        idx = closes_df.index[i]
        # idx may be tz-aware
        idx_year = idx.year
        if idx_year == target_year:
            year_start_idx = i
            break
    ytd_open = closes[year_start_idx] if year_start_idx < len(closes) else closes[0]
    ytd_chg = round((last_price - ytd_open) / ytd_open * 100, 2) if ytd_open else 0.0

    # Volume + turnover estimate (use last 1d volume)
    last_vol = int(hist_filtered["Volume"].iloc[-1]) if not pd.isna(hist_filtered["Volume"].iloc[-1]) else 0
    turnover = round(last_price * last_vol, 0)

    # 5d avg volume ratio
    vol_5d = sum(hist_filtered["Volume"].tail(5).fillna(0).tolist()) / 5 if len(hist_filtered) >= 5 else 0
    vol_ratio = round(last_vol / vol_5d, 2) if vol_5d > 0 else 1.0

    # Day high/low (use day_range from as_of_date row)
    last_row = hist_filtered.iloc[-1]
    day_high = round(float(last_row["High"]), 4) if not pd.isna(last_row["High"]) else last_price
    day_low = round(float(last_row["Low"]), 4) if not pd.isna(last_row["Low"]) else last_price
    day_range_pct = round((day_high - day_low) / last_price * 100, 2) if last_price else 0.0

    # market_cap (rough — not all tickers have it via yfinance)
    market_cap = None
    try:
        info = t.info
        if isinstance(info, dict):
            market_cap = info.get("marketCap")
    except Exception:
        pass

    # PE/PB from yfinance info
    pe_ttm = None
    pb = None
    div_yield = None
    try:
        info = t.info
        if isinstance(info, dict):
            pe_ttm = info.get("trailingPE")
            pb = info.get("priceToBook")
            div_yield = info.get("dividendYield")
    except Exception:
        pass

    # 52w position (last_price / 52w high) — used to detect "extended" setups
    extended_pct = round((last_price - w52_high) / w52_high * 100, 2) if w52_high else 0.0

    snap = {
        "code": code,
        "name_zh": "",
        "name_en": "",
        "last_price": round(last_price, 4),
        "prev_close": round(prev_close, 4),
        "change_pct": change_pct,
        "day_high": day_high,
        "day_low": day_low,
        "volume": last_vol,
        "turnover_hkd": turnover,
        "turnover_local": turnover,
        "day_range_pct": day_range_pct,
        "vol_ratio": vol_ratio,
        "pe_ttm": pe_ttm,
        "pb": pb,
        "dividend_yield": div_yield,
        "market_cap_hkd": market_cap,
        "market_cap": market_cap,
        "market_cap_local": market_cap,
        "ma20": ma20,
        "ma50": ma50,
        "ma100": ma100,
        "ma200": ma200,
        "rsi14": rsi14,
        "52w_high": w52_high,
        "52w_low": w52_low,
        "ytd_change_pct": ytd_chg,
        "kline_30d": kline_30d,
        "sector": "",
        "source": "yfinance_historical",
        "history_bars": len(closes),
        "history_note": f"Historical snapshot as of {as_of_date} via yfinance 2y history. Used for backtest only — not real-time.",
        "data_as_of": as_of_date,
        "extended_pct": extended_pct,
    }
    _save_cache(code, as_of_date, snap)
    return snap


if __name__ == "__main__":
    # Smoke test
    import sys
    logging.basicConfig(level=logging.WARNING)
    code = sys.argv[1] if len(sys.argv) > 1 else "0700.HK"
    as_of = sys.argv[2] if len(sys.argv) > 2 else "2026-05-29"
    snap = fetch_historical_snapshot(code, as_of)
    if snap:
        print(f"\n=== {code} as of {as_of} ===")
        print(f"  last_price: {snap['last_price']}")
        print(f"  change_pct: {snap['change_pct']}")
        print(f"  ma20/50/100/200: {snap['ma20']}/{snap['ma50']}/{snap['ma100']}/{snap['ma200']}")
        print(f"  rsi14: {snap['rsi14']}")
        print(f"  52w high/low: {snap['52w_high']}/{snap['52w_low']}")
        print(f"  kline_30d bars: {len(snap['kline_30d'])}")
    else:
        print(f"{code} as of {as_of}: FAILED")
