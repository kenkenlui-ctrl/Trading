"""Re-run 2026-07-08 report using yfinance for ALL tickers (HK + US).

Futu OpenD kline API returned ret=-1 today, so fall back to yfinance for HK too.
5-digit HK tickers (e.g. 9988.HK) work in yfinance even though some 4-digit don't.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["DSA_REPORT_DATE_OVERRIDE"] = sys.argv[1] if len(sys.argv) > 1 else "2026-07-08"
os.environ["DSA_LLM_MAX_TOKENS"] = "8000"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import yfinance as yf

from src.analyzer import analyze, render_summary_md, render_report_md
from src.db import save_report, init_db
from src.news_fetcher import fetch_news
from src.ticker_loader import load_hk_tickers, load_us_tickers
from src.config import get_config

cfg = get_config()
MAX_WORKERS = int(os.environ.get("DSA_PARALLEL", "15"))
TARGET_DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-07-08"
TARGET_DT = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
NEXT_DT = TARGET_DT + timedelta(days=1)


def fetch_yfinance(code: str) -> dict | None:
    """Fetch target_date close + prev_close via yfinance."""
    try:
        t = yf.Ticker(code)
        # Wider window to handle holidays + weekends
        hist = t.history(start=(TARGET_DT - timedelta(days=15)).strftime("%Y-%m-%d"),
                         end=(NEXT_DT + timedelta(days=1)).strftime("%Y-%m-%d"))
        if hist.empty:
            return None
        # Find target row
        target_idx = None
        target_date_str = TARGET_DT.strftime("%Y-%m-%d")
        for i, (dt, _) in enumerate(hist.iterrows()):
            if dt.strftime("%Y-%m-%d") == target_date_str:
                target_idx = i
                break
        if target_idx is None:
            # Holiday fallback: use last available row
            target_idx = len(hist) - 1
        k = hist.iloc[target_idx]
        last = float(k["Close"])
        if target_idx > 0:
            prev_close = float(hist.iloc[target_idx - 1]["Close"])
        else:
            prev_close = last
        change_pct = (last - prev_close) / prev_close * 100 if prev_close else 0
        return {
            "code": code,
            "last_price": last,
            "prev_close": prev_close,
            "change_pct": round(change_pct, 2),
            "day_high": float(k["High"]),
            "day_low": float(k["Low"]),
            "open": float(k["Open"]),
            "volume": float(k["Volume"]),
            "source": f"yfinance-{hist.index[target_idx].strftime('%Y-%m-%d')}",
            "target_date": TARGET_DATE,
            "actual_data_date": hist.index[target_idx].strftime("%Y-%m-%d"),
        }
    except Exception as e:
        return None


def fetch_tencent_gtimg(code: str) -> dict | None:
    """For HK 5-digit codes yfinance doesn't have, use Tencent gtimg."""
    if not code.endswith(".HK"):
        return None
    # 0700.HK → hk00700 (Tencent uses 5-digit)
    num = code[:-3].lstrip("0") or "0"
    tencent_code = f"hk{num.zfill(5)}"
    try:
        url = f"http://qt.gtimg.cn/q={tencent_code}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        text = r.content.decode("gbk", errors="ignore")
        if "pv_none_match" in text or "none_match" in text:
            return None
        # Format: v_hk00700="100~00700~騰訊控股~00700~461.20~465.40~461.20~12345678~..."
        # Field map: [3]=code, [4]=last, [29]=timestamp, [31]=change, [32]=change_pct,
        #            [33]=high, [34]=low, [5]=prev_close, [6]=open
        m = re.search(r'"([^"]+)"', text)
        if not m:
            return None
        parts = m.group(1).split("~")
        if len(parts) < 35:
            return None
        last = float(parts[5]) if parts[5] else 0
        prev_close = float(parts[4]) if parts[4] else 0
        if not last or not prev_close:
            return None
        change_pct = float(parts[32]) if parts[32] else 0
        return {
            "code": code,
            "last_price": last,
            "prev_close": prev_close,
            "change_pct": round(change_pct, 2),
            "day_high": float(parts[33]) if parts[33] else 0,
            "day_low": float(parts[34]) if parts[34] else 0,
            "open": float(parts[5]) if parts[5] else prev_close,
            "volume": float(parts[6]) * 1000 if parts[6] else 0,
            "source": f"tencent-gtimg-{TARGET_DATE}",
            "target_date": TARGET_DATE,
        }
    except Exception:
        return None


def fetch_snapshot_for_ticker(code: str) -> dict | None:
    """Try yfinance first, then Tencent gtimg, then None."""
    snap = fetch_yfinance(code)
    if snap:
        return snap
    snap = fetch_tencent_gtimg(code)
    return snap


def process_one(code: str) -> tuple[str, bool]:
    """Process single ticker. Returns (code, success)."""
    try:
        snap = fetch_snapshot_for_ticker(code)
        if not snap:
            print(f"  [{code}] no snapshot", flush=True)
            return code, False

        news = fetch_news(code=code, max_results=5, days=7)

        result = analyze(code, snap.get("name_zh") or code, snap, news, language="zh-Hant")
        if not result:
            print(f"  [{code}] LLM failed", flush=True)
            return code, False

        full_md = render_report_md(result, snap, language="zh-Hant")
        summary_md = render_summary_md(result, language="zh-Hant")

        save_report(
            code=code,
            report_date=TARGET_DATE,
            score=result.score,
            sentiment=result.sentiment,
            trend=result.trend,
            operation_advice=result.operation_advice,
            summary_md=summary_md,
            full_md=full_md,
            news=news,
            data_snapshot=snap,
            llm_model=result.llm_model,
            score_breakdown_json=json.dumps(result.score_breakdown or {}, ensure_ascii=False),
            trade_direction=result.trade_direction,
            support_zone=result.support_zone,
            resistance_zone=result.resistance_zone,
            key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
        )
        return code, True
    except Exception as e:
        print(f"  [{code}] error: {e}", flush=True)
        return code, False


def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed

    init_db()

    # Delete existing records for this date
    import sqlite3
    con = sqlite3.connect("data/dsa_hk.db")
    n_del = con.execute("DELETE FROM daily_report WHERE report_date=?", (TARGET_DATE,)).rowcount
    con.commit()
    con.close()
    print(f"=== Re-run {TARGET_DATE} — yfinance for all (Futu broken) ===")
    print(f"  Deleted {n_del} existing {TARGET_DATE} records")

    hk = load_hk_tickers()
    us = load_us_tickers()
    all_codes = hk + us
    print(f"Total: {len(hk)} HK + {len(us)} US = {len(all_codes)} tickers\n")

    ok = 0
    fail = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(process_one, code): code for code in all_codes}
        for i, fut in enumerate(as_completed(futures), 1):
            code, success = fut.result()
            if success:
                ok += 1
            else:
                fail += 1
            if i % 50 == 0 or i == len(all_codes):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(all_codes) - i) / rate if rate > 0 else 0
                print(f"  [{i}/{len(all_codes)}] ok={ok} fail={fail} {rate:.1f}/s ETA {eta:.0f}s", flush=True)

    elapsed = time.time() - start
    print(f"\n=== Done: {ok}/{len(all_codes)} ok, {fail} fail, {elapsed:.1f}s ===")


if __name__ == "__main__":
    main()