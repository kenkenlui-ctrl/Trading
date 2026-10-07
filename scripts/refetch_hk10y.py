#!/usr/bin/env python3
"""refetch_hk10y.py — build a 10-year HK bar store so HK can finally be measured.

WHY THIS EXISTS
    build_v2_signals.py carries the comment

        INCLUDE_HK = False   # study found HK is ~0 edge after 0.45% round-trip

    That comment is the reason the homepage tells every Hong Kong visitor that
    the engine does not cover their market. It is also unsourced: no HK 10-year
    bar store has ever existed on this project.

        refetch_10y_clean.py line 122:  continue   # HK has no 10y bars in this store
        $ ls data/bt10y2/ | grep -c HK_  ->  0

    So "HK is ~0 edge" was never measured on this project's own machinery — it
    is a remembered number. Kenneth flagged on 2026-10-07 that his actual HK
    round-trip is 0.25% (Futu commission-free, HK$100k/trade), which is exactly
    what core.FEES["hk200"] already says. The 0.45% in that comment is the
    *realistic* scenario (fees + slippage), not the cost the site publishes.

    A published claim that decides whether a market is served must be measured,
    not remembered. This script produces the store that lets it be.

METHOD — deliberately identical to refetch_10y_clean.py
    One symbol at a time (yfinance's multi-symbol auto_adjust path is what
    corrupted 8 files in the original store), same date window, same
    serialisation, same >=200-bar minimum. Nothing here is a new pipeline; it
    is the same one pointed at a different market, so HK numbers are comparable
    to the US/JP numbers already published.

    The existing glitch mask (data/bt10y_glitch_mask.json) does NOT cover HK.
    bt_clean.filter_candidates() is market-agnostic, so the gate still runs,
    but it will mask nothing until HK symbols are added to that file. Treat a
    first HK run as unverified until the mask is regenerated.

Usage:
    python3 scripts/refetch_hk10y.py            # resumable, skips done files
    python3 scripts/refetch_hk10y.py --report   # QC only, no download
"""
from __future__ import annotations

import argparse
import json
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

REPO = Path("/Users/kenken/dev/dsa-hk")
NEW = REPO / "data" / "bt10y2_hk"
NEW.mkdir(parents=True, exist_ok=True)
UNIVERSE = REPO / "hk_universe_200.json"
# Same window as the US/JP store so the two are directly comparable.
START, END = "2016-09-01", "2026-10-06"


def to_safe(sym: str) -> str:
    return sym.replace(".", "_").replace("^", "IDX_").replace("/", "_")


def yf_symbol(sym: str) -> list[str]:
    """HK code forms to try, most likely first.

    yfinance does NOT accept the 5-digit padded HK form. hk_universe_200.json
    is uniformly 5-digit (00700.HK, 09988.HK, 07709.HK) because that is what
    the site uses for URLs, but the vendor wants 4 digits:

        09988.HK -> 0 rows        9988.HK -> 678 rows
        09618.HK -> 0 rows        9618.HK -> 678 rows
        00700.HK -> 0 rows        0700.HK -> 678 rows

    Zero rows is silent — yfinance returns an empty frame, not an error, so a
    naive loop marks all 200 symbols "failed" and reports nothing fetched.
    The file NAME keeps the 5-digit form: that is the site's public identity
    and must not change.
    """
    base, _, suf = sym.partition(".")
    # HK codes are 4-digit zero-padded: 00700 -> 0700, 00148 -> 0148, 09988 -> 9988.
    # Taking the LAST 4 digits handles both directions in one step; stripping
    # every leading zero would turn 00700 into 700, which is a different (invalid)
    # symbol and would silently waste two requests per name.
    short = base[-4:] if len(base) > 4 else base
    cands = []
    if short != base:
        cands.append(f"{short}.{suf}" if suf else short)
    if sym not in cands:
        cands.append(sym)
    return cands


def as_series(obj) -> pd.DataFrame | None:
    if obj is None or getattr(obj, "empty", True):
        return None
    d = obj
    if getattr(d.columns, "nlevels", 1) > 1:
        d = d.copy()
        d.columns = [c[0] for c in d.columns]
    cols = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in d.columns for c in cols[:4]):
        return None
    d = d[cols].copy()
    d.columns = ["open", "high", "low", "close", "volume"]
    d.index = pd.to_datetime(d.index)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d = d.dropna(subset=["open", "high", "low", "close"])
    d = d[(d[["open", "high", "low", "close"]] > 0).all(axis=1)]
    return d if len(d) >= 200 else None


def serialise(d: pd.DataFrame) -> list[dict]:
    out = []
    for dt, r in d.iterrows():
        out.append({"date": pd.Timestamp(dt).strftime("%Y-%m-%d"),
                    "open": float(r["open"]), "high": float(r["high"]),
                    "low": float(r["low"]), "close": float(r["close"]),
                    "volume": float(r["volume"]) if not pd.isna(r["volume"]) else 0.0})
    return out


def has_enough(p: Path) -> bool:
    try:
        rows = json.loads(p.read_text())
        return isinstance(rows, list) and len(rows) >= 200
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="QC only, no download")
    ap.add_argument("--pause", type=float, default=0.45)
    ap.add_argument("--retries", type=int, default=3)
    a = ap.parse_args()

    import yfinance as yf

    uni = json.load(open(UNIVERSE))
    print(f"HK universe: {len(uni)} names -> {NEW}")

    if a.report:
        rows, short = [], []
        for t in uni:
            p = NEW / f"{to_safe(t)}.json"
            if not p.exists():
                short.append((t, 0, "missing"))
                continue
            d = pd.DataFrame(json.loads(p.read_text()))
            d["date"] = pd.to_datetime(d["date"])
            rows.append((t, len(d), str(d["date"].min().date()), str(d["date"].max().date())))
            if len(d) < 500:
                short.append((t, len(d), "short history"))
        print(f"stored: {len(rows)}/{len(uni)}")
        if rows:
            lens = [r[1] for r in rows]
            print(f"bars: min={min(lens)} median={int(np.median(lens))} max={max(lens)}")
            print(f"window: {min(r[2] for r in rows)} -> {max(r[3] for r in rows)}")
        if short:
            print(f"\nweak/short ({len(short)}):")
            for t, n, why in short[:20]:
                print(f"  {t:12} {n:>6} bars  {why}")
        return 0

    ok = miss = 0
    for i, t in enumerate(uni, 1):
        safe = to_safe(t)
        p = NEW / f"{safe}.json"
        if has_enough(p):
            ok += 1
            continue
        got = False
        for attempt in range(a.retries):
            try:
                d = None
                for cand in yf_symbol(t):
                    d = as_series(yf.Ticker(cand).history(
                        start=START, end=END, auto_adjust=False))
                    if d is not None:
                        break
                if d is None:
                    time.sleep(a.pause * (attempt + 1))
                    continue
                p.write_text(json.dumps(serialise(d)))
                ok += 1
                got = True
                break
            except Exception:
                time.sleep(a.pause * (attempt + 1))
        if not got:
            miss += 1
        if i % 25 == 0:
            print(f"  {i}/{len(uni)}  ok={ok} miss={miss}", flush=True)
        time.sleep(a.pause)

    print(f"\nDONE  stored={ok}  failed={miss}  ->  {NEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())