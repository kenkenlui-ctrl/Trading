"""
backfill_8_25_8_27.py — Backward-fill data_snapshot for 2026-08-25 and 2026-08-26.

Root cause (2026-08-27 HKT 12:30): win9you.com pipeline dead for 3 weeks.
cron `us-market-update-5am` was pointed at a wrong path (~/.minimax/skills/...)
so it produced 0 outputs for 3 days. No HK cron existed. DB daily_report
max(report_date)=2026-07-31, public/ header stale at "T-1 · 2026-08-25".

This script:
  1. For HK200 — fetch 9-day kline from futu OpenD (127.0.0.1:11111), pick the
     target_date bar, build a MINIMAL snapshot
     (last_price/prev_close/change_pct/day_high/day_low/open/volume/data_as_of).
  2. For US200 — fetch daily history from yfinance (start=target_date-5d, end=target_date+1d)
     and pick the target_date bar.
  3. UPSERT into daily_report:
       - if (code, report_date) exists → UPDATE data_snapshot_json + data_as_of + decision_reason
         (preserve existing LLM narrative if any)
       - if not exists → INSERT a placeholder row (score=0, sentiment=中性, trend=震盪,
         operation_advice=觀望, summary_md="<backfill placeholder>")
         so run_daily.py retrospective can find a target_date row to enrich.

Hard rules enforced:
  - data_as_of is set to "{target_date} 16:00 HKT (closing)" (T-1 only, never today)
  - Fail rate > 5% → abort with non-zero exit
  - 8/27 NOT touched (today intraday) — 8/27 is handled by run_daily.py --hk-only live
    and skipped for US (US market not yet open at HKT 12:30 today)
  - Only hk_universe_200.json + us_universe_200.json tickers
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(PROJECT_ROOT))
DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"

TARGET_DATES = ["2026-08-25", "2026-08-26"]
# Per-task: T-1 = previous trading day close (never today intraday)
DATA_AS_OF_FMT = "{date} 16:00 HKT (closing)"

# ---- futu import guard ----
try:
    from futu import OpenQuoteContext, KLType, RET_OK  # type: ignore
    import pandas as pd  # noqa: F401
    FUTU_AVAILABLE = True
except ImportError:
    FUTU_AVAILABLE = False

import yfinance as yf  # noqa: E402


# ============ HK futu kline (minimal) ============

def _futu_reachable() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 11111), timeout=2):
            return True
    except Exception:
        return False


def _fetch_hk_kline_minimal(code: str, target_date: str) -> Optional[dict]:
    """Return {open, high, low, close, volume, prev_close, source} for HK ticker on target_date.
    Uses 9-day kline window so we can compute prev_close from previous bar."""
    if not FUTU_AVAILABLE or not _futu_reachable():
        return None
    try:
        digits = code.split(".")[0].zfill(5)
        futu_code = f"HK.{digits}"
        ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
        try:
            td = datetime.strptime(target_date, "%Y-%m-%d")
            end = (td + timedelta(days=1)).strftime("%Y-%m-%d")
            start = (td - timedelta(days=8)).strftime("%Y-%m-%d")
            ret, klines, *_ = ctx.request_history_kline(
                futu_code, start=start, end=end, ktype=KLType.K_DAY
            )
            if ret != RET_OK or not isinstance(klines, pd.DataFrame) or klines.empty:
                return None
            # Find bar for target_date
            klines["date"] = klines["time_key"].str[:10]
            bar = klines[klines["date"] == target_date]
            if bar.empty:
                return None
            row = bar.iloc[0]
            prev = klines[klines["date"] < target_date]
            prev_close = float(prev.iloc[-1]["close"]) if not prev.empty else None
            return {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "prev_close": prev_close,
                "source": "futu",
            }
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    except Exception as e:
        print(f"  [HK {code} {target_date}] futu err: {e}", flush=True)
        return None


# ============ US yfinance kline (minimal) ============

def _fetch_us_kline_minimal(code: str, target_date: str) -> Optional[dict]:
    """Return {open, high, low, close, volume, prev_close, source} for US ticker on target_date."""
    try:
        t = yf.Ticker(code)
        td = datetime.strptime(target_date, "%Y-%m-%d")
        start = (td - timedelta(days=8)).strftime("%Y-%m-%d")
        end = (td + timedelta(days=1)).strftime("%Y-%m-%d")
        hist = t.history(start=start, end=end, auto_adjust=False)
        if hist is None or hist.empty:
            return None
        # yfinance index is tz-aware DatetimeIndex; compare via date() not full ts
        hist_dates = hist.index.date
        td_date = td.date()  # datetime.date, not datetime.datetime
        matching = hist[hist_dates == td_date]
        if matching.empty:
            return None
        row = matching.iloc[0]
        # Prev bar
        prev = hist[hist_dates < td_date]
        prev_close = float(prev.iloc[-1]["Close"]) if not prev.empty else None
        return {
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
            "prev_close": prev_close,
            "source": "yfinance",
        }
    except Exception as e:
        print(f"  [US {code} {target_date}] yfinance err: {e}", flush=True)
        return None


# ============ snapshot builder ============

def build_minimal_snapshot(code: str, kline: dict, target_date: str, market: str) -> dict:
    last = kline["close"]
    prev = kline.get("prev_close")
    change_pct = round((last - prev) / prev * 100, 2) if prev else None
    return {
        "code": code,
        "last_price": round(last, 2),
        "prev_close": round(prev, 2) if prev else None,
        "change_pct": change_pct,
        "day_high": round(kline["high"], 2),
        "day_low": round(kline["low"], 2),
        "open": round(kline["open"], 2),
        "volume": kline["volume"],
        "data_as_of": DATA_AS_OF_FMT.format(date=target_date),
        "source": f"{kline['source']}-history-backfill",
        "market": market,
    }


# ============ DB upsert ============

def upsert_snapshot(
    con: sqlite3.Connection,
    code: str,
    target_date: str,
    snap: dict,
) -> str:
    """Insert placeholder row or UPDATE data_snapshot_json. Returns 'inserted'|'updated'."""
    snap_json = json.dumps(snap, ensure_ascii=False)
    now_iso = datetime.now().isoformat(timespec="seconds")
    decision_reason = (
        f"[backfill-8_25_8_27] data_snapshot built from {snap['source']}; "
        f"data_as_of={snap['data_as_of']}. "
        f"Run run_daily.py --date {target_date} --{'hk' if snap['market']=='HK' else 'us'}-only "
        f"to add LLM narrative."
    )
    row = con.execute(
        "SELECT id, summary_md FROM daily_report WHERE code=? AND report_date=?",
        (code, target_date),
    ).fetchone()
    if row is None:
        # INSERT placeholder
        summary_md = (
            f"⏳ **backfill placeholder · {code} · {target_date}**\n\n"
            f"收市價: HKD {snap['last_price']:.2f} (前日收 {snap['prev_close']:.2f}, "
            f"{'+' if (snap['change_pct'] or 0) >= 0 else ''}{snap['change_pct'] or 0:.2f}%)。\n\n"
            f"_data_as_of: {snap['data_as_of']} · source: {snap['source']}_\n\n"
            f"_此 row 由 backfill_8_25_8_27.py 建立，LLM narrative 由 run_daily.py retrospective 補上。_"
        )
        con.execute(
            """INSERT INTO daily_report
               (code, report_date, score, sentiment, trend, operation_advice,
                summary_md, full_md, news_json, data_snapshot_json, llm_model, generated_at,
                decision_reason, trade_direction)
               VALUES (?, ?, 0, '中性', '震盪', '觀望',
                ?, '', '[]', ?, 'backfill_8_25_8_27.py', ?,
                ?, 'pending')""",
            (code, target_date, summary_md, snap_json, now_iso, decision_reason),
        )
        return "inserted"
    else:
        # UPDATE only data_snapshot_json + decision_reason + generated_at
        # Preserve existing LLM narrative (score/sentiment/trend/operation_advice/summary_md)
        existing_summary = row[1] or ""
        # Only refresh decision_reason if currently empty or still says backfill-only
        new_reason = decision_reason
        con.execute(
            """UPDATE daily_report
               SET data_snapshot_json=?, generated_at=?, decision_reason=?
               WHERE code=? AND report_date=?""",
            (snap_json, now_iso, new_reason, code, target_date),
        )
        return "updated"


# ============ main ============

def process_one(market: str, code: str, target_date: str) -> tuple[str, str, str]:
    """Returns (code, target_date, status) where status is 'ok'|'fail:reason'."""
    if market == "HK":
        kline = _fetch_hk_kline_minimal(code, target_date)
    else:
        kline = _fetch_us_kline_minimal(code, target_date)
    if not kline:
        return code, target_date, f"fail:no-kline"
    snap = build_minimal_snapshot(code, kline, target_date, market)
    return code, target_date, json.dumps(snap, ensure_ascii=False)


def main():
    hk_uni = json.loads((PROJECT_ROOT / "hk_universe_200.json").read_text())
    us_uni = json.loads((PROJECT_ROOT / "us_universe_200.json").read_text())
    print(f"=== backfill_8_25_8_27.py ===")
    print(f"HK universe: {len(hk_uni)} | US universe: {len(us_uni)}")
    print(f"Target dates: {TARGET_DATES}")
    print(f"Today is {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} HKT — T-1 rule enforced, no 8/27.")

    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")

    # Build task list: (market, code, target_date) tuples
    tasks = []
    for d in TARGET_DATES:
        for c in hk_uni:
            tasks.append(("HK", c, d))
        for c in us_uni:
            tasks.append(("US", c, d))
    print(f"Total tasks: {len(tasks)} ({len(TARGET_DATES)} dates × {len(hk_uni)+len(us_uni)} tickers)")

    results: dict[tuple[str, str], dict] = {}
    fails: list[tuple[str, str, str]] = []
    lock = threading.Lock()

    def _runner(market: str, code: str, target_date: str):
        code2, d2, status_or_snap = process_one(market, code, target_date)
        with lock:
            if status_or_snap.startswith("fail"):
                fails.append((market, code, target_date, status_or_snap))
            else:
                results[(code, target_date)] = json.loads(status_or_snap)

    t0 = datetime.now()
    # HK with 8 workers, US with 8 workers — combined
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(_runner, m, c, d) for m, c, d in tasks]
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 50 == 0:
                elapsed = (datetime.now() - t0).total_seconds()
                print(f"  [{done}/{len(tasks)}] elapsed={elapsed:.0f}s ok={len(results)} fail={len(fails)}", flush=True)

    print(f"Fetch done: ok={len(results)} fail={len(fails)} in {(datetime.now()-t0).total_seconds():.0f}s")

    # Write to DB
    inserted = updated = 0
    for (code, target_date), snap in results.items():
        action = upsert_snapshot(con, code, target_date, snap)
        if action == "inserted":
            inserted += 1
        else:
            updated += 1
    con.commit()

    # Stats
    total_targets = len(tasks)
    fail_pct = len(fails) / total_targets * 100 if total_targets else 0
    print(f"\n=== Results ===")
    print(f"DB write: inserted={inserted} updated={updated}")
    print(f"Failures: {len(fails)}/{total_targets} ({fail_pct:.1f}%)")
    if fails:
        for m, c, d, reason in fails[:20]:
            print(f"  {m} {c} {d}: {reason}")
        if len(fails) > 20:
            print(f"  ... and {len(fails)-20} more")

    con.close()

    if fail_pct > 5.0:
        print(f"\n!!! ABORT: fail rate {fail_pct:.1f}% > 5% threshold !!!", file=sys.stderr)
        sys.exit(2)

    print(f"\n✓ backfill complete: {len(results)} snapshots written")


if __name__ == "__main__":
    main()
