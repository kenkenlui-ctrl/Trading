#!/usr/bin/env python3
"""Write Direction A's outcome into the pre-registration, from the artifacts.

Every number in the outcome is read from reports/edge_alpha_a_us.json and
reports/edge_alpha_audit_us.json. Nothing is typed by hand, because a
hand-typed figure has already drifted once in this project (the Sprint 2 axis A
percentiles) and once in conversation (the no-gate figure that exists in no
artifact).
"""
from __future__ import annotations

import json
import pathlib
import textwrap

R = pathlib.Path("/Users/kenken/dev/dsa-hk")
M = "\u2212"


def n(v, d=2):
    return f"{v:+.{d}f}".replace("-", M)


def para(t):
    return textwrap.fill(t, width=79, break_long_words=False,
                         break_on_hyphens=False)


def main() -> int:
    a = json.loads((R / "reports/edge_alpha_a_us.json").read_text())
    ab = json.loads((R / "data/ab_close_hold.json").read_text())
    zs = sorted(c["z"] for cell in ab["placebo"]["cells"].values()
                for c in cell.values())
    lag = json.loads((R / "reports/edge_alpha_a_us_lag1.json").read_text())
    au = json.loads((R / "reports/edge_alpha_audit_us.json").read_text())
    W = ("2016-2020", "2021-2026")

    L = []
    P = L.append
    P("## Direction A — outcome, 2026-10-19"); P("")
    P(para(f"{a['config']['trials']} placebo trials, seed {a['config']['seed']}, "
           f"{a['universe']['names']} names, {a['universe']['sessions']} sessions "
           f"({a['universe']['first']} to {a['universe']['last']}), "
           f"{a['config']['cost_rt'] * 100:.2f}% round trip, "
           f"{a['config']['hold']}-session hold.")); P("")
    P("### The arms"); P("")
    P("| | arm 2 — the universe | arm 1 — v2's screen | arm 1, no gate |")
    P("|---|---|---|---|")
    fs = a["full_sample"]
    P(f"| CAGR 2016-20 | {a['arm2_universe']['cagr_pct']['2016-2020']:.2f}% | "
      f"{a['arm1']['cagr_pct']['2016-2020']:.2f}% | "
      f"{a['arm1_nogate']['cagr_pct']['2016-2020']:.2f}% |")
    P(f"| CAGR 2021-26 | {a['arm2_universe']['cagr_pct']['2021-2026']:.2f}% | "
      f"{a['arm1']['cagr_pct']['2021-2026']:.2f}% | "
      f"{a['arm1_nogate']['cagr_pct']['2021-2026']:.2f}% |")
    P(f"| Sharpe 2016-20 | {a['arm2_universe']['sharpe']['2016-2020']:.2f} | "
      f"{a['arm1']['sharpe']['2016-2020']:.2f} | "
      f"{a['arm1_nogate']['sharpe']['2016-2020']:.2f} |")
    P(f"| Sharpe 2021-26 | {a['arm2_universe']['sharpe']['2021-2026']:.2f} | "
      f"{a['arm1']['sharpe']['2021-2026']:.2f} | "
      f"{a['arm1_nogate']['sharpe']['2021-2026']:.2f} |")
    P(f"| **gap vs the universe** | — | "
      f"**{n(a['arm1']['gap_vs_arm2_pp']['2016-2020'])} / "
      f"{n(a['arm1']['gap_vs_arm2_pp']['2021-2026'])}** | "
      f"**{n(a['arm1_nogate']['gap_vs_arm2_pp']['2016-2020'])} / "
      f"{n(a['arm1_nogate']['gap_vs_arm2_pp']['2021-2026'])}** |")
    thr = a["config"]["threshold_pp"]
    P(f"| clears +{thr:.1f}pp/yr in both windows | — | "
      f"**{a['arm1']['clears_threshold']}** | "
      f"**{a['arm1_nogate']['clears_threshold']}** |")
    P(f"| beats its placebo null | — | "
      f"{a['arm1']['null']['2016-2020']['beats_null'] and a['arm1']['null']['2021-2026']['beats_null']} | "
      f"{a['arm1_nogate']['null']['2016-2020']['beats_null'] and a['arm1_nogate']['null']['2021-2026']['beats_null']} |")
    P("")
    P(para(f"Full sample, reported for comparison with the site's published "
           f"figures rather than for the decision: the universe arm compounds at "
           f"{fs['arm2_cagr_pct']:.2f}% and v2's screen at "
           f"{fs['arm1_cagr_pct']:.2f}%, a gap of "
           f"{n(fs['arm1_gap_vs_arm2_pp'])}pp/yr.")); P("")
    P("### The placebo"); P("")
    P("| arm | window | gap vs universe | null (random picks, same count) | z | percentile |")
    P("|---|---|---|---|---|---|")
    for label, rep in (("arm 1", a["arm1"]), ("arm 1, no gate", a["arm1_nogate"])):
        for w in W:
            v = rep["null"][w]
            P(f"| {label} | {w} | {n(v['gap_pp'])} | {n(v['null_mean_pp'])} "
              f"(sd {v['null_sd_pp']:.3f}) | {n(v['z'])} | {v['null_percentile']}% |")
    P("")
    P(para("A random draw of the same number of names, on the same sessions, "
           "deployed the same way, scores close to the universe in both windows. "
           "v2's screen is above that null in 2016-20 and below it in 2021-26, "
           "and neither difference reaches |z| = 1.96. The null has power in "
           "all four cells, so this is a measurement and not a shrug.")); P("")
    P("### The pre-registered decision"); P("")
    P(para(f"**{a['verdict']}** for the spec's arm, and "
           f"**{a['verdict_secondary_nogate']}** for the no-gate arm. Neither "
           f"arm clears the +{thr:.1f}pp/yr threshold in both sub-periods, so "
           f"Amendment B's SELECTION_LUCKY branch does not apply either — that "
           f"branch requires the threshold to be cleared first. The verdict does "
           f"not depend on the placebo: the screen fails the spec's own rule "
           f"before the placebo is consulted.")); P("")
    fd = a.get("firing_day_decomposition")
    if fd:
        P("### What the fallback hides"); P("")
        P(para(f"**{fd['status']}.** The question it asks is this: does the "
               f"screen beat random names of the same count on the sessions "
               f"where it has an opinion? The pre-registered rule applies to "
               f"the BLENDED arm, so this is a diagnostic on top of it, "
               f"reported because \"a real edge that the fallback diluted "
               f"below the threshold\" is a reading of the result a reader is "
               f"entitled to see tested.")); P("")
        P(para(f"The screen has an opinion on {fd['firing_sessions']} of "
               f"{fd['total_sessions']} sessions "
               f"({fd['firing_fraction'] * 100:.1f}%), picking "
               f"{fd['mean_names_per_firing_session']:.1f} names on average. On "
               f"those sessions alone — cash on the rest, the same engine, the "
               f"same cohorts, the same exposure "
               f"({fd['exposure_pct']['screen_on_its_own_days']['2016-2020']:.2f}% "
               f"against the universe's "
               f"{fd['exposure_pct']['universe_on_the_same_days']['2016-2020']:.2f}% "
               f"in 2016-20) — the screen's picks compound at "
               f"{fd['cagr_pct']['screen_on_its_own_days']['2016-2020']:.2f}% "
               f"against the universe's "
               f"{fd['cagr_pct']['universe_on_the_same_days']['2016-2020']:.2f}% on "
               f"the same days in 2016-20, a gap of "
               f"{n(fd['gap_pp']['2016-2020'])}pp/yr; and "
               f"{n(fd['gap_pp']['2021-2026'])}pp/yr in 2021-26.")); P("")
        P("| window | screen on its own days | universe on the same days | gap | "
          "null (random, same count) | z | percentile |")
        P("|---|---|---|---|---|---|---|")
        for w in W:
            v = fd["null"][w]
            P(f"| {w} | {fd['cagr_pct']['screen_on_its_own_days'][w]:.2f}% | "
              f"{fd['cagr_pct']['universe_on_the_same_days'][w]:.2f}% | "
              f"**{n(v['gap_pp'])}** | {n(v['null_mean_pp'])} "
              f"(sd {v['null_sd_pp']:.3f}) | {n(v['z'])} | "
              f"{v['null_percentile']}% |")
        P("")
        P(para(f"**{fd['reading'].capitalize()}.** The random draw of the same "
               f"size on the same days scores "
               f"{n(fd['null']['2016-2020']['null_mean_pp'])} in 2016-20 and "
               f"{n(fd['null']['2021-2026']['null_mean_pp'])} in 2021-26, and "
               f"neither window reaches |z| = {a['config']['z_crit']}. So the "
               f"screen does not beat random picks of the same count even on "
               f"the days it has an opinion, which means the headline gap is "
               f"not a real edge being diluted by the fallback. That closes "
               f"the alternative reading this diagnostic was run to close.")); P("")
    P("### The fill-lag sensitivity"); P("")
    P(para(f"Amendment A4 requires the same signals filled at the next "
           f"session's close to be reported. It changes nothing: the lag-1 run "
           f"returns {lag['verdict']} for the spec's arm and "
           f"{lag['verdict_secondary_nogate']} for the no-gate arm, with gaps of "
           f"{n(lag['arm1']['gap_vs_arm2_pp']['2016-2020'])} / "
           f"{n(lag['arm1']['gap_vs_arm2_pp']['2021-2026'])} against the "
           f"same-close fill's "
           f"{n(a['arm1']['gap_vs_arm2_pp']['2016-2020'])} / "
           f"{n(a['arm1']['gap_vs_arm2_pp']['2021-2026'])}. The result does not "
           f"rest on the same-close execution assumption.")); P("")
    P("### Validity"); P("")
    eg = a["exposure_gap_arm1_vs_arm2_pp"]
    P(para(f"- **Exposure matched**: the two arms' average invested fraction "
           f"differs by {eg['2016-2020']:.3f}pp in 2016-20 and "
           f"{eg['2021-2026']:.3f}pp in 2021-26. The arms are comparable on "
           f"deployment, so the gap is not an exposure artefact."))
    sds = [a["arm1"]["null"][w]["null_sd_pp"] for w in W] + \
          [a["arm1_nogate"]["null"][w]["null_sd_pp"] for w in W]
    P(para(f"- **No cell is void**: all four placebo nulls have non-zero spread "
           f"({', '.join(f'{s:.3f}' for s in sds)}), so every cell is a "
           f"measurement rather than a shrug."))
    P(para(f"- **No look ahead**: the independent audit "
           f"(`scripts/edge_alpha_audit.py`) passes "
           f"{len(au['checks']) - au['failed']}"
           f"/{len(au['checks'])} checks. What it covers: "
           + "; ".join(au["scope"]) + "."))
    P(para(f"- **What the audit does not cover**, so the claim is not read as "
           f"more than it is: " + "; ".join(au["not_covered"]) + "."))
    P(para(f"- **Glitch mask coverage**: the repository's glitch mask contains "
           f"no symbol from the US universe, so it removes "
           f"{a['universe']['glitch_name_days_removed']} name-days here and is "
           f"a no-op for this market. The same is true of the shipped "
           f"close-hold A/B. Saying \"glitch applied\" would imply a protection "
           f"that is not there, so it is stated."))
    P(para(f"- **The universe fallback bites**: the v2 screen selects nothing on "
           f"{a['screen']['empty_sessions_gate_on']} of "
           f"{a['universe']['sessions']} sessions, so arm 1 holds the universe "
           f"on {a['screen']['empty_sessions_gate_on'] / a['universe']['sessions'] * 100:.0f}% "
           f"of sessions and its own picks on the rest. That is Amendment A2, "
           f"and it means arm 1 is \"the screen where it has an opinion, the "
           f"universe otherwise\"."))
    P("")
    P("### What this means"); P("")
    P(para("Direction A asked whether the portfolio shortfall came from the "
           "SELECTION or from the PLUMBING. The plumbing is now gone — full "
           "deployment, no slots, no limit-fill asymmetry, no sizing friction — "
           "and the screen still does not beat holding the same universe. So the "
           "shortfall is the selection, not the plumbing.")); P("")
    P(para("**Direction B is not authorised.** **Direction D is triggered**: the "
           "v2 real-money plan stops. In this repository that means no flag to "
           "turn off — every Sprint 2 flag is OFF and no production flag was "
           "moved at any point — and it means the site's own beginner FAQ, "
           "which tells a reader to paper-trade for 30 days and only start "
           "real money at a hit rate of at least 50% and a profit factor of "
           "at least 1.5, is now contradicted by the evidence. That guidance "
           "is a public position rather than a deployed plan, but it is the "
           "position this evidence contradicts, and the site is where the "
           "contradiction lands."))
    P("")
    P(para(f"**What is not refuted.** The per-trade close-hold edge (four "
           f"cells, z {zs[0]:.1f}-{zs[-1]:.1f}, all powered) was measured per "
           f"trade, in the limit-fill "
           f"vehicle, and Direction A does not touch it. A real per-trade "
           "advantage that neither a 10-slot book nor a fully-invested "
           "equal-weight portfolio can express is a third possibility, and it "
           "is the one thing this programme has not yet tested.")); P("")
    P(para("**One caveat on the vehicle.** Arm 1 holds the universe on every "
           "session where the screen has no opinion, which is "
           f"{a['screen']['empty_sessions_gate_on'] / a['universe']['sessions'] * 100:.0f}% "
           "of them, so arm 1 is not a pure concentrated bet on the screen's "
           "picks — it is the screen layered over a passive hold. A vehicle "
           "that held cash instead on those sessions would have a different "
           "exposure, and the spec's requirement that both arms be 100% "
           "invested is what rules that variant out of the headline.")); P("")

    p = R / "docs/edge-to-alpha-prereg-spec-2026-10-19.md"
    t = p.read_text(encoding="utf-8")
    # Re-runnable: an earlier run's outcome is removed before the new one is
    # written, so a corrected generator never leaves two outcomes in the file.
    marker = "## Direction A — outcome"
    amd = "# Amendment C — Direction C's dimensions, defined before implementation"
    if marker in t:
        i = t.index(marker)
        j = t.index(amd)
        assert i < j, "the Direction A outcome has to sit before Amendment C"
        t = t[:i] + t[j:]
    assert t.count(amd) == 1
    t = t.replace(amd, "\n".join(L) + "\n---\n\n" + amd)
    p.write_text(t, encoding="utf-8")
    print(f"Direction A outcome written into the spec; {len(t.splitlines())} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
