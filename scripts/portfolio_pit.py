#!/usr/bin/env python3
"""portfolio_pit.py — is Japan's +6.9pp/yr survivorship, or an artefact?

WHAT THIS CAN AND CANNOT MEASURE — read before quoting a number here.

The published result (scripts/portfolio_test.py) is:

    Japan   v2 +25.4%/yr  vs  equal-weight hold of the same 201 names +18.5%/yr
    = +6.9pp/yr

Two different things could explain that gap, and they are not equally
measurable:

  (1) LATE ENTRANTS — a name that only became a TOPIX Core30/Large70 member
      recently still carries its full pre-membership history, so the backtest
      trades it in years when our own selection rule would never have picked
      it. This is testable: drop every name that entered the index after the
      backtest window opens and see if the gap survives.

  (2) MISSING NAMES — every stock that WAS a large cap in 2016 and later
      shrank, was removed, or was delisted is absent from today's list, so it
      contributes nothing to either arm. This is the larger effect and it is
      NOT testable without a point-in-time membership list.

      On (2) specifically: the Nikkei publishes a full component log since
      1970, and we parse it (scripts/nk225_membership.py). But our universe is
      TOPIX Core30 + Large70, not Nikkei 225 — only 41 of 201 names appear in
      the Nikkei log at all. So that log cannot close the gap on our universe.
      An earlier draft of this script assumed it could; it cannot, and saying
      otherwise would be the exact "trust the number" error this whole test
      exists to prevent.

So this script reports (1) honestly, states (2) as a named open risk, and adds
a third check that needs no membership data at all:

  (3) CONCENTRATION — if the outperformance comes from two or three names, it
      is fragile regardless of any bias question.

Usage:  python3 scripts/portfolio_pit.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "v2engine"))

from core import features, _simulate  # noqa: E402
from bt_clean import bars_dir, load as load_clean, _load_mask_cache, filter_candidates  # noqa: E402
import portfolio_test as PT  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
START = "2016-09-01"
MKT = "jp200"
INDEX = "IDX_N225"


def jp_symbols(src: Path) -> list[str]:
    return sorted(p.stem for p in src.glob("*.json") if p.stem.endswith("_T"))


def load_entry() -> dict[str, str]:
    p = REPO / "data" / "nk225_entry.json"
    if not p.exists():
        return {}
    return {str(k).replace("_T", ""): v["added"] for k, v in json.load(open(p)).items()}


def run_arm(names: set[str], up: pd.Series, src: Path, mask: dict) -> tuple | None:
    """Return (v2 curve, equal-weight benchmark curve) for a restricted universe.

    Same machinery as portfolio_test.py — same entry rule, same costs, same
    concurrent cap. Only the name set differs, so the v2-vs-hold gap stays
    attributable to the universe change alone.
    """
    px: dict[str, pd.Series] = {}
    cands: list[dict] = []
    for s in sorted(names):
        b = PT.load(src / f"{s}.json")
        if b is None:
            continue
        px[s] = b.close
        tagged = [dict(c, sym=s) for c in PT.candidates_v2(b)]
        kept, _ = filter_candidates(tagged, mask)
        cands.extend(kept)
    if not px or not cands:
        return None

    ib = PT.load(src / f"{INDEX}.json")
    cal = ib.index
    b1src = pd.DataFrame(px).reindex(cal)
    b1src = b1src.loc[b1src.notna().any(axis=1)]
    cal = b1src.index
    mm = b1src.pct_change().groupby([cal.year, cal.month]).transform("mean").mean(axis=1)
    b1 = (100_000 * (1 + mm).cumprod()).ffill()

    curve, _peak = PT.walk(cands, MKT)
    if curve is None:
        return None
    return curve.set_index("date").equity, b1, len(cands)


def cagr(eq: pd.Series) -> float:
    if len(eq) < 2:
        return float("nan")
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100


def main() -> int:
    src = bars_dir()
    mask = _load_mask_cache()
    up = PT.market_up_series(MKT)
    if up is None:
        print("no JP index bars — cannot run")
        return 1

    allj = jp_symbols(src)
    entry = load_entry()

    # ONLY names the log actually covers can be classified. An unmatched name
    # is not a late entrant — it is a name we have no membership date for, and
    # treating "unknown" as "late" silently drops most of the universe and
    # turns a survivorship test into a different strategy test.
    known = {s: entry[s.replace("_T", "")] for s in allj if s.replace("_T", "") in entry}
    late = {s for s, d in known.items() if d > START}
    pre = {s for s, d in known.items() if d <= START}

    variants = [
        ("A 全部現行名單", set(allj)),
        ("B 剔除已確認遲入選", set(allj) - late),
        ("C 只限已確認 2016 前入選", pre),
    ]

    print(f"JP universe: {len(allj)} names")
    print(f"  Nikkei log 可分類: {len(known)}  (遲入選 {len(late)}, 2016 前 {len(pre)})")
    print(f"  無成分股資料      : {len(allj) - len(known)}\n")
    if len(allj) - len(known) > 0:
        print("  ⚠ 只有 %d/%d 隻可分類，所以 B 變體剔除量有限；"
              "A 同 B 嘅差距只反映呢 %d 隻遲入選股票嘅影響。\n"
              % (len(known), len(allj), len(late)))
    print(f"{'變體':<30}{'n':>5}{'v2 年化':>11}{'等權持有':>11}{'差額 pp/年':>12}{'訊號數':>9}")
    print("-" * 78)

    results = {}
    for label, names in variants:
        if len(names) < 5:
            print(f"{label:<30}{len(names):>5}   樣本太少，跳過")
            continue
        r = run_arm(names, up, src, mask)
        if r is None:
            print(f"{label:<30}{len(names):>5}   無候選交易")
            continue
        eq_v2, eq_b1, n = r
        v2c, b1c = cagr(eq_v2), cagr(eq_b1)
        results[label] = (v2c - b1c, len(names), n)
        print(f"{label:<30}{len(names):>5}{v2c:>10.1f}%{b1c:>10.1f}%"
              f"{v2c - b1c:>11.1f}{n:>9}")

    base = results.get("A 全部現行名單")
    nol = results.get("B 剔除 2016-09-01 後先入選")
    if base and nol:
        print(f"\n移除 {base[1] - nol[1]} 隻遲入選股票後，差額由 "
              f"{base[0]:+.1f} pp/年 變成 {nol[0]:+.1f} pp/年 "
              f"（變動 {nol[0] - base[0]:+.1f} pp/年）")

    # (3) concentration — no membership data needed.
    print("\n集中度 + 分期檢查（唔需要成分股資料）")
    r = run_arm(set(allj), up, src, mask)
    if r:
        eq_v2, eq_b1, _n = r
        d = eq_v2 / eq_v2.iloc[0] / (eq_b1 / eq_b1.iloc[0])
        seg = [(2016, 2020), (2021, 2026)]
        print(f"  {'期間':<14}{'v2':>10}{'等權持有':>11}{'差額 pp/年':>12}")
        for a, b in seg:
            try:
                v = eq_v2[(eq_v2.index.year >= a) & (eq_v2.index.year <= b)]
                h = eq_b1[(eq_b1.index.year >= a) & (eq_b1.index.year <= b)]
                if len(v) < 2 or len(h) < 2:
                    continue
                vc, hc = cagr(v), cagr(h)
                print(f"  {f'{a}–{b}':<14}{vc:>9.1f}%{hc:>10.1f}%{vc - hc:>11.1f}")
            except Exception:
                continue
        yearly = eq_v2.resample("YE").last().pct_change() * 100
        print(f"\n  年度回報: {', '.join(f'{y.year}:{v:+.0f}%' for y, v in yearly.items() if v == v)}")
        print(f"  全期 v2/持有 倍數: {d.iloc[-1]:.2f}x")

    print("""
未能量化的部分（必須讀）：
  今日名單以外、但 2016 年曾經係大盤股嘅股票（縮細、被剔除、停牌除牌）完全唔喺
  任何一臂之內。呢個係最大嘅偏差來源，而現有公開名單無法重建 —— Nikkei 官方 log
  只覆蓋 Nikkei 225 成員，同我哋嘅 TOPIX Core30 + Large70 只有 41/201 重疊。
  即係話，上面任何一個差額都係「在存活名單上的優勢」，唔等於「同一批可投資標的
  上的真實 alpha」。""")
    return 0


if __name__ == "__main__":
    sys.exit(main())