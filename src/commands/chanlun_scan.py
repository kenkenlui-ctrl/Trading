"""
Chanlun scan command — run 3rd-class BUY detection on HK (or US) universe.

Usage:
    python -m src.main chanlun              # scan full HK universe (200)
    python -m src.main chanlun --hk         # explicit HK
    python -m src.main chanlun --us         # scan US universe (200)
    python -m src.main chanlun 0700.HK,9988.HK  # scan specific tickers
    python -m src.main chanlun --backtest   # run 60d-hold backtest too

Each scan:
  - Fetches ~300 daily bars per ticker via yfinance (DSA-HK's existing data layer)
  - Runs the full Chanlun pipeline (K-line → stroke → 中樞 → 3rd-class)
  - Writes any new signals to chanlun_signal table
  - Prints a summary
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

log = logging.getLogger(__name__)


# =============================================================================
# Universe loaders (reuse DSA-HK config)
# =============================================================================

def load_hk_universe() -> List[str]:
    """Load the 200-ticker HK universe from hk_universe_200.json."""
    # chanlun_scan.py is in src/commands/, so go up 3 levels: commands -> src -> dsa-hk
    p = Path(__file__).resolve().parent.parent.parent / "hk_universe_200.json"
    if not p.exists():
        log.warning(f"hk_universe_200.json not found at {p}")
        return []
    return json.loads(p.read_text())


def load_us_universe() -> List[str]:
    p = Path(__file__).resolve().parent.parent.parent / "us_universe_200.json"
    if not p.exists():
        log.warning(f"us_universe_200.json not found at {p}")
        return []
    return json.loads(p.read_text())


# =============================================================================
# Daily data fetcher (reuse DSA-HK yfinance path, 300 bars)
# =============================================================================

def fetch_daily_bars(code: str, days: int = 400) -> Optional[pd.DataFrame]:
    """
    Fetch daily OHLCV via yfinance. Falls back gracefully if ticker
    not available.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance not installed")
        return None

    end = datetime.utcnow().date()
    start = end - timedelta(days=days)

    # Convert HK ticker format if needed
    yf_code = code
    if code.endswith(".HK"):
        yf_code = code  # yfinance accepts "0700.HK" directly

    try:
        df = yf.Ticker(yf_code).history(
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            auto_adjust=True,
        )
        if df is None or df.empty:
            return None
        # Normalize columns
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df
    except Exception as e:
        log.warning(f"{code}: fetch failed: {str(e)[:80]}")
        return None


# =============================================================================
# Scan core
# =============================================================================

def scan_one(
    code: str,
    save_to_db: bool = True,
    backtest: bool = False,
    hold_days: int = 60,
    llm_filter: bool = False,
    min_llm_score: int = 7,
) -> Optional[dict]:
    """
    Run Chanlun scan on one ticker. Returns dict summary or None on failure.

    llm_filter: if True, score the signal with MiniMax-M3 and drop if score < min_llm_score
    """
    from src.strategies.chanlun import find_latest_buy_signal
    from src.db import save_chanlun_signal

    df = fetch_daily_bars(code, days=400)
    if df is None or len(df) < 180:
        log.info(f"{code}: insufficient data ({len(df) if df is not None else 0} bars)")
        return None

    sig = find_latest_buy_signal(df, code)
    if sig is None:
        return None

    # Optional: quick backtest to verify
    backtest_result = None
    if backtest:
        from src.strategies.chanlun import find_buy_signals
        all_sigs = find_buy_signals(df, code)
        closes = df["Close"].values
        results = []
        for s in all_sigs:
            entry_idx = df.index.get_loc(s.signal_date)
            exit_idx = min(entry_idx + hold_days, len(df) - 1)
            exit_price = float(closes[exit_idx])
            ret = exit_price / s.entry_price - 1
            results.append({"entry": s.signal_date.strftime("%Y-%m-%d"),
                            "entry_price": s.entry_price,
                            "exit_price": exit_price,
                            "return_pct": ret,
                            "win": ret > 0})
        if results:
            wins = sum(1 for r in results if r["win"])
            backtest_result = {
                "n_trades": len(results),
                "win_rate": wins / len(results),
                "avg_return": sum(r["return_pct"] for r in results) / len(results),
            }

    # Build base dict for persistence
    sig_dict = {
        "code": sig.code,
        "signal_date": sig.signal_date.strftime("%Y-%m-%d"),
        "entry_price": sig.entry_price,
        "stop_loss": sig.stop_loss,
        "target": sig.target,
        "confidence": sig.confidence,
        "central_zg": sig.central_zg,
        "central_zd": sig.central_zd,
        "central_gg": sig.central_gg,
        "central_dd": sig.central_dd,
        "had_pullback": sig.had_pullback,
        "rationale": sig.rationale,
    }

    # Optional: LLM secondary scoring
    if llm_filter:
        from src.strategies.chanlun_llm import score_signal as llm_score_signal
        width_pct = (
            (sig.central_zg - sig.central_zd) / sig.central_zd * 100
            if sig.central_zd else 0.0
        )
        score = llm_score_signal(
            code=sig.code,
            entry_price=sig.entry_price,
            stop_loss=sig.stop_loss,
            target=sig.target,
            tech_confidence=sig.confidence,
            central_zg=sig.central_zg,
            central_zd=sig.central_zd,
            central_gg=sig.central_gg,
            central_dd=sig.central_dd,
            width_pct=width_pct,
            signal_date=sig.signal_date.strftime("%Y-%m-%d"),
        )
        if score is not None:
            sig_dict["llm_score"] = score.score
            sig_dict["llm_conviction"] = score.conviction
            sig_dict["llm_reasoning"] = score.reasoning
            sig_dict["llm_risks"] = score.risks
            if score.score < min_llm_score:
                log.info(f"{code}: LLM score {score.score} < {min_llm_score}, dropping")
                sig_dict["status"] = "llm_rejected"
                if save_to_db:
                    save_chanlun_signal(sig_dict)  # persist rejected for analysis
                return None
        else:
            # LLM failed (empty response, network, etc.) — keep the
            # technical signal but mark as unscored. Better to have
            # all technical signals than to lose them on flaky LLM.
            log.info(f"{code}: LLM scoring failed (kept as unscored technical signal)")
            sig_dict["llm_score"] = None
            sig_dict["llm_reasoning"] = "LLM scoring failed — technical signal only"

    # Persist to DB
    sig_id = 0
    if save_to_db:
        try:
            sig_id = save_chanlun_signal(sig_dict)
        except Exception as e:
            log.warning(f"{code}: DB save failed: {e}")

    summary = {
        **sig_dict,
        "db_id": sig_id,
        "backtest": backtest_result,
    }
    return summary


def scan_universe(
    universe: List[str],
    label: str,
    save_to_db: bool = True,
    backtest: bool = False,
    hold_days: int = 60,
    llm_filter: bool = False,
    min_llm_score: int = 7,
) -> dict:
    """Scan a whole universe and return summary stats."""
    llm_marker = " + LLM filter" if llm_filter else ""
    print(f"\n=== Chanlun 3rd-class scan: {label} ({len(universe)} tickers){llm_marker} ===\n")
    print(f"{'Date':<12}{'Code':<10}{'Entry':>10}{'ZG':>10}{'Tech':>5}{'LLM':>5}  Rationale")
    print("-" * 110)

    signals: List[dict] = []
    failed = 0
    rejected_by_llm = 0
    for i, code in enumerate(universe, 1):
        try:
            summary = scan_one(
                code, save_to_db=save_to_db, backtest=backtest,
                hold_days=hold_days, llm_filter=llm_filter, min_llm_score=min_llm_score,
            )
        except Exception as e:
            log.warning(f"{code}: scan failed: {e}")
            failed += 1
            continue
        if summary is None:
            continue
        signals.append(summary)
        llm_score = summary.get("llm_score")
        llm_score_str = f"{llm_score}" if llm_score else "-"
        print(f"{summary['signal_date']:<12}{summary['code']:<10}"
              f"{summary['entry_price']:>10.2f}{summary['central_zg']:>10.2f}"
              f"{summary['confidence']:>5}{llm_score_str:>5}  "
              f"{summary['rationale'][:55]}...")

    print(f"\n{'-' * 110}")
    print(f"Total scanned: {len(universe)}  Failed: {failed}  "
          f"Signals (kept): {len(signals)}")
    if llm_filter:
        total_with_llm = sum(1 for s in signals if s.get("llm_score"))
        print(f"LLM-scored: {total_with_llm}  (dropped below threshold: not shown)")

    if backtest and signals:
        with_bt = [s for s in signals if s.get("backtest")]
        if with_bt:
            avg_wr = sum(s["backtest"]["win_rate"] for s in with_bt) / len(with_bt)
            avg_ret = sum(s["backtest"]["avg_return"] for s in with_bt) / len(with_bt)
            print(f"Backtest ({hold_days}d hold): avg WR={avg_wr*100:.1f}%, "
                  f"avg return={avg_ret*100:+.2f}%")

    return {
        "universe": label,
        "total": len(universe),
        "failed": failed,
        "signals": len(signals),
        "results": signals,
    }


# =============================================================================
# CLI dispatcher
# =============================================================================

def cmd_chanlun_scan(
    codes: Optional[List[str]] = None,
    market: Optional[str] = None,
    backtest: bool = False,
    hold_days: int = 60,
    llm_filter: bool = False,
    min_llm_score: int = 7,
) -> int:
    """
    Main entry point. Called by main.py and scheduler.

    Args:
        codes: explicit list of tickers (overrides universe)
        market: "hk" / "us" / None (auto = HK)
        backtest: also run hold_days backtest
        hold_days: backtest holding period
        llm_filter: score each signal with MiniMax-M3 and drop below threshold
        min_llm_score: threshold for llm_filter (default 7)
    """
    from src.db import init_db
    init_db()  # ensure chanlun_signal table exists

    if codes:
        summary = scan_universe(codes, "explicit", save_to_db=True,
                                backtest=backtest, hold_days=hold_days,
                                llm_filter=llm_filter, min_llm_score=min_llm_score)
    else:
        if market is None or market == "hk":
            hk = load_hk_universe()
            summary = scan_universe(hk, "HK (200)", save_to_db=True,
                                    backtest=backtest, hold_days=hold_days,
                                    llm_filter=llm_filter, min_llm_score=min_llm_score)
        else:
            summary = {"signals": 0}

        if market == "all":
            us = load_us_universe()
            us_summary = scan_universe(us, "US (200)", save_to_db=True,
                                       backtest=backtest, hold_days=hold_days,
                                       llm_filter=llm_filter, min_llm_score=min_llm_score)
            summary["signals"] += us_summary["signals"]

    print(f"\n✓ Done. {summary['signals']} signals emitted.")
    return 0 if summary["signals"] >= 0 else 1


def cmd_chanlun_list(days: int = 30, code: Optional[str] = None) -> int:
    """List recent Chanlun signals from DB."""
    from src.db import list_chanlun_signals
    signals = list_chanlun_signals(days=days, code=code)
    if not signals:
        print(f"No Chanlun signals in last {days} days.")
        return 0

    print(f"\n=== Recent Chanlun signals (last {days} days) ===\n")
    print(f"{'Date':<12}{'Code':<10}{'Entry':>10}{'Stop':>10}{'Target':>10}"
          f"{'Tech':>5}{'LLM':>5}  Rationale")
    print("-" * 100)
    for s in signals:
        llm_score = s.get('llm_score')
        llm_str = f"{llm_score}" if llm_score else "-"
        tech_str = f"{s['confidence']}"
        rationale = s.get('rationale', '')[:55]
        if s.get('llm_reasoning'):
            rationale = s['llm_reasoning'][:55]
        print(f"{s['signal_date']:<12}{s['code']:<10}"
              f"{s['entry_price']:>10.2f}{s['stop_loss']:>10.2f}{s['target']:>10.2f}"
              f"{tech_str:>5}{llm_str:>5}  {rationale}...")
    print(f"\nTotal: {len(signals)} signals")
    return 0


if __name__ == "__main__":
    cmd_chanlun_scan()