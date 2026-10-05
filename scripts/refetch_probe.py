"""refetch_probe.py — is the glitch in the STORE or in the SOURCE?

Re-downloads the symbols bt10y_qc flagged, one at a time, and compares the
fresh series against the cached one. If the fresh series is clean and the cache
is not, the corruption happened in fetch_10y.py's batch download, and the fix is
a re-fetch. If the fresh series has the same glitch, the source itself is bad
and no re-fetch helps.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
BARS = Path("/Users/kenken/dev/dsa-hk/data/bt10y")
PROBE = ["3133.T", "1306.T", "3070.T", "8766.T", "3083.T", "APP", "MRNA", "CVNA"]


def as_series(obj) -> pd.Series:
    s = obj["Close"] if "Close" in obj else obj
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = pd.Series(np.asarray(s).ravel(), index=pd.to_datetime(s.index))
    return s[~s.index.duplicated(keep="last")].dropna().sort_index()


def bad_days(s: pd.Series, thresh=0.25) -> pd.Series:
    r = s.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    return r[r.abs() > thresh]


def main() -> int:
    import yfinance as yf
    for sym in PROBE:
        safe = sym.replace(".", "_").replace("^", "IDX_")
        cache = BARS / f"{safe}.json"
        print(f"\n=== {sym} ===")
        try:
            d = yf.download(sym, start="2016-09-01", end="2026-10-02",
                            progress=False, auto_adjust=True, threads=False)
            fresh = as_series(d) if d is not None and not d.empty else pd.Series(dtype=float)
        except Exception as e:
            print(f"  refetch failed: {e}")
            fresh = pd.Series(dtype=float)
        if not len(fresh):
            print("  refetch: EMPTY (yfinance no longer serves it)")
            fresh = None
        else:
            print(f"  fresh  n={len(fresh)}  {fresh.index[0].date()}..{fresh.index[-1].date()}"
                  f"  |move|>25% days={len(bad_days(fresh))}")
            for i, v in bad_days(fresh).head(4).items():
                print(f"      {i.date()}  {v:+.1%}")
        if cache.exists():
            rows = json.loads(cache.read_text())
            cs = pd.Series([r["close"] for r in rows],
                           index=pd.to_datetime([r["date"] for r in rows])).sort_index()
            print(f"  cache  n={len(cs)}  {cs.index[0].date()}..{cs.index[-1].date()}"
                  f"  |move|>25% days={len(bad_days(cs))}")
            for i, v in bad_days(cs).head(4).items():
                print(f"      {i.date()}  {v:+.1%}")
            if fresh is not None and len(fresh) == len(cs):
                j = cs.index.intersection(fresh.index)
                diff = (cs.reindex(j) - fresh.reindex(j)).abs() / cs.reindex(j)
                print(f"  max relative divergence: {diff.max():.4%} on {diff.idxmax().date()}")
        else:
            print("  cache: missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
