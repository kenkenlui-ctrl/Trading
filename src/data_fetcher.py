"""
HK stock data fetcher. Primary: Futu OpenD. Fallback: YFinance.

Returns structured snapshot for LLM consumption:
{
    "code": "0700.HK",
    "name_zh": "騰訊控股",
    "name_en": "Tencent Holdings",
    "last_price": 410.6,
    "prev_close": 408.2,
    "change_pct": 0.59,
    "day_high": 412.0,
    "day_low": 407.8,
    "volume": 12345678,
    "turnover_hkd": 5067890123,
    "pe_ttm": 18.5,
    "pb": 4.2,
    "dividend_yield": 0.85,
    "market_cap_hkd": 3.85e12,
    "ma20": 405.3,
    "ma50": 402.1,
    "ma100": 398.7,
    "ma200": 392.5,
    "rsi14": 58.2,
    "52w_high": 425.0,
    "52w_low": 295.6,
    "ytd_change_pct": 18.5,
    "kline_30d": [...],      # last 30 days OHLCV
    "intraday_15m": [...],   # optional: today's 15m bars (from cached files)
    "sector": "科技 / 互聯網",
    "source": "futu" | "yfinance"
}
"""

from __future__ import annotations

import json
import logging
import math
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from .config import get_config

logger = logging.getLogger(__name__)

# Optional Futu import — only fail when actually trying to use it
try:
    from futu import OpenQuoteContext, RET_OK, KLType, SubType  # type: ignore
    FUTU_AVAILABLE = True
except ImportError:
    FUTU_AVAILABLE = False
    logger.debug("futu-api not installed; will use YFinance only")


# ============ Helpers ============

def _safe_float(x, default=None):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _hk_code_yfinance(code: str) -> str:
    """0700.HK -> 0700.HK (yfinance already accepts this format)."""
    return code


def _hk_int_yfinance(code: str) -> int:
    """0700.HK -> 700 (yfinance uses 4-digit zero-padded)."""
    digits = code.split(".")[0].lstrip("0")
    return int(digits) if digits else 0


# ============ Indicators ============

def _rsi(closes: list[float], period: int = 14) -> Optional[float]:
    # Filter out NaN/None values
    valid = [c for c in closes if c is not None and not math.isnan(c)]
    if len(valid) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(valid)):
        diff = valid[i] - valid[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _ma(closes: list[float], period: int) -> Optional[float]:
    valid = [c for c in closes if c is not None and not math.isnan(c)]
    if len(valid) < period:
        return None
    return sum(valid[-period:]) / period


# ============ YFinance backend ============

def _fetch_yfinance(code: str) -> Optional[dict]:
    """Fetch snapshot from YFinance as fallback. Thread-safe (no signal.alarm)."""
    try:
        yf_code = _hk_code_yfinance(code)
        t = yf.Ticker(yf_code)

        # Thread-safe timeout wrapper — works in main AND subprocess threads
        import threading

        def _call_history():
            nonlocal hist_result
            hist_result = t.history(period="1y", auto_adjust=False)

        hist_result = None
        hist_thread = threading.Thread(target=_call_history)
        hist_thread.start()
        hist_thread.join(timeout=25)  # 25s cap
        if hist_thread.is_alive():
            # Timeout — thread still running, ticker is unresponsive
            logger.warning(f"YFinance history timed out for {code}")
            return None
        hist = hist_result

        # Guard against ambiguous DataFrame truth value (NaN rows cause this)
        try:
            is_empty = hist is None or len(hist) == 0
        except (ValueError, TypeError):
            is_empty = True

        if is_empty:
            logger.warning(f"YFinance: no history for {code}")
            return None

        # Build closes list — drop NaN values (e.g. today's incomplete bar)
        all_closes = hist["Close"].dropna().astype(float).tolist()
        if len(all_closes) < 1:
            logger.warning(f"YFinance: not enough valid close bars for {code}")
            return None

        # Track actual history bars fetched — so LLM can distinguish "new listing"
        # (YFinance has full history but ticker is fresh) from "data source limitation"
        # (YFinance returns 1 row because foreign-listed Chinese stock has incomplete data)
        history_bars = len(all_closes)

        last_price = all_closes[-1]
        prev_close = all_closes[-2] if len(all_closes) >= 2 else last_price
        change_pct = (last_price - prev_close) / prev_close * 100 if prev_close else 0.0

        # Last bar info for H/L/V
        last_bar = hist.loc[hist["Close"].last_valid_index()]

        # K-line (last 30 days with valid closes)
        kline_30d = []
        for i in range(len(hist) - 1, max(0, len(hist) - 30) - 1, -1):
            row = hist.iloc[i]
            close = _safe_float(row["Close"])
            if close is None:
                continue  # skip today's incomplete bar
            kline_30d.insert(0, {
                "date": hist.index[i].strftime("%Y-%m-%d"),
                "open": _safe_float(row["Open"]) or close,
                "high": _safe_float(row["High"]) or close,
                "low": _safe_float(row["Low"]) or close,
                "close": close,
                "volume": _safe_float(row["Volume"]) or 0.0,
            })
            if len(kline_30d) >= 30:
                break

        # Info (may be empty for HK tickers via YFinance) — thread-safe timeout
        info = {}
        def _call_info():
            nonlocal info_result
            info_result = t.info or {}
        info_result = None
        info_thread = threading.Thread(target=_call_info)
        info_thread.start()
        info_thread.join(timeout=10)  # 10s cap
        if not info_thread.is_alive():
            info = info_result or {}

        name_zh = info.get("longName") or info.get("shortName") or ""
        # YFinance returns Chinese names for HK sometimes
        if name_zh and not any("\u4e00" <= c <= "\u9fff" for c in name_zh):
            name_zh = _guess_chinese_name_from_code(code) or name_zh

        day_high = float(last_bar.get("High", last_price))
        day_low = float(last_bar.get("Low", last_price))
        day_volume = float(last_bar.get("Volume", 0))
        day_range_pct = round(((day_high - day_low) / last_price * 100), 2) if last_price else 0.0
        avg_vol_5d = float(hist["Volume"].tail(5).mean()) if len(hist) >= 5 else 0
        vol_ratio = round(day_volume / avg_vol_5d, 2) if avg_vol_5d > 0 else 0.0

        snapshot = {
            "code": code,
            "name_zh": name_zh or _guess_chinese_name_from_code(code),
            "name_en": info.get("longName") or info.get("shortName") or "",
            "last_price": last_price,
            "prev_close": prev_close,
            "change_pct": round(change_pct, 2),
            "day_high": day_high,
            "day_low": day_low,
            "volume": day_volume,
            "turnover_hkd": day_volume * last_price,
            "day_range_pct": day_range_pct,
            "vol_ratio": vol_ratio,
            "pe_ttm": _safe_float(info.get("trailingPE")),
            "pb": _safe_float(info.get("priceToBook")),
            "dividend_yield": _safe_float(info.get("dividendYield")),
            "market_cap_hkd": _safe_float(info.get("marketCap")),
            "ma20": _ma(all_closes, 20),
            "ma50": _ma(all_closes, 50),
            "ma100": _ma(all_closes, 100),
            "ma200": _ma(all_closes, 200),
            "rsi14": _rsi(all_closes, 14),
            "52w_high": max(all_closes[-252:]) if len(all_closes) >= 252 else max(all_closes),
            "52w_low": min(all_closes[-252:]) if len(all_closes) >= 252 else min(all_closes),
            "ytd_change_pct": round((last_price - all_closes[0]) / all_closes[0] * 100, 2) if all_closes else 0.0,
            "kline_30d": kline_30d,
            "sector": info.get("sector") or info.get("industry") or "",
            "source": "yfinance",
            "history_bars": history_bars,
            "history_note": "" if history_bars >= 100 else f"WARNING: only {history_bars} daily bars available from YFinance (need 200+ for MA200). Data source limitation — likely a newer/foreign-listed HK ticker. DO NOT infer this is a newly-listed stock based on missing MAs.",
        }
        # Round float fields
        for k in ("last_price", "prev_close", "day_high", "day_low", "ma20", "ma50", "ma100", "ma200",
                  "52w_high", "52w_low", "market_cap_hkd", "turnover_hkd"):
            v = snapshot.get(k)
            if isinstance(v, float) and not math.isnan(v):
                snapshot[k] = round(v, 4)
        for k in ("ma20", "ma50", "ma100", "ma200", "rsi14"):
            v = snapshot.get(k)
            if v is not None:
                snapshot[k] = round(v, 2)
        return snapshot
    except Exception as e:
        logger.warning(f"YFinance failed for {code}: {e}")
        return None


# ============ Futu backend ============

def _futu_reachable() -> bool:
    """Fast TCP probe to Futu OpenD. Returns True only if reachable."""
    cfg = get_config()
    try:
        s = socket.create_connection((cfg.futu_host, cfg.futu_port), timeout=2)
        s.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def _fetch_futu(code: str) -> Optional[dict]:
    """Fetch snapshot from Futu OpenD. Returns None if Futu unavailable or times out."""
    if not FUTU_AVAILABLE:
        return None
    cfg = get_config()
    ctx = None
    try:
        # Pre-check: if OpenD isn't reachable, skip immediately (avoids 30s+ hang)
        if not _futu_reachable():
            logger.debug(f"Futu OpenD unreachable at {cfg.futu_host}:{cfg.futu_port}")
            return None
        ctx = OpenQuoteContext(host=cfg.futu_host, port=cfg.futu_port)
        # Convert code: "0700.HK" -> "HK.00700" for Futu
        digits = code.split(".")[0].zfill(5)
        futu_code = f"HK.{digits}"

        # Snapshot
        ret, snap = ctx.get_market_snapshot([futu_code])
        snap_ok = isinstance(snap, pd.DataFrame) and not snap.empty
        if ret != RET_OK or not snap_ok:
            logger.warning(f"Futu snapshot failed for {code}: {snap}")
            return None
        s = snap.iloc[0]  # DataFrame → first row as Series

        # Daily K-line for indicators (need 300+ days for MA200)
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=300)).strftime("%Y-%m-%d")
        ret, klines, *_ = ctx.request_history_kline(futu_code, start=start, end=end, ktype=KLType.K_DAY)
        # Guard against DataFrame ambiguity — use .empty property, never bool()
        klines_ok = isinstance(klines, pd.DataFrame) and not klines.empty
        if ret != RET_OK or not klines_ok:
            logger.warning(f"Futu kline failed for {code}: {klines}")
            return None
        # Convert DataFrame to list-of-dicts so row-access code works unchanged
        if isinstance(klines, pd.DataFrame):
            klines = klines.to_dict("records")

        closes = [float(k["close"]) for k in klines]
        last_bar = klines[-1]
        prev_close = float(klines[-2]["close"]) if len(klines) >= 2 else float(last_bar["close"])
        last_price = float(last_bar["close"])
        change_pct = (last_price - prev_close) / prev_close * 100 if prev_close else 0.0

        kline_30d = [
            {
                "date": k["time_key"][:10],
                "open": float(k["open"]),
                "high": float(k["high"]),
                "low": float(k["low"]),
                "close": float(k["close"]),
                "volume": float(k["volume"]),
            }
            for k in klines[-30:]
        ]

        # 52-week range from kline history
        last_252 = klines[-252:] if len(klines) >= 252 else klines
        hi_52w = max(float(k["high"]) for k in last_252)
        lo_52w = min(float(k["low"]) for k in last_252)
        ytd_open = next((float(k["open"]) for k in klines if k["time_key"][:4] == datetime.now().strftime("%Y")), closes[0])
        ytd_chg = (last_price - ytd_open) / ytd_open * 100 if ytd_open else 0.0

        day_high_f = float(s.get("high_price", last_price))
        day_low_f = float(s.get("low_price", last_price))
        day_vol_f = float(s.get("volume", 0))
        day_range_pct_f = round(((day_high_f - day_low_f) / last_price * 100), 2) if last_price else 0.0
        avg_vol_5d_f = sum(float(k["volume"]) for k in klines[-5:]) / 5.0 if len(klines) >= 5 else 0
        vol_ratio_f = round(day_vol_f / avg_vol_5d_f, 2) if avg_vol_5d_f > 0 else 0.0

        snapshot = {
            "code": code,
            "name_zh": s.get("name", ""),
            "name_en": s.get("name_en", "") or s.get("name", ""),
            "last_price": last_price,
            "prev_close": _safe_float(s.get("prev_close_price")) or prev_close,
            "change_pct": round(change_pct, 2),
            "day_high": day_high_f,
            "day_low": day_low_f,
            "volume": day_vol_f,
            "turnover_hkd": float(s.get("turnover", 0)),
            "day_range_pct": day_range_pct_f,
            "vol_ratio": vol_ratio_f,
            "pe_ttm": _safe_float(s.get("pe_ttm_ratio")) or _safe_float(s.get("pe_ratio")),
            "pb": _safe_float(s.get("pb_ratio")),
            "dividend_yield": _safe_float(s.get("dividend_ratio_ttm")) or _safe_float(s.get("dividend_ratio")) or _safe_float(s.get("dividend_yield")),
            "market_cap_hkd": _safe_float(s.get("total_market_val")) or _safe_float(s.get("market_val")) or _safe_float(s.get("market_cap")),
            "ma20": round(_ma(closes, 20), 2),
            "ma50": round(_ma(closes, 50), 2),
            "ma100": round(_ma(closes, 100), 2),
            "ma200": round(_ma(closes, 200), 2),
            "rsi14": round(_rsi(closes, 14), 2) if _rsi(closes, 14) is not None else None,
            "52w_high": hi_52w,
            "52w_low": lo_52w,
            "ytd_change_pct": round(ytd_chg, 2),
            "kline_30d": kline_30d,
            "sector": "",
            "source": "futu",
            "history_bars": len(klines),
            "history_note": "" if len(klines) >= 100 else f"WARNING: only {len(klines)} daily bars available from Futu (need 200+ for MA200).",
        }
        # Round
        for k in ("last_price", "prev_close", "day_high", "day_low", "52w_high", "52w_low",
                  "market_cap_hkd", "turnover_hkd"):
            v = snapshot.get(k)
            if isinstance(v, float) and not math.isnan(v):
                snapshot[k] = round(v, 4)
        return snapshot
    except Exception as e:
        logger.warning(f"Futu failed for {code}: {e}")
        return None
    finally:
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass


# ============ Sina / Tencent live quote overlay ============
# YFinance 對 HK stock 經常有 1-day delay — 今日 close 往往係 yesterday。
# Sina hq.sinajs.cn 同 Tencent qt.gtimg.cn 係實時（< 1 min delay），免費，無 API key。
# 用佢哋做 live price overlay 確保 LLM 攞到嘅係 today's price 而非 stale yesterday。

import urllib.request

# Sina:  "var hq_str_hk02513="KNOWLEDGE ATLAS,智譜,2334,2350,2360,1933,2046,-304,-12.94,..."
#       columns: name_en,name_zh,open,prev_close,high,low,current,change_amt,change_pct,
#                bid,ask,turnover_hkd,volume,...
def _parse_sina_hk(raw: str) -> Optional[dict]:
    try:
        # raw like:  var hq_str_hk02513="KNOWLEDGE ATLAS,...,2026/06/26,16:08";
        i1 = raw.find('"')
        i2 = raw.rfind('"')
        if i1 < 0 or i2 <= i1:
            return None
        body = raw[i1+1:i2]
        fields = body.split(',')
        if len(fields) < 13:
            return None
        return {
            "name_en": fields[0],
            "name_zh": fields[1],
            "open": _safe_float(fields[2]),
            "prev_close": _safe_float(fields[3]),
            "high": _safe_float(fields[4]),
            "low": _safe_float(fields[5]),
            "current": _safe_float(fields[6]),
            "change_amt": _safe_float(fields[7]),
            "change_pct": _safe_float(fields[8]),
            "turnover_hkd": _safe_float(fields[10]),
            "volume": _safe_float(fields[11]),
            "datetime": f"{fields[fields.index('2026') if '2026' in fields else len(fields)-2]} {fields[fields.index('16:08') if '16:08' in fields else len(fields)-1]}" if '2026' in fields else "",
        }
    except Exception:
        return None


def _fetch_sina_live(code: str) -> Optional[dict]:
    """Fetch live HK quote from Sina hq.sinajs.cn. Returns None on failure."""
    try:
        # 0700.HK -> hk00700
        digits = code.split(".")[0].zfill(5)
        url = f"https://hq.sinajs.cn/list=hk{digits}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn",
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("gbk", errors="replace")
        parsed = _parse_sina_hk(raw)
        if not parsed or parsed.get("current") is None or parsed.get("current") == 0:
            logger.debug(f"Sina live: no data for {code}")
            return None
        return parsed
    except Exception as e:
        logger.debug(f"Sina live failed for {code}: {e}")
        return None


# Tencent:  v_hk02513="100~智譜~02513~2046.000~2350.000~2334.000~2124287.0~...~KNOWLEDGE ATLAS~..."
# Verified field indices from raw dump:
#   [3]  current    [4]  prev_close   [5]  open       [6]  volume
#   [30] datetime   [31] change_amt  [32] change_pct [33] high     [34] low
#   [37] turnover_yuan (in HK$, NOT 億)              [43] pe_ttm
#   [44] pb        [45] market_cap (億)              [46] name_en
#   [48] 52w_high  [49] 52w_low
def _parse_tencent_hk(raw: str) -> Optional[dict]:
    try:
        i1 = raw.find('"')
        i2 = raw.rfind('"')
        if i1 < 0 or i2 <= i1:
            return None
        body = raw[i1+1:i2]
        fields = body.split('~')
        if len(fields) < 50:
            return None
        return {
            "name_zh": fields[1],
            "code": fields[2],
            "current": _safe_float(fields[3]),
            "prev_close": _safe_float(fields[4]),
            "open": _safe_float(fields[5]),
            "volume": _safe_float(fields[6]),
            "datetime": fields[30],
            "change_amt": _safe_float(fields[31]),
            "change_pct": _safe_float(fields[32]),
            "high": _safe_float(fields[33]),
            "low": _safe_float(fields[34]),
            "turnover": _safe_float(fields[37]) if fields[37] else None,  # in 元 (HK$)
            "pe_ttm": _safe_float(fields[43]),
            "pb": _safe_float(fields[44]),
            "market_cap": _safe_float(fields[45]),  # in 億
            "name_en": fields[46] if len(fields) > 46 else "",
            "52w_high": _safe_float(fields[48]) if len(fields) > 48 else None,
            "52w_low": _safe_float(fields[49]) if len(fields) > 49 else None,
        }
    except Exception:
        return None


def _fetch_tencent_live(code: str) -> Optional[dict]:
    """Fetch live HK quote from Tencent qt.gtimg.cn. Returns comprehensive fields incl PE/PB/market_cap."""
    try:
        digits = code.split(".")[0].zfill(5)
        url = f"https://qt.gtimg.cn/q=hk{digits}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("gbk", errors="replace")
        parsed = _parse_tencent_hk(raw)
        if not parsed or parsed.get("current") is None or parsed.get("current") == 0:
            logger.debug(f"Tencent live: no data for {code}")
            return None
        return parsed
    except Exception as e:
        logger.debug(f"Tencent live failed for {code}: {e}")
        return None


def _overlay_live_price(snapshot: dict, code: str) -> dict:
    """Overlay Sina/Tencent live price onto YFinance historical snapshot.

    YFinance last_price is often yesterday's close for HK stocks (15-min delayed or stale).
    We fetch live price from Tencent (primary) or Sina (fallback) and OVERLAY only:
    - last_price, prev_close, change_pct (definitely correct)
    - day_high, day_low, open (today's intraday)
    - volume, turnover (live cumulative)
    - name_zh / name_en (Tencent is more reliable than YFinance guess table)
    - data_as_of timestamp (so LLM knows data freshness)

    We KEEP YFinance values for:
    - pe_ttm, pb, market_cap (Tencent field indices unreliable for large caps — e.g.
      [44] for 0700.HK returns 37507 which is market_cap in 億, not PB)
    - ma20/50/100/200, rsi14, kline_30d (YFinance historical bars)
    - ytd_change_pct
    """
    live = _fetch_tencent_live(code) or _fetch_sina_live(code)
    if not live:
        return snapshot

    live_price = live.get("current")
    prev_close = live.get("prev_close") or snapshot.get("prev_close")

    if live_price and live_price > 0:
        snapshot["last_price"] = live_price
        if prev_close and prev_close > 0:
            snapshot["prev_close"] = prev_close
            snapshot["change_pct"] = round((live_price - prev_close) / prev_close * 100, 2)
        if live.get("high"):
            snapshot["day_high"] = live["high"]
        if live.get("low"):
            snapshot["day_low"] = live["low"]
        if live.get("volume"):
            snapshot["volume"] = live["volume"]
        if live.get("turnover"):
            snapshot["turnover_hkd"] = live["turnover"]
        elif live.get("turnover_hkd"):
            snapshot["turnover_hkd"] = live["turnover_hkd"]
        # Name from live source (more reliable than YFinance guess table)
        if live.get("name_zh"):
            snapshot["name_zh"] = live["name_zh"]
        elif live.get("name_en"):
            snapshot["name_en"] = live["name_en"]

        snapshot["data_as_of"] = live.get("datetime") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        snapshot["live_source"] = "tencent" if live.get("volume") else "sina"
    return snapshot

# Lightweight built-in table for common HK tickers — extended over time.
_HK_NAMES_ZH = {
    "0001.HK": "長和",
    "0002.HK": "中電控股",
    "0003.HK": "香港中華煤氣",
    "0004.HK": "九龍倉集團",
    "0005.HK": "匯豐控股",
    "0008.HK": "電訊盈科",
    "0011.HK": "恒生銀行",
    "0019.HK": "太古股份公司A",
    "0267.HK": "中信股份",
    "0268.HK": "金蝶國際",
    "0288.HK": "萬洲國際",
    "0291.HK": "華潤啤酒",
    "0316.HK": "東方海外國際",
    "0322.HK": "康師傅控股",
    "0347.HK": "鞍鋼股份",
    "0354.HK": "中國軟件國際",
    "0388.HK": "香港交易所",
    "0425.HK": "敏實集團",
    "0489.HK": "東風集團股份",
    "0576.HK": "浙江滬杭甬",
    "0639.HK": "首鋼資源",
    "0683.HK": "嘉里建設",
    "0909.HK": "明源雲",
    "0981.HK": "中芯國際",
    "1024.HK": "快手-W",
    "1088.HK": "中國神華",
    "1109.HK": "華潤置地",
    "1211.HK": "比亞迪股份",
    "1299.HK": "友邦保險",
    "1398.HK": "工商銀行",
    "1548.HK": "金斯瑞生物科技",
    "1801.HK": "信達生物",
    "1810.HK": "小米集團-W",
    "1880.HK": "中國旅遊集團",
    "2015.HK": "理想汽車-W",
    "2020.HK": "安踏體育",
    "2238.HK": "廣汽集團",
    "2269.HK": "藥明生物",
    "2331.HK": "李寧",
    "2359.HK": "藥明康德",
    "2382.HK": "舜宇光學科技",
    "3690.HK": "美團-W",
    "3888.HK": "金山軟件",
    "3988.HK": "中國銀行",
    "6618.HK": "京東健康",
    "6837.HK": "海倫司",
    "6996.HK": "名創優品",
    "9926.HK": "康方生物",
    "9988.HK": "阿里巴巴-W",
    "0700.HK": "騰訊控股",
    "9618.HK": "京東集團-SW",
}


def _guess_chinese_name_from_code(code: str) -> Optional[str]:
    return _HK_NAMES_ZH.get(code)


# ============ Intraday 15m (from trading-platform cache) ============

def _try_load_intraday_15m(code: str) -> list[dict]:
    """Try to load today's 15m bars from trading-platform cache."""
    cfg = get_config()
    # Path pattern: <trading-platform>/apps/worker/data/bars-15m/<code>.json
    # But we don't know exactly where — try a few common spots
    candidates = [
        Path("/Users/kenken/Documents/Gstack/trading-platform/apps/worker/data/bars-15m"),
        Path("/Users/kenken/Documents/Gstack/trading-platform/data/bars-15m"),
        Path("/Users/kenken/Documents/Gstack/trading-platform/data"),
    ]
    for d in candidates:
        f = d / f"{code}.json"
        if f.exists():
            try:
                bars = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(bars, list):
                    return bars[-30:]  # last 30 bars
            except Exception:
                pass
    return []


# ============ Public API ============

def fetch_snapshot(code: str, include_intraday: bool = True) -> Optional[dict]:
    """
    Fetch snapshot for a HK ticker. Try Futu first (full HKEX history); fall back to
    YFinance for history; then overlay live price from Tencent/Sina (YFinance HK is
    15-min delayed, often shows yesterday's close as "last_price").
    Returns dict or None if all sources fail.
    """
    snapshot = _fetch_futu(code) or _fetch_yfinance(code)
    if not snapshot:
        # No YFinance history at all (rare — e.g. 0100.HK only has 1 row).
        # Try Tencent/Sina for a snapshot from live source alone.
        live = _fetch_tencent_live(code) or _fetch_sina_live(code)
        if live and live.get("current"):
            snapshot = {
                "code": code,
                "name_zh": live.get("name_zh", ""),
                "name_en": live.get("name_en", ""),
                "last_price": live["current"],
                "prev_close": live.get("prev_close"),
                "change_pct": live.get("change_pct"),
                "day_high": live.get("high"),
                "day_low": live.get("low"),
                "volume": live.get("volume"),
                "turnover_hkd": live.get("turnover") or live.get("turnover_hkd"),
                "day_range_pct": round(((live["high"] - live["low"]) / live["current"] * 100), 2) if (live.get("high") and live.get("low") and live["current"]) else 0.0,
                "vol_ratio": 0.0,
                "pe_ttm": live.get("pe_ttm"),
                "pb": live.get("pb"),
                "dividend_yield": None,
                "market_cap_hkd": live.get("market_cap"),
                "ma20": None, "ma50": None, "ma100": None, "ma200": None,
                "rsi14": None,
                "52w_high": live.get("52w_high"),
                "52w_low": live.get("52w_low"),
                "ytd_change_pct": None,
                "kline_30d": [],
                "sector": "",
                "source": "tencent" if live.get("pe_ttm") else "sina",
                "history_bars": 0,
                "history_note": "NO historical bars available. Live price/turnover from Tencent/Sina only. Cannot compute MAs/RSI — treat current snapshot as real-time quote, not technical setup.",
                "data_as_of": live.get("datetime"),
            }
    else:
        # Got history (Futu or YFinance). Overlay live price to fix HK delay.
        snapshot = _overlay_live_price(snapshot, code)
    if not snapshot:
        return None
    if include_intraday:
        snapshot["intraday_15m"] = _try_load_intraday_15m(code)
    return snapshot


def fetch_multiple(codes: list[str]) -> dict[str, Optional[dict]]:
    """Fetch snapshots for multiple tickers. Returns {code: snapshot or None}."""
    results = {}
    for code in codes:
        results[code] = fetch_snapshot(code)
    return results


if __name__ == "__main__":
    # Smoke test
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    test_codes = ["0700.HK", "9988.HK", "1810.HK"]
    for code in test_codes:
        snap = fetch_snapshot(code)
        if snap:
            print(f"\n=== {code} ===")
            print(f"  name_zh: {snap.get('name_zh')}")
            print(f"  last_price: {snap.get('last_price')} ({snap.get('change_pct')}%)")
            print(f"  pe: {snap.get('pe_ttm')}, pb: {snap.get('pb')}")
            print(f"  ma5/20/50: {snap.get('ma5')}/{snap.get('ma20')}/{snap.get('ma50')}")
            print(f"  rsi14: {snap.get('rsi14')}")
            print(f"  source: {snap.get('source')}")
            print(f"  kline_30d bars: {len(snap.get('kline_30d', []))}")
        else:
            print(f"\n{code}: FAILED both sources")
