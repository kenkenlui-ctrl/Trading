"""Re-run 2026-07-03 report with PROPER 3/7 historical data.

Bug: previous run used LIVE snapshot (which on 6/7 was 6/7 data), not 3/7.
Fix: fetch proper 3/7 historical close via yfinance (4-digit HK format + US).

For each ticker:
  - Fetch 3/7 historical via yfinance (with 4-digit HK format fallback)
  - Construct snapshot dict matching fetch_snapshot() format
  - Save snapshot_json + re-run LLM analysis
  - Result: report_date=2026-07-03 with actual 3/7 prices + correct "今日" = 3/7
"""
import json
import os
import sys
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Force correct date BEFORE any pipeline import
os.environ["DSA_REPORT_DATE_OVERRIDE"] = "2026-07-03"
os.environ["DSA_LLM_MAX_TOKENS"] = "8000"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report, init_db, get_db, list_reports
from src.news_fetcher import fetch_news
from src.ticker_loader import load_hk_tickers, load_us_tickers
from src.config import get_config

MAX_WORKERS = int(os.environ.get("DSA_PARALLEL", "5"))  # Throttled — yfinance rate limit


def to_yf_ticker(code: str) -> str:
    """5-digit .HK → 4-digit .HK; .HK stays; US stays."""
    if code.endswith(".HK"):
        stem = code[:-3]
        # Strip leading zeros
        stripped = stem.lstrip("0")
        if stripped and stripped != stem:
            return stripped + ".HK"
    return code


def fetch_historical_snapshot(code: str, target_date: str = "2026-07-03") -> dict | None:
    """Fetch 3/7 historical data via yfinance. Returns snapshot dict or None."""
    yf_code = to_yf_ticker(code)
    try:
        t = yf.Ticker(yf_code)
        # Fetch a 5-day window to get the target date reliably
        hist = t.history(start="2026-06-29", end="2026-07-05")
        if hist.empty:
            # yfinance failed for this ticker — try Tencent live fallback for HK
            if code.endswith(".HK"):
                return fetch_tencent_fallback_snapshot(code, target_date)
            return None
        # Convert target_date to Timestamp
        import pandas as pd
        target_ts = pd.Timestamp(target_date).tz_localize(hist.index.tz)
        if target_ts not in hist.index:
            # yfinance has data but not for 3/7 — fall back to Tencent for HK
            if code.endswith(".HK"):
                return fetch_tencent_fallback_snapshot(code, target_date)
            return None
        row = hist.loc[target_ts]
        prev_ts = hist.index[hist.index.get_loc(target_ts) - 1] if hist.index.get_loc(target_ts) > 0 else None
        prev_close = float(hist.loc[prev_ts]["Close"]) if prev_ts is not None else float(row["Close"])
        last = float(row["Close"])
        chg_pct = (last - prev_close) / prev_close * 100 if prev_close else 0

        # Fetch info for fundamentals (PE/PB/etc) — use yfinance .info
        # But .info can hang on some HK tickers — skip if no data
        info = {}
        try:
            raw_info = t.info or {}
            # Map to our snapshot format
            info = {
                "pe_ttm": raw_info.get("trailingPE"),
                "pb": raw_info.get("priceToBook"),
                "market_cap": raw_info.get("marketCap"),
                "dividend_yield": (raw_info.get("dividendYield") or 0) * 100 if raw_info.get("dividendYield") else None,
                "52w_high": raw_info.get("fiftyTwoWeekHigh"),
                "52w_low": raw_info.get("fiftyTwoWeekLow"),
            }
        except Exception:
            pass

        # Build snapshot in fetch_snapshot() format
        snap = {
            "code": code,
            "last_price": last,
            "change_pct": round(chg_pct, 2),
            "day_high": float(row["High"]),
            "day_low": float(row["Low"]),
            "volume": float(row["Volume"]),
            "open": float(row["Open"]),
            "prev_close": prev_close,
            "source": f"yfinance-historical-3/7 ({yf_code})",
            "target_date": target_date,
            **info,
        }
        return snap
    except Exception as e:
        print(f"  [{code}] yfinance error: {e}", flush=True)
        if code.endswith(".HK"):
            return fetch_tencent_fallback_snapshot(code, target_date)
        return None


def fetch_tencent_fallback_snapshot(code: str, target_date: str) -> dict | None:
    """For HK tickers yfinance can't get — fetch Tencent qtimg live and add caveat."""
    import urllib.request
    yf_code = to_yf_ticker(code)
    url = f"http://qt.gtimg.cn/q=hk{yf_code[:-3]}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
        text = raw.decode("gbk", errors="replace")
        # Format: v_hk00700="100~name~code~price~..."
        if "~" not in text:
            return None
        fields = text.split("=")[1].strip().strip('";').split("~")
        if len(fields) < 50:
            return None
        # Field map: 1=name_zh, 3=last, 4=prev_close, 5=open, 6=volume(k),
        # 31=change, 32=change_pct, 33=day_high, 34=day_low, 38=PE
        def f(i):
            try: return float(fields[i])
            except: return None
        last = f(3)
        prev_close = f(4)
        change_pct = f(32)
        if last is None:
            return None
        if prev_close is None or prev_close == 0:
            prev_close = last
        return {
            "code": code,
            "name_zh": fields[1] if len(fields) > 1 else code,
            "last_price": last,
            "prev_close": prev_close,
            "change_pct": round(change_pct, 2) if change_pct is not None else 0,
            "day_high": f(33),
            "day_low": f(34),
            "open": f(5),
            "volume": f(6),
            "pe_ttm": f(38),
            "market_cap": f(43) * 1e6 if f(43) else None,
            "source": f"tencent-live-fallback (target={target_date}, fetched {datetime.now().strftime('%Y-%m-%d')})",
            "target_date": target_date,
            "data_caveat": f"⚠️ HK ticker — yfinance failed; Tencent data is LIVE (fetched {datetime.now().strftime('%Y-%m-%d')}), not historical {target_date}. Price is the latest available, may differ from actual 3/7 close.",
        }
    except Exception as e:
        print(f"  [{code}] tencent fallback error: {e}", flush=True)
        return None


def process_one(code: str) -> tuple[str, str]:
    try:
        snap = fetch_historical_snapshot(code, target_date="2026-07-03")
        if not snap:
            return code, "no-historical-data"

        # Use snapshot's last_price/sector — but we don't have sector; infer from code or skip
        # For sector, fallback to "?Unknown"
        snap.setdefault("sector", "")
        snap.setdefault("name_zh", code)
        snap.setdefault("name_en", code)

        # Fetch news (news doesn't depend on date — use recent)
        news = fetch_news(
            code=code,
            name_zh=snap["name_zh"],
            name_en=snap["name_en"],
            max_results=5,
            days=3,
        )

        # Run LLM analysis with historical snapshot
        result = analyze(
            code=code,
            name=snap["name_zh"],
            snapshot=snap,
            news=news or [],
            language="zh-Hant",
        )
        if result is None:
            return code, "analyze-none"

        summary_md = render_summary_md(result, language="zh-Hant")
        full_md = render_report_md(result, snap, language="zh-Hant")

        save_report(
            code=code,
            report_date="2026-07-03",
            score=result.score,
            sentiment=result.sentiment,
            trend=result.trend,
            operation_advice=result.operation_advice,
            summary_md=summary_md,
            full_md=full_md,
            news=news or [],
            data_snapshot=snap,
            llm_model=result.llm_model,
            score_breakdown_json=json.dumps(result.score_breakdown or {}, ensure_ascii=False),
            trade_direction=result.trade_direction,
            support_zone=result.support_zone,
            resistance_zone=result.resistance_zone,
            key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
        )
        return code, f"OK op={result.operation_advice} score={result.score}"
    except Exception as e:
        return code, f"ERR {type(e).__name__}: {e}"


def delete_existing_3_7_records():
    """Delete all existing 3/7 records so the rerun is clean."""
    import sqlite3
    db_path = "/Users/kenken/Documents/dsa-hk/data/dsa_hk.db"
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    n = cur.execute("DELETE FROM daily_report WHERE report_date='2026-07-03'").rowcount
    con.commit()
    con.close()
    print(f"  Deleted {n} existing 3/7 records")


def main():
    init_db()
    print("=== Re-run 3/7 (2026-07-03) with PROPER historical data ===")
    delete_existing_3_7_records()
    hk = load_hk_tickers()
    us = load_us_tickers()
    all_codes = hk + us
    print(f"Total: {len(hk)} HK + {len(us)} US = {len(all_codes)} tickers")

    t0 = time.time()
    ok = 0
    fail = 0
    fail_codes = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(process_one, c): c for c in all_codes}
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
                eta = (len(all_codes) - i) / rate
                print(f"  {i}/{len(all_codes)} ok={ok} fail={fail} {rate:.1f}/s ETA {eta:.0f}s", flush=True)
    print(f"\nDone: {ok} ok / {fail} fail in {time.time()-t0:.0f}s")
    if fail_codes:
        print(f"Top failures:")
        for c, s in fail_codes[:10]:
            print(f"  {c}: {s}")


if __name__ == "__main__":
    main()