"""
QA stale-check: detect daily_report rows whose stored change_pct disagrees with
live Tencent gtimg quote.

Background (2026-06-27 bug):
  YFinance returned Thursday's close (11.31) for 2291.HK on Saturday morning.
  The live overlay either failed or never ran, so the LLM saw change_pct=+20.06%
  (the Thursday intraday move) instead of the actual Friday move (-11.94%).
  The dashboard then displayed "今日爆升20.06%" — exactly the wrong narrative.

This script:
  1. Iterates daily_report rows where report_date = <target_date> and code != ''.
  2. For each row:
     - Reads stored change_pct from data_snapshot_json (most reliable — that's
       the value the LLM was given).
     - Fetches live quote from Tencent qt.gtimg.cn/q=hk{code5} or q=us{SYM}.
     - Computes live change_pct from (current - prev_close) / prev_close.
  3. Compares stored vs live. If |delta| > STALE_THRESHOLD_PP, marks "stale".
  4. Prints table: code | stored_change | live_change | delta | action.

Usage:
  python -m src.qa_stale_check                       # default 2026-06-27
  python -m src.qa_stale_check --date 2026-06-26
  python -m src.qa_stale_check --threshold 2.0
  python -m src.qa_stale_check --json /tmp/stale.json   # machine-readable output
  python -m src.qa_stale_check --rerun                # auto-delete stale rows
                                                     # (use with --dry-run first!)
  python -m src.qa_stale_check --codes 2291.HK,0700.HK  # limit to specific tickers

This script is READ-ONLY by default. It only writes to the DB if --rerun is passed
(to delete rows so the next `python -m src.main one <code>` regenerates them).
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_config  # noqa: E402

logger = logging.getLogger("qa_stale_check")


# ============ Tencent quote fetching ============

def _to_tencent_code(code: str) -> str:
    """Map DSA-HK internal ticker codes to Tencent gtimg.cn path.

    HK: '2291.HK' -> 'hk02291' (5-digit zero-padded)
    US: 'AAPL'    -> 'usAAPL' (no padding)
    """
    code = code.strip()
    if code.endswith(".HK"):
        digits = code.split(".")[0].zfill(5)
        return f"hk{digits}"
    return f"us{code}"


def fetch_tencent_quote(code: str, timeout: float = 4.0) -> Optional[dict]:
    """Fetch live quote from Tencent qt.gtimg.cn.

    Returns dict with keys: current, prev_close, change_pct, change_amt, datetime.
    Field indices are identical for HK and US endpoints:
      [3]=current, [4]=prev_close, [31]=change_amt, [32]=change_pct, [30]=datetime
    """
    try:
        url = f"https://qt.gtimg.cn/q={_to_tencent_code(code)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("gbk", errors="replace")
        i1 = raw.find('"')
        i2 = raw.rfind('"')
        if i1 < 0 or i2 <= i1:
            return None
        body = raw[i1 + 1 : i2]
        fields = body.split('~')
        if len(fields) < 33:
            return None
        def _f(idx: int) -> Optional[float]:
            try:
                v = float(fields[idx])
                if v != v:  # NaN
                    return None
                return v
            except (TypeError, ValueError, IndexError):
                return None
        current = _f(3)
        prev_close = _f(4)
        change_pct_raw = _f(32)
        # Prefer Tencent's reported pct (more reliable than recomputing for splits).
        # Fall back to recomputed pct if Tencent's is missing.
        if current is not None and prev_close is not None and prev_close != 0:
            computed_pct = round((current - prev_close) / prev_close * 100, 2)
        else:
            computed_pct = None
        change_pct = change_pct_raw if change_pct_raw is not None else computed_pct
        return {
            "current": current,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "change_amt": _f(31),
            "datetime": fields[30] if len(fields) > 30 else "",
            "computed_pct": computed_pct,
        }
    except Exception as e:
        logger.debug(f"Tencent fetch failed for {code}: {e}")
        return None


# ============ Stored snapshot parsing ============

def parse_stored_change_pct(snapshot_json: Optional[str], summary_md: Optional[str]) -> Optional[float]:
    """Best-effort extraction of the change_pct that the LLM was given.

    Priority order:
      1. data_snapshot_json.change_pct — most reliable (it's the literal value
         the LLM saw in the prompt).
      2. Regex on summary_md — fallback if snapshot_json is missing/corrupt.
    """
    # 1. data_snapshot_json
    if snapshot_json:
        try:
            d = json.loads(snapshot_json)
            v = d.get("change_pct")
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        except (json.JSONDecodeError, TypeError):
            pass

    # 2. Regex on summary_md
    if not summary_md:
        return None
    import re

    # Priority A: "(+X.XX%)" / "(-X.XX%)" / "(X.XX%)" inside parens
    # Priority B: "急升/爆升/上漲/重挫/急跌/下跌 + X.XX%"
    # Priority C: "今日 ... X%" — but skip YTD-like contexts
    # Use first match — heuristics, not perfect.

    # Try parenthetical first (most unambiguous)
    paren = re.search(r"\(([+-]?\d+(?:\.\d+)?)\s*%\)", summary_md)
    if paren:
        try:
            return float(paren.group(1))
        except ValueError:
            pass

    # Try action-verb + pct: 急升/爆升/上漲/上升 + NUM%
    verb_up = re.search(r"(?:急升|爆升|上漲|上升|漲|飆升|揚|彈升|升)\s*[+]?(\d+(?:\.\d+)?)\s*%", summary_md)
    if verb_up:
        try:
            return float(verb_up.group(1))
        except ValueError:
            pass

    # Try action-verb + pct: 急跌/暴跌/下挫/下跌/跌/重挫 + NUM%
    verb_dn = re.search(r"(?:急跌|暴跌|下挫|下跌|跌|重挫|急挫|挫|低開)\s*[-]?(\d+(?:\.\d+)?)\s*%", summary_md)
    if verb_dn:
        try:
            return -float(verb_dn.group(1))
        except ValueError:
            pass

    # Last resort: signed pct near 今日/當日
    today = re.search(r"(?:今日|當日|今天)[^%]{0,15}?([+-]\d+(?:\.\d+)?)\s*%", summary_md)
    if today:
        try:
            return float(today.group(1))
        except ValueError:
            pass

    return None


# ============ DB scan ============

def iter_target_rows(conn: sqlite3.Connection, date: str, codes: Optional[list[str]] = None) -> list[dict]:
    """Return rows for the target date with code != ''."""
    if codes:
        placeholders = ",".join("?" for _ in codes)
        sql = (
            f"SELECT id, code, summary_md, data_snapshot_json "
            f"FROM daily_report WHERE report_date=? AND code != '' AND code IN ({placeholders}) "
            f"ORDER BY code ASC"
        )
        rows = conn.execute(sql, [date, *codes]).fetchall()
    else:
        sql = (
            "SELECT id, code, summary_md, data_snapshot_json "
            "FROM daily_report WHERE report_date=? AND code != '' "
            "ORDER BY code ASC"
        )
        rows = conn.execute(sql, (date,)).fetchall()
    return [dict(r) for r in rows]


def delete_row(conn: sqlite3.Connection, row_id: int) -> int:
    """Delete a single daily_report row. Returns affected rowcount."""
    cur = conn.execute("DELETE FROM daily_report WHERE id=?", (row_id,))
    conn.commit()
    return cur.rowcount


# ============ Main ============

def main() -> int:
    parser = argparse.ArgumentParser(description="QA stale-check daily reports vs live Tencent")
    parser.add_argument("--date", default="2026-06-27", help="report_date to check (YYYY-MM-DD)")
    parser.add_argument("--threshold", type=float, default=1.5,
                        help="absolute pp delta to flag as stale (default 1.5)")
    parser.add_argument("--json", metavar="PATH", help="write machine-readable JSON output to PATH")
    parser.add_argument("--rerun", action="store_true",
                        help="delete stale rows from DB (use with --dry-run first)")
    parser.add_argument("--dry-run", action="store_true",
                        help="don't actually delete rows, just print what would happen")
    parser.add_argument("--codes", metavar="CSV",
                        help="comma-separated list of tickers to limit check (e.g. 2291.HK,0700.HK)")
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = get_config()
    db_path = Path(cfg.database_path)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        codes_filter = (
            [c.strip() for c in args.codes.split(",") if c.strip()]
            if args.codes else None
        )
        rows = iter_target_rows(conn, args.date, codes=codes_filter)
        if not rows:
            print(f"No rows for report_date={args.date} (filter={codes_filter})")
            return 0

        print(f"=== QA stale-check: report_date={args.date} (n={len(rows)}, threshold={args.threshold}pp) ===\n")
        print(f"{'code':<12} {'stored%':>9} {'live%':>9} {'delta':>8}  {'action':<10}  note")
        print("-" * 72)

        results = []
        stale_codes: list[str] = []
        ok = 0
        skipped = 0
        for r in rows:
            code = r["code"]
            stored = parse_stored_change_pct(r.get("data_snapshot_json"), r.get("summary_md"))
            if stored is None:
                skipped += 1
                print(f"{code:<12} {'?':>9} {'?':>9} {'?':>8}  {'skip':<10}  no stored pct")
                results.append({"code": code, "action": "skip", "reason": "no stored pct"})
                continue

            live = fetch_tencent_quote(code)
            if not live or live["change_pct"] is None:
                skipped += 1
                note = "tencent fetch failed"
                print(f"{code:<12} {stored:>+9.2f} {'?':>9} {'?':>8}  {'skip':<10}  {note}")
                results.append({
                    "code": code, "stored_change": stored, "action": "skip",
                    "reason": note,
                })
                continue

            live_pct = float(live["change_pct"])
            delta = abs(stored - live_pct)
            if delta > args.threshold:
                action = "re-run"
                stale_codes.append(code)
                if args.rerun and not args.dry_run:
                    delete_row(conn, r["id"])
                    action = "re-run (deleted)"
            else:
                action = "ok"
                ok += 1
            note = f"live current={live['current']} prev={live['prev_close']} @ {live['datetime']}"
            print(f"{code:<12} {stored:>+9.2f} {live_pct:>+9.2f} {delta:>8.2f}  {action:<10}  {note}")
            results.append({
                "code": code,
                "row_id": r["id"],
                "stored_change": stored,
                "live_change": live_pct,
                "delta": round(delta, 4),
                "live_current": live["current"],
                "live_prev_close": live["prev_close"],
                "live_datetime": live["datetime"],
                "action": "stale" if action.startswith("re-run") else "ok",
                "row_deleted": bool(args.rerun and not args.dry_run and action.startswith("re-run")),
            })

        stale_n = len(stale_codes)
        print(f"\n=== Summary ===")
        print(f"Total rows:        {len(rows)}")
        print(f"OK:                {ok}")
        print(f"Stale (> {args.threshold}pp):  {stale_n}")
        print(f"Skipped:           {skipped}")
        if stale_codes:
            print(f"\nStale tickers: {', '.join(stale_codes)}")
            if not args.rerun:
                print("\nTo regenerate stale reports, either:")
                print(f"  python -m src.main one <code>             # one at a time")
                print(f"  python -m src.qa_stale_check --rerun      # delete stale rows (next run will regenerate)")
        else:
            print("\n✅ All stored change_pct values are within threshold of live Tencent data.")

        if args.json:
            out = {
                "report_date": args.date,
                "threshold_pp": args.threshold,
                "total": len(rows),
                "ok": ok,
                "stale": stale_n,
                "skipped": skipped,
                "stale_codes": stale_codes,
                "results": results,
            }
            Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2))
            print(f"\nWrote JSON to {args.json}")

        # Exit code: 0 if no stale, 2 if stale (so CI can gate), 1 on error.
        return 0 if stale_n == 0 else 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())