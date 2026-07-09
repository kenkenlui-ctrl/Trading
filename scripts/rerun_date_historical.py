"""Re-run 2026-07-03 report using Futu OpenD for HK historical data.

Fix vs yfinance version:
- HK tickers: use Futu OpenD `request_history_kline(start=target_date, end=next_day)`
  to fetch actual 3/7 OHLC + close. Falls back to live snapshot only if kline fails.
- US tickers: keep yfinance (proven works).

Architecture:
  1. Open one shared Futu OpenQuoteContext for all HK tickers
  2. Per HK ticker: fetch kline, build snapshot dict, run LLM analysis
  3. Per US ticker: use yfinance
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Force correct date BEFORE any pipeline import
os.environ["DSA_REPORT_DATE_OVERRIDE"] = sys.argv[1] if len(sys.argv) > 1 else "2026-07-03"
os.environ["DSA_LLM_MAX_TOKENS"] = "8000"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import yfinance as yf
from futu import OpenQuoteContext, RET_OK, KLType

from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report, init_db
from src.news_fetcher import fetch_news
from src.ticker_loader import load_hk_tickers, load_us_tickers
from src.config import get_config

cfg = get_config()
MAX_WORKERS = int(os.environ.get("DSA_PARALLEL", "10"))
TARGET_DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-07-03"
TARGET_DT = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
NEXT_DT = TARGET_DT + timedelta(days=1)


def to_futu_code(code: str) -> str:
    """0700.HK → HK.00700; HK.00700 stays; US stays as code."""
    if code.endswith(".HK"):
        return f"HK.{code[:-3]}"
    return code


def parse_entry_stop_target(full_md: str) -> tuple[str | None, str | None, str | None]:
    """Extract entry_zone, stop_loss, target_price from LLM markdown."""
    import re
    entry = stop = target = None
    m = re.search(r"入場區間[：:]\s*([^\n]+)", full_md or "")
    if m:
        entry = m.group(1).strip()
    m = re.search(r"止[損蝕]位[：:]\s*([^\n]+)", full_md or "")
    if m:
        stop = m.group(1).strip()
    m = re.search(r"目標價[：:]\s*([^\n]+)", full_md or "")
    if m:
        target = m.group(1).strip()
    return entry, stop, target


def fetch_futu_kline_snapshot(code: str, ctx: OpenQuoteContext) -> dict | None:
    """Fetch actual TARGET_DATE OHLC + close from Futu for HK ticker.

    prev_close is the close of the PREVIOUS trading day (not same as last),
    so we fetch a wider window to get the row before TARGET_DATE.
    """
    futu_code = to_futu_code(code)
    try:
        # Fetch wider window to get both TARGET_DATE close + previous trading day close
        start = (TARGET_DT - timedelta(days=10)).strftime("%Y-%m-%d")
        end = NEXT_DT.strftime("%Y-%m-%d")
        ret, klines, *_ = ctx.request_history_kline(
            futu_code, start=start, end=end, ktype=KLType.K_DAY
        )
        if ret != RET_OK or klines is None or len(klines) == 0:
            return None
        # Find target row
        target_idx = None
        for i, k in klines.iterrows():
            ts = pd.Timestamp(k["time_key"]).strftime("%Y-%m-%d")
            if ts == TARGET_DATE:
                target_idx = i
                break
        if target_idx is None:
            return None
        k = klines.iloc[target_idx]
        last = float(k["close"])
        # prev_close = close of row before target
        if target_idx > 0:
            prev_close = float(klines.iloc[target_idx - 1]["close"])
        else:
            prev_close = last  # fallback if no prior row

        # Try to get pe_ratio from kline
        try:
            pe = float(k["pe_ratio"]) if pd.notna(k.get("pe_ratio")) else None
        except Exception:
            pe = None

        # Try market snapshot for extra fields (sector, market_cap, etc.)
        snap_extra = {}
        try:
            ret2, snap_df, *_ = ctx.get_market_snapshot([futu_code])
            if ret2 == RET_OK and snap_df is not None and len(snap_df) > 0:
                s = snap_df.iloc[0]
                snap_extra = {
                    "name_zh": s.get("name", code),
                    "lot_size": int(s["lot_size"]) if pd.notna(s.get("lot_size")) else None,
                    "52w_high": float(s["highest52weeks_price"]) if pd.notna(s.get("highest52weeks_price")) else None,
                    "52w_low": float(s["lowest52weeks_price"]) if pd.notna(s.get("lowest52weeks_price")) else None,
                    "market_cap": float(s["total_market_val"]) if pd.notna(s.get("total_market_val")) else None,
                    "pe_ttm": float(s["pe_ttm_ratio"]) if pd.notna(s.get("pe_ttm_ratio")) else (pe),
                    "pb": float(s["pb_ratio"]) if pd.notna(s.get("pb_ratio")) else None,
                    "dividend_yield": float(s.get("dividend_ratio_ttm", 0)) if pd.notna(s.get("dividend_ratio_ttm")) else None,
                    "sector": str(s.get("plate_code", "") or ""),
                }
        except Exception:
            pass

        change_pct = (last - prev_close) / prev_close * 100 if prev_close else 0
        return {
            "code": code,
            "last_price": last,
            "prev_close": prev_close,
            "change_pct": round(change_pct, 2),
            "day_high": float(k["high"]),
            "day_low": float(k["low"]),
            "open": float(k["open"]),
            "volume": float(k["volume"]) * 1000,
            "turnover": float(k["turnover"]) if pd.notna(k.get("turnover")) else None,
            "source": f"futu-kline-{TARGET_DATE} ({futu_code})",
            "target_date": TARGET_DATE,
            **snap_extra,
        }
    except Exception as e:
        print(f"  [{code}] futu error: {e}", flush=True)
        return None


def fetch_yfinance_historical(code: str) -> dict | None:
    """Fetch US ticker's data for TARGET_DATE. Falls back to most recent trading day
    if TARGET_DATE is a US market holiday."""
    try:
        t = yf.Ticker(code)
        # Fetch wider window ending AFTER TARGET_DATE so the row is included
        start_window = (TARGET_DT - timedelta(days=20)).strftime("%Y-%m-%d")
        end_window = (NEXT_DT + timedelta(days=1)).strftime("%Y-%m-%d")
        hist = t.history(start=start_window, end=end_window)
        if hist.empty:
            return None
        target_ts = pd.Timestamp(TARGET_DATE).tz_localize(hist.index.tz)
        prev_idx = -1
        if target_ts in hist.index:
            row = hist.loc[target_ts]
            data_date = TARGET_DATE
            source_tag = f"yfinance-historical-{TARGET_DATE} ({code})"
            is_holiday_fallback = False
        else:
            # Holiday/weekend fallback: use most recent close BEFORE target
            available_dates = hist.index[hist.index < target_ts]
            if len(available_dates) == 0:
                return None
            actual_date = available_dates[-1]
            row = hist.loc[actual_date]
            data_date = actual_date.strftime("%Y-%m-%d")
            source_tag = f"yfinance-holiday-fallback ({code}, {TARGET_DATE} US market closed, using {data_date} close)"
            is_holiday_fallback = True
            prev_idx = -1

        # prev_idx: get row before current row for change_pct
        try:
            current_loc = hist.index.get_loc(target_ts) if not is_holiday_fallback else hist.index.get_loc(pd.Timestamp(data_date).tz_localize(hist.index.tz))
            prev_close = float(hist.iloc[current_loc - 1]["Close"]) if current_loc > 0 else float(row["Close"])
        except Exception:
            prev_close = float(row["Close"])

        last = float(row["Close"])
        chg_pct = (last - prev_close) / prev_close * 100 if prev_close else 0
        info = {}
        try:
            raw_info = t.info or {}
            info = {
                "pe_ttm": raw_info.get("trailingPE"),
                "pb": raw_info.get("priceToBook"),
                "market_cap": raw_info.get("marketCap"),
                "dividend_yield": (raw_info.get("dividendYield") or 0) * 100 if raw_info.get("dividendYield") else None,
                "52w_high": raw_info.get("fiftyTwoWeekHigh"),
                "52w_low": raw_info.get("fiftyTwoWeekLow"),
                "name_zh": raw_info.get("longName") or raw_info.get("shortName") or code,
            }
        except Exception:
            pass
        return {
            "code": code,
            "last_price": last,
            "prev_close": prev_close,
            "change_pct": round(chg_pct, 2),
            "day_high": float(row["High"]),
            "day_low": float(row["Low"]),
            "open": float(row["Open"]),
            "volume": float(row["Volume"]),
            "source": source_tag,
            "target_date": TARGET_DATE,
            "actual_data_date": data_date,
            "us_holiday_fallback": is_holiday_fallback,
            **info,
        }
    except Exception as e:
        print(f"  [{code}] yfinance error: {e}", flush=True)
        return None


def process_hk(code: str, ctx: OpenQuoteContext) -> tuple[str, str]:
    try:
        snap = fetch_futu_kline_snapshot(code, ctx)
        if not snap:
            return code, "futu-no-data"
        news = fetch_news(
            code=code, name_zh=snap.get("name_zh", code),
            name_en=snap.get("name_en", code), max_results=5, days=3,
        )
        result = analyze(
            code=code, name=snap.get("name_zh", code),
            snapshot=snap, news=news or [], language="zh-Hant",
        )
        if result is None:
            return code, "analyze-none"
        summary_md = render_summary_md(result, language="zh-Hant")
        full_md = render_report_md(result, snap, language="zh-Hant")
        entry_zone, stop_loss, target_price = parse_entry_stop_target(full_md)
        save_report(
            code=code, report_date=TARGET_DATE, score=result.score,
            sentiment=result.sentiment, trend=result.trend,
            operation_advice=result.operation_advice, summary_md=summary_md,
            full_md=full_md, news=news or [], data_snapshot=snap,
            llm_model=result.llm_model,
            score_breakdown_json=json.dumps(result.score_breakdown or {}, ensure_ascii=False),
            trade_direction=result.trade_direction,
            entry_zone=entry_zone, stop_loss=stop_loss, target_price=target_price,
            support_zone=result.support_zone, resistance_zone=result.resistance_zone,
            key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
        )
        return code, f"OK op={result.operation_advice} score={result.score}"
    except Exception as e:
        return code, f"ERR {type(e).__name__}: {e}"


def process_us(code: str) -> tuple[str, str]:
    try:
        snap = fetch_yfinance_historical(code)
        if not snap:
            return code, "yfinance-no-data"
        news = fetch_news(
            code=code, name_zh=snap.get("name_zh", code),
            name_en=snap.get("name_en", code), max_results=5, days=3,
        )
        result = analyze(
            code=code, name=snap.get("name_zh", code),
            snapshot=snap, news=news or [], language="zh-Hant",
        )
        if result is None:
            return code, "analyze-none"
        summary_md = render_summary_md(result, language="zh-Hant")
        full_md = render_report_md(result, snap, language="zh-Hant")
        entry_zone, stop_loss, target_price = parse_entry_stop_target(full_md)
        save_report(
            code=code, report_date=TARGET_DATE, score=result.score,
            sentiment=result.sentiment, trend=result.trend,
            operation_advice=result.operation_advice, summary_md=summary_md,
            full_md=full_md, news=news or [], data_snapshot=snap,
            llm_model=result.llm_model,
            score_breakdown_json=json.dumps(result.score_breakdown or {}, ensure_ascii=False),
            trade_direction=result.trade_direction,
            entry_zone=entry_zone, stop_loss=stop_loss, target_price=target_price,
            support_zone=result.support_zone, resistance_zone=result.resistance_zone,
            key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
        )
        return code, f"OK op={result.operation_advice} score={result.score}"
    except Exception as e:
        return code, f"ERR {type(e).__name__}: {e}"


def delete_existing_3_7_records():
    import sqlite3
    db_path = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    n = cur.execute(f"DELETE FROM daily_report WHERE report_date='{TARGET_DATE}'").rowcount
    con.commit()
    con.close()
    print(f"  Deleted {n} existing 3/7 records")


def main():
    init_db()
    print(f"=== Re-run 3/7 ({TARGET_DATE}) — Futu for HK + yfinance for US ===")
    delete_existing_3_7_records()

    hk = load_hk_tickers()
    us = load_us_tickers()
    print(f"Total: {len(hk)} HK + {len(us)} US = {len(hk)+len(us)} tickers")

    # Open Futu context ONCE for all HK tickers
    print("Opening Futu OpenD connection...")
    futu_ctx = OpenQuoteContext(host=cfg.futu_host, port=cfg.futu_port)
    print(f"  ✓ Connected to Futu OpenD at {cfg.futu_host}:{cfg.futu_port}")

    t0 = time.time()
    ok = 0
    fail = 0
    fail_codes = []

    from concurrent.futures import ThreadPoolExecutor, as_completed
    # HK with Futu (single ctx, threaded — Futu supports concurrent requests)
    print("\n--- HK tickers via Futu ---")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(process_hk, c, futu_ctx): c for c in hk}
        for i, fut in enumerate(as_completed(futs), 1):
            code, status = fut.result()
            if status.startswith("OK"):
                ok += 1
            else:
                fail += 1
                fail_codes.append((code, status))
            if i % 50 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed
                eta = (len(hk) - i) / rate if rate > 0 else 0
                print(f"  HK {i}/{len(hk)} ok={ok} fail={fail} {rate:.1f}/s ETA {eta:.0f}s", flush=True)

    # US with yfinance
    us_start_ok = ok
    us_start_fail = fail
    print(f"\n--- US tickers via yfinance ---")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(process_us, c): c for c in us}
        for i, fut in enumerate(as_completed(futs), 1):
            code, status = fut.result()
            if status.startswith("OK"):
                ok += 1
            else:
                fail += 1
                fail_codes.append((code, status))
            if i % 50 == 0:
                elapsed = time.time() - t0
                rate = (i + len(hk)) / elapsed
                eta = (len(us) - i) / rate if rate > 0 else 0
                print(f"  US {i}/{len(us)} ok={ok-us_start_ok} fail={fail-us_start_fail} {rate:.1f}/s ETA {eta:.0f}s", flush=True)

    futu_ctx.close()
    print(f"\nDone: {ok} ok / {fail} fail in {time.time()-t0:.0f}s")
    if fail_codes:
        print(f"Top failures:")
        for c, s in fail_codes[:15]:
            print(f"  {c}: {s}")


if __name__ == "__main__":
    main()