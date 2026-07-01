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
        # Allow backfill via env var (used by --date=YYYY-MM-DD CLI flag)
        report_date = os.environ.get("DSA_REPORT_DATE_OVERRIDE") or datetime.now().strftime("%Y-%m-%d")
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
            support_zone=result.support_zone,
            resistance_zone=result.resistance_zone,
            key_levels_json=json.dumps(result.key_levels or {}, ensure_ascii=False),
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
    target_date: Optional[str] = None,
) -> dict:
    """
    Run analysis for HK + US tickers (or specified list).
    Returns summary dict {total, done, failed, duration_sec, hk_done, us_done}.

    target_date: Override the report_date (YYYY-MM-DD). Used for backfilling
    e.g. running 2026-06-27 analysis on a Sunday (when skip_non_trading=True).
    """
    cfg = get_config()

    # Check trading day
    if skip_non_trading and cfg.skip_non_trading_days and not is_hk_trading_day():
        logger.info("Non-trading day (weekend) — skipping full run")
        return {"total": 0, "done": 0, "failed": 0, "skipped": True, "duration_sec": 0}

    # Set DB override if requested (used by CLI to backfill a specific date)
    if target_date:
        os.environ["DSA_REPORT_DATE_OVERRIDE"] = target_date
        logger.info(f"Backfilling reports for date={target_date}")

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


def build_dashboard_md(
    report_date: Optional[str] = None,
    language: Optional[str] = None,
    trade_direction: Optional[str] = None,
    market: Optional[str] = None,
    operation: Optional[str] = None,
) -> str:
    """
    Build the daily decision dashboard markdown. Aggregates all reports for a date.

    Filters (all optional):
      trade_direction: None / "" / "all" → no filter
                      "long" / "short" / "both" → only matching trade_direction
      market:          None / "" / "all" → no filter
                      "HK" → only codes ending .HK
                      "US" → codes without .HK suffix
      operation:       None / "" / "all" → no filter
                      "buy" / "hold" / "sell" → only matching operation_advice
                      (also accepts zh-Hant: "買入" / "觀望" / "賣出")
    """
    cfg = get_config()
    language = language or cfg.report_language
    is_zh = language == "zh-Hant"

    report_date = report_date or datetime.now().strftime("%Y-%m-%d")
    reports = list_reports(report_date=report_date, limit=500)

    # Apply filters — chain them so the stats line shows final filtered counts.
    filter_parts = []

    if trade_direction and trade_direction not in ("", "all", "全部"):
        before = len(reports)
        reports = [r for r in reports if (r.get("trade_direction") or "both") == trade_direction]
        filter_parts.append({
            "long": "只做多 LONG", "short": "只做空 SHORT", "both": "雙向"
        }.get(trade_direction, trade_direction))
        logger.info(f"Dashboard filter trade_direction={trade_direction}: {before} → {len(reports)}")

    if market and market not in ("", "all", "全部"):
        before = len(reports)
        if market == "HK":
            reports = [r for r in reports if r["code"].endswith(".HK")]
        elif market == "US":
            reports = [r for r in reports if not r["code"].endswith(".HK")]
        else:
            reports = [r for r in reports if r["code"].endswith(f".{market}")]
        filter_parts.append({"HK": "港股 HK", "US": "美股 US"}.get(market, market))
        logger.info(f"Dashboard filter market={market}: {before} → {len(reports)}")

    if operation and operation not in ("", "all", "全部"):
        before = len(reports)
        # Map zh-Hant + en aliases
        op_aliases = {
            "buy":  ("買入", "buy"),
            "hold": ("觀望", "hold"),
            "sell": ("賣出", "sell"),
            "買入": ("買入", "buy"),
            "觀望": ("觀望", "hold"),
            "賣出": ("賣出", "sell"),
        }
        wanted = op_aliases.get(operation, (operation,))
        reports = [r for r in reports if r.get("operation_advice") in wanted]
        filter_parts.append({
            "buy": "🟢買入 BUY", "hold": "🟡觀望 HOLD", "sell": "🔴賣出 SELL",
            "買入": "🟢買入 BUY", "觀望": "🟡觀望 HOLD", "賣出": "🔴賣出 SELL",
        }.get(operation, operation))
        logger.info(f"Dashboard filter operation={operation}: {before} → {len(reports)}")

    filter_label = " · ".join(filter_parts) if filter_parts else "全部"

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

    # Title — compact, no H1 (tab already has 決策儀表板 label, double-heading wastes ~50px)
    if is_zh:
        stats_line = f"**{report_date}** · 共分析 **{n}** 隻股票 · 🟢買入:{n_buy} · 🟡觀望:{n_hold} · 🔴賣出:{n_sell}"
    else:
        stats_line = f"**{report_date}** · Analyzed **{n}** stocks · 🟢Buy:{n_buy} · 🟡Hold:{n_hold} · 🔴Sell:{n_sell}"

    # Sort by score desc
    sorted_reports = sorted(reports, key=lambda r: r["score"] or 0, reverse=True)

    # Render each card as a styled HTML <div> with explicit border + padding
    # so cards are visually distinct (1 per row). Markdown soft-breaks were
    # collapsing cards into one paragraph — owner complaint 2026-06-27.
    # Also normalize summary_md text — escape HTML chars in case LLM emitted raw HTML.
    import html as _html
    if is_zh:
        section_label = "## 📊 分析結果摘要"
    else:
        section_label = "## 📊 Analysis Summary"

    cards_html = []
    for r in sorted_reports:
        raw = r.get("summary_md", "") or ""
        # Strip the embedded "· 買入/觀望/賣出 ·" tag — DB op_advice is canonical,
        # and the badges/emoji at the top of the card already convey the signal.
        # The LLM-emitted op tag in body text is often stale (overridden by rule).
        import re as _re
        raw = _re.sub(
            r'(評分\s*\d+)\s*·\s*(?:買入|觀望|賣出|buy|hold|sell|賣出（反彈做空）)\s*·\s*',
            r'\1 · ', raw, count=1
        )
        # Override leading status emoji based on op_advice (canonical) — LLM often
        # emits ⚪ or wrong emoji even when rule-based override flipped the signal
        op = r.get("operation_advice", "")
        target_emoji = None
        if op in ("買入", "buy"):
            target_emoji = "🟢"
        elif op in ("賣出", "sell", "賣出（反彈做空）"):
            target_emoji = "🔴"
        elif op in ("觀望", "hold"):
            target_emoji = "🟡"
        if target_emoji:
            raw = _re.sub(r'^(?:<[^>]+>)*\s*(?:🟢|🟡|🔴|⚪)', target_emoji, raw, count=1)
        # Escape any < > & in LLM output so they render as text not markup
        safe = _html.escape(raw).replace("\n", "<br>")
        # Compute R:R badge from key_levels + last_price
        rr_badge = ""
        op = r.get("operation_advice", "")
        if op in ("買入", "buy", "賣出", "sell", "賣出（反彈做空）"):
            try:
                kl = json.loads(r.get("key_levels_json") or "{}")
                snap = json.loads(r.get("data_snapshot_json") or "{}")
                last = snap.get("last_price")
                support = kl.get("support_floor")
                day_low = kl.get("day_low_value")
                target = kl.get("resistance_target")
                if op in ("買入", "buy"):
                    # Long setup: target > last, stop = min(support, day_low) < last
                    if all(x is not None for x in [last, target, support, day_low]) and last > 0 and target > last:
                        best_stop = min(support, day_low)
                        if best_stop < last:
                            risk_pct = abs(last - best_stop) / last * 100
                            reward_pct = abs(target - last) / last * 100
                            if risk_pct > 0.5:  # min 0.5% risk to avoid div-by-zero noise
                                rr = reward_pct / risk_pct
                                if rr >= 2.0:
                                    rr_badge = f' <span class="rr-badge rr-good">🛡️ R:R {rr:.1f}</span>'
                                elif rr >= 1.0:
                                    rr_badge = f' <span class="rr-badge rr-ok">🛡️ R:R {rr:.1f}</span>'
                                else:
                                    rr_badge = f' <span class="rr-badge rr-bad">🛡️ R:R {rr:.1f}</span>'
                # Short setup (sell): target < last, stop = max(resistance, day_high) > last
                elif op in ("賣出", "sell", "賣出（反彈做空）"):
                    day_high = kl.get("day_high_value")
                    # For short setup: target = support_floor (going down), stop = max(resistance, day_high)
                    short_target = support
                    short_stop = max(target, day_high) if day_high else target
                    if all(x is not None for x in [last, short_target, short_stop]) and last > 0 and short_target < last and short_stop > last:
                        risk_pct = abs(short_stop - last) / last * 100
                        reward_pct = abs(last - short_target) / last * 100
                        if risk_pct > 0.5:
                            rr = reward_pct / risk_pct
                            if rr >= 2.0:
                                rr_badge = f' <span class="rr-badge rr-good">🛡️ R:R {rr:.1f}</span>'
                            elif rr >= 1.0:
                                rr_badge = f' <span class="rr-badge rr-ok">🛡️ R:R {rr:.1f}</span>'
                            else:
                                rr_badge = f' <span class="rr-badge rr-bad">🛡️ R:R {rr:.1f}</span>'
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        # Build timeframe hint (buy multi-day / sell day-trade)
        hint = ""
        if op in ("買入", "buy"):
            hint = ' <span class="hint hint-buy">multi-day hold OK</span>'
        elif op in ("賣出", "sell", "賣出（反彈做空）"):
            # Sell signals mean-revert within 1 day per backtest (1D 60% → 1W 48%).
            # Add explicit warning so user doesn't hold overnight.
            hint = ' <span class="hint hint-sell">⚠️ mean-revert · close by 4 PM · 唔好 hold 過夜</span>'

        card = (
            f'<div style="border:1px solid var(--border);border-left:3px solid var(--accent);'
            f'background:var(--panel);border-radius:4px;padding:10px 14px;'
            f'margin:8px 0;font-size:0.85rem;line-height:1.5;'
            f'font-family:JetBrains Mono, monospace;">'
            f'<div style="margin-bottom:4px;">{rr_badge}{hint}</div>'
            f'{safe}'
            f'</div>'
        )
        cards_html.append(card)

    md = (
        f"{stats_line}\n\n"
        f"{section_label}\n\n"
        + "\n".join(cards_html)
        + f"\n\n---\n*生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · LLM: {cfg.litellm_model}*\n"
    )
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
            skip_non_trading = True
            target_date = None
            for arg in sys.argv[3:]:
                if arg.startswith("--date="):
                    target_date = arg.split("=", 1)[1]
                elif arg == "--force":
                    skip_non_trading = False
            result = run_full_analysis(
                codes=codes,
                trigger="cli",
                skip_non_trading=skip_non_trading,
                target_date=target_date,
            )
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
            print("       analyze [codes] [--date=YYYY-MM-DD] [--force]")
    else:
        print("Usage: python -m src.pipeline [analyze|one <code>|dashboard]")
