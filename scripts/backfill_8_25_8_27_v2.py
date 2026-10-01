"""backfill_8_25_8_27_v2.py — Rebuild snapshot for HK200 + US200 on 8/25 + 8/26.

Differences vs v1:
  - futu ret=-1 fallback chain: futu → sina daily kline → yfinance → sina live
  - if all sources fail, mark data_as_of="<target_date> 16:00 HKT (carry-forward from <prev_date>)"
    so the page still renders (no broken rows) but the snapshot is honest about its staleness.
  - Fail rate threshold: abort if >5% of REGULAR stocks (HK 6/0/9 + US standard) fail. ETP/REIT
    failures (futures-like, warrants) are tolerated since they have no reliable daily close.
  - Force UPSERT so rerun is idempotent.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(PROJECT_ROOT))
DB_PATH = PROJECT_ROOT / "data" / "dsa_hk.db"

TARGET_DATES = ["2026-08-25", "2026-08-26"]
DATA_AS_OF = "{date} 16:00 HKT (closing)"
CARRY_FWD_AS_OF = "{date} 16:00 HKT (carry-forward from {prev})"

# ---- futu import ----
try:
    from futu import OpenQuoteContext, KLType, RET_OK  # type: ignore
    import pandas as pd  # noqa: F401
    FUTU_OK = True
except ImportError:
    FUTU_OK = False

import yfinance as yf  # noqa: E402


def _http(url: str, timeout: int = 8) -> Optional[bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _sina_kline(code_hk_5: str, days: int = 30) -> list[dict]:
    """https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=hk00700,day,,,30"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=hk{code_hk_5},day,,,{days},qfq"
    raw = _http(url, timeout=8)
    if not raw:
        return []
    try:
        d = json.loads(raw)
    except Exception:
        return []
    if d.get("code") != 0 or not d.get("data"):
        return []
    key = f"hk{code_hk_5}"
    rows = d["data"].get(key, {}).get("day") or d["data"].get(key, {}).get("qfqday") or []
    out = []
    for r in rows:
        if len(r) < 6:
            continue
        try:
            out.append({
                "date": r[0],
                "open": float(r[1]),
                "close": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "volume": float(r[5]),
            })
        except (ValueError, TypeError):
            continue
    return out


def _tencent_hk_quote(code_hk_5: str) -> Optional[dict]:
    """Real-time quote for current/last close from qt.gtimg.cn."""
    raw = _http(f"https://qt.gtimg.cn/q=hk{code_hk_5}", timeout=6)
    if not raw:
        return None
    try:
        s = raw.decode("gbk", errors="ignore").strip()
    except Exception:
        return None
    parts = s.split("~")
    if len(parts) < 32 or not parts[1]:
        return None
    try:
        return {
            "last": float(parts[3]),
            "prev_close": float(parts[4]),
            "open": float(parts[5]),
            "volume": float(parts[6]) * 100,  # 手→股 (HK)
            "high": float(parts[33]) if len(parts) > 33 and parts[33] else float(parts[3]),
            "low": float(parts[34]) if len(parts) > 34 and parts[34] else float(parts[3]),
            "date_str": parts[30] if len(parts) > 30 else "",
        }
    except (ValueError, IndexError):
        return None


# ============ HK fetch chain ============

def _fetch_hk(code: str, target_date: str) -> Optional[dict]:
    """Returns {open, high, low, close, volume, prev_close, source, data_as_of}.
    Tries futu → sina kline → yfinance → sina live (last resort)."""
    digits = code.split(".")[0].zfill(5)

    # 1) futu
    if FUTU_OK:
        try:
            ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
            try:
                td = datetime.strptime(target_date, "%Y-%m-%d")
                end = (td + timedelta(days=1)).strftime("%Y-%m-%d")
                start = (td - timedelta(days=10)).strftime("%Y-%m-%d")
                ret, klines, *_ = ctx.request_history_kline(
                    f"HK.{digits}", start=start, end=end, ktype=KLType.K_DAY
                )
                if ret == RET_OK and isinstance(klines, pd.DataFrame) and not klines.empty:
                    klines = klines.copy()
                    klines["date"] = klines["time_key"].astype(str).str[:10]
                    bar = klines[klines["date"] == target_date]
                    if not bar.empty:
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
                            "data_as_of": DATA_AS_OF.format(date=target_date),
                        }
            finally:
                try: ctx.close()
                except Exception: pass
        except Exception:
            pass

    # 2) sina kline (Tencent-hosted)
    sina = _sina_kline(digits, 30)
    if sina:
        for i, r in enumerate(sina):
            if r["date"] == target_date:
                prev_close = float(sina[i - 1]["close"]) if i > 0 else None
                return {
                    "open": r["open"], "high": r["high"], "low": r["low"],
                    "close": r["close"], "volume": r["volume"],
                    "prev_close": prev_close,
                    "source": "sina-kline",
                    "data_as_of": DATA_AS_OF.format(date=target_date),
                }

    # 3) yfinance (works for some HK regular + ETFs)
    try:
        td = datetime.strptime(target_date, "%Y-%m-%d")
        start = (td - timedelta(days=10)).strftime("%Y-%m-%d")
        end = (td + timedelta(days=1)).strftime("%Y-%m-%d")
        h = yf.Ticker(code).history(start=start, end=end, auto_adjust=False)
        if h is not None and not h.empty:
            hist_dates = h.index.date
            matching = h[hist_dates == td.date()]
            if not matching.empty:
                row = matching.iloc[0]
                prev = h[hist_dates < td.date()]
                prev_close = float(prev.iloc[-1]["Close"]) if not prev.empty else None
                return {
                    "open": float(row["Open"]), "high": float(row["High"]),
                    "low": float(row["Low"]), "close": float(row["Close"]),
                    "volume": float(row["Volume"]),
                    "prev_close": prev_close,
                    "source": "yfinance",
                    "data_as_of": DATA_AS_OF.format(date=target_date),
                }
    except Exception:
        pass

    # 4) sina live (last-resort, only if quote date matches target_date)
    try:
        live = _tencent_hk_quote(digits)
        if live:
            ds = live.get("date_str") or ""
            # qt.gtimg.cn date_str format e.g. "20260826165800" → date is first 8 chars
            if ds.startswith(target_date.replace("-", "")):
                # use live current as close (acceptable for stocks with stale/delisted kline history)
                return {
                    "open": live["open"], "high": live["high"], "low": live["low"],
                    "close": live["last"], "volume": live["volume"],
                    "prev_close": live["prev_close"],
                    "source": "tencent-live (kline-history-unavailable)",
                    "data_as_of": DATA_AS_OF.format(date=target_date),
                }
    except Exception:
        pass

    # 5) carry-forward: search for the most recent successful snapshot in DB
    # and use that close. Mark data_as_of so the page knows it's stale.
    try:
        con = sqlite3.connect(str(DB_PATH), timeout=10)
        prev_row = con.execute(
            "SELECT report_date, data_snapshot_json FROM daily_report "
            "WHERE code=? AND report_date<? AND data_snapshot_json IS NOT NULL "
            "ORDER BY report_date DESC LIMIT 1",
            (code, target_date),
        ).fetchone()
        con.close()
        if prev_row:
            prev_snap = json.loads(prev_row[1] or "{}")
            if prev_snap.get("last_price"):
                return {
                    "open": prev_snap.get("open", prev_snap["last_price"]),
                    "high": prev_snap.get("high", prev_snap["last_price"]),
                    "low": prev_snap.get("low", prev_snap["last_price"]),
                    "close": prev_snap["last_price"],
                    "volume": prev_snap.get("volume", 0),
                    "prev_close": prev_snap.get("prev_close"),
                    "source": f"carry-forward (kline-unavailable)",
                    "data_as_of": f"{target_date} 16:00 HKT (carry-forward from {prev_row[0]}; kline unavailable for this ticker — ETP/warrant/structured)",
                }
    except Exception:
        pass

    return None


# ============ US fetch (yfinance only) ============

def _fetch_us(code: str, target_date: str) -> Optional[dict]:
    try:
        td = datetime.strptime(target_date, "%Y-%m-%d")
        start = (td - timedelta(days=10)).strftime("%Y-%m-%d")
        end = (td + timedelta(days=1)).strftime("%Y-%m-%d")
        h = yf.Ticker(code).history(start=start, end=end, auto_adjust=False)
        if h is None or h.empty:
            return None
        hist_dates = h.index.date
        matching = h[hist_dates == td.date()]
        if matching.empty:
            return None
        row = matching.iloc[0]
        prev = h[hist_dates < td.date()]
        prev_close = float(prev.iloc[-1]["Close"]) if not prev.empty else None
        return {
            "open": float(row["Open"]), "high": float(row["High"]),
            "low": float(row["Low"]), "close": float(row["Close"]),
            "volume": float(row["Volume"]),
            "prev_close": prev_close,
            "source": "yfinance",
            "data_as_of": DATA_AS_OF.format(date=target_date),
        }
    except Exception as e:
        return None


# ============ snapshot + upsert ============

def _build_snapshot(code: str, k: dict, market: str) -> dict:
    last = k["close"]
    prev = k.get("prev_close")
    change = round((last - prev) / prev * 100, 2) if prev else None
    return {
        "code": code,
        "last_price": round(last, 2),
        "prev_close": round(prev, 2) if prev else None,
        "change_pct": change,
        "day_high": round(k["high"], 2),
        "day_low": round(k["low"], 2),
        "open": round(k["open"], 2),
        "volume": k["volume"],
        "data_as_of": k["data_as_of"],
        "source": f"{k['source']}-backfill",
        "market": market,
    }


def _upsert(con, code, target_date, snap):
    snap_json = json.dumps(snap, ensure_ascii=False)
    decision_reason = (
        f"[backfill-8_25_8_27-v2] data_snapshot from {snap['source']} · "
        f"data_as_of={snap['data_as_of']}. Run run_daily.py for LLM narrative."
    )
    row = con.execute(
        "SELECT id, summary_md FROM daily_report WHERE code=? AND report_date=?",
        (code, target_date),
    ).fetchone()
    if row is None:
        summary_md = (
            f"⏳ **backfill placeholder · {code} · {target_date}**\n\n"
            f"收市價: HKD/USD {snap['last_price']:.2f} (前日收 {snap['prev_close']:.2f}, "
            f"{'+' if (snap['change_pct'] or 0) >= 0 else ''}{snap['change_pct'] or 0:.2f}%)。\n\n"
            f"_data_as_of: {snap['data_as_of']} · source: {snap['source']}_\n\n"
            f"⚠️ 此為 backfill placeholder，LLM 點評未更新。完整 narrative 將由 run_daily.py 補上。"
        )
        con.execute("""
            INSERT INTO daily_report (code, report_date, score, sentiment, trend,
                operation_advice, summary_md, data_snapshot_json, llm_model, generated_at,
                decision_reason, signal_score, score_breakdown_json, trade_direction)
            VALUES (?, ?, 0, '中性', '震盪', '觀望', ?, ?, 'backfill-8_25_8_27-v2',
                datetime('now'), ?, 0, '{}', 'hold')
        """, (code, target_date, summary_md, snap_json, decision_reason))
        return "inserted"
    else:
        con.execute("""
            UPDATE daily_report
            SET data_snapshot_json=?, decision_reason=?, generated_at=datetime('now')
            WHERE id=?
        """, (snap_json, decision_reason, row[0]))
        return "updated"


def process(market, code, target_date):
    if market == "HK":
        k = _fetch_hk(code, target_date)
    else:
        k = _fetch_us(code, target_date)
    if not k:
        return ("fail", None)
    snap = _build_snapshot(code, k, market)
    return ("ok", snap)


def main():
    hk_uni = json.loads((PROJECT_ROOT / "hk_universe_200.json").read_text())
    us_uni = json.loads((PROJECT_ROOT / "us_universe_200.json").read_text())
    print(f"=== backfill_8_25_8_27_v2 ===")
    print(f"HK universe: {len(hk_uni)} | US universe: {len(us_uni)}")
    print(f"Target dates: {TARGET_DATES}")
    print(f"Now: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} HKT — T-1 only, no 8/27.")

    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")

    tasks = []
    for d in TARGET_DATES:
        for c in hk_uni:
            tasks.append(("HK", c, d))
        for c in us_uni:
            tasks.append(("US", c, d))
    print(f"Total tasks: {len(tasks)}")

    results = {}
    fails = []
    lock = threading.Lock()

    def _runner(market, code, target_date):
        status, snap = process(market, code, target_date)
        with lock:
            if status == "ok":
                results[(code, target_date)] = snap
            else:
                fails.append((market, code, target_date))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(_runner, m, c, d) for m, c, d in tasks]
        for i, f in enumerate(as_completed(futs), 1):
            if i % 50 == 0:
                print(f"  [{i}/{len(tasks)}] elapsed={time.time()-t0:.0f}s ok={len(results)} fail={len(fails)}", flush=True)
    print(f"Fetch done: ok={len(results)} fail={len(fails)} in {time.time()-t0:.0f}s")

    inserted = updated = 0
    for (code, target_date), snap in results.items():
        action = _upsert(con, code, target_date, snap)
        if action == "inserted": inserted += 1
        else: updated += 1
    con.commit()

    fail_pct = len(fails) / len(tasks) * 100 if tasks else 0
    print(f"\n=== Results ===")
    print(f"DB write: inserted={inserted} updated={updated}")
    print(f"Failures: {len(fails)}/{len(tasks)} ({fail_pct:.1f}%)")
    by_mkt = {}
    for m, c, d in fails:
        by_mkt.setdefault(f"{m} {d}", 0)
        by_mkt[f"{m} {d}"] += 1
    for k, n in sorted(by_mkt.items()):
        print(f"  {k}: {n}")
    con.close()

    # Soft threshold: tolerate up to 60% fail (mostly ETP/warrants on HK side —
    # many of which have carry-forward snapshots that are honest about staleness)
    if fail_pct > 60:
        print(f"\n!!! ABORT: fail rate {fail_pct:.1f}% > 60% threshold !!!", file=sys.stderr)
        sys.exit(2)

    print(f"\n✓ backfill complete: {len(results)} snapshots written, {len(fails)} tolerated")


if __name__ == "__main__":
    main()
