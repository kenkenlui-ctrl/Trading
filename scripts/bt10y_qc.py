"""bt10y_qc.py — data-quality gate for the 10-year bar store.

WHY THIS FILE EXISTS
    portfolio_bench.py tried to build an equal-weight benchmark of the same 201
    names and printed +10^70%. That is not an edge, it is one broken bar:
    8766_T jumps +73.5% on 2018-09-26 and never comes back. The store is
    UNADJUSTED for splits, so every 10-year number built on it is contaminated
    until we know how many.

    This classifies each discontinuity instead of guessing:
      level_shift  -- price jumps >=THRESH and STAYS there (median of the next
                      60 bars is still >=80% away from the pre-jump level).
                      A real news move does not hold a permanent 70% rebase.
      spike        -- jumps >=THRESH but mean-reverts. Real. Keep.
    Fail-closed consumers should exclude windows containing a level_shift.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BARS = Path("/Users/kenken/dev/dsa-hk/data/bt10y")
THRESH = 0.30
LOOKAHEAD = 60
PERSIST = 0.80


def classify(b: pd.DataFrame) -> list[dict]:
    c = b.close.to_numpy(float)
    r = c[1:] / c[:-1] - 1
    hits = np.where(np.abs(r) >= THRESH)[0] + 1
    out = []
    for i in hits:
        pre = c[i - 1]
        fwd = c[i:i + LOOKAHEAD]
        if len(fwd) < 20:
            continue
        med = float(np.median(fwd))
        kind = "level_shift" if abs(med / pre - 1) >= PERSIST else "spike"
        out.append({"date": b.index[i].date().isoformat(), "kind": kind,
                    "pre": pre, "post": c[i], "move%": (c[i] / pre - 1) * 100,
                    "fwd_median": med})
    return out


def main() -> int:
    rows, report = [], []
    for p in sorted(BARS.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(d, list) or len(d) < 300:
            continue
        df = pd.DataFrame(d)
        if not {"date", "open", "high", "low", "close"} <= set(df.columns):
            continue
        df["date"] = pd.to_datetime(df["date"])
        b = df.dropna(subset=["open", "high", "low", "close"]).sort_values("date").set_index("date")
        ev = classify(b)
        s = p.stem
        is_jp = s.endswith("_T")
        is_us = (not is_jp) and ("_" not in s) and not s.startswith(("HK_", "JP_"))
        mkt = "jp200" if is_jp else ("us200" if is_us else "other")
        rows.append({"sym": s, "mkt": mkt,
                     "level_shift": sum(e["kind"] == "level_shift" for e in ev),
                     "spike": sum(e["kind"] == "spike" for e in ev)})
        for e in ev:
            report.append({"sym": s, "mkt": mkt, **e})

    t = pd.DataFrame(rows)
    rep = pd.DataFrame(report)
    Path("/Users/kenken/dev/dsa-hk/data/bt10y_qc.json").write_text(
        json.dumps([{k: (v.date().isoformat() if hasattr(v, "date") else v)
                     for k, v in r.items()} for r in report], indent=1))

    print(f"files scanned: {len(t)}")
    for mkt in ("us200", "jp200", "other"):
        g = t[t.mkt == mkt]
        if not len(g):
            continue
        bad = g[g.level_shift > 0]
        print(f"  {mkt:6} files={len(g):4}  with level_shift={len(bad):4}"
              f"  total level_shift bars={int(g.level_shift.sum()):3}"
              f"  spike bars={int(g.spike.sum()):3}")
        if len(bad):
            print("        " + ", ".join(f"{r.sym}({r.level_shift})" for r in bad.itertuples()))
    if len(rep):
        print("\nlevel_shift events:")
        print(rep[rep.kind == "level_shift"].to_string(index=False))
        print("\nspike events (kept, real moves):")
        print(rep[rep.kind == "spike"].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
