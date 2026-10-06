"""control_test.py — Is v2 a timing edge, or just trend exposure?

THE QUESTION THIS ANSWERS
    v2 buys uptrending large caps, entering on a dip to S1 and holding 10 days.
    The claim on the methodology page is therefore at risk of being "hold
    uptrending stocks", dressed as a timing system. The only way to know is a
    control that removes the timing and keeps everything else.

CONTROL DESIGN (the part that is easy to get wrong)
    The control must differ from v2 in EXACTLY ONE thing: the entry trigger.
      v2      : limit buy at S1, requires open > S1 AND close >= S1 (close-hold)
      control : buy at the OPEN of any qualifying day, no S1 condition at all
    Everything else is shared: same universe, same per-symbol features, same
    3xATR stop, same 10-session time exit, same per-market costs, same
    date-clustered t-statistic.

    A control with a different universe, or with the market filter applied
    differently, stratifies two different populations and the comparison
    means nothing. The peer broadcast that prompted this made exactly that
    point — "if the placebo uses a static ticker list while the live signal
    uses a point-in-time universe, you are stratifying two different
    populations however carefully you stratify".

    Both arms also run twice: train (2016-2022) and held-out test (2023-2026).
    A control that beats v2 on one side and loses on the other is a window
    artefact, not an answer.

USAGE
    python3 scripts/control_test.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
from bt_clean import bars_dir, _load_mask_cache  # noqa: E402
from core import features, _simulate, cost_rt  # noqa: E402

BARS = bars_dir()   # data/bt10y2 when present: the old cache had 8 corrupt files
OUT = REPO / "data" / "control_test.csv"
SPLIT = pd.Timestamp("2023-01-01")
HOLD = 10
# 2026-10-05: these are FILENAMES in data/bt10y, not Yahoo symbols. The JP
# ETF file is 1306_T.json, so looking for "1306.T.json" silently returned None
# and the whole JP arm was skipped without a word.
MARKETS = {"us200": ("SPY", "IDX_GSPC"),
           "jp200": ("1306_T", "IDX_N225")}


def load(path: Path) -> pd.DataFrame | None:
    try:
        rows = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) < 300:
        return None
    df = pd.DataFrame(rows)
    if not {"date", "open", "high", "low", "close"} <= set(df.columns):
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("date")
    return df.set_index("date")


def market_up_series(mkt: str) -> pd.Series | None:
    """Per-date market filter, aligned to the symbol's calendar.

    Returns a boolean Series indexed by date. Using the SAME index for both
    arms matters: a control that sees more up-days than the signal is not a
    control, it is a looser filter.
    """
    for sym in MARKETS[mkt]:
        p = BARS / f"{sym}.json"
        if not p.exists():
            continue
        b = load(p)
        if b is None:
            continue
        f = features(b)
        return f.prev_close > f.ma200
    return None


def arm_v2(df: pd.DataFrame) -> list[tuple]:
    """Shipped rule: limit at S1, open > S1, close holds S1, 3xATR stop, 10d."""
    f = features(df)
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a, s1 = f.atr_pct.to_numpy(), f.S1.to_numpy()
    ma200, slope, boxw = f.ma200.to_numpy(), f.ma200_slope.to_numpy(), f.box_w.to_numpy()
    ok = (f.prev_close.to_numpy() > ma200) & (slope > 0) & (a < 0.06) & (boxw < 0.35)
    out = []
    for i in np.where(ok)[0]:
        if np.isnan(s1[i]) or np.isnan(a[i]) or a[i] <= 0:
            continue
        if not (O[i] > s1[i] and L[i] <= s1[i]):
            continue
        if C[i] < s1[i]:
            continue                                   # close-hold
        stop = s1[i] * (1 - 3 * a[i])
        if not (0 < stop < s1[i]):
            continue
        r = _simulate(1, i, s1[i], stop, 1e12, O, H, L, C, HOLD, True)
        if r is None:
            continue
        j, xp, why = r
        out.append((df.index[i], xp / s1[i] - 1, why))
    return out


def arm_control(df: pd.DataFrame, up: pd.Series) -> list[tuple]:
    """Control: buy at the OPEN of any qualifying day. No S1, no dip, no timing.

    Same universe, same uptrend filter, same market filter (reindexed onto the
    symbol's own calendar so both arms see identical up-days), same 3xATR
    stop, same 10-session exit, same costs. The ONLY difference from v2 is
    that there is no S1 level to wait for and no close-hold requirement.
    """
    f = features(df)
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = f.atr_pct.to_numpy()
    ma200, slope, boxw = f.ma200.to_numpy(), f.ma200_slope.to_numpy(), f.box_w.to_numpy()
    ok = (f.prev_close.to_numpy() > ma200) & (slope > 0) & (a < 0.06) & (boxw < 0.35)
    idx = df.index
    upa = up.reindex(idx).fillna(False).to_numpy(bool)
    out: list[tuple] = []
    for i in np.where(ok)[0]:
        if np.isnan(a[i]) or a[i] <= 0 or not upa[i]:
            continue
        stop = O[i] * (1 - 3 * a[i])
        if stop <= 0:
            continue
        r = _simulate(1, i, O[i], stop, 1e12, O, H, L, C, HOLD, False)
        if r is None:
            continue
        _, xp, why = r
        out.append((idx[i], xp / O[i] - 1, why))
    return out


def stats(df: pd.DataFrame) -> dict:
    if df is None or len(df) == 0:
        return {"n": 0, "win%": 0.0, "avg%": 0.0, "pf": 0.0, "t": 0.0}
    net = df.net.to_numpy(float)
    w, l = net[net > 0], net[net <= 0]
    by_date = df.groupby("date").net.mean()
    t = 0.0
    if len(by_date) > 2 and by_date.std(ddof=1) > 0:
        t = by_date.mean() / (by_date.std(ddof=1) / np.sqrt(len(by_date)))
    pf = (w.sum() / abs(l.sum())) if len(l) and l.sum() != 0 else 0.0
    return {"n": len(net), "win%": round(float((net > 0).mean() * 100), 1),
            "avg%": round(float(net.mean() * 100), 3), "pf": round(float(pf), 2),
            "t": round(float(t), 2)}


def main() -> int:
    rows = []
    for mkt in MARKETS:
        up = market_up_series(mkt)
        if up is None:
            print(f"  {mkt}: no index bars, skipping")
            continue
        acc: dict[str, list] = {"v2": [], "control": []}
        for p in sorted(BARS.glob("*.json")):
            s = p.stem
            is_jp = s.endswith("_T")
            is_us = (not is_jp) and ("_" not in s) and not s.startswith(("HK_", "JP_"))
            if mkt == "jp200" and not is_jp:
                continue
            if mkt == "us200" and not is_us:
                continue
            b = load(p)
            if b is None:
                continue
            upa = up.reindex(b.index)
            for label, fn in (("v2", lambda d: arm_v2(d)),
                              ("control", lambda d: arm_control(d, upa))):
                try:
                    got = fn(b)
                except Exception:
                    got = []
                for date, gross, why in got or []:
                    acc[label].append((mkt, pd.Timestamp(date), gross, why, s))

        for label in ("v2", "control"):
            if not acc[label]:
                continue
            d = pd.DataFrame(acc[label], columns=["mkt", "date", "gross", "reason", "sym"])
            # fail-closed: a trade whose [entry-304d, exit] window touches a data
            # glitch is dropped, same rule as bt_clean.filter_candidates
            mask = _load_mask_cache()
            if mask:
                keep = []
                for r in d.itertuples(index=False):
                    g = mask.get(r.sym)
                    if g and any(r.date - pd.Timedelta(days=304) <= x <= r.date + pd.Timedelta(days=14)
                                 for x in g):
                        continue
                    keep.append(r)
                d = pd.DataFrame(keep, columns=d.columns)
            d["net"] = d.gross - cost_rt(mkt, "site", d.reason.eq("stop").to_numpy())
            tr, te = d[d.date < SPLIT], d[d.date >= SPLIT]
            s_tr, s_te = stats(tr), stats(te)
            rows.append({"market": mkt, "arm": label,
                         "train_n": s_tr["n"], "train_avg%": s_tr["avg%"], "train_t": s_tr["t"],
                         "test_n": s_te["n"], "test_avg%": s_te["avg%"], "test_t": s_te["t"]})
            print(f"\n{mkt}  {label:8}")
            print(f"  train  n={s_tr['n']:>6}  avg={s_tr['avg%']:+.3f}%  "
                  f"win={s_tr['win%']:.1f}%  t={s_tr['t']:+.2f}")
            print(f"  test   n={s_te['n']:>6}  avg={s_te['avg%']:+.3f}%  "
                  f"win={s_te['win%']:.1f}%  t={s_te['t']:+.2f}")

    print("\n" + "=" * 74)
    print("TIMING EDGE?  (v2 minus control, same universe/filters/stops/costs)")
    for mkt in MARKETS:
        v = next((r for r in rows if r["market"] == mkt and r["arm"] == "v2"), None)
        c = next((r for r in rows if r["market"] == mkt and r["arm"] == "control"), None)
        if not (v and c):
            continue
        for w in ("train", "test"):
            dv = v[f"{w}_avg%"] - c[f"{w}_avg%"]
            if abs(dv) < 0.02:
                verdict = "NO EDGE — v2 matches the control"
            elif dv < 0:
                verdict = "control BEATS v2"
            else:
                verdict = "v2 ahead, but see magnitude"
            print(f"  {mkt:6} {w:5}  v2 {v[f'{w}_avg%']:+.3f}%  vs  "
                  f"control {c[f'{w}_avg%']:+.3f}%   delta {dv:+.3f}pp   => {verdict}")

    if rows:
        pd.DataFrame(rows).to_csv(OUT, index=False)
        print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
