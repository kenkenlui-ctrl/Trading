#!/usr/bin/env python3
"""
vix_config.py — research-only switches for the VIX regime-filter study.

SPEC: docs/vix-regime-filter-test-spec.md

This file exists so the study can branch without any production code having
to change. Nothing under the daily build path imports it; the guard at the
bottom of scripts/self_test.py asserts exactly that.

THE FLAG IS OFF. Turning it on would do nothing at all, because the only
consumer is scripts/vix_regime_test.py, which is a research script. That is
the point: the spec requires a branch, and the cheapest correct branch is one
no build path can reach.
"""
from __future__ import annotations

# Master switch. OFF means the v2 signal generator behaves exactly as it does
# today — no VIX input, no regime label, no filtering.
vix_regime_filter: bool = False

# Market the study covers. The spec scopes this to Japan (non-goal: US/HK).
vix_market: str = "jp200"

# ---------------------------------------------------------------------------
# Which VIX form the filter would use. The spec forbids picking a winner by
# eyeballing, so the study reports all three and this constant only names
# which one the (off) filter branch would consult if it were ever enabled.
#
#   "level"      raw VIX close at T-1
#   "spike"      1-day change, z-scored over a trailing 60 observations
#   "percentile" rank of the T-1 level within its trailing N observations
# ---------------------------------------------------------------------------
vix_form: str = "percentile"

# Percentile window for vix_form == "percentile". The spec names both 20 and
# 60; the study evaluates both and does not choose here.
vix_percentile_window: int = 20

# Lookback for the spike z-score.
vix_spike_window: int = 60

# Feature lookback, in TRADING observations. T-1 discipline is structural:
# a signal that fills on date D may only use VIX from strictly before D,
# because the resting limit order that produced the fill had to be placed the
# evening before. Enforced in vix_regime_test.attach_vix, not here.
vix_t1_strict: bool = True