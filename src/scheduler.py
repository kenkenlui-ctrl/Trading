"""APScheduler-based daily scheduler. Runs at SCHEDULE_TIME HKT, weekdays only."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_config
from .pipeline import run_full_analysis
from .telegram_bot import send_message
from .commands.chanlun_scan import scan_universe, load_hk_universe

logger = logging.getLogger(__name__)


def scheduled_job() -> None:
    """The job that runs daily. Logs status, sends Telegram notification on completion."""
    logger.info(f"=== Scheduled run triggered at {datetime.now().isoformat()} ===")
    try:
        result = run_full_analysis(trigger="schedule", skip_non_trading=True)
        if result.get("skipped"):
            logger.info("Skipped (non-trading day)")
            return

        # Build summary message
        msg = (
            f"✅ DSA-HK 每日分析完成\n\n"
            f"📊 完成: {result['done']}/{result['total']}\n"
            f"❌ 失敗: {result['failed']}\n"
            f"⏱️ 耗時: {result['duration_sec']}s\n"
            f"💡 用 /dashboard 查看完整報告\n"
            f"🌐 http://localhost:{get_config().webui_port}"
        )
        send_message(msg)
    except Exception as e:
        logger.exception(f"Scheduled job failed: {e}")
        send_message(f"❌ DSA-HK 排程失敗: {e}")


def chanlun_scheduled_job() -> None:
    """Daily Chanlun 3rd-class BUY scan across HK universe.
    Runs 30 min after the main analysis to pick up the latest daily bar.
    Uses MiniMax-M3 LLM secondary filter by default (drops score < 7).
    Emits signals to chanlun_signal table and notifies via Telegram.
    """
    logger.info(f"=== Chanlun scheduled scan triggered at {datetime.now().isoformat()} ===")
    try:
        hk = load_hk_universe()
        # LLM filter enabled by default in scheduler — drops low-quality signals
        summary = scan_universe(hk, "HK (200)", save_to_db=True,
                                backtest=False, llm_filter=True, min_llm_score=7)
        n = summary["signals"]

        if n > 0:
            # Build per-signal notification message
            lines = [
                f"📊 纏論第三類買點 + LLM 過濾完成",
                f"",
                f"通過 LLM 過濾（≥7/10）嘅信號：{n} 個",
                f"",
            ]
            for s in summary["results"][:10]:  # top 10 only
                llm_score = s.get("llm_score", "-")
                llm_conv = s.get("llm_conviction", "")
                lines.append(
                    f"• {s['signal_date']} {s['code']} @ {s['entry_price']:.2f}  "
                    f"(ZG {s['central_zg']:.2f}, LLM {llm_score}/10 {llm_conv})"
                )
            if len(summary["results"]) > 10:
                lines.append(f"  ... 仲有 {len(summary['results']) - 10} 個")
            lines.append(f"")
            lines.append(f"💡 用 python -m src.main chanlun-list 30 睇全部")
            lines.append(f"🌐 http://localhost:{get_config().webui_port}")
            send_message("\n".join(lines))
        else:
            send_message(f"📊 纏論掃描 + LLM 過濾完成（HK 200）— 今日 0 個通過過濾")
    except Exception as e:
        logger.exception(f"Chanlun scheduled scan failed: {e}")
        send_message(f"❌ 纏論掃描失敗: {e}")


def start_scheduler() -> BlockingScheduler:
    """Build and start the scheduler. Returns the scheduler instance."""
    cfg = get_config()
    scheduler = BlockingScheduler(timezone="Asia/Hong_Kong")

    # Parse HH:MM
    try:
        hh, mm = cfg.schedule_time.split(":")
        hh, mm = int(hh), int(mm)
    except (ValueError, AttributeError):
        logger.warning(f"Invalid SCHEDULE_TIME={cfg.schedule_time}, defaulting to 18:00")
        hh, mm = 18, 0

    # Weekdays only (Mon-Fri = 0-4)
    scheduler.add_job(
        scheduled_job,
        CronTrigger(hour=hh, minute=mm, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="daily_analysis",
        name=f"Daily HK analysis @ {cfg.schedule_time} HKT",
        replace_existing=True,
    )

    # Chanlun 3rd-class BUY scan — runs 30 min after the main analysis
    # so it can pick up the latest daily bar and emit signals for next session.
    chanlun_hh, chanlun_mm = hh, mm + 30
    if chanlun_mm >= 60:
        chanlun_hh = (chanlun_hh + 1) % 24
        chanlun_mm -= 60
    scheduler.add_job(
        chanlun_scheduled_job,
        CronTrigger(hour=chanlun_hh, minute=chanlun_mm, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="daily_chanlun",
        name=f"Daily Chanlun scan @ {chanlun_hh:02d}:{chanlun_mm:02d} HKT",
        replace_existing=True,
    )

    logger.info(f"Scheduler started. Next run at {cfg.schedule_time} HKT, weekdays only.")
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    sched = start_scheduler()
    print(f"DSA-HK scheduler running. Daily at {get_config().schedule_time} HKT. Press Ctrl+C to stop.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
