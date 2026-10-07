#!/usr/bin/env python3
"""hk_edge_test.py — does the v2 rule actually have no edge in Hong Kong?

WHY THIS SCRIPT EXISTS
    build_v2_signals.py has carried this since before v2 shipped:

        INCLUDE_HK = False   # study found HK is ~0 edge after 0.45% round-trip

    That comment is why every Hong Kong visitor is told the engine does not
    cover their market. It has three problems:

      1. UNSOURCED. No HK 10-year bar store existed on this project, so "HK is
         ~0 edge" was never measured on this project's own machinery. It is a
         remembered number.
      2. THE COST IS WRONG FOR THE PERSON WHO IS ASKING. core.FEES["hk200"] is
         0.0025 — 0.25% round trip, which is what the site publishes and what
         a commission-free HK$100k Futu trade actually costs. The 0.45% in the
         comment is the *realistic* scenario (fees + slippage), a stricter
         assumption the site never states.
      3. 0.45% vs 0.25% is not a rounding difference. At 10 trades a year over
         10 years that is 20 percentage points of cumulative cost.

    So: measure it. Same machinery as portfolio_test.py — same entry rule, same
    filters, same concurrent cap, same benchmark (equal-weight hold of the SAME
    names). Only the cost scenario and the date window change, so every number
    here is attributable to those two things.

READING THE RESULT
    The gap v2-minus-hold is the only number that matters, and the absolute
    levels are not trustworthy (today's universe applied retroactively = static
    survivorship bias, see methodology 4.7). A full-period gap is also not
    enough: the same measurement on Japan showed +6.9pp/yr overall that was
    -7.9pp/yr in 2016-20 and +16.7pp/yr in 2021-26. This script splits the
    window for the same reason.

Usage:  python3 scripts/hk_edge_test.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "v2engine"))

from core import cost_rt  # noqa: E402
from bt_clean import _load_mask_cache, filter_candidates  # noqa: E402
import portfolio_test as PT  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HK_BARS = REPO / "data" / "bt10y2_hk"
IDX = REPO / "data" / "bt10y2" / "IDX_HSI.json"
MKT = "hk200"


def hk_files() -> list[tuple[str, Path]]:
    """(safe_stem, path) for the HK store.

    refetch_hk10y.to_safe() only replaces the dot, so files are named
    00001_HK.json / 07709_HK.json — the safe form the site uses for URLs, not
    a HK_ prefix. Match that instead of guessing a prefix.
    """
    out = []
    for p in sorted(HK_BARS.glob("*.json")):
        stem = p.stem
        if not stem.endswith("_HK"):
            continue
        out.append((stem, p))
    return out


def hk_symbols() -> list[str]:
    """Safe stems (07709_HK) -> the .HK form yfinance uses (7709.HK)."""
    out = []
    for stem, _ in hk_files():
        num = stem[:-3]
        out.append((num[-4:] if len(num) > 4 else num) + ".HK")
    return out


def cagr(eq: pd.Series) -> float:
    if len(eq) < 2:
        return float("nan")
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100


def run(names: list[str], up: pd.Series, scenario: str, mask: dict):
    PT.COST = scenario
    PT.MARKETS[MKT] = ("IDX_HSI", "IDX_HSI")
    px: dict[str, pd.Series] = {}
    cands: list[dict] = []
    for (safe, path) in hk_files():
        b = PT.load(path)
        if b is None:
            continue
        px[safe] = b.close
        kept, _ = filter_candidates([dict(c, sym=safe) for c in PT.candidates_v2(b)], mask)
        cands.extend(kept)
    if not px or not cands:
        return None

    ib = PT.load(IDX)
    cal = ib.index
    b1 = pd.DataFrame(px).reindex(cal)
    b1 = b1.loc[b1.notna().any(axis=1)]
    cal = b1.index
    mm = b1.pct_change().groupby([cal.year, cal.month]).transform("mean").mean(axis=1)
    b1 = (100_000 * (1 + mm).cumprod()).ffill()
    curve, _ = PT.walk(cands, MKT)
    if curve is None:
        return None
    return curve.set_index("date").equity, b1, len(cands)


def main() -> int:
    mask = _load_mask_cache()
    names = hk_symbols()
    ib = PT.load(IDX)
    from core import features
    up = features(ib).prev_close > features(ib).ma200
    up.name = "mkt_up"

    import pandas as _pd
    _w = [str(min(_pd.read_json(p, orient="records")["date"])) for _, p in hk_files()]
    _z = [str(max(_pd.read_json(p, orient="records")["date"])) for _, p in hk_files()]
    print(f"HK bars: {len(names)} symbols  window: {min(_w)} -> {max(_z)}")
    print(f"glitch mask covers {len([k for k in mask if k.endswith('_HK')])} HK symbols\n")

    scen = [("site 0.25%（你實際成本）", "site"),
            ("realistic ≈0.45%（費+滑價）", "realistic")]
    print(f"{'情境':<32}{'v2 年化':>10}{'等權持有':>11}{'差額 pp/年':>12}{'訊號':>8}")
    print("-" * 73)
    res = {}
    for label, sc in scen:
        r = run(names, up, sc, mask)
        if r is None:
            print(f"{label:<32}  無候選交易")
            continue
        eq, b1, n = r
        v, h = cagr(eq), cagr(b1)
        res[label] = (v, h, v - h, eq, b1, n)
        print(f"{label:<32}{v:>9.1f}%{h:>10.1f}%{v - h:>11.1f}{n:>8}")

    for label in res:
        v, h, gap, eq, b1, n = res[label]
        print(f"\n{label} · 拆市況")
        print(f"  {'期間':<14}{'v2':>10}{'等權持有':>11}{'差額 pp/年':>12}")
        for a, b in [(2016, 2020), (2021, 2026)]:
            vv = eq[(eq.index.year >= a) & (eq.index.year <= b)]
            hh = b1[(b1.index.year >= a) & (b1.index.year <= b)]
            if len(vv) < 2 or len(hh) < 2:
                continue
            vc, hc = cagr(vv), cagr(hh)
            print(f"  {f'{a}–{b}':<14}{vc:>9.1f}%{hc:>10.1f}%{vc - hc:>11.1f}")

    print("""
讀法
  · 差額 = v2 年化 − 同一批股票等權持有。絕對水平唔可信（今日名單倒套 10 年 = 靜態
    倖存者偏差），只有差額有意義。
  · 全期差額唔夠，一定要睇分段 —— 日本嗰次就係 +6.9pp/yr 全期，但拆開係 −7.9 / +16.7。
  · 若果 site 情境（0.25%）有正差額、realistic（≈0.45%）冇，代表結論完全係由成本
    假設決定，而唔係由規則決定。呢個先係「值唔值得上」真正嘅答案。""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())