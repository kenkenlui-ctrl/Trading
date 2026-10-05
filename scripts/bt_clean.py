"""bt_clean.py — fail-closed data gate for the 10-year bar store.

WHY
    A backtest number is only as trustworthy as the bars under it. The 10-year
    store turned out to contain isolated bars printed at the wrong price scale
    (3133.T at exactly 2x for one day; 1306.T at 1/10 for two days with 10x
    volume). One such bar inside a 10-day holding window is worth more than the
    whole edge.

HOW A GLITCH IS TOLD FROM A REAL MOVE
    A real gap (APP +46% on earnings, MRNA +177%) is followed by a market that
    agrees with it: the next 20 sessions trade at the new level. A data glitch
    is contradicted on BOTH sides — the previous 20 sessions and the next 20
    sessions agree with each other, and the bar disagrees with both.

    glitch  <=>  |log(day) - log(med20_before)| > LEVEL_TOL
              and  |log(day) - log(med20_after )| > LEVEL_TOL
              and  the two medians agree with each other (< AGREEMENT)

    A 20-session lookahead is required, so the last 20 sessions of a series are
    never classified. That is intentional: we do not get to call the last bar a
    glitch just because nothing followed it.

CONSUMER RULE
    Any trade whose [entry - LOOKBACK, exit] window touches a masked bar is
    dropped. LOOKBACK covers the feature warm-up, because S1 / ma200 / atr are
    computed from shifted closes and a bad bar poisons them for 200 sessions.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
CLEAN = REPO / "data" / "bt10y2"
LEGACY = REPO / "data" / "bt10y"
LOOK = 20
LEVEL_TOL = math.log(1.20)     # 20% disagreement with the neighbourhood
AGREEMENT = math.log(1.10)     # the two neighbourhoods must agree within 10%
FEATURE_LOOKBACK = 210         # ma200 needs 200 shifted closes
GLITCH_EPS = 0.25              # only look at moves big enough to matter


def bars_dir() -> Path:
    return CLEAN if CLEAN.exists() and any(CLEAN.glob("*.json")) else LEGACY


def load(p: Path) -> pd.DataFrame | None:
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


def glitch_dates(b: pd.DataFrame) -> list[pd.Timestamp]:
    c = b.close.to_numpy(float)
    n = len(c)
    lc = np.log(np.maximum(c, 1e-9))
    out = []
    for i in range(LOOK, n - LOOK):
        r = c[i] / c[i - 1] - 1
        if abs(r) < GLITCH_EPS:
            continue
        pre = float(np.median(lc[i - LOOK:i]))
        post = float(np.median(lc[i + 1:i + 1 + LOOK]))
        if abs(pre - post) >= AGREEMENT:
            continue                     # neighbourhoods disagree: real regime change
        if abs(lc[i] - pre) > LEVEL_TOL and abs(lc[i] - post) > LEVEL_TOL:
            out.append(b.index[i])
    return out


def build_mask(d: Path | None = None) -> dict[str, set]:
    d = d or bars_dir()
    mask: dict[str, set] = {}
    for p in sorted(d.glob("*.json")):
        b = load(p)
        if b is None:
            continue
        g = glitch_dates(b)
        if g:
            mask[p.stem] = set(g)
    return mask


def _load_mask_cache() -> dict[str, set]:
    cp = REPO / "data" / "bt10y_glitch_mask.json"
    src = bars_dir()
    if cp.exists():
        try:
            blob = json.loads(cp.read_text())
            if blob.get("_source") == src.name:
                return {k: set(v) for k, v in blob["mask"].items()}
        except Exception:
            pass
    m = build_mask(src)
    cp.write_text(json.dumps({"_source": src.name,
                              "mask": {k: sorted(str(d.date()) for d in v) for k, v in m.items()}},
                             indent=1))
    return m


def filter_candidates(cands: list[dict], mask: dict[str, set] | None = None,
                      lookback: int = FEATURE_LOOKBACK) -> tuple[list[dict], int]:
    """Drop any candidate whose [entry - lookback, exit] window touches a glitch."""
    mask = mask if mask is not None else _load_mask_cache()
    keep, dropped = [], 0
    for c in cands:
        g = mask.get(c["sym"])
        if g:
            lo = (c["entry_date"] - pd.Timedelta(days=int(lookback * 1.45))).date()
            hi = c["exit_date"].date()
            if any(lo <= d <= hi for d in g):
                dropped += 1
                continue
        keep.append(c)
    return keep, dropped


if __name__ == "__main__":
    src = bars_dir()
    m = build_mask(src)
    tot = sum(len(v) for v in m.values())
    print(f"source: {src}")
    print(f"symbols with >=1 masked bar: {len(m)} / {len(list(src.glob('*.json')))}")
    print(f"total masked bars: {tot}")
    for k, v in sorted(m.items(), key=lambda kv: -len(kv[1]))[:25]:
        ds = sorted(v)
        print(f"  {k:10} {len(v):2}  {ds[0].date()} .. {ds[-1].date()}")
