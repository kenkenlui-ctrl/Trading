#!/usr/bin/env python3
"""
hk_momentum_engine.py — HK cross-sectional 12-month momentum, built to the same standard as v2.

Follows docs/vol-regime-multi-market-spec.md's guardrail philosophy, applied to
the one strategy family that survived screening in the 2026-10-11 study.

    python3 scripts/hk_momentum_engine.py

PRE-REGISTERED. These parameters were frozen before any of this engine's own
numbers existed, and they are exactly the configuration that screened positive
in the exploratory pass (252-day lookback, top 20%, monthly). There is no grid
search and no threshold tuning. The screening pass compared 4 descriptors x 3
rebalance frequencies = 12 combinations, and picked one; that selection is real
and is recorded in the output under `selection_disclosure`, because a rule
chosen from 12 candidates enters any test carrying the weight of 12 trials.

WHY A BASKET, NOT walk()
    portfolio_test.walk sizes positions off the distance between entry and stop
    and enforces 10 concurrent slots. Momentum has no stop. Forcing it through
    that engine would mean inventing a stop it never had. So the portfolio here
    is a monthly-rebalanced equal-weight basket, and every GUARD from the v2 rig
    is reimplemented on top of it. The portfolio mechanics differ; the evidence
    requirements do not.

THE LIMITATION THAT CANNOT BE FIXED IN CODE
    The universe is 181 CURRENT HK constituents. Names that were delisted,
    privatised or acquired since 2016 are absent, and their absence inflates
    every number below. This is not a caveat that a longer run will remove; it
    is a data-provenance gap that would need point-in-time index membership to
    close. It is reported as a first-class field, not a footnote.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/kenken/dev/dsa-hk")
sys.path.insert(0, str(REPO / "scripts"))

from bt_clean import build_mask, load                        # noqa: E402

DATA = REPO / "data"
OUT_JSON = DATA / "hk_momentum_summary.json"
OUT_CSV = DATA / "hk_momentum_periods.csv"
OUT_PDF = DATA / "hk_momentum_holdings.csv"

# ---------------------------------------------------------------------------
# Pre-registered parameters. Frozen. Changing any of these invalidates the
# pre-registration claim and turns this into a search.
# ---------------------------------------------------------------------------
PARAMS = {
    "lookback_bars": 252,        # 12-month trailing return, no skip month
    "top_fraction": 0.20,        # hold the strongest quintile
    "rebalance_every_bars": 21,  # monthly
    "min_history_bars": 253,     # otherwise the lookback is undefined
    "market": "hk200",
    "hold": "until next rebalance (no stop, no target)",
    "weighting": "equal weight across constituents",
}
MKT = PARAMS["market"]
COST_RT = 0.0025                # repo standard hk200 realistic round trip

EVENT_WINDOWS = [("2020-02-01", "2020-05-01"),      # Mar 2020
                 ("2021-11-01", "2023-03-01")]      # 2022 bear / yen unwind


def load_panel() -> tuple[pd.DataFrame, dict]:
    """Close-price panel for the HK universe, plus the data-quality record."""
    bars_dir = DATA / "bt10y2_hk"
    mask = build_mask(bars_dir)
    masked = {k for k in mask if k.endswith("_HK")}
    series, spans = [], []
    for p in sorted(bars_dir.glob("*.json")):
        b = load(p)
        if b is None or b.empty or p.stem in masked:
            continue
        series.append(b.close.rename(p.stem))
        spans.append((b.index.min(), b.index.max()))
    P = pd.concat(series, axis=1).sort_index()
    meta = {
        "universe_n": int(P.shape[1]),
        "bars_n": int(P.shape[0]),
        "window": [str(spans and min(s[0] for s in spans).date()),
                   str(max(s[1] for s in spans).date())],
        "glitch_masked_tickers_excluded": sorted(masked),
        "pre_ipo_nan_cells": int(P.isna().sum().sum()),
    }
    return P, meta


def rebalance_dates(P: pd.DataFrame) -> list[int]:
    start = PARAMS["lookback_bars"]
    step = PARAMS["rebalance_every_bars"]
    return list(range(start, len(P) - step, step))


def score(i: int, P: pd.DataFrame) -> pd.Series:
    """Trailing return as of bar i. Only data at or before i is used."""
    lb = PARAMS["lookback_bars"]
    return (P.iloc[i] / P.iloc[i - lb] - 1).replace([np.inf, -np.inf], np.nan).dropna()


def run(pd_offset: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (period_returns, holdings).

    pd_offset shifts every rebalance decision by N bars. That is the placebo:
    a rule whose edge lives in a specific calendar alignment should not survive
    being moved. pd_offset=0 is the real strategy.
    """
    P, _ = load_panel()
    step = PARAMS["rebalance_every_bars"]
    top_f = PARAMS["top_fraction"]
    dates, prev = rebalance_dates(P), None
    if pd_offset:
        dates = [d + pd_offset for d in dates
                 if 0 <= d + pd_offset < len(P) - step]
    rows, hold = [], []
    for i in dates:
        s = score(i, P)
        if len(s) < 40:
            continue
        k = max(1, int(round(len(s) * top_f)))
        top = s.sort_values(ascending=False).index[:k]
        fut = P.iloc[i + 1:i + 1 + step]
        px = P.iloc[i]
        # Every name bought and every name sold crosses the full round trip once.
        one_way = len(top) if prev is None else len(set(top) ^ set(prev))
        ret = float(fut[list(top)].div(px[list(top)], axis=1).mean(axis=1).mean()) - 1
        cost = COST_RT * one_way / max(len(top), 1)
        rows.append({"signal_date": str(P.index[i].date()),
                     "effective_date": str(P.index[i + 1].date()),
                     "n_held": int(len(top)), "n_traded": int(one_way),
                     "turnover_pct": round(one_way / len(top) * 100, 1),
                     "gross_ret_pct": round(ret * 100, 4),
                     "cost_pct": round(cost * 100, 4),
                     "net_ret_pct": round((ret - cost) * 100, 4)})
        for t in top:
            hold.append({"signal_date": str(P.index[i].date()),
                         "effective_date": str(P.index[i + 1].date()),
                         "sym": t, "score": float(s[t]),
                         "held_bars": step})
        prev = set(top)
    return pd.DataFrame(rows), pd.DataFrame(hold)


def stats(periods: pd.DataFrame) -> dict:
    r = periods["net_ret_pct"].to_numpy(float) / 100
    eq = np.cumprod(1 + r)
    dd = eq / np.maximum.accumulate(eq) - 1
    n = len(r)
    return {
        "n_periods": n,
        "annualised_pct": round(float(r.mean() * (252 / PARAMS["rebalance_every_bars"]) * 100), 2),
        "vol_pct": round(float(r.std(ddof=1) * np.sqrt(252 / PARAMS["rebalance_every_bars"]) * 100), 2),
        "sharpe_like": round(float(r.mean() / r.std(ddof=1) * np.sqrt(252 / PARAMS["rebalance_every_bars"])), 3),
        "hit_rate_pct": round(float((r > 0).mean() * 100), 1),
        "total_return_pct": round(float((eq[-1] - 1) * 100), 1),
        "max_dd_pct": round(float(dd.min() * 100), 2),
        "best_period_pct": round(float(r.max() * 100), 2),
        "worst_period_pct": round(float(r.min() * 100), 2),
        "avg_turnover_pct": round(float(periods["turnover_pct"].mean()), 1),
    }


def look_ahead_probe() -> dict:
    """The signal must be a function of bars at or before D, never D+1.

    Probe: inject a single implausible spike on the bar AFTER a rebalance date
    and confirm the basket chosen on D is unchanged — comparing the actual
    member set, not just its size. A count check would pass a strategy that
    swapped one name for another and still held the same number.
    """
    P, _ = load_panel()
    dates = rebalance_dates(P)
    probe_date = dates[len(dates) // 2]
    dkey = str(P.index[probe_date].date())

    before = _basket_on_panel(P, probe_date)
    P2 = P.copy()
    P2.iloc[probe_date + 1, :] = P2.iloc[probe_date + 1, :] * 1.5   # D+1 spike
    after = _basket_on_panel(P2, probe_date)

    return {"probe_signal_date": dkey,
            "probe_spike_injected_on": str(P.index[probe_date + 1].date()),
            "n_held_before": len(before), "n_held_after": len(after),
            "members_identical": bool(before == after),
            "symmetric_only_members_before": len(before - after),
            "passed": bool(before == after)}


def _basket_on_panel(P: pd.DataFrame, i: int) -> set:
    """The exact member set the rule would hold from bar i+1."""
    s = score(i, P)
    if len(s) < 40:
        return set()
    k = max(1, int(round(len(s) * PARAMS["top_fraction"])))
    return set(s.sort_values(ascending=False).index[:k])


def event_robustness(periods: pd.DataFrame) -> dict:
    d = pd.to_datetime(periods["effective_date"])
    mask = pd.Series(False, index=periods.index)
    for lo, hi in EVENT_WINDOWS:
        mask |= (d >= lo) & (d <= hi)
    kept = periods[~mask]
    per_year = 252 / PARAMS["rebalance_every_bars"]
    # net_ret_pct is already a percentage. Scaling it by 100 again is how this
    # first reported "1511%/yr", which is a rebalance-period mean wearing an
    # annualised label.
    ann = lambda f: round(float(f["net_ret_pct"].mean() * per_year), 2)
    return {
        "excluded_windows": EVENT_WINDOWS,
        "n_all": int(len(periods)), "n_without_events": int(len(kept)),
        "annualised_pct_all": ann(periods),
        "annualised_pct_no_events": ann(kept) if len(kept) else None,
        "hit_rate_pct_no_events": round(float((kept["net_ret_pct"] > 0).mean() * 100), 1)
        if len(kept) else None,
    }


def placebo_reading(real: dict, placebos: dict) -> dict:
    """How to read the placebo result, which differs from the v2 regime study.

    In the volatility study a placebo that matched the real result meant the
    finding was an artifact. Here the placebo shifts the REBALANCE DATE, and
    cross-sectional momentum is not a calendar effect — shifting it by a month
    or a quarter should leave the signal intact. So the evidence is not "did a
    placebo beat it" but "do all five variants land in the same range". A tight
    cluster means the result is insensitive to the alignment; a wide scatter
    would mean the real figure is one lucky draw.
    """
    vals = [real["annualised_pct"]] + [v["annualised_pct"] for v in placebos.values()]
    at_or_above = sum(1 for v in placebos.values()
                      if v["annualised_pct"] >= real["annualised_pct"])
    return {
        "reals_and_placebos_annualised_pct": sorted(vals),
        "spread_pp": round(max(vals) - min(vals), 2),
        "placebos_at_or_above_real": f"{at_or_above}/{len(placebos)}",
        "interpretation": (
            f"五個變體（真實 + 4 個位移）落在 {min(vals)}%–{max(vals)}% 之間，"
            f"跨度 {max(vals)-min(vals):.2f} pp。"
            + ("區間窄，代表結果對調倉日對齊唔敏感，唔係單一日期嘅巧合。"
               if (max(vals) - min(vals)) <= 2.0 else
               f"區間闊（>{max(vals)-min(vals):.1f} pp），真實數字只係其中一個抽樣，"
               f"穩健性存疑。")),
        "note": ("此處的安慰劑位移調倉日，與 v2 regime 測試位移訊號日不同。"
                 "動量本來就唔係曆日效應，所以「有位移 placebo 贏咗真實」"
                 "唔等於造假；要睇的是五個變體是否聚集喺同一區間。"),
    }


def main() -> int:
    print("=== HK 12-month momentum engine (pre-registered) ===")
    print(f"  params: {PARAMS}")
    P, meta = load_panel()
    print(f"  panel: {meta['bars_n']} bars x {meta['universe_n']} tickers  "
          f"{meta['window'][0]} -> {meta['window'][1]}")
    print(f"  excluded by glitch mask: {len(meta['glitch_masked_tickers_excluded'])} tickers")

    probe = look_ahead_probe()
    print(f"  T-1 look-ahead probe: {probe['n_held_before']} members, "
          f"identical after a D+1 spike: {probe['members_identical']} -> "
          f"{'PASS' if probe['passed'] else 'FAIL'}")
    if not probe["passed"]:
        return 2

    periods, holdings = run()
    real = stats(periods)
    print(f"\n  REAL: {real['annualised_pct']}%/yr  Sharpe {real['sharpe_like']}  "
          f"hit {real['hit_rate_pct']}%  MDD {real['max_dd_pct']}%  "
          f"avg turnover {real['avg_turnover_pct']}%")

    placebos = {}
    for off in (-63, -21, 21, 63):
        p, _ = run(pd_offset=off)
        placebos[str(off)] = stats(p)
        print(f"  placebo {off:+>4} bars: {placebos[str(off)]['annualised_pct']}%/yr  "
              f"Sharpe {placebos[str(off)]['sharpe_like']}")
    pread = placebo_reading(real, placebos)
    print(f"  placebo reading: {pread['interpretation']}")

    ev = event_robustness(periods)
    print(f"  event robustness: {ev['annualised_pct_all']}% -> "
          f"{ev['annualised_pct_no_events']}%/yr without event windows")

    periods.to_csv(OUT_CSV, index=False)
    holdings.to_csv(OUT_PDF, index=False)
    out = {
        "spec": "docs/vol-regime-multi-market-spec.md (guardrail philosophy)",
        "strategy": "HK cross-sectional 12-month momentum, long-only, monthly",
        "pre_registered": True,
        "params": PARAMS,
        "data": meta,
        "lookahead_probe": probe,
        "real": real,
        "placebo": placebos,
        "placebo_reading": pread,
        "event_robustness": ev,
        "selection_disclosure": (
            "These parameters were chosen in a prior screening pass that "
            "compared 4 momentum descriptors x 3 rebalance frequencies = 12 "
            "combinations. This run tests ONE pre-registered rule, but that "
            "rule was chosen from 12, so its evidence carries the weight of "
            "12 trials and the multiple-testing penalty applies in full. A "
            "clean test would re-run on a period or universe never seen "
            "during screening."),
        "survivorship_disclosure": (
            f"The universe is {meta['universe_n']} CURRENT constituents. "
            "Stocks delisted, privatised or acquired since "
            f"{meta['window'][0]} are absent, which inflates every figure "
            "here. Closing this gap needs point-in-time index membership, "
            "which this repository does not hold."),
        "cost_model": (f"{COST_RT*100:.2f}% round trip charged pro-rata on "
                       f"the fraction of the basket traded each rebalance"),
        "portfolio_construction_note": (
            "Basket rebalance, not portfolio_test.walk. walk sizes off "
            "entry-to-stop distance and momentum has no stop. The guardrails "
            "are reimplemented here; the position-sizing engine is not shared."),
    }
    OUT_JSON.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    print(f"\n  -> {OUT_JSON}\n  -> {OUT_CSV}\n  -> {OUT_PDF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())