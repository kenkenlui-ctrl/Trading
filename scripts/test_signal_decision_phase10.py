#!/usr/bin/env python3
"""Unit tests for Phase 10 next-day long/short rules."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.signal_decision import (  # noqa: E402
    decide,
    next_day_long_score,
    next_day_short_score,
    next_day_bias,
    signal_score,
)


def test_gold_long():
    d = decide(
        llm_op="觀望",
        sentiment="中性",
        score_breakdown={"value_score": 78, "momentum_score": 35, "quality_score": 60, "order_flow_score": 50},
        data_snapshot={"change_pct": -0.8, "pe_ttm": 9.5, "last_price": 10},
    )
    assert d.op == "買入", d
    assert d.matched_rule == "GOLD_LONG", d


def test_gold_long_blocked_hsi_bear():
    d = decide(
        llm_op="觀望",
        sentiment="中性",
        score_breakdown={"value_score": 78, "momentum_score": 35, "quality_score": 60, "order_flow_score": 50},
        data_snapshot={"change_pct": -0.8, "pe_ttm": 9.5},
        hsi_yesterday_chg=-2.0,
    )
    assert d.op == "觀望"
    assert d.matched_rule == "HSI_REGIME"


def test_fade_short():
    d = decide(
        llm_op="買入",
        sentiment="樂觀",
        score_breakdown={"value_score": 30, "momentum_score": 72, "quality_score": 50, "order_flow_score": 60},
        data_snapshot={"change_pct": 3.5, "pe_ttm": 28},
    )
    assert d.op == "賣出", d
    assert d.matched_rule == "FADE_SHORT", d


def test_anti_knife_no_short():
    d = decide(
        llm_op="賣出",
        sentiment="悲觀",
        score_breakdown={"value_score": 50, "momentum_score": 40, "quality_score": 50, "order_flow_score": 40},
        data_snapshot={"change_pct": -4.0, "pe_ttm": 18},
    )
    assert d.op == "觀望"
    assert d.matched_rule == "ANTI-KNIFE"


def test_scores_rank():
    sb_long = {"value_score": 80, "momentum_score": 30, "quality_score": 55, "order_flow_score": 50}
    ds_long = {"change_pct": -1.0, "pe_ttm": 8}
    sb_short = {"value_score": 25, "momentum_score": 75, "quality_score": 40, "order_flow_score": 70}
    ds_short = {"change_pct": 3.8, "pe_ttm": 35}
    assert next_day_long_score(sb_long, ds_long, "悲觀") > next_day_long_score(sb_short, ds_short, "樂觀")
    assert next_day_short_score(sb_short, ds_short, "樂觀") > next_day_short_score(sb_long, ds_long, "悲觀")
    b = next_day_bias(sb_long, ds_long, "悲觀", "GOLD_LONG")
    assert b["long_score"] >= b["short_score"]
    assert b.get("direction_score", 0) >= 70


def test_signal_score_table():
    assert signal_score("GOLD_LONG", "買入") >= 80
    # FADE_SHORT is LOW on the bidirectional dial (near 0 = short)
    assert signal_score("FADE_SHORT", "賣出") <= 20


def test_direction_score_scale():
    from src.signal_decision import direction_score, direction_label, direction_action
    # GOLD long → high
    d_long = direction_score(
        {"value_score": 78, "momentum_score": 35, "quality_score": 60, "order_flow_score": 50},
        {"change_pct": -0.8, "pe_ttm": 9.5},
        "中性",
        "GOLD_LONG",
    )
    assert d_long >= 85, d_long
    assert direction_label(d_long) == "STRONG_LONG"
    assert direction_action(d_long) == "BUY"
    # FADE short → low
    d_short = direction_score(
        {"value_score": 30, "momentum_score": 72, "quality_score": 50, "order_flow_score": 60},
        {"change_pct": 3.5, "pe_ttm": 28},
        "樂觀",
        "FADE_SHORT",
    )
    assert d_short <= 15, d_short
    assert direction_label(d_short) == "STRONG_SHORT"
    assert direction_action(d_short) == "SHORT"
    assert d_long > d_short


if __name__ == "__main__":
    test_gold_long()
    test_gold_long_blocked_hsi_bear()
    test_fade_short()
    test_anti_knife_no_short()
    test_scores_rank()
    test_signal_score_table()
    test_direction_score_scale()
    print("OK — all Phase 10 signal tests passed")
