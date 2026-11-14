#!/usr/bin/env python3
"""rebuild_snapshots.py — recompute the daily-sr-chart snapshot FROM the
stored OHLC series.

WHY THIS EXISTS (2026-11-14)
----------------------------
A ticker page shows TWO files that must agree: the series the interactive
chart draws (public/<mkt>/ohlc/<sym>_ohlc.json) and the analysis payload
beside it (charts/<mkt>/<sym>.json — phase, S/R ladders, last_bar). When
they are produced by two different fetches they drift, and the page then
shows a fresh chart beside stale levels.

They had drifted badly. 77 of the 245 HK names carried an October snapshot
while their series was already at 2026-11-13: the pages drew a candle from
the 13th next to support levels computed from the 29th of October. The
self-test missed it because those pages were carrying the "數據未更新"
banner, and the chart-vs-analysis gate skips a page that already discloses
its own staleness.

The root cause is that the snapshot and the series were fetched
INDEPENDENTLY, by different steps, on different days, from different
vendors (refresh.sh step 2 ran daily-sr-chart with --source auto, which
prefers Futu's qfq series, while the series itself is Yahoo RAW — a
6%-apart price basis on a dividend payer).

THE FIX IS STRUCTURAL, NOT ANOTHER FETCH
-----------------------------------------
daily_sr.run() computes everything from whatever fetch_ohlc() returns, so
feeding it the STORED series makes the two files the same series BY
CONSTRUCTION: no vendor call, no basis to disagree about, no chance of
drift. It also removes the need for the series to be re-fetched to refresh
the analysis — the analysis is a pure function of the series.

The PNG is written too, so the noscript fallback stays in step.

Usage:
    python3 scripts/rebuild_snapshots.py --markets hk
    python3 scripts/rebuild_snapshots.py --markets hk --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path("/Users/kenken/.minimax/skills/daily-sr-chart/scripts")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))
sys.path.insert(0, str(REPO / "scripts"))

COLS = ("date", "open", "high", "low", "close", "volume")
# The snapshot rounds C to 2dp, so a stored close may differ by half a cent
# without the two files disagreeing.
CLOSE_TOL = 0.006


def market_dirs() -> dict:
    import update_ohlc
    return update_ohlc.MARKETS


def read_series(path: Path) -> list[dict]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and "date" in r]


def snapshot_is_current(snap_path: Path, rows: list[dict], days: int = 200) -> bool:
    """True when the snapshot was computed from THIS series.

    The last bar alone is not enough: the levels are computed from the whole
    200-bar window, so a snapshot taken from a different window (or from a
    different price basis that happens to share the last close) is not
    current. The window start and the last bar have to agree.
    """
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    lb = snap.get("last_bar") or {}
    if lb.get("date") != rows[-1]["date"]:
        return False
    try:
        if abs(float(lb.get("C")) - float(rows[-1]["close"])) > CLOSE_TOL:
            return False
    except (TypeError, ValueError):
        return False
    window = rows[-days:] if len(rows) >= days else rows
    want_start = window[0]["date"]
    dr = str(snap.get("data_range") or "")
    return dr.startswith(want_start)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="hk")
    ap.add_argument("--days", type=int, default=200,
                    help="bars fed to the analysis (matches daily-sr-chart)")
    ap.add_argument("--all", action="store_true",
                    help="rebuild every ticker, not just the stale ones")
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args()

    import daily_sr
    import pandas as pd
    from update_ohlc import universe

    manifest_path = REPO / "config" / "ohlc_tail_futu.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    def patched_fetch(ticker, days=200, source="auto", asof=None):
        """daily_sr.run() asks for the series; we hand back the stored one."""
        rows = patched_fetch._rows
        df = pd.DataFrame(rows[-days:]).copy()
        df["date"] = pd.to_datetime(df["date"])
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float)
        df["volume"] = df["volume"].astype(float)
        label = patched_fetch._label
        return df, label, patched_fetch._name

    rebuilt, current, failed = [], [], []
    for mkt in [m.strip() for m in a.markets.split(",") if m.strip()]:
        upath, out_dir = market_dirs()[mkt]
        for tk, safe in universe(upath, out_dir):
            rows = read_series(out_dir / f"{safe}_ohlc.json")
            if not rows:
                continue
            snap_path = out_dir / f"{safe}.json"
            if not a.all and snapshot_is_current(snap_path, rows, a.days):
                current.append(tk)
                continue

            name = tk
            old = {}
            try:
                old = json.loads(snap_path.read_text(encoding="utf-8"))
                name = old.get("name") or tk
            except Exception:
                pass
            # The label states where the series came from, and a series
            # whose newest bar came from Futu says so. The record lives in
            # config/ (tracked) so the claim is reproducible.
            label = "yfinance+futu" if f"{mkt}:{safe}" in manifest else "yfinance"

            patched_fetch._rows = rows
            patched_fetch._label = label
            patched_fetch._name = name
            daily_sr.fetch_ohlc = patched_fetch
            try:
                png = out_dir / f"{safe}.png"
                snap = daily_sr.run(tk, str(png) if not a.no_png else "/dev/null",
                                    days=a.days, source=label, asof=None)
                # Keep any extra key the market's own writer added (JP's
                # flat asof/chg_pct/close), but never let it keep describing
                # a session the rebuilt snapshot no longer claims.
                for k, v in old.items():
                    if k not in snap:
                        snap[k] = v
                if "asof" in snap:
                    snap["asof"] = snap["last_bar"]["date"]
                snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
                lb = snap["last_bar"]
                rebuilt.append((tk, lb["date"], lb["C"], label))
            except Exception as e:
                failed.append((tk, f"{type(e).__name__}: {e}"))

    print(f"\nrebuilt={len(rebuilt)} already_current={len(current)} failed={len(failed)}")
    for tk, d, c, label in rebuilt:
        print(f"  ✓ {tk}: last_bar {d} C={c} ({label})")
    for tk, why in failed:
        print(f"  ✗ {tk}: {why}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
