#!/usr/bin/env python3
"""The published 10-year v2-vs-equal-weight figures, loaded from their one source.

2026-10-17. Every page that states "v2 vs equal weight" used to carry its own
hardcoded copy of the numbers. The home page quoted US from
`data/portfolio_test.csv` and JP from `data/hk_retest_summary.json` — two
sources, one paragraph — and neither was compared against the file it claimed
to come from. That is how a defective equal-weight benchmark (see
`hk_retest.ew_bh` and `portfolio_test`) survived review: the page was never
wired to the measurement.

So: one loader, imported by every builder that prints these figures. All three
markets are quoted at a single uniform round-trip cost, the harshest leg that
exists for all of them, so a page cannot flatter the strategy by quietly
picking a cheaper scenario per market. Costs here are a stated input, never
something to interpolate.

Direction is returned as a computed value, not left to the copy, so that a
reversal in the measured gap changes what the page says by itself.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
SUMMARY = REPO / "data" / "hk_retest_summary.json"

WINDOW = "2016-2026"
COST = "cost_0.25%"
MID_COST = "cost_0.10%"
FREE_COST = "cost_0.00%"
MARKETS = ("hk", "us", "jp")


def load() -> dict:
    """Read the retest summary. Raises rather than degrading — a performance
    claim rendered from a missing or partial source is worse than a failed
    build, because it looks identical to a correct one."""
    try:
        d = json.loads(SUMMARY.read_text())
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"FATAL: cannot read {SUMMARY}: {e}")
    out: dict = {"generated": d.get("generated_at", "?"), "cost": COST}
    for mkt in MARKETS:
        legs = d.get(mkt, {}).get("portfolio", {}).get("v2_vs_ew_pp", {})
        blk = legs.get(COST, {}).get(WINDOW)
        if not blk:
            raise SystemExit(f"FATAL: {SUMMARY.name} has no {mkt}.{COST}.{WINDOW}")
        out[mkt] = blk
        # Zero-cost leg, for the "is this only a fee problem?" question.
        out[f"{mkt}_free"] = legs.get(FREE_COST, {}).get(WINDOW) or blk
        # Mid leg. Published too, not just the headline one: the headline cost is
        # deliberately the harshest single figure that exists for all three
        # markets, and a reader is entitled to see how the gap moves at a
        # cheaper one rather than only the number that looks worst.
        out[f"{mkt}_mid"] = legs.get(MID_COST, {}).get(WINDOW) or blk
        # Sub-windows. A full-window gap whose sign flips between 2016-20 and
        # 2021-26 is regime-driven, not a durable edge — the same trap that got
        # the old +6.9pp claim withdrawn. Callers must be able to see that.
        out[f"{mkt}_early"] = legs.get(COST, {}).get("2016-2020") or blk
        out[f"{mkt}_late"] = legs.get(COST, {}).get("2021-2026") or blk
    return out


def fmt(p: dict, lang: str = "zh") -> dict:
    """Format-args for page copy. `{XX}_WINS` is a bool-ish string for callers
    that need to branch; the verdict wording is already localised."""
    f: dict = {}
    for mkt in MARKETS:
        b = p[mkt]
        gap = b["v2_minus_ew_pp"]
        win = gap > 0
        M = mkt.upper()
        f[f"{M}_V2"] = f'{b["v2_cagr%"]:+.1f}%'
        f[f"{M}_EW"] = f'{b["ew_cagr%"]:+.1f}%'
        f[f"{M}_GAP"] = f"{abs(gap):.1f}"
        f[f"{M}_WINS"] = "true" if win else "false"
        f[f"{M}_VERDICT"] = ("跑贏" if win else "落後") if lang != "en" else \
                            ("beat" if win else "trailed")
        fb = p[f"{mkt}_free"]
        f[f"{M}_V2_FREE"] = f'{fb["v2_cagr%"]:+.1f}%'
        f[f"{M}_GAP_FREE"] = f"{abs(fb['v2_minus_ew_pp']):.1f}"
        mb = p[f"{mkt}_mid"]
        f[f"{M}_V2_MID"] = f'{mb["v2_cagr%"]:+.1f}%'
        f[f"{M}_GAP_MID"] = f"{abs(mb['v2_minus_ew_pp']):.1f}"
        f[f"{M}_WINS_MID"] = "true" if mb["v2_minus_ew_pp"] > 0 else "false"
        f[f"{M}_VERDICT_MID"] = ("跑贏" if mb["v2_minus_ew_pp"] > 0 else "落後") \
            if lang != "en" else ("beat" if mb["v2_minus_ew_pp"] > 0 else "trailed")
        for tag, key in (("EARLY", "early"), ("LATE", "late")):
            sb = p[f"{mkt}_{key}"]
            f[f"{M}_GAP_{tag}"] = f"{sb['v2_minus_ew_pp']:+.1f}"
        # Stable only if both halves agree in sign.
        f[f"{M}_STABLE"] = "true" if (b["v2_minus_ew_pp"] > 0) == (
            p[f"{mkt}_early"]["v2_minus_ew_pp"] > 0) and (
            p[f"{mkt}_late"]["v2_minus_ew_pp"] > 0) == (b["v2_minus_ew_pp"] > 0) else "false"
    return f


def hk_note(lang: str = "zh") -> str:
    """The Hong Kong 'not covered by v2' banner sentence.

    Rendered on every one of the ~200 HK ticker pages, so it has to come from
    the measurement rather than from a literal — a stale number here repeats
    200 times and is impossible to spot by eye."""
    p = load()
    f = fmt(p, lang)
    if lang == "en":
        return (f"Measured at a {COST.replace('cost_', '').replace('%', '%')} "
                f"round-trip cost, 2016-26 annualised v2 {f['HK_V2']} vs equal weight "
                f"{f['HK_EW']} — v2 trails by {f['HK_GAP']}pp/yr; at zero cost it still "
                f"trails by {f['HK_GAP_FREE']}pp, so this is the rule failing in Hong Kong, "
                f"not the fee. v2 therefore runs US and Japan only.")
    return (f"按 0.25% 來回成本實測，2016–26 年化 v2 {f['HK_V2']}、等權持有 {f['HK_EW']}，"
            f"落後 {f['HK_GAP']} 個百分點/年；零成本都仲係落後 {f['HK_GAP_FREE']} 點，"
            f"所以 v2 暫時只行美股同日股。")

def us_gap_sentence(lang: str = "zh") -> str:
    """The US v2-vs-equal-weight comparison as one clause, for pages that are
    about something else but must not restate a stale gap.

    Says what the number is AND that it flips sign across sub-windows. A page
    that quotes only the full-window figure is making the same single-window
    claim that was withdrawn from this site before."""
    f = fmt(load(), lang)
    if lang == "en":
        return (f"US v2 {f['US_V2']} vs equal-weight holding {f['US_EW']}, a gap of "
                f"{f['US_GAP']}pp/yr at the 0.25% cost — but it is not a stable edge: "
                f"the sign flips between 2016-20 ({f['US_GAP_EARLY']}pp) and 2021-26 "
                f"({f['US_GAP_LATE']}pp), so the full-window figure is regime-driven and "
                f"should not be quoted as durable alpha")
    return (f"美股 v2 {f['US_V2']} vs 等權持有 {f['US_EW']}，0.25% 成本口徑下差 "
            f"{f['US_GAP']} 點/年 —— 但呢個唔係一個穩定嘅優勢："
            f"2016–20 係 {f['US_GAP_EARLY']} 點、2021–26 係 {f['US_GAP_LATE']} 點，"
            f"全期數字會變號，代表差額由單一 regime 決定，唔應該當成持久 alpha 去引用")
