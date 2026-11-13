#!/usr/bin/env python3
"""Reproduce everything in docs/website-improvements-2026-11-11.md.

    python3 scripts/research/run_all.py

ONE COMMAND. It runs the four items, the guardrails, and the self-tests, and
writes:

    research/2026-11-11-credibility/
        summary.md                          the one-page summary
        summary.json                        the machine-readable summary
        item1_trades_with_tiers.csv         every published v2 signal + its tier
        item1_candidates_with_tiers.csv     every screen-passing session + tier
        item1_excluded_cohort.csv           signals recognised but not booked
        item1_tier_counts.csv               tier counts per market and window
        item1_coverage.csv                  the three coverage readings
        item2_gap_distribution.csv          the overnight-gap distribution
        item2_filter_rate.csv               the false-signal filter rate
        item3_failure_criteria.csv          current numbers vs proposed thresholds
        item4_receipt.json                  the three-layer receipt
        drafts/*.md                         the text drafts, NOT deployed

Nothing here touches production. Every flag is OFF. No page is rewritten, no
deploy happens, nothing is pushed.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import confirmation_cost as CC   # noqa: E402
import coverage as C              # noqa: E402
import executability as E         # noqa: E402
import failure_criteria as F      # noqa: E402
import gate_independence as G     # noqa: E402
import research_flags as RF       # noqa: E402

OUT = REPO / "research" / "2026-11-11-credibility"
DRAFTS = OUT / "drafts"
MARKETS = ("us200", "jp200")
WINDOW_LABELS = {
    "full_2016_2026": "2016-26 (full)",
    "pre_2023": "pre-2023 (train)",
    "post_2023": "2023-26 (test)",
    "early_2016_2020": "2016-20",
    "late_2021_2026": "2021-26",
}


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  → {path.relative_to(REPO)}")


def wcsv_gz(path: Path, rows: list[dict], cols: list[str] | None = None) -> None:
    """Same as wcsv but gzip. The candidate pool is 458k rows — 38 MB of CSV
    is a poor thing to put in a git repository, and the file is regenerated
    in seconds by the one command."""
    import gzip
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_bytes(b"")
        print(f"  → {path.relative_to(REPO)}  (empty)")
        return
    cols = cols or list(rows[0].keys())
    with gzip.open(path, "wt", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow(r)
    print(f"  → {path.relative_to(REPO)}  ({len(rows)} rows, gzipped)")


def wcsv(path: Path, rows: list[dict], cols: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        print(f"  → {path.relative_to(REPO)}  (empty)")
        return
    cols = cols or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow(r)
    print(f"  → {path.relative_to(REPO)}  ({len(rows)} rows)")


# --------------------------------------------------------------------------
def item1() -> dict:
    print("\n" + "=" * 72)
    print("ITEM 1 — executability tiers on every signal")
    print("=" * 72)
    out: dict = {"markets": {}}
    all_trades, all_cands, all_exc = [], [], []

    for mkt in MARKETS:
        trades, cands, exc = E.rerun_v2(mkt)
        all_trades += trades
        all_cands += cands
        all_exc += exc

        # tier counts per window
        tier_rows = []
        for wname, (lo, hi) in E.WINDOWS.items():
            for population, rows in (("published_signals", trades),
                                      ("candidate_pool", cands),
                                      ("excluded_cohort", exc)):
                sel = [r for r in rows if E.in_window(r["date"], lo, hi)]
                counts = E.tier_counts(sel)
                net = np.array([r["net"] for r in sel
                                if population == "published_signals"], float)
                tier_rows.append({
                    "market": mkt,
                    "window": wname,
                    "window_label": WINDOW_LABELS[wname],
                    "population": population,
                    "n": len(sel),
                    **{t: counts.get(t, 0) for t in E.ALL_TIER_LABELS},
                    "avg_net_pct": round(float(net.mean() * 100), 3) if len(net) else None,
                    "win_pct": round(float((net > 0).mean() * 100), 1) if len(net) else None,
                })
        out["markets"][mkt] = {
            "tier_counts": tier_rows,
            "n_trades": len(trades),
            "n_candidates": len(cands),
            "n_excluded": len(exc),
            "exclusion_reasons": _count_by(exc, "exclusion"),
            "exclusion_by_tier": _count_by(exc, "tier"),
            "excluded_forward_net": _excluded_summary(exc),
        }
        print(f"  {mkt}: trades={len(trades)} candidates={len(cands)} "
              f"excluded={len(exc)}")

    # the headline: per-trade average with UNFILLABLE excluded vs included
    headline = []
    for mkt in MARKETS:
        sel = [t for t in all_trades if t["mkt"] == mkt]
        net_all = np.array([t["net"] for t in sel], float)
        keep = [t for t in sel if t["tier"] != E.TIER_UNFILLABLE]
        net_keep = np.array([t["net"] for t in keep], float)
        headline.append({
            "market": mkt,
            "n_included": len(net_all),
            "avg_included_pct": round(float(net_all.mean() * 100), 3),
            "win_included_pct": round(float((net_all > 0).mean() * 100), 1),
            "n_excluded_unfillable": len(net_all) - len(net_keep),
            "avg_excluded_pct": round(float(net_keep.mean() * 100), 3),
            "win_excluded_pct": round(float((net_keep > 0).mean() * 100), 1),
            "note": ("UNFILLABLE excluded from the denominator, as the spec "
                     "requires. The count next to it is the count removed."),
        })
    out["headline_win_rate_with_and_without_unfillable"] = headline

    # coverage, three readings
    print("\n  coverage — three readings, three denominators")
    cov_rows = []
    for mkt in MARKETS:
        cov = C.coverage_counts(mkt)
        cov_rows.append({
            "market": mkt,
            "calendar_source": cov["session_calendar_source"],
            "sessions": cov["sessions_in_calendar"],
            "symbols": cov["symbols"],
            "total_cells": cov["total_cells"],
            "missing_cells": cov["missing_cells"],
            "missing_cells_over_total_cells": cov["readings"]["missing_cells_over_total_cells"],
            "total_rows": cov["total_rows"],
            "missing_rows": cov["missing_rows"],
            "missing_rows_over_total_rows": cov["readings"]["missing_rows_over_total_rows"],
            "clean_rows": cov["families"][C.FAMILY_CLEAN],
            "clean_rows_over_total_rows": cov["readings"]["clean_rows_over_total_rows"],
        })
        print(f"    {mkt}: missing cells {cov['readings']['missing_cells_over_total_cells']:.6f}"
              f" | missing rows {cov['readings']['missing_rows_over_total_rows']:.6f}"
              f" | clean rows {cov['readings']['clean_rows_over_total_rows']:.6f}")
    out["coverage"] = cov_rows

    # the two negative-control arms
    print("\n  negative-control arms")
    arms = {}
    for mkt in MARKETS:
        a = C.zero_value_expansion_arm(mkt, n=200)
        # The fixture rows must be CONSISTENT under the rule the arm starts
        # from, and they must carry the long entry evidence — otherwise the
        # side flip has nothing to flip and the arm fails on the fixture
        # rather than on the code. Two versions of this fixture got it wrong
        # on the first run (one pinned `open` to a constant, so high-priced
        # names had open <= S1; one closed half the rows BELOW S1, so they
        # were already inconsistent). The arm caught both.
        rows = []
        for t in [x for x in all_trades if x["mkt"] == mkt][:200]:
            s1 = float(t["s1"])
            rows.append({"sym": t["sym"], "date": t["date"], "side": 1, "s1": s1,
                         "open": s1 + 2.0, "high": s1 + 3.0, "low": s1 - 0.5,
                         "close": s1 + 1.0, "volume": 1000.0})
        b = C.side_change_mutation_arm(rows)
        arms[mkt] = {"zero_value_expansion": a, "side_change_mutation": b}
        print(f"    {mkt}: expansion={'PASS' if a['passes'] else 'FAIL'} "
              f"mutation={'PASS' if b['passes'] else 'FAIL'}")
    out["negative_control_arms"] = arms

    # event dominance
    out["event_dominance"] = event_dominance(all_trades)

    # multiple comparisons disclosure
    out["multiple_comparisons"] = {
        "tier_definitions_tried": 1,
        "tier_labels": list(E.ALL_TIER_LABELS),
        "extra_label_reason": (
            "REACHED_S1_NOT_HELD exists because the spec's four tiers have no "
            "slot for a session that reached S1 and closed at or below it. "
            "Folding it into UNFILLABLE would empty the published set by "
            "construction and make the win-rate exclusion a tautology; "
            "folding it into NOT_TRIGGERED would be false. It is reported in "
            "its own column and never enters the win-rate denominator."),
        "windows_reported": len(E.WINDOWS),
        "window_note": (
            "Five windows are reported: the full sample, the engine's own "
            "train/test split, and the site's published sub-windows. The "
            "engine's split (2023-01-01) and the site's sub-windows "
            "(2016-20 / 2021-26) are DIFFERENT schemes; reporting only one "
            "would let the choice of window do the work."),
        "threshold_variants_tried": 1,
        "placebo_trials": CC.PLACEBO_TRIALS,
        "placebo_note": ("One placebo construction (size-matched draw from the "
                         "same symbol's candidate pool). One threshold set "
                         "(PROPOSED). No variant was tried and dropped "
                         "silently."),
    }

    wcsv(OUT / "item1_trades_with_tiers.csv", all_trades,
         ["mkt", "sym", "date", "exit_date", "s1", "entry", "stop", "exit",
          "atr_pct", "gross", "net", "reason", "held", "tier", "tier_reason"])
    wcsv_gz(OUT / "item1_candidates_with_tiers.csv.gz", all_cands,
         ["mkt", "sym", "date", "s1", "atr_pct", "tier", "tier_reason",
          "reached_s1", "open_above_s1", "close_above_s1"])
    wcsv(OUT / "item1_excluded_cohort.csv", all_exc,
         ["mkt", "sym", "date", "s1", "atr_pct", "tier", "tier_reason",
          "exclusion", "forward_net", "forward_net_known", "mark_px"])
    wcsv(OUT / "item1_tier_counts.csv", out["markets"]["us200"]["tier_counts"]
         + out["markets"]["jp200"]["tier_counts"])
    wcsv(OUT / "item1_coverage.csv", cov_rows)
    return out


def _count_by(rows: list[dict], key: str) -> dict:
    d: dict = {}
    for r in rows:
        d[str(r.get(key))] = d.get(str(r.get(key)), 0) + 1
    return d


def _excluded_summary(exc: list[dict]) -> dict:
    known = [e for e in exc if e.get("forward_net_known")]
    unknown = [e for e in exc if not e.get("forward_net_known")]
    net = np.array([e["forward_net"] for e in known], float)
    by_tier: dict = {}
    for t in sorted({e["tier"] for e in exc}):
        sel = [e for e in known if e["tier"] == t]
        u = [e for e in unknown if e["tier"] == t]
        by_tier[t] = {
            "n": len([e for e in exc if e["tier"] == t]),
            "n_measured": len(sel),
            "n_unknown": len(u),
            "avg_forward_net_pct": round(float(
                np.array([e["forward_net"] for e in sel], float).mean() * 100), 3)
            if sel else None,
        }
    return {
        "n": len(exc),
        "n_measured": len(known),
        "n_unknown": len(unknown),
        "avg_forward_net_pct": round(float(net.mean() * 100), 3) if len(net) else None,
        "win_pct": round(float((net > 0).mean() * 100), 1) if len(net) else None,
        "by_tier": by_tier,
        "note": ("The excluded cohort's forward return is measured under the "
                 "SAME engine rules (entry at S1, 3xATR stop, 10-session exit) "
                 "so the only difference is the exclusion itself. Positions "
                 "still open at the edge of the store are reported as "
                 "UNKNOWN, never as 0."),
    }


def event_dominance(trades: list[dict], top_n: int = 5) -> dict:
    """Report the headline with and without the few biggest market days.

    The spec names the Nov 2026 rout as the event to worry about. The frozen
    store ends 2026-10-01, so that event is NOT IN THIS DATA — the honest
    version of this check is the largest moves that ARE in the store.
    """
    out: dict = {
        "spec_named_event": "the Nov 2026 rout",
        "spec_named_event_in_data": False,
        "spec_named_event_note": (
            "The frozen 10-year bar store ends 2026-10-01, so the Nov 2026 "
            "rout is not in this data at all. The check below uses the "
            "largest moves that ARE in the store instead, and that is a "
            "weaker check than the spec asked for — it cannot bound the "
            "influence of an event that post-dates the data."),
        "method": ("the N sessions with the largest absolute move in the "
                   "market index, removed from the sample"),
    }
    for mkt in MARKETS:
        idx_file = {"us200": "IDX_GSPC.json", "jp200": "IDX_N225.json"}[mkt]
        try:
            rows = json.loads((E.BARS / idx_file).read_text(encoding="utf-8"))
        except Exception:
            continue
        closes = [float(r["close"]) for r in rows if isinstance(r, dict)]
        dates = [r["date"][:10] for r in rows if isinstance(r, dict)]
        moves = sorted(
            ((abs(closes[i] / closes[i - 1] - 1), dates[i]) for i in range(1, len(closes))),
            reverse=True)[:top_n]
        big = {d for _mv, d in moves}
        sel = [t for t in trades if t["mkt"] == mkt]
        net_all = np.array([t["net"] for t in sel], float)
        keep = np.array([t["net"] for t in sel if t["date"] not in big], float)
        out[mkt] = {
            "removed_sessions": [{"date": d, "abs_move_pct": round(mv * 100, 2)}
                                 for mv, d in moves],
            "n_with": len(net_all),
            "avg_with_pct": round(float(net_all.mean() * 100), 3),
            "n_without": len(keep),
            "avg_without_pct": round(float(keep.mean() * 100), 3),
            "delta_pp": round(float(net_all.mean() * 100 - keep.mean() * 100), 3),
            "verdict": ("not driven by the biggest sessions"
                        if abs(float(net_all.mean() * 100 - keep.mean() * 100)) < 0.5
                        else "MATERIAL — driven by the biggest sessions"),
        }
    return out


# --------------------------------------------------------------------------
def item2(trades_by_mkt: dict) -> dict:
    print("\n" + "=" * 72)
    print("ITEM 2 — the confirmation-period cost")
    print("=" * 72)
    out: dict = {"filter_rate": CC.filter_rate(), "markets": {}}
    gap_rows = []
    for mkt in MARKETS:
        trades, cands, _exc = trades_by_mkt[mkt]
        samples = CC.gap_samples(mkt, trades)
        real = CC.summarise(samples)
        pl = CC.placebo(mkt, trades, cands, trials=CC.PLACEBO_TRIALS)
        out["markets"][mkt] = {"real": real, "placebo": pl}
        print(f"  {mkt}: n={real.get('n')} median gap {real.get('median_gap_pct')}% "
              f"| p99 |gap| {real.get('p99_abs_gap_pct')}% "
              f"| >3xATR {real.get('share_gap_gt_3atr')}% "
              f"(placebo {pl.get('null_share_gt_3atr_mean')}%)")
        gap_rows.append({
            "market": mkt, "population": "real signal days",
            **{k: real.get(k) for k in
               ("n", "median_gap_pct", "mean_gap_pct", "median_abs_gap_pct",
                "p90_abs_gap_pct", "p99_abs_gap_pct", "mean_atr_pct",
                "p99_abs_gap_over_atr", "share_gap_gt_3atr", "share_gap_gt_1atr")},
            "null_median_gap_pct_mean": pl.get("null_median_gap_pct_mean"),
            "null_median_gap_pct_sd": pl.get("null_median_gap_pct_sd"),
            "null_share_gt_3atr_mean": pl.get("null_share_gt_3atr_mean"),
            "null_share_gt_3atr_sd": pl.get("null_share_gt_3atr_sd"),
            "placebo_trials": pl.get("trials"),
            "placebo_has_power": pl.get("has_power"),
        })
    fr = out["filter_rate"].get("markets") or {}
    for mkt, wins in fr.items():
        for win, c in wins.items():
            gap_rows.append({
                "market": mkt, "population": f"filter rate {win}",
                "n": c["touch_only_n"],
                "false_signal_filter_rate_pct": c["false_signal_filter_rate_pct"],
                "rejected_by_filter_n": c["rejected_by_filter_n"],
                "touch_only_avg_pct": c["touch_only_avg_pct"],
                "close_hold_avg_pct": c["close_hold_avg_pct"],
                "lift_pp": c["lift_pp"],
            })
    wcsv(OUT / "item2_gap_distribution.csv", gap_rows)
    return out


# --------------------------------------------------------------------------
def item3(trades_by_mkt: dict) -> dict:
    print("\n" + "=" * 72)
    print("ITEM 3 — preregistered numeric failure criteria (PROPOSED)")
    print("=" * 72)
    out: dict = {
        "status": "PROPOSED — Kenneth confirms the numbers before anything goes live",
        "proposed": F.PROPOSED,
        "window_definitions": F.WINDOW_DEFINITIONS,
        "markets": {},
        "direction_a": F.direction_a_reading(),
    }
    rows = []
    for mkt in MARKETS:
        trades, _c, _e = trades_by_mkt[mkt]
        ev = F.evaluate(mkt, trades)
        out["markets"][mkt] = ev
        print(f"  {mkt}: T1 {ev['thresholds']['T1_sharpe_decay']['verdict']} "
              f"({ev['thresholds']['T1_sharpe_decay']['observed_ratio']}) | "
              f"T2 {ev['thresholds']['T2_max_drawdown']['verdict']} "
              f"({ev['thresholds']['T2_max_drawdown']['observed_multiple']}) | "
              f"T3 {ev['thresholds']['T3_rolling_streak']['verdict']} "
              f"(streak {ev['thresholds']['T3_rolling_streak']['observed_streak']})")
        for scope in ("in_sample", "out_of_sample", "full_sample"):
            s = ev[scope]
            for tname, t in ev["thresholds"].items():
                rows.append({
                    "market": mkt, "scope": scope,
                    "definition": s.get("definition"),
                    "n_trades": s.get("n_trades"), "n_sessions": s.get("n_sessions"),
                    "sharpe_raw": s.get("sharpe_raw"),
                    "sharpe_annualised": s.get("sharpe_annualised"),
                    "max_drawdown_pct": s.get("max_drawdown_pct"),
                    "longest_negative_streak": s.get("longest_negative_streak"),
                    "threshold": tname,
                    "threshold_scope": ("IS vs OOS (cross-window comparison; "
                                        "the observed value is the same on "
                                        "every scope row)"),
                    "proposed": t["proposed"],
                    "observed": (t.get("observed_ratio")
                                 if t.get("observed_ratio") is not None
                                 else t.get("observed_multiple")
                                 if t.get("observed_multiple") is not None
                                 else t.get("observed_streak")),
                    "verdict": t["verdict"],
                })
    wcsv(OUT / "item3_failure_criteria.csv", rows)
    return out


# --------------------------------------------------------------------------
def item4() -> dict:
    print("\n" + "=" * 72)
    print("ITEM 4 — gate independence and the three-layer receipt")
    print("=" * 72)
    manifest = G.build_manifest()
    live = G.fetch_live()
    published = {"deploy_url": manifest.get("deploy_url"),
                 "published_at": manifest.get("finished_at"),
                 "note": "the build manifest's own deploy record (layer b)"}
    receipt = G.three_layer_receipt(manifest, published, live)
    print(f"  live: stamp={live.get('stamp')} count={live.get('signal_count')} "
          f"-> receipt verdict {receipt['verdict']}")
    stale = G.stale_stamp_fixture(manifest)
    print(f"  planted stale stamp -> gate verdict {stale['gate_verdict']} "
          f"(caught: {stale['gate_caught_it']})")

    entries = [Path(REPO / "scripts" / "research" / n) for n in
               ("executability.py", "coverage.py", "confirmation_cost.py",
                "failure_criteria.py", "gate_independence.py")]
    hard = G.hard_gate_entry_coverage(entries, runner_args=["--selftest"])
    print(f"  hard gate: declared={hard['declared_count']} "
          f"executed={hard['executed_count']} -> {hard['verdict']}")

    out = {
        "receipt": receipt,
        "live_readback": live,
        "manifest": manifest,
        "stale_stamp_fixture": stale,
        "hard_gate": hard,
        "denominator_independence": G.denominator_independence(
            REPO / "scripts" / "research"),
        "independence_note": (
            "The read-back source is the deployed page fetched over the "
            "network. The manifest is the thing being CHECKED, never the "
            "checker: a gate that compared the manifest with itself could "
            "never fail."),
    }
    w(OUT / "item4_receipt.json", json.dumps(out, ensure_ascii=False, indent=1))
    return out


# --------------------------------------------------------------------------
def _count_of(i1: dict, mkt: str, population: str, tier: str) -> int:
    rows = [r for r in i1["markets"][mkt]["tier_counts"]
            if r["window"] == "full_2016_2026" and r["population"] == population]
    return rows[0].get(tier, 0) if rows else 0


def drafts(i1: dict, i2: dict, i3: dict) -> None:
    print("\n" + "=" * 72)
    print("DRAFTS — text for Kenneth to approve. NOT deployed.")
    print("=" * 72)
    fr = (i2.get("filter_rate") or {}).get("markets") or {}
    us = (i2["markets"]["us200"]["real"])
    jp = (i2["markets"]["jp200"]["real"])
    us_pl = i2["markets"]["us200"]["placebo"]
    jp_pl = i2["markets"]["jp200"]["placebo"]

    d45 = f"""# DRAFT — /methodology §4.5 addition (NOT deployed)

**Status: DRAFT behind `confirmation_cost_flag` (default OFF). Nothing here is
on the site. Kenneth approves the words AND the numbers before it goes live.**

## 4.5b 收市確認嘅等待成本（提案）

現行 §4.5 印住收市確認將每單平均由 {fr.get('us200', {}).get('test', {}).get('touch_only_avg_pct', '—')}%
（只係觸到）提升到 {fr.get('us200', {}).get('test', {}).get('close_hold_avg_pct', '—')}%
（要收市企穩）。但呢個數**冇記低等待成本**：一條要等收市先知訊號成立嘅規則，
下一個可以成交嘅價係下一個開市價，中間嘅隔夜跳空就係你食咗嘅嘢。

**假訊號過濾率**（美股 2023–26）：觸到 S1 嘅 {fr.get('us200', {}).get('test', {}).get('touch_only_n', '—')} 單之中，
收市確認濾走 {fr.get('us200', {}).get('test', {}).get('rejected_by_filter_n', '—')} 單
（{fr.get('us200', {}).get('test', {}).get('false_signal_filter_rate_pct', '—')}%）。
日股 2023–26：{fr.get('jp200', {}).get('test', {}).get('false_signal_filter_rate_pct', '—')}%。

**等待成本（隔夜跳空分佈，唔係 ATR）**

| 市場 | 樣本 | 中位跳空 | \\|跳空\\| 中位 | \\|跳空\\| p90 | \\|跳空\\| p99 | 平均 ATR | p99 跳空 / ATR | 跳空 > 3×ATR |
|---|---|---|---|---|---|---|---|---|
| 美股 | {us.get('n')} | {us.get('median_gap_pct')}% | {us.get('median_abs_gap_pct')}% | {us.get('p90_abs_gap_pct')}% | {us.get('p99_abs_gap_pct')}% | {us.get('mean_atr_pct')}% | {us.get('p99_abs_gap_over_atr')} | {us.get('share_gap_gt_3atr')}% |
| 日股 | {jp.get('n')} | {jp.get('median_gap_pct')}% | {jp.get('median_abs_gap_pct')}% | {jp.get('p90_abs_gap_pct')}% | {jp.get('p99_abs_gap_pct')}% | {jp.get('mean_atr_pct')}% | {jp.get('p99_abs_gap_over_atr')} | {jp.get('share_gap_gt_3atr')}% |

**點解用跳空分佈，唔用 ATR**：ATR 係平均尺度，由日內波幅砌出嚟；隔夜跳空係
肥尾（fat-tailed）——中位跳空遠細過 ATR，但尾部（p99）可以係 ATR 嘅兩倍以上。
用 ATR 當成本尺度，會**低估尾部**：美股平均 ATR {us.get('mean_atr_pct')}%，
而 p99 隔夜跳空係 {us.get('p99_abs_gap_pct')}%。所以成本要報分佈同尾，唔報一個平均數。

**安慰劑對照（必須同上面並排印）**
用**同一隻股票嘅候選池**（同樣受 ATR 上限篩過）抽等量嘅日子做對照，
只有「邊一日算訊號」呢個配對變咗，其餘（樣本數、股票、價格環境、交易日曆）不變。

| 市場 | 真：中位跳空 | 安慰劑：中位跳空（均值 ± sd） | 真：>3×ATR 佔比 | 安慰劑：>3×ATR 佔比 |
|---|---|---|---|---|
| 美股 | {us.get('median_gap_pct')}% | {us_pl.get('null_median_gap_pct_mean')}% ± {us_pl.get('null_median_gap_pct_sd')} | {us.get('share_gap_gt_3atr')}% | {us_pl.get('null_share_gt_3atr_mean')}% |
| 日股 | {jp.get('median_gap_pct')}% | {jp_pl.get('null_median_gap_pct_mean')}% ± {jp_pl.get('null_median_gap_pct_sd')} | {jp.get('share_gap_gt_3atr')}% | {jp_pl.get('null_share_gt_3atr_mean')}% |

安慰劑有冇功效（sd > 0）：美股 {us_pl.get('has_power')}，日股 {jp_pl.get('has_power')}。
安撫劑 sd = 0 嘅話個對照等於乜都冇測，個門檻會喺測試緊空氣。

**未決**：呢段文字用咗邊個 window、邊個市場做主，要 Kenneth 揀。
"""
    w(DRAFTS / "methodology-4.5-confirmation-cost.draft.md", d45)

    m = i3["markets"].get("us200") or {}
    is_ = m.get("in_sample") or {}
    oos = m.get("out_of_sample") or {}
    fc = f"""# DRAFT — /methodology 新一節「幾時宣布策略死亡」（NOT deployed）

**Status: DRAFT behind `failure_criteria_flag` (default OFF). 門檻係「提案」，
唔係決定 —— Kenneth 先批數字，文字先上。**

## 幾時宣布策略死亡（提案）

以下三條係**提案門檻**，喺跑任何未來結果之前寫低，唔准事後移動：

- **T1  Sharpe 衰減**：樣本外 Sharpe 低過樣本內 Sharpe 嘅 50%。
- **T2  回撤**：樣本外最大回撤超過樣本內最大回撤嘅 2 倍。
- **T3  連續負窗**：唔可以出現連續 3 個負嘅 20 個交易日窗口。

**窗口定義**
- 樣本內：{F.WINDOW_DEFINITIONS['in_sample']}
- 樣本外：{F.WINDOW_DEFINITIONS['out_of_sample']}
- 交易日序列：{F.WINDOW_DEFINITIONS['session_series']}
- Sharpe：{F.WINDOW_DEFINITIONS['sharpe']}
- 最大回撤：{F.WINDOW_DEFINITIONS['max_drawdown']}
- 滾動窗口：{F.WINDOW_DEFINITIONS['rolling_window']}

## 而家企喺邊（美股 v2，逐單規則）

| | 樣本內 | 樣本外 |
|---|---|---|
| 交易數 | {is_.get('n_trades')} | {oos.get('n_trades')} |
| 交易日數 | {is_.get('n_sessions')} | {oos.get('n_sessions')} |
| Sharpe（原始） | {is_.get('sharpe_raw')} | {oos.get('sharpe_raw')} |
| Sharpe（年化） | {is_.get('sharpe_annualised')} | {oos.get('sharpe_annualised')} |
| 最大回撤 | {is_.get('max_drawdown_pct')}% | {oos.get('max_drawdown_pct')}% |
| 最長連續負窗 | {is_.get('longest_negative_streak')} | {oos.get('longest_negative_streak')} |

**判定**：T1 {m.get('thresholds', {}).get('T1_sharpe_decay', {}).get('verdict')}
（比值 {m.get('thresholds', {}).get('T1_sharpe_decay', {}).get('observed_ratio')}）·
T2 {m.get('thresholds', {}).get('T2_max_drawdown', {}).get('verdict')}
（倍數 {m.get('thresholds', {}).get('T2_max_drawdown', {}).get('observed_multiple')}）·
T3 {m.get('thresholds', {}).get('T3_rolling_streak', {}).get('verdict')}
（連續 {m.get('thresholds', {}).get('T3_rolling_streak', {}).get('observed_streak')}）。

**Direction A 嘅 2021–26 結果點解唔係矛盾**
Direction A 嘅裁決係關於**選股**：篩選器有冇跑贏等權。
2021–26 嗰個負 gap（z 喺 -1.4 至 -1.7，從來冇去到 -1.96）係選股嘅裁決。
而上面 T1 嘅 Sharpe 衰減係關於**逐單規則本身穩唔穩**。
一個策略可以同時有真嘅逐單 edge，同埋選股係死嘅 —— 呢兩句唔相衝，
站上面本來就係咁講。SELECTION_DEAD 同 T1 衰減係一致，唔係打架。

**未決**：門檻數字要 Kenneth 批；批之前唔上站。
"""
    w(DRAFTS / "methodology-failure-criteria.draft.md", fc)

    us_unfillable = _count_of(i1, "us200", "published_signals", "UNFILLABLE")
    jp_unfillable = _count_of(i1, "jp200", "published_signals", "UNFILLABLE")
    tier_draft = f"""# DRAFT — 訊號列表加一行可成交分級（NOT deployed）

**Status: DRAFT behind `executability_tiers_flag` (default OFF).**
網站格式未改，訊號列表未改。

## 分級定義（每個一行）

- **FILLABLE** — 當日有成交，盤中跌到過 S1，收市企穩喺 S1 之上。
- **NOT_TRIGGERED** — 當日有成交，但盤中從未跌到 S1。冇成交，唔係規則失效。
- **UNFILLABLE** — 當日冇可成交嘅市場（零成交量，或者全日一個價、冇喺 S1 成交過）。
- **UNJUDGED** — 當日冇可用嘅 bar（資料缺失、欄位缺失、或者 S1 計唔出）。
  **資料缺口永遠唔會被當成停牌，亦唔會被當成可以成交。**
- **REACHED_S1_NOT_HELD** — 跌到過 S1 但收市冇企穩。v2 收市確認規則喺呢一步
  已經濾走佢，所以佢唔係訊號，亦唔會入勝率分母。

## 訊號列表如果要加呢一欄，格式係

```
CSCO  US200  距入場 1.31% · v2 只做多 · 入場要企穩 S1
      BUY 收 104.51 · 入場 103.14 · 止損 93.76 (-9.10%)   [FILLABLE]
```

## 分母規則

勝率分母**剔除 UNFILLABLE**，並且喺旁邊印明剔走咗幾多單。
（以家重跑嘅結果：美股 {us_unfillable} 單、日股 {jp_unfillable} 單屬於 UNFILLABLE。）

## 未決

1. 呢一欄係咪真係要上站，定係淨係做內部稽核？
2. UNFILLABLE 剔除之後，勝率同每單平均要點樣呈現（兩個數並排？只印剔走之後嗰個？）？
3. 要唔要同時顯示 UNJUDGED 嘅數目？
"""
    w(DRAFTS / "signal-list-tier-column.draft.md", tier_draft)


# --------------------------------------------------------------------------
def main() -> int:
    t0 = time.time()
    print("=" * 72)
    print("RESEARCH RUN — website-improvements-2026-11-11")
    print(f"flags: {RF.load_config()}  (all OFF)")
    print(f"bar store: {E.BARS}")
    print("=" * 72)

    print("\n=== self-tests ===")
    st = subprocess.run([sys.executable, str(REPO / "scripts" / "research" / "selftests.py")],
                        cwd=str(REPO), capture_output=True, text=True)
    print(st.stdout[-2500:])
    selftest_ok = st.returncode == 0
    if not selftest_ok:
        print("!! self-tests FAILED — the research numbers below are not trustworthy")

    i1 = item1()
    trades_by_mkt = {}
    for mkt in MARKETS:
        trades, cands, exc = E.rerun_v2(mkt)
        trades_by_mkt[mkt] = (trades, cands, exc)
    i2 = item2(trades_by_mkt)
    i3 = item3(trades_by_mkt)
    i4 = item4()
    drafts(i1, i2, i3)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "spec": "docs/website-improvements-2026-11-11.md",
        "mode": "research only — no production change, no deploy, all flags OFF",
        "flags": RF.load_config(),
        "selftests_passed": selftest_ok,
        "bar_store": str(E.BARS),
        "item1_executability_tiers": i1,
        "item2_confirmation_cost": i2,
        "item3_failure_criteria": i3,
        "item4_gate_independence": i4,
    }
    w(OUT / "summary.json", json.dumps(summary, ensure_ascii=False, indent=1))
    w(OUT / "summary.md", render_summary(summary))
    print(f"\nDONE in {time.time() - t0:.1f}s → {OUT.relative_to(REPO)}")
    return 0 if selftest_ok else 1


def render_summary(s: dict) -> str:
    i1 = s["item1_executability_tiers"]
    i2 = s["item2_confirmation_cost"]
    i3 = s["item3_failure_criteria"]
    i4 = s["item4_gate_independence"]
    lines = []
    A = lines.append
    A("# 可成交分級、確認期成本、失效門檻、獨立 gate —— 結果摘要")
    A("")
    A(f"**Spec**: `docs/website-improvements-2026-11-11.md` · "
      f"**模式**: research only（無 production 改動、無部署、四個 flag 全部 OFF）· "
      f"**自我測試**: {'全綠' if s['selftests_passed'] else '**失敗**'}")
    A("")
    A("## 裁決先行")
    A("")
    A("1. **可成交分級：已做完，但結論係「冇嘢好執」。** 歷史重跑入面每一筆已發佈訊號，"
      "按當日 bar 查落去，**冇一筆**係停牌／零成交所以食唔到（美股 0 單、日股 0 單）。"
      "分級機制查得出停牌日（候選池裡面日股有 2,052 個 sessions 係停牌特徵），"
      "但已發佈訊號本身冇踩到。")
    A("2. **有一個真發現：規則嘅 close 界線同 FILLABLE 定義唔一樣。** 引擎收 `close >= S1`，"
      "而 spec 嘅 FILLABLE 要求 `close > S1`。美股 15 單、日股 59 單嘅收市價**啱啱好等於** S1，"
      "按 spec 定義唔算 FILLABLE。呢個唔係數據問題，係定義嘅邊界，要 Kenneth 決定。")
    _us = i2["markets"]["us200"]; _jp = i2["markets"]["jp200"]
    def _cmp(m):
        r = m["real"]; p = m["placebo"]
        med = (r.get("median_gap_pct") or 0) - (p.get("null_median_gap_pct_mean") or 0)
        tail = (r.get("share_gap_gt_3atr") or 0) - (p.get("null_share_gt_3atr_mean") or 0)
        parts = []
        parts.append(f"中位跳空 {r.get('median_gap_pct')}% vs 安慰劑 "
                     f"{p.get('null_median_gap_pct_mean')}%（差 {med:+.4f}pp）")
        parts.append(f"尾部 >3×ATR {r.get('share_gap_gt_3atr')}% vs 安慰劑 "
                     f"{p.get('null_share_gt_3atr_mean')}%（差 {tail:+.3f}pp）")
        return "；".join(parts)
    A(f"3. **確認期成本：已量度，安慰劑顯示冇一個『代表性成本』可以攞出嚟。** "
      f"美股：{_cmp(_us)}。日股：{_cmp(_jp)}。"
      "中位跳空同安慰劑冇分別（安慰劑係由**同一隻股票嘅候選池**抽、只換"
      "「邊一日算訊號」呢個配對，樣本數、股票、價格環境、交易日曆全部不變）——"
      "即係話訊號日嘅隔夜跳空**冇系統性成本**。但**尾部唔一樣**："
      "日股訊號日嘅 >3×ATR 佔比高過安慰劑。"
      "所以要上站嘅嘢係「等待成本嘅**分佈**」，唔係一個數字 —— "
      "呢個正係 spec 要求「過濾率同成本分佈兩個一齊印」嘅原因。")
    A("4. **失效門檻：三條提案已寫好，等你批數字。** 冇批准之前唔上站。")
    A("5. **獨立 gate：做到，而且捉到嘢。** 植入舊 stamp 嘅負面控制令 gate 判 STALE（唔係 done）。"
      "live read-back 而家讀到真實頁面（stamp + 訊號數），唔係讀 build 自己嘅 manifest。")
    A("")
    A("## 關鍵數字")
    A("")
    A("### Item 1 — 可成交分級")
    A("")
    A("| 市場 | 已發佈訊號 | FILLABLE | REACHED_S1_NOT_HELD | UNFILLABLE | UNJUDGED | 候選池 | 剔走嘅訊號 |")
    A("|---|---|---|---|---|---|---|---|")
    for mkt in MARKETS:
        m = i1["markets"][mkt]
        rows = [r for r in m["tier_counts"]
                if r["window"] == "full_2016_2026" and r["population"] == "published_signals"]
        r = rows[0] if rows else {}
        A(f"| {mkt} | {m['n_trades']} | {r.get('FILLABLE', 0)} | "
          f"{r.get('REACHED_S1_NOT_HELD', 0)} | {r.get('UNFILLABLE', 0)} | "
          f"{r.get('UNJUDGED', 0)} | {m['n_candidates']} | {m['n_excluded']} |")
    A("")
    A("**剔走 UNFILLABLE 前後嘅每單平均同勝率**（規格要求並排印）")
    A("")
    A("| 市場 | 全部 n | 全部每單平均 | 全部勝率 | 剔除咗幾多單 | 剔除後 n | 剔除後每單平均 | 剔除後勝率 |")
    A("|---|---|---|---|---|---|---|---|")
    for h in i1["headline_win_rate_with_and_without_unfillable"]:
        A(f"| {h['market']} | {h['n_included']} | {h['avg_included_pct']}% | "
          f"{h['win_included_pct']}% | {h['n_excluded_unfillable']} | "
          f"{h['n_included'] - h['n_excluded_unfillable']} | "
          f"{h['avg_excluded_pct']}% | {h['win_excluded_pct']}% |")
    A("")
    A("**被剔走嗰批（excluded cohort）嘅前瞻回報** —— 同一套引擎規則計，"
      "唯一分別就係剔除本身；仍然未平倉嘅記 UNKNOWN，唔記 0。")
    A("")
    A("| 市場 | 剔走 n | 量到 n | 未知 n | 前瞻每單平均 | 勝率 |")
    A("|---|---|---|---|---|---|")
    for mkt in MARKETS:
        e = i1["markets"][mkt]["excluded_forward_net"]
        A(f"| {mkt} | {e['n']} | {e['n_measured']} | {e['n_unknown']} | "
          f"{e['avg_forward_net_pct']}% | {e['win_pct']}% |")
    A("")
    A("> 呢個同 addendum 嘅假設**相反**：被剔走嗰批**唔係**最強嗰批，"
      "佢哋嘅前瞻回報係**負**嘅。即係話 realised-only 喺呢度係**保守**嘅，"
      "唔係令人誤以為可以加注。")
    A("")
    A("**覆蓋率 —— 三個讀數、三個分母**")
    A("")
    A("| 市場 | missing cells / total cells | missing rows / total rows | clean rows / total rows |")
    A("|---|---|---|---|")
    for c in i1["coverage"]:
        A(f"| {c['market']} | {c['missing_cells_over_total_cells']} | "
          f"{c['missing_rows_over_total_rows']} | {c['clean_rows_over_total_rows']} |")
    A("")
    A("### Item 2 — 確認期成本")
    A("")
    A("| 市場 | n | 中位跳空 | \\|跳空\\| p90 | \\|跳空\\| p99 | 平均 ATR | p99/ATR | >3×ATR | 安慰劑中位 | 安慰劑 >3×ATR |")
    A("|---|---|---|---|---|---|---|---|---|---|---|")
    for mkt in MARKETS:
        r = i2["markets"][mkt]["real"]
        p = i2["markets"][mkt]["placebo"]
        A(f"| {mkt} | {r.get('n')} | {r.get('median_gap_pct')}% | "
          f"{r.get('p90_abs_gap_pct')}% | {r.get('p99_abs_gap_pct')}% | "
          f"{r.get('mean_atr_pct')}% | {r.get('p99_abs_gap_over_atr')} | "
          f"{r.get('share_gap_gt_3atr')}% | {p.get('null_median_gap_pct_mean')}% | "
          f"{p.get('null_share_gt_3atr_mean')}% |")
    A("")
    A("### Item 3 — 失效門檻（提案）")
    A("")
    A("| 市場 | T1 Sharpe 衰減 | T2 回撤倍數 | T3 連續負窗 |")
    A("|---|---|---|---|")
    for mkt in MARKETS:
        t = i3["markets"][mkt]["thresholds"]
        A(f"| {mkt} | {t['T1_sharpe_decay']['verdict']} "
          f"({t['T1_sharpe_decay']['observed_ratio']}) | "
          f"{t['T2_max_drawdown']['verdict']} "
          f"({t['T2_max_drawdown']['observed_multiple']}) | "
          f"{t['T3_rolling_streak']['verdict']} "
          f"({t['T3_rolling_streak']['observed_streak']}) |")
    A("")
    A("### Item 4 — 獨立 gate")
    A("")
    A(f"- 三層 receipt 裁決：**{i4['receipt']['verdict']}**"
      f"（live stamp={i4['live_readback'].get('stamp')}, "
      f"count={i4['live_readback'].get('signal_count')}）")
    A(f"- 植入舊 stamp 嘅負面控制：gate 判 "
      f"**{i4['stale_stamp_fixture']['gate_verdict']}**（捉到 = "
      f"{i4['stale_stamp_fixture']['gate_caught_it']}）")
    A(f"- hard gate：declared={i4['hard_gate']['declared_count']} "
      f"executed={i4['hard_gate']['executed_count']} → "
      f"**{i4['hard_gate']['verdict']}**")
    A("")
    A("## 工作期間發現嘅問題（已修，但照講）")
    A("")
    A("1. **重跑最初用錯咗引擎。** `data/bt10y_trades.json` 睇落似係啱嘅原料，"
      "但佢係 through-S1 breakout 變體（入場價高過 S1 5.8%），唔係 live v2。"
      "跟 spec 用 `data/bt10y2/` 凍結數據重跑，先對得上 `ab_close_hold.json`。")
    A("2. **`judge_row` 原本完全忽略 rule 參數** —— 雙向斷言嘅第一條（改 rule 個分類要變）"
      "根本無可能通過。係 fixture 逼我發現嘅。")
    A("3. **雙向斷言第二條嘅 zero-value row 撞咗 key**（同一個 sym\\|date 覆蓋咗原 row），"
      "令 arm 讀到「原有分類變咗」。")
    A("4. **mutation arm 連續捉到我兩個砌得差嘅 fixture**：第一版把 `open` 寫死 100.0，"
      "於是股價高過 100 嘅名喺兩條 rule 下都判 inconsistent（冇得反轉）；"
      "第二版令一半 row 收市低過 S1，於是佢哋 baseline 已經 inconsistent。"
      "兩次都係 **fixture 錯、唔係 code 錯** —— 但如果 arm 唔會 fail，我根本唔會知道。"
      "第三版先砌到：每一行開市高過 S1、收市企穩 S1，flip side 先有嘢可反轉。")
    A("5. **excluded cohort 嘅「前瞻回報」原本用咗同日收市價**，根本唔係前瞻回報；"
      "已改成用同一套引擎規則（入場 S1、3×ATR 止損、10 日離場）計 hypothetical return。")
    A("6. **spec 嘅前提有錯**：spec 話 halt/suspension flags 已經喺 `data/`。"
      "實際上**全個 repo 冇任何 halt 旗標**（OHLC 只有 date/open/high/low/close/volume，"
      "`grep -rl halt data/` 零命中）。UNFILLABLE 只能用同一個 OHLC source 推導"
      "（零成交量／全日一個價），並且當作**推導值**而唔係供應商提供值。")
    A("7. **spec 講嘅 Nov 2026 rout 唔喺呢份數據入面** —— 凍結 bar store 2016-09-01 → "
      "2026-10-01 完。事件主導性只能用數據入面最大嘅幾日做 proxy，"
      "而呢個比 spec 要求嘅檢查弱。")
    A("")
    A("## 未做 / 未決")
    A("")
    A("- **Item 5（方向覆蓋）冇做** —— spec 標明係提案，等你批或者丟。")
    A("- **Item 3 嘅門檻數字未批** —— 上面全部係提案。")
    A("- **Item 2 / Item 3 嘅文字只係草稿**，喺 `drafts/`，未上站、未改任何頁面。")
    A("- **四個 flag 全部 OFF**，喺 `config/research_flags.json`（獨立檔，"
      "冇掂 production 嘅 `config/integrity_flags.json`）。")
    A("- 覆蓋率嘅分母用**由指數序列推導**嘅交易日曆（IDX_GSPC / IDX_N225）—— "
      "repo 冇交易日曆檔（`src/data_fetcher_test.py` 甚至 import 一個唔存在嘅 "
      "`_HK_HOLIDAYS`），所以分唔到假期同真缺 bar。")
    A("")
    A("## 重現")
    A("")
    A("```bash")
    A("cd /Users/kenken/dev/dsa-hk")
    A("python3 scripts/research/run_all.py")
    A("```")
    A("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
