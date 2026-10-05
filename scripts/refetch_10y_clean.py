#!/usr/bin/env python3
"""refetch_10y_clean.py — rebuild data/bt10y2/ one symbol at a time.

WHY
    bt10y_qc.py found isolated price discontinuities in the 10-year store, and
    refetch_probe.py showed 8766_T differs from a fresh single-symbol download
    by 43,781%. fetch_10y.py pulled symbols 25 at a time with auto_adjust=True
    and group_by="ticker"; yfinance's multi-symbol auto-adjust path is known to
    misalign adjustment factors, which produces exactly this shape of damage —
    one or two bars at a wrong price scale, then back to normal.

    The old directory is left untouched so the previously published numbers stay
    reproducible. Everything downstream should point at bt10y2.

    Note this does NOT fix glitches that are in Yahoo's own response (3133.T
    really does print +101.2% on 2017-01-30 and -48.2% the next day). Those are
    masked, not repaired, by the glitch mask in this file.

Usage:
    python3 scripts/refetch_10y_clean.py            # resumable
    python3 scripts/refetch_10y_clean.py --report   # just diff old vs new
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
OLD = REPO / "data" / "bt10y"
NEW = REPO / "data" / "bt10y2"
NEW.mkdir(parents=True, exist_ok=True)
REPORT = REPO / "data" / "bt10y2_diff.csv"
START, END = "2016-09-01", "2026-10-02"


def to_safe(sym: str) -> str:
    return sym.replace(".", "_").replace("^", "IDX_").replace("/", "_")


def as_series(obj) -> pd.DataFrame | None:
    if obj is None or getattr(obj, "empty", True):
        return None
    # yfinance returns a (Price, Ticker) MultiIndex even for ONE symbol, so
    # `"Close" in obj.columns` is False and a naive select silently returns None
    # for every symbol. Flatten the ticker level first.
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


def load_cache(p: Path) -> pd.DataFrame | None:
    try:
        rows = json.loads(p.read_text())
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) < 200:
        return None
    df = pd.DataFrame(rows)
    if not {"date", "open", "high", "low", "close"} <= set(df.columns):
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.dropna(subset=["open", "high", "low", "close"]).sort_values("date").set_index("date")


def compare(old: pd.DataFrame | None, new: pd.DataFrame | None) -> dict:
    if old is None or new is None:
        return {"rows_old": 0 if old is None else len(old),
                "rows_new": 0 if new is None else len(new), "max_rel_diff": np.nan,
                "worst_date": "", "verdict": "missing"}
    j = old.index.intersection(new.index)
    if not len(j):
        return {"rows_old": len(old), "rows_new": len(new), "max_rel_diff": np.nan,
                "worst_date": "", "verdict": "no overlap"}
    rel = ((old.reindex(j)["close"] - new.reindex(j)["close"]).abs()
           / old.reindex(j)["close"].replace(0, np.nan)).dropna()
    mx = float(rel.max()) if len(rel) else 0.0
    wd = str(rel.idxmax().date()) if len(rel) and rel.max() > 0 else ""
    return {"rows_old": len(old), "rows_new": len(new), "max_rel_diff": mx,
            "worst_date": wd,
            "verdict": "CORRUPT" if mx > 0.01 else ("drift" if mx > 1e-9 else "identical")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="diff only, do not download")
    ap.add_argument("--pause", type=float, default=0.45)
    ap.add_argument("--retries", type=int, default=3)
    a = ap.parse_args()

    todo = []
    for p in sorted(OLD.glob("*.json")):
        safe = p.stem
        if safe.startswith("HK_"):
            continue                       # HK has no 10y bars in this store
        if (NEW / f"{safe}.json").exists() and not a.report:
            continue
        sym = safe.replace("IDX_", "^").replace("_T", ".T").replace("_", ".")
        todo.append((sym, safe))
    print(f"symbols to fetch: {len(todo)}", flush=True)

    if not a.report:
        import yfinance as yf
        t0, done = time.time(), 0
        for sym, safe in todo:
            got = None
            for t in range(a.retries):
                try:
                    d = yf.download(sym, start=START, end=END, progress=False,
                                    auto_adjust=True, threads=False)
                    got = as_series(d)
                    if got is not None:
                        break
                except Exception:
                    pass
                time.sleep(2 * (t + 1))
            if got is not None:
                (NEW / f"{safe}.json").write_text(json.dumps(serialise(got)))
            done += 1
            if done % 20 == 0:
                print(f"  [{done}/{len(todo)}] {time.time()-t0:.0f}s", flush=True)
            time.sleep(a.pause)
        print(f"fetched {done}, wrote {len(list(NEW.glob('*.json')))} files", flush=True)

    rows = []
    for p in sorted(OLD.glob("*.json")):
        safe = p.stem
        c = compare(load_cache(OLD / f"{safe}.json"), load_cache(NEW / f"{safe}.json"))
        c["sym"] = safe
        rows.append(c)
    df = pd.DataFrame(rows)[["sym", "rows_old", "rows_new", "max_rel_diff", "worst_date", "verdict"]]
    df.to_csv(REPORT, index=False)
    print(f"\n{len(df)} symbols compared -> {REPORT}")
    print(df.verdict.value_counts().to_string())
    bad = df[df.verdict == "CORRUPT"].sort_values("max_rel_diff", ascending=False)
    if len(bad):
        print(f"\nCORRUPT (>1% divergence, cache was wrong):")
        print(bad.to_string(index=False))
    drift = df[df.verdict == "drift"]
    if len(drift):
        print(f"\ndrift (tiny, likely rounding): {len(drift)} symbols, "
              f"max {drift.max_rel_diff.max():.2e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
