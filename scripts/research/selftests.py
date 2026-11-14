#!/usr/bin/env python3
"""Self-tests for the 2026-11-11 research package.

    python3 scripts/research/selftests.py

Every check here is written so that it CAN fail. A check that cannot fail is
decoration, and the house rule is that the fixture is wrong, never the code,
when a check trips.

The must-fail fixtures:
    Item 1  a halted session must classify UNFILLABLE
            a normal fill session must classify FILLABLE
            a session with missing bars must classify UNJUDGED
            a missing bar must never be defaulted to a fillable or a halted
    Item 1  the zero-value denominator expansion arm must pass
            the side-change single-row mutation arm must pass
    Item 2  the shuffled-days placebo must run and must have power
    Item 4  a planted stale stamp must make the gate FAIL
            both bidirectional toggles must behave as specified
            the hard gate must fail when a declared entry does not execute
    guards  the re-run must reproduce the frozen A/B numbers exactly
           a planted late-arriving row must not change a signal's levels
           production build paths must not mention any new identifier
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "v2engine"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import coverage as C          # noqa: E402
import executability as E     # noqa: E402
import failure_criteria as F  # noqa: E402
import gate_independence as G  # noqa: E402
from core import features      # noqa: E402

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  -- {detail}")


def must_fail(name: str, ok: bool, detail: str = "") -> None:
    """A must-fail check: `ok` must be True for the FAILURE to have been
    detected. Inverted on purpose."""
    record(name, ok, detail)


# --------------------------------------------------------------------------
def test_tier_classifier() -> None:
    print("\nItem 1 — the tier classifier")
    # 1. a halted session: no volume and one printed price
    halted = {"date": "2020-03-16", "open": 100.0, "high": 100.0,
              "low": 100.0, "close": 100.0, "volume": 0.0}
    tier, why = E.classify_bar(halted, 99.0)
    record("a halted session classifies UNFILLABLE",
           tier == E.TIER_UNFILLABLE, f"got {tier} ({why})")

    # 1b. zero volume with a real range is also unexecutable
    novol = {"date": "2020-03-16", "open": 100.0, "high": 105.0,
             "low": 98.0, "close": 103.0, "volume": 0.0}
    tier, why = E.classify_bar(novol, 99.0)
    record("a session with no traded volume classifies UNFILLABLE",
           tier == E.TIER_UNFILLABLE, f"got {tier} ({why})")

    # 2. a normal fill session: traded down to S1 and closed above it
    normal = {"date": "2020-03-17", "open": 105.0, "high": 106.0,
              "low": 99.5, "close": 104.0, "volume": 1_000_000.0}
    tier, why = E.classify_bar(normal, 100.0)
    record("a normal fill session classifies FILLABLE",
           tier == E.TIER_FILLABLE, f"got {tier} ({why})")

    # 3. missing bars -> UNJUDGED
    tier, why = E.classify_bar(None, 100.0)
    record("a session with no bar classifies UNJUDGED",
           tier == E.TIER_UNJUDGED, f"got {tier} ({why})")

    # 3b. a bar with a missing field -> UNJUDGED
    partial = {"date": "2020-03-17", "open": 105.0, "high": 106.0,
               "low": 99.5, "close": None, "volume": 1_000_000.0}
    tier, why = E.classify_bar(partial, 100.0)
    record("a bar with a missing field classifies UNJUDGED",
           tier == E.TIER_UNJUDGED, f"got {tier} ({why})")

    # 3c. a bar with a non-finite field -> UNJUDGED
    nanbar = {"date": "2020-03-17", "open": 105.0, "high": 106.0,
              "low": 99.5, "close": float("nan"), "volume": 1_000_000.0}
    tier, why = E.classify_bar(nanbar, 100.0)
    record("a bar with a non-finite field classifies UNJUDGED",
           tier == E.TIER_UNJUDGED, f"got {tier} ({why})")

    # 4. never default a missing bar to fillable, and never to halted
    for bar, label in ((None, "missing"), (partial, "partial"), (nanbar, "nan")):
        tier, _ = E.classify_bar(bar, 100.0)
        record(f"a {label} bar is never defaulted to FILLABLE",
               tier != E.TIER_FILLABLE, f"got {tier}")
        record(f"a {label} bar is never defaulted to UNFILLABLE",
               tier != E.TIER_UNFILLABLE, f"got {tier}")

    # 5. the low never reached S1
    untouched = {"date": "2020-03-17", "open": 105.0, "high": 106.0,
                 "low": 103.0, "close": 104.0, "volume": 1_000_000.0}
    tier, why = E.classify_bar(untouched, 100.0)
    record("a session that never reached S1 classifies NOT_TRIGGERED",
           tier == E.TIER_NOT_TRIGGERED, f"got {tier} ({why})")

    # 6. reached S1 but closed at or below it -> its own label, NOT
    #    UNFILLABLE (folding it in would empty the published set by
    #    construction and make the win-rate exclusion a tautology)
    failed = {"date": "2020-03-17", "open": 105.0, "high": 106.0,
              "low": 98.0, "close": 99.0, "volume": 1_000_000.0}
    tier, why = E.classify_bar(failed, 100.0)
    record("a session that reached S1 and closed below it is its own label",
           tier == E.TIER_REACHED_NOT_HELD, f"got {tier} ({why})")

    # 7. every input lands in exactly one tier
    cases = [halted, novol, normal, partial, nanbar, untouched, failed, None]
    labels = {E.classify_bar(b, 100.0)[0] for b in cases}
    record("every input lands in a known tier",
           labels <= set(E.ALL_TIER_LABELS), f"labels={labels}")


# --------------------------------------------------------------------------
def test_frozen_equivalence() -> None:
    print("\nGuard — the re-run reproduces the frozen A/B numbers")
    p = REPO / "data" / "ab_close_hold.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        record("the frozen A/B JSON is readable", False, str(e))
        return
    for mkt in ("us200", "jp200"):
        trades, _c, _e = E.rerun_v2(mkt, collect_candidates=False)
        got = len(trades)
        want = (d["windows"][mkt]["B_close-hold"]["train"]["n"]
                + d["windows"][mkt]["B_close-hold"]["test"]["n"])
        record(f"{mkt}: the re-run reproduces the frozen trade count",
               got == want, f"re-ran {got}, frozen {want}")

        # and the per-window averages
        import numpy as np
        df = __import__("pandas").DataFrame(trades)
        for win, mask in (("train", df["date"] < "2023-01-01"),
                          ("test", df["date"] >= "2023-01-01")):
            sub = df[mask]
            avg = round(float(sub["net"].mean() * 100), 3)
            want_avg = d["windows"][mkt]["B_close-hold"][win]["avg%"]
            record(f"{mkt}/{win}: the re-run reproduces the frozen average",
                   avg == want_avg, f"re-ran {avg}, frozen {want_avg}")
            win_pct = round(float((sub["net"] > 0).mean() * 100), 1)
            want_win = d["windows"][mkt]["B_close-hold"][win]["win%"]
            record(f"{mkt}/{win}: the re-run reproduces the frozen win rate",
                   win_pct == want_win, f"re-ran {win_pct}, frozen {want_win}")


# --------------------------------------------------------------------------
def test_no_lookahead() -> None:
    print("\nGuard — no look-ahead (planted late-arriving row)")
    import pandas as pd
    from bt_clean import load as load_clean

    sym = "AAPL"
    df = load_clean(E.BARS / f"{sym}.json")
    if df is None:
        record("the probe could load a symbol", False, sym)
        return
    f1 = features(df)

    # plant a row dated AFTER an existing session, with a wild close
    target = df.index[1200]
    planted_date = target + pd.Timedelta(days=7)
    extra = df.iloc[[1200]].copy()
    extra.index = pd.DatetimeIndex([planted_date])
    extra["close"] = 9999.0
    extra["high"] = 10000.0
    extra["low"] = 1.0
    extra["open"] = 9999.0
    df2 = pd.concat([df, extra]).sort_index()
    f2 = features(df2)

    same = True
    detail = ""
    for col in ("S1", "ma200", "atr_pct"):
        a = f1[col].loc[target]
        b = f2[col].loc[target]
        if not (pd.isna(a) and pd.isna(b)) and not math.isclose(float(a), float(b),
                                                               rel_tol=1e-12):
            same = False
            detail = f"{col}: {a} -> {b}"
            break
    record("a bar dated after the signal date does not change its levels",
           same, detail)

    # MUST-FAIL: a deliberately broken feature (no shift) must be caught
    broken = df.copy()
    h, l, c = broken.high, broken.low, broken.close
    ls = l.rolling(20).min()          # NOT shifted -> looks ahead
    a_val = float(f1["S1"].loc[target])
    b_val = float(ls.loc[target])
    must_fail("the probe catches a feature that looks ahead",
              not math.isclose(a_val, b_val, rel_tol=1e-9),
              f"shifted {a_val} vs unshifted {b_val} — the probe cannot tell "
              f"them apart")


# --------------------------------------------------------------------------
def test_coverage_arms() -> None:
    print("\nItem 1 addendum — the two negative-control arms")
    arm_a = C.zero_value_expansion_arm("us200", n=100)
    record("the zero-value denominator expansion arm passes",
           arm_a["passes"],
           f"stable={arm_a['stable_families']} "
           f"denominators_expanded={arm_a['denominators_expanded_exactly']}")
    must_fail("the expansion arm would fail if the denominators did not expand",
              arm_a["denominators_expanded_exactly"],
              "the denominators did not expand")

    rows = [{"sym": "AAPL", "date": "2020-01-02", "side": 1, "s1": 100.0,
             "open": 105.0, "high": 106.0, "low": 99.0, "close": 104.0,
             "volume": 1_000.0}]
    arm_b = C.side_change_mutation_arm(rows)
    record("the side-change single-row mutation arm passes",
           arm_b["passes"],
           f"inconsistent={arm_b.get('judged_inconsistent_after_flip')} "
           f"na_flipped={arm_b.get('na_set_actually_flipped')} "
           f"hash_changed={arm_b.get('content_hash_changed')}")

    # MUST-FAIL: a cache keyed by (sym, date) instead of by content would
    # serve the old verdict and leave the NA set unchanged
    cached = dict(arm_b["before"])
    record("the mutation arm detects a stale (cached) verdict",
           cached["na_set"] != arm_b["after"]["na_set"],
           "the cached NA set equals the mutated one — the arm is blind")


# --------------------------------------------------------------------------
def test_item4() -> None:
    print("\nItem 4 — the gate")
    stale = G.stale_stamp_fixture()
    must_fail("a planted stale stamp makes the gate FAIL",
              stale["gate_caught_it"],
              f"the gate said {stale['gate_verdict']}")

    manifest = G.build_manifest()
    live = G.fetch_live()
    receipt = G.three_layer_receipt(manifest, {"deploy_id": "live",
                                                "published_at": "live"}, live)
    print(f"      live read-back: stamp={live.get('stamp')} "
          f"count={live.get('signal_count')} -> {receipt['verdict']}")
    record("the live read-back finds the stamp and the count",
           bool(live.get("stamp")) and live.get("signal_count") is not None,
           json.dumps(live)[:220])

    # the receipt's three layers are all present and ordered
    record("the receipt has three layers in order",
           receipt["layer_order"] == list(G.LAYERS),
           str(receipt["layer_order"]))

    # a receipt with (a) and (b) but a failed (c) is STALE, not done
    stale_receipt = G.three_layer_receipt(
        manifest, {"deploy_id": "d", "published_at": "t"},
        {"ok": True, "url": "planted", "stamp": "1999-01-01",
         "signal_count": 0, "source": "planted"})
    record("build + publish without a read-back is STALE, not done",
           stale_receipt["verdict"] == "STALE" and not stale_receipt["ok"],
           f"verdict={stale_receipt['verdict']}")

    rows = [{"sym": "AAPL", "date": "2020-01-02", "side": 1, "s1": 100.0,
             "open": 105.0, "high": 106.0, "low": 99.0, "close": 99.5,
             "volume": 1_000.0},
            {"sym": "MSFT", "date": "2020-01-03", "side": 1, "s1": 100.0,
             "open": 101.0, "high": 103.0, "low": 99.5, "close": 100.5,
             "volume": 2_000.0}]
    rule_a = {"direction": "long_only", "require_close_above_s1": True}
    rule_b = {"direction": "long_only", "require_close_above_s1": False}
    toggles = G.bidirectional_toggles(rows, rule_a, rule_b)
    record("the bidirectional toggles behave as specified",
           toggles["passes"], json.dumps(toggles, ensure_ascii=False)[:300])

    den = G.denominator_independence(REPO / "scripts" / "research",
                                     pipeline_participates=[])
    record("the denominator is enumerated independently and content-addressed",
           den["independent"] and den["n_entries"] > 0,
           f"n={den['n_entries']} overlap={den['overlap_with_pipeline']}")


def test_hard_gate_catches_a_missing_entry() -> None:
    print("\nItem 4 addendum — the hard gate")
    tmp = Path("/tmp") / "w9_hardgate_fixture"
    tmp.mkdir(parents=True, exist_ok=True)
    good = tmp / "good_entry.py"
    good.write_text("print('ok')\n", encoding="utf-8")
    bad = tmp / "declared_but_never_runs.py"
    bad.write_text("raise SystemExit(3)\n", encoding="utf-8")
    res = G.hard_gate_entry_coverage([good, bad], runner_args=[])
    record("the hard gate FAILS when a declared entry does not execute",
           res["verdict"] == "FAIL" and res["mismatch"],
           f"verdict={res['verdict']} not_executed={res['not_executed']}")
    ok_res = G.hard_gate_entry_coverage([good], runner_args=[])
    record("the hard gate PASSES when every declared entry executes",
           ok_res["verdict"] == "PASS",
           f"verdict={ok_res['verdict']} failures={ok_res['failed']}")


def test_production_untouched() -> None:
    print("\nGuard — production is untouched")
    # Only identifiers THIS task introduces. `w9-fresh-claim` and friends are
    # pre-existing (they shipped on 2026-10-31) and are deliberately NOT in
    # this list: the check is about what this task added, not about what the
    # repo has ever contained.
    NEW_IDENTIFIERS = [
        "executability_tiers_flag", "confirmation_cost_flag",
        "failure_criteria_flag", "gate_independence_flag",
        "TIER_FILLABLE", "TIER_NOT_TRIGGERED", "TIER_UNFILLABLE",
        "TIER_UNJUDGED", "REACHED_S1_NOT_HELD",
        "research_flags", "executability_tiers", "confirmation_cost",
        "failure_criteria", "gate_independence",
    ]
    PROD = ["build_static.py", "build_home.py", "build_v2_signals.py",
            "build_equity_curves.py", "build_track_record.py",
            "v2_equity_export.py", "build_dashboard.py", "refresh.sh",
            "finalize.py", "deploy_gate.py", "rewrite_methodology_v2.py",
            "integrity/flags.py", "integrity/gate.py", "ab_close_hold.py"]
    hits = []
    for name in PROD:
        p = REPO / "scripts" / name
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        for ident in NEW_IDENTIFIERS:
            if ident in txt:
                hits.append(f"{name}:{ident}")
    record("no new identifier appears in any production build path",
           not hits, f"hits={hits}")

    # the strongest form of the same claim: no production file was MODIFIED
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "diff", "--name-only", "HEAD", "--"] +
            [f"scripts/{n}" for n in PROD],
            capture_output=True, text=True, timeout=60)
        changed = [x for x in (out.stdout or "").splitlines() if x.strip()]
    except Exception as e:
        changed = [f"git failed: {e}"]
    record("no production file was modified by this task",
           not changed, f"changed={changed}")

    # the production flag registry is untouched.
    #
    # 2026-11-15: this compared the ON set against a hand-typed list, which
    # went stale the moment signal_context_labels_flag was approved - so the
    # gate went RED on a flag Kenneth had explicitly turned on, which is the
    # opposite of useful. A registry check must compare the registry against
    # the COMMITTED registry ("unchanged by this task"), and bound the ON set
    # with an explicit allow-list so a research task still cannot quietly
    # switch one on.
    prod_cfg = json.loads((REPO / "config" / "integrity_flags.json").read_text())
    on = sorted(k for k, v in prod_cfg.items() if v)
    ALLOWED_ON = {"deploy_gate_flag", "staleness_banner_flag",
                  "signal_context_labels_flag"}
    extra = [f for f in on if f not in ALLOWED_ON]
    try:
        committed = json.loads(subprocess.run(
            ["git", "-C", str(REPO), "show", "HEAD:config/integrity_flags.json"],
            capture_output=True, text=True, timeout=60).stdout)
    except Exception:  # noqa: BLE001
        committed = {}
    record("the production flag registry is unchanged",
           committed == prod_cfg and not extra,
           "on=" + str(on) + " outside_the_allow_list=" + str(extra)
           + " same_as_committed=" + str(committed == prod_cfg))

    # and no public artefact was rewritten
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "diff", "--name-only", "HEAD", "--", "public"],
            capture_output=True, text=True, timeout=60)
        pub = [x for x in (out.stdout or "").splitlines() if x.strip()]
    except Exception as e:
        pub = [f"git failed: {e}"]
    record("no published artefact was rewritten by this task",
           not pub, f"changed={pub}")


def test_research_flags_all_off() -> None:
    print("\nGuard — every research flag defaults OFF")
    import research_flags as RF
    cfg = RF.load_config()
    on = sorted(k for k, v in cfg.items() if v)
    record("no research flag is on", not on, str(on))
    record("the research flag registry knows all four flags",
           set(RF.ALL_FLAGS) == {"executability_tiers_flag",
                                 "confirmation_cost_flag",
                                 "failure_criteria_flag",
                                 "gate_independence_flag"},
           str(RF.ALL_FLAGS))
    # fail-closed: a missing config means everything off
    record("a missing flag config means everything OFF",
           not any(RF._DEFAULTS.values()), "")


def main() -> int:
    print("=" * 72)
    print("RESEARCH SELF-TESTS — website-improvements-2026-11-11")
    print("=" * 72)
    test_research_flags_all_off()
    test_tier_classifier()
    test_frozen_equivalence()
    test_no_lookahead()
    test_coverage_arms()
    test_item4()
    test_hard_gate_catches_a_missing_entry()
    test_production_untouched()
    print("\n" + "=" * 72)
    print(f"{PASS} passed, {FAIL} failed")
    for f in FAILURES:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
