"""
DSA-HK CLI entry point.

Usage:
    python -m src.main init              Initialize database
    python -m src.main config            Show config status
    python -m src.main analyze [codes]   Analyze all HK tickers (or comma-separated list)
    python -m src.main one <code>        Analyze single ticker
    python -m src.main dashboard         Build dashboard markdown (prints to stdout)
    python -m src.main webui             Start Streamlit web UI (port 8200)
    python -m src.main bot               Start Telegram bot
    python -m src.main schedule          Start scheduler (daily at SCHEDULE_TIME)
    python -m src.main serve             Start both web UI + scheduler + bot
    python -m src.main smoke             Run smoke test (0700.HK, 9988.HK, 1810.HK)
    python -m src.main chanlun [--hk|--us|code1,code2,...] [--backtest] [--llm] [--min-llm-score N]  Scan Chanlun 3rd-class BUY signals. --llm enables MiniMax-M3 secondary filter (default threshold 7/10)
    python -m src.main chanlun-list [days] [code]   List recent Chanlun signals
"""

from __future__ import annotations

import logging
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_init() -> int:
    from src.db import init_db
    init_db()
    print("✓ Database initialized")
    return 0


def cmd_config() -> int:
    from src.config import get_config
    cfg = get_config()
    print("=== DSA-HK Configuration ===\n")
    print(f"LLM Model:     {cfg.litellm_model}")
    print(f"Report Lang:   {cfg.report_language}")
    print(f"LLM Keys:      {', '.join(cfg.available_llm_providers()) or 'NONE'}")
    print(f"News Sources:  {', '.join(cfg.available_news_sources()) or 'NONE'}")
    print(f"Futu OpenD:    {cfg.futu_host}:{cfg.futu_port}")
    print(f"Telegram:      {'configured' if cfg.has_telegram() else 'not configured'}")
    print(f"Database:      {cfg.database_path}")
    print(f"Reports dir:   {cfg.reports_dir}")
    print(f"Schedule:      {cfg.schedule_time} HKT (enabled={cfg.schedule_enabled})")
    print(f"Web UI port:   {cfg.webui_port}")
    print(f"Radar path:    {cfg.radar_path}")
    print()
    if cfg.warnings():
        print("⚠️  Warnings:")
        for w in cfg.warnings():
            print(f"   - {w}")
        return 1
    return 0


def cmd_analyze(codes_str: str | None = None) -> int:
    from src.pipeline import run_full_analysis
    codes = [c.strip() for c in codes_str.split(",") if c.strip()] if codes_str else None
    result = run_full_analysis(codes=codes, trigger="cli")
    return 0 if result["done"] > 0 else 1


def cmd_one(code: str) -> int:
    from src.pipeline import analyze_ticker
    result = analyze_ticker(code)
    if result:
        print(f"\n✅ {code}: score={result.score}, {result.operation_advice}")
        print(f"   Summary: {result.summary}")
        return 0
    print(f"\n❌ {code}: analysis failed")
    return 1


def cmd_dashboard() -> int:
    from src.pipeline import build_dashboard_md
    print(build_dashboard_md())
    return 0


def cmd_webui() -> int:
    from src.config import get_config
    import subprocess
    cfg = get_config()
    print(f"Starting Streamlit web UI on http://localhost:{cfg.webui_port}")
    env = os.environ.copy()
    subprocess.run(
        [
            sys.executable, "-m", "streamlit", "run",
            str(PROJECT_ROOT / "src" / "web_ui.py"),
            "--server.port", str(cfg.webui_port),
            "--server.address", "0.0.0.0",
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    return 0


def cmd_bot() -> int:
    from src.telegram_bot import run_bot
    run_bot()
    return 0


def cmd_schedule() -> int:
    from src.config import get_config
    from src.scheduler import start_scheduler
    cfg = get_config()
    sched = start_scheduler()
    print(f"Scheduler running. Daily at {cfg.schedule_time} HKT. Press Ctrl+C to stop.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    return 0


def cmd_serve() -> int:
    """Run web UI + scheduler + telegram bot all together (uses subprocess)."""
    import subprocess
    import time
    processes = []

    def spawn(name, args):
        print(f"Starting {name}...")
        p = subprocess.Popen([sys.executable, "-m", "src.main"] + args, cwd=str(PROJECT_ROOT))
        processes.append((name, p))
        return p

    spawn("webui", ["webui"])
    time.sleep(2)
    if get_config().has_telegram():
        spawn("bot", ["bot"])
    spawn("scheduler", ["schedule"])

    print("\nAll services started. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
            # Restart dead processes
            for name, p in processes:
                if p.poll() is not None:
                    print(f"⚠️  {name} died, restarting...")
                    if name == "webui":
                        spawn("webui", ["webui"])
                    elif name == "bot":
                        spawn("bot", ["bot"])
                    elif name == "scheduler":
                        spawn("scheduler", ["schedule"])
    except KeyboardInterrupt:
        print("\nStopping all services...")
        for name, p in processes:
            try:
                p.terminate()
            except Exception:
                pass
    return 0


def cmd_smoke() -> int:
    """Run smoke test with 3 tickers."""
    from src.data_fetcher import fetch_snapshot
    from src.news_fetcher import fetch_news
    from src.analyzer import analyze, render_report_md

    test_codes = ["0700.HK", "9988.HK", "1810.HK"]
    print(f"=== Smoke test: {', '.join(test_codes)} ===\n")

    failures = 0
    for code in test_codes:
        print(f"--- {code} ---")
        # 1. Snapshot
        snap = fetch_snapshot(code)
        if not snap:
            print(f"  ❌ Snapshot fetch failed")
            failures += 1
            continue
        print(f"  ✓ Snapshot: {snap.get('name_zh')} @ {snap.get('last_price')} HKD ({snap.get('change_pct')}%) [{snap.get('source')}]")

        # 2. News
        news = fetch_news(code, snap.get("name_zh"), snap.get("name_en"))
        print(f"  ✓ News: {len(news)} items")

        # 3. LLM analysis (may fail if no API key)
        result = analyze(code, snap.get("name_zh", ""), snap, news)
        if result:
            print(f"  ✓ LLM: score={result.score}, {result.operation_advice}")
            print(f"    Summary: {result.summary[:120]}")
        else:
            print(f"  ⚠️  LLM analysis failed (check API key)")
            failures += 1

    print(f"\n=== Result: {len(test_codes) - failures}/{len(test_codes)} passed ===")
    return 0 if failures == 0 else 1


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]
    setup_logging()

    if cmd == "init":
        return cmd_init()
    elif cmd == "config":
        return cmd_config()
    elif cmd == "analyze":
        codes_str = sys.argv[2] if len(sys.argv) > 2 else None
        return cmd_analyze(codes_str)
    elif cmd == "one":
        if len(sys.argv) < 3:
            print("Usage: python -m src.main one <code>")
            return 1
        return cmd_one(sys.argv[2])
    elif cmd == "dashboard":
        return cmd_dashboard()
    elif cmd == "webui":
        return cmd_webui()
    elif cmd == "bot":
        return cmd_bot()
    elif cmd == "schedule":
        return cmd_schedule()
    elif cmd == "serve":
        return cmd_serve()
    elif cmd == "smoke":
        return cmd_smoke()
    elif cmd == "chanlun":
        # Parse flags
        market = None
        codes = None
        backtest = False
        hold_days = 60
        llm_filter = False
        min_llm_score = 7
        i = 2
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg in ("--hk", "--us", "--all"):
                market = arg.lstrip("--")
                i += 1
            elif arg == "--backtest":
                backtest = True
                i += 1
            elif arg == "--llm":
                llm_filter = True
                i += 1
            elif arg == "--min-llm-score":
                if i + 1 < len(sys.argv):
                    min_llm_score = int(sys.argv[i + 1])
                    i += 2
                else:
                    i += 1
            elif arg == "--hold":
                if i + 1 < len(sys.argv):
                    hold_days = int(sys.argv[i + 1])
                    i += 2
                else:
                    i += 1
            elif arg.startswith("--"):
                i += 1
            else:
                # Treat as comma-separated ticker list
                codes = [c.strip() for c in arg.split(",") if c.strip()]
                i += 1
        from src.commands.chanlun_scan import cmd_chanlun_scan
        return cmd_chanlun_scan(
            codes=codes, market=market, backtest=backtest,
            hold_days=hold_days, llm_filter=llm_filter, min_llm_score=min_llm_score,
        )
    elif cmd == "chanlun-list":
        days = 30
        code = None
        for arg in sys.argv[2:]:
            if arg.isdigit():
                days = int(arg)
            else:
                code = arg
        from src.commands.chanlun_scan import cmd_chanlun_list
        return cmd_chanlun_list(days=days, code=code)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
