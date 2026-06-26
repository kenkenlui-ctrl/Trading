"""Main analysis pipeline. Orchestrates fetch -> analyze -> save -> render."""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from .analyzer import analyze, render_report_md, render_summary_md, AnalysisResult
from .config import get_config
from .data_fetcher import fetch_snapshot
from .db import (
    get_db,
    save_report,
    start_run,
    finish_run,
    update_run_progress,
    upsert_ticker,
    list_reports,
)
from .news_fetcher import fetch_news
from .ticker_loader import load_hk_tickers, load_us_tickers, is_hk_trading_day

logger = logging.getLogger(__name__)

# Number of concurrent ticker analyses. Tuned for M2.7 (~30s/call):
# 5 workers → ~6s effective per ticker, ~25 min for 400 tickers.
# Lower if you hit LLM rate limits; raise if your key tier allows more.
DEFAULT_MAX_WORKERS = 20


def analyze_ticker(code: str, save: bool = True, language: Optional[str] = None) -> Optional[AnalysisResult]:
    """
    Full analysis for one ticker. Returns AnalysisResult, also saves to DB if save=True.
    """
    cfg = get_config()
    language = language or cfg.report_language

    logger.info(f"[{code}] Starting analysis...")
    snap = fetch_snapshot(code)
    if not snap:
        logger.warning(f"[{code}] No snapshot — skipping")
        return None

    # Cache ticker info
    upsert_ticker(
        code=code,
        name_zh=snap.get("name_zh"),
        name_en=snap.get("name_en"),
        sector=snap.get("sector"),
        last_price=snap.get("last_price"),
    )

    news = fetch_news(
        code=code,
        name_zh=snap.get("name_zh"),
        name_en=snap.get("name_en"),
        max_results=5,
        days=7,
    )

    result = analyze(code, snap.get("name_zh") or snap.get("name_en", ""), snap, news, language=language)
    if not result:
        logger.warning(f"[{code}] LLM analysis failed")
        return None

    if save:
        # Render full markdown report
        full_md = render_report_md(result, snap, language=language)
        summary_md = render_summary_md(result, language=language)
        report_date = datetime.now().strftime("%Y-%m-%d")
        save_report(
            code=code,
            report_date=report_date,
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
        )
        # Also save to reports/ dir as a markdown file (for archive)
        try:
            reports_dir = Path(cfg.reports_dir)
            filename = reports_dir / f"{code}_{report_date}.md"
            filename.write_text(full_md, encoding="utf-8")
        except Exception as e:
            logger.warning(f"[{code}] Failed to write report file: {e}")

        logger.info(
            f"[{code}] ✓ score={result.score} {result.operation_advice} "
            f"({snap.get('source')}, {len(news)} news)"
        )

    return result


def run_full_analysis(
    codes: Optional[list[str]] = None,
    markets: Optional[list[str]] = None,
    trigger: str = "cli",
    skip_non_trading: bool = True,
) -> dict:
    """
    Run analysis for HK + US tickers (or specified list).
    Returns summary dict {total, done, failed, duration_sec, hk_done, us_done}.
    """
    cfg = get_config()

    # Check trading day
    if skip_non_trading and cfg.skip_non_trading_days and not is_hk_trading_day():
        logger.info("Non-trading day (weekend) — skipping full run")
        return {"total": 0, "done": 0, "failed": 0, "skipped": True, "duration_sec": 0}

    if codes is None:
        # Load both markets by default
        markets = markets or ["HK", "US"]
        all_codes = []
        if "HK" in markets:
            all_codes += load_hk_tickers()
        if "US" in markets:
            all_codes += load_us_tickers()
        codes = all_codes

    if not codes:
        logger.error("No tickers to analyze.")
        return {"total": 0, "done": 0, "failed": 0, "error": "no tickers"}

    run_id = start_run(trigger=trigger, tickers_total=len(codes))
    done = 0
    failed = 0
    started = time.time()

    # Parallelism: override via env DSA_PARALLEL, default DEFAULT_MAX_WORKERS.
    max_workers = int(os.getenv("DSA_PARALLEL", str(DEFAULT_MAX_WORKERS)))
    max_workers = max(1, min(max_workers, 100))

    logger.info(
        f"=== Starting full analysis: {len(codes)} tickers (HK+US) "
        f"(run_id={run_id}, workers={max_workers}) ==="
    )

    # Thread-safe counters (GIL makes += safe enough for progress tracking).
    def _process(code: str) -> tuple[str, bool]:
        try:
            result = analyze_ticker(code, save=True)
            return (code, bool(result))
        except Exception as e:
            logger.exception(f"[{code}] Unexpected error: {e}")
            return (code, False)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process, code): code for code in codes}
        for fut in as_completed(futures):
            code, ok = fut.result()
            if ok:
                done += 1
            else:
                failed += 1
            # Live progress → so the Streamlit sidebar can show real % instead of 0/N.
            update_run_progress(run_id, done, failed)

    duration = time.time() - started
    status = "success" if failed == 0 else ("partial" if done > 0 else "failed")
    finish_run(run_id, status=status, tickers_done=done, tickers_failed=failed)

    summary = {
        "total": len(codes),
        "done": done,
        "failed": failed,
        "duration_sec": round(duration, 1),
        "status": status,
        "run_id": run_id,
    }
    logger.info(
        f"=== Done: {done}/{len(codes)} succeeded, {failed} failed, "
        f"{duration:.1f}s ==="
    )
    return summary


def build_dashboard_md(report_date: Optional[str] = None, language: Optional[str] = None, trade_direction: Optional[str] = None) -> str:
    """
    Build the daily decision dashboard markdown. Aggregates all reports for a date.

    trade_direction filter:
      - None / "" / "all" → no filter (show all)
      - "long" → only trade_direction='long'
      - "short" → only trade_direction='short'
      - "both" → only trade_direction='both'
    """
    cfg = get_config()
    language = language or cfg.report_language
    is_zh = language == "zh-Hant"

    report_date = report_date or datetime.now().strftime("%Y-%m-%d")
    reports = list_reports(report_date=report_date, limit=500)

    # Apply trade_direction filter (None/all = show everything)
    filter_label = "全部"
    if trade_direction and trade_direction not in ("", "all", "全部"):
        before = len(reports)
        reports = [r for r in reports if (r.get("trade_direction") or "both") == trade_direction]
        after = len(reports)
        filter_label = {
            "long": "只做多 LONG",
            "short": "只做空 SHORT",
            "both": "雙向",
        }.get(trade_direction, trade_direction)
        logger.info(f"Dashboard filter trade_direction={trade_direction}: {before} → {after} reports")

    if not reports:
        return (
            f"# 📊 HK+US 決策儀表板\n\n"
            f"日期: {report_date} · 篩選: {filter_label}\n\n"
            f"_此條件下無報告。_"
            if is_zh else
            f"# 📊 Leeks Terminal HK+US Decision Dashboard\n\n"
            f"Date: {report_date} · Filter: {filter_label}\n\n"
            f"_No reports under this filter._"
        )

    # Stats
    n = len(reports)
    n_buy = sum(1 for r in reports if r["operation_advice"] in ("買入", "buy"))
    n_hold = sum(1 for r in reports if r["operation_advice"] in ("觀望", "hold"))
    n_sell = sum(1 for r in reports if r["operation_advice"] in ("賣出", "sell"))

    # Title
    if is_zh:
        title = f"# 🎯 {report_date} HK+US 決策儀表板"
        stats_line = f"共分析 {n} 隻股票 | 🟢買入: {n_buy} 🟡觀望: {n_hold} 🔴賣出: {n_sell}"
    else:
        title = f"# 🎯 {report_date} Leeks Terminal HK+US Decision Dashboard"
        stats_line = f"Analyzed {n} stocks | 🟢Buy: {n_buy} 🟡Hold: {n_hold} 🔴Sell: {n_sell}"

    md = f"{title}\n\n{stats_line}\n\n"

    # Sort by score desc
    sorted_reports = sorted(reports, key=lambda r: r["score"] or 0, reverse=True)

    if is_zh:
        md += "## 📊 分析結果摘要\n\n"
        for r in sorted_reports:
            md += f"{r.get('summary_md', '')}\n\n"
    else:
        md += "## 📊 Analysis Summary\n\n"
        for r in sorted_reports:
            md += f"{r.get('summary_md', '')}\n\n"

    # Footer
    md += f"\n---\n\n*生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · LLM: {cfg.litellm_model}*\n"
    return md


def get_market_review_data() -> dict:
    """
    Fetch HSI + HSCEI snapshot for market review section.
    """
    cfg = get_config()
    indices = ["^HSI", "^HSCE"]  # Yahoo Finance tickers for HSI + HSCEI
    snapshots = {}
    for idx in indices:
        try:
            import yfinance as yf
            t = yf.Ticker(idx)
            hist = t.history(period="5d")
            if not hist.empty:
                last = hist.iloc[-1]
                prev = hist.iloc[-2] if len(hist) >= 2 else hist.iloc[-1]
                last_close = float(last["Close"])
                prev_close = float(prev["Close"])
                chg = (last_close - prev_close) / prev_close * 100 if prev_close else 0
                snapshots[idx] = {
                    "last": last_close,
                    "prev": prev_close,
                    "change_pct": round(chg, 2),
                }
        except Exception as e:
            logger.warning(f"Failed to fetch {idx}: {e}")
    return snapshots


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    import sys
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "analyze":
            codes = sys.argv[2].split(",") if len(sys.argv) > 2 else None
            result = run_full_analysis(codes=codes, trigger="cli")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif cmd == "one":
            code = sys.argv[2]
            result = analyze_ticker(code)
            if result:
                print(f"\n{result.code}: score={result.score}, {result.operation_advice}")
                print(f"  Summary: {result.summary}")
        elif cmd == "dashboard":
            print(build_dashboard_md())
        else:
            print(f"Unknown command: {cmd}")
            print("Usage: python -m src.pipeline [analyze|one <code>|dashboard]")
    else:
        print("Usage: python -m src.pipeline [analyze|one <code>|dashboard]")
