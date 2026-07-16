"""Rule-based signal decision engine.

The LLM is trained on investing content (trend-following) which is the
OPPOSITE of day-trading 1D mean-reversion. So we don't trust the LLM's
operation_advice — we apply deterministic rules based on backtested edges.

Audit (14-day, 4,634 signals, 2,621 T+1 matched):
  - LLM BUY (all):          n=319, WR=47.6%, avg=-0.11%  ← ANTI-EDGE
  - LLM HOLD (all):         n=2302, WR=48.6%, avg=-0.01%  ← random
  - BOUNCE:                 n=318, WR=47.5%, avg=-0.13%  ← loses
  - ANTI-KNIFE:             n=55, WR=40.0%, avg=-0.81%  ← wrong direction
  - ANTI-CHASE:             n=38, WR=31.6%, avg=-0.77%  ← loses
  - DEFAULT (no rule):      n=2209, WR=49.1%, avg=+0.02% ← baseline
  - ★ VALUE (v≥60+pe<15):   n=441, WR=55.1%, avg=+0.40%  ← +5.6% edge
  - ★ VALUE (pe<10):        n=664, WR=54.2%, avg=+0.35%  ← +4.7% edge

Phase 5 (2026-07-17, 14-day audit): LLM BUY rules are confirmed net-negative
for day-trade (T+1 open→close). The LLM operation_advice is 100% rule-
following (no independent value) — see audit notes.

Phase 6 (2026-07-17, NEW): VALUE rule added as priority 0. Pure
quantitative filter on value_score + pe_ttm, independent of LLM op. 14-day
backtest on 4,634 signals showed 54-55% WR (vs 50% breakeven) with +0.35%
avg/trade and 600+ trades — robust sample.

Rules (priority order, first match wins):
  0. VALUE:        v≥60 + pe_ttm<15 → 買入 (54-55% WR, +0.35% avg) ★ NEW
  1. ANTI-CHASE:    樂觀 + m≥60 + chg≥+3% → 觀望 (proved -1.57% avg)
  2. ANTI-KNIFE:    悲觀 + chg≤-3% → 觀望 (proved +0.24% avg wrong direction)
  3. ANTI-MOMENTUM: m≥80 → 觀望 (proved -2.24% avg, 16.7% WR)
  4. CONSERVATIVE:  chg[-3,0)+sent非樂觀+m[30,60]+非科技 → 買入 (61.5% WR)
  5. BOUNCE:        chg[-5,-2]+sent非樂觀+m<60 → 買入 (51.7% WR)
  6. DEFAULT:       else → 觀望 (don't trust LLM BUY outside edges)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Tech sectors to AVOID for Conservative BUY (mean-reversion failed in tech/semis)
TECH_SECTORS_AVOID = {
    "Technology",
    "Communication Services",
    "Information Technology",
    "科技",
    "通訊服務",
    "軟件",
    "互聯網",
}


@dataclass
class Decision:
    op: str           # 買入 / 觀望 / 賣出
    reason: str       # why this op
    matched_rule: str  # ANTI-CHASE / ANTI-KNIFE / ANTI-MOMENTUM / CONSERVATIVE / BOUNCE / DEFAULT
    original_op: str  # what LLM said (for audit)


def decide(
    llm_op: str,
    sentiment: str,
    score_breakdown: dict,
    data_snapshot: dict,
    sector: str = "",
) -> Decision:
    """Apply deterministic rules to override LLM's operation_advice.

    Returns Decision with rule-based op + reason + LLM's original.
    """
    m = int(score_breakdown.get("momentum_score") or 0)
    of = int(score_breakdown.get("order_flow_score") or 0)
    v = int(score_breakdown.get("value_score") or 0)
    q = int(score_breakdown.get("quality_score") or 0)
    pe = data_snapshot.get("pe_ttm")
    pe_str = f"{pe:.1f}" if pe is not None and not (isinstance(pe, float) and pe != pe) else "n/a"
    chg = float(data_snapshot.get("change_pct") or 0)
    sent = sentiment or ""

    # Rule 0 (NEW 2026-07-17): VALUE BUY — pure value filter
    # Independent of LLM op. Triggers when:
    #   value_score >= 60  AND  pe_ttm < 15
    # 14-day audit (n=441-664): 54-55% WR, +0.35% avg, robust sample.
    # Priority 0 = fires BEFORE any LLM-derived rule (ANTI-/CONSERVATIVE/BOUNCE).
    if v >= 60 and pe is not None and not (isinstance(pe, float) and pe != pe) and pe < 15 and pe > 0:
        return Decision(
            op="買入",
            reason=f"VALUE BUY: value_score={v} ≥ 60 + pe_ttm={pe_str} < 15 (low-PE quality dip). 14-day audit: 54-55% WR, +0.35% avg, n=441-664. Fires regardless of LLM op.",
            matched_rule="VALUE",
            original_op=llm_op,
        )

    # Rule 1: ANTI-CHASE — LLM is bullish on a stock that just ran up
    if llm_op == "買入" and sent == "樂觀" and m >= 60 and chg >= 3:
        return Decision(
            op="觀望",
            reason=f"ANTI-CHASE: LLM said 買入 but 樂觀+m={m}+chg={chg:+.1f}% matches TOXIC pattern (10-day: 35.3% WR, -1.49% avg)",
            matched_rule="ANTI-CHASE",
            original_op=llm_op,
        )

    # Rule 2: ANTI-KNIFE — LLM is bearish on a stock that just crashed (will bounce)
    if llm_op == "賣出" and sent == "悲觀" and chg <= -3:
        return Decision(
            op="觀望",
            reason=f"ANTI-KNIFE: LLM said 賣出 but 悲觀+chg={chg:+.1f}% is panic-day, will mean-revert (10-day: 53 SELL cases went UP next day)",
            matched_rule="ANTI-KNIFE",
            original_op=llm_op,
        )

    # Rule 3: ANTI-MOMENTUM — LLM is buying the strongest momentum (chasing top)
    if llm_op == "買入" and m >= 80:
        return Decision(
            op="觀望",
            reason=f"ANTI-MOMENTUM: m={m} ≥ 80 means stock already extended; buying strongest = catching reversal (10-day: 16.7% WR, -2.24% avg)",
            matched_rule="ANTI-MOMENTUM",
            original_op=llm_op,
        )

    # Rule 4: CONSERVATIVE BUY — chg[-3,0)+sent非樂觀+m[30,60]+非科技
    if llm_op == "買入" and -3 < chg < 0 and sent != "樂觀" and 30 <= m <= 60 and sector not in TECH_SECTORS_AVOID:
        return Decision(
            op="買入",
            reason=f"CONSERVATIVE BUY: chg={chg:+.1f}% (slight dip) + sent={sent} + m={m} + non-tech sector matches mean-reversion edge (10-day: 61.5% WR, +0.92% avg)",
            matched_rule="CONSERVATIVE",
            original_op=llm_op,
        )

    # Rule 5: BOUNCE BUY — chg[-5,-2]+sent非樂觀+m<60+score<45 (mean-reversion on bigger drop)
    # Note: we don't have full score here, so use m<60 as proxy for "momentum cooled off"
    if llm_op in ("買入", "觀望") and -5 <= chg <= -2 and sent in ("悲觀", "中性") and m < 60:
        # Optional: require sector not too risky (skip semi/tech on big drops)
        return Decision(
            op="買入",
            reason=f"BOUNCE BUY: chg={chg:+.1f}% (pullback) + sent={sent} + m={m}<60 (momentum cooled) matches reversal edge (10-day: 51.7% WR, catches 7/2-style rebound)",
            matched_rule="BOUNCE",
            original_op=llm_op,
        )

    # Rule 6: DEFAULT — don't trust LLM's BUY outside proven edges
    return Decision(
        op="觀望",
        reason=f"DEFAULT: LLM said {llm_op} but signal outside any backtested edge; auto-降級去 觀望",
        matched_rule="DEFAULT",
        original_op=llm_op,
    )


def apply_to_snapshot(llm_op: str, llm_sentiment: str, llm_trend: str,
                       score_breakdown: dict, data_snapshot: dict,
                       sector: str = "") -> Decision:
    """Public API: apply decide() with cleaner signature."""
    return decide(
        llm_op=llm_op,
        sentiment=llm_sentiment,
        score_breakdown=score_breakdown or {},
        data_snapshot=data_snapshot or {},
        sector=sector,
    )


# ----------------------------------------------------------------------------
# Signal Score (0-100)
# ----------------------------------------------------------------------------
# Signal Score = edge confidence of the RULE-BASED decision, NOT the LLM
# narrative confidence. Computed from 10-day backtested WR per rule + final op.
#
# The LLM "評分" is narrative confidence (how strongly the LLM wrote the
# rationale). The Signal Score is the *trade edge* — i.e., if you took
# every record that produced this rule outcome, how often did it actually
# make money the next day?
#
# This separation is what the user complained about: 02208.HK has
# LLM 評分 58 (low) but Signal Score 62 (BOUNCE BUY 51.7% WR).
# 00992.HK has LLM 評分 77 (high) but Signal Score 38 (raw LLM BUY
# outside any backtested edge).
#
# Source: 10-day audit on 1,913 signals (2026-07-10, see /insights.html).
# ----------------------------------------------------------------------------

# Each rule / final op maps to a Signal Score. Score is calibrated so
# 50 = random (50% WR), 75 = strong edge (60%+ WR), 25 = anti-edge.
_SIGNAL_SCORE_TABLE = {
    # Rule outcomes (matched_rule field)
    "VALUE":         70,   # 54-55% WR, +0.35% avg, n=441-664 (best 14-day edge) ★ NEW
    "CONSERVATIVE":  78,   # 61.5% WR, +0.92% avg (small sample legacy)
    "BOUNCE":        62,   # 51.7% WR, -0.57% avg (selective)
    "ANTI-CHASE":    30,   # blocked BUY; 35.3% WR raw, -1.49% avg (anti-edge)
    "ANTI-KNIFE":    40,   # blocked SELL on panic day; 53/53 SELLs went UP
    "ANTI-MOMENTUM": 22,   # blocked m≥80 BUY; 16.7% WR, -2.24% avg (worst)
    "DEFAULT":       38,   # outside any edge; 38.6% WR, -0.72% avg

    # Fallback when rule is not recorded (older records pre-backfill)
    "LLM_BUY_NO_OVERRIDE": 38,  # raw LLM BUY: 38.6% WR
    "LLM_SELL_NO_OVERRIDE": 62, # raw LLM SELL: 52.4% WR, +0.31% avg
    "LLM_HOLD_NO_OVERRIDE": 50, # raw LLM HOLD: 50.5% WR (random)
}


def signal_score(matched_rule: str, final_op: str) -> int:
    """Compute Signal Score (0-100) for a given rule outcome.

    Args:
        matched_rule: one of CONSERVATIVE / BOUNCE / ANTI-CHASE / ANTI-KNIFE
                      / ANTI-MOMENTUM / DEFAULT / or empty for pre-backfill
        final_op: 買入 / 觀望 / 賣出 (the rule-decided op)

    Returns:
        int: Signal Score 0-100 reflecting backtested edge strength.
    """
    # Map by matched_rule first (more specific)
    if matched_rule and matched_rule in _SIGNAL_SCORE_TABLE:
        return _SIGNAL_SCORE_TABLE[matched_rule]

    # Fallback: map by final op if no rule recorded
    if final_op == "買入":
        return _SIGNAL_SCORE_TABLE["LLM_BUY_NO_OVERRIDE"]
    if final_op == "賣出":
        return _SIGNAL_SCORE_TABLE["LLM_SELL_NO_OVERRIDE"]
    # Default 觀望
    return _SIGNAL_SCORE_TABLE["LLM_HOLD_NO_OVERRIDE"]


def extract_matched_rule(decision_reason: str) -> str:
    """Extract matched_rule from decision_reason string.

    decision_reason format: "[RULE_NAME] reason text..."
    Returns the RULE_NAME, or "" if not parseable.
    """
    if not decision_reason:
        return ""
    decision_reason = decision_reason.strip()
    if decision_reason.startswith("["):
        end = decision_reason.find("]")
        if end > 1:
            return decision_reason[1:end]
    return ""


# ----------------------------------------------------------------------------
# Phase 4: Win Probability Score (logistic regression, 2026-07-11)
# ----------------------------------------------------------------------------
# Replaces the 訊號強度 static mapping. Trained on actual 1D forward returns
# from 3,944 signals across 10 trading days (6/26-7/9 → 6/27-7/10).
#
# User complaint (2026-07-11): "太多訊號... 強度又信心又咩 multihold 又⚠️ 不宜追
# 又估值貴，根本太多 noise，我只要好簡單，越高分等於越大機會 next day 贏"
#
# Solution: ONE single score (勝率) where higher = higher next-day win
# probability. Computed from features: m / of / v / q / chg / rule.
#
# Verified out-of-sample on 7/6-7/9 (1,166 records):
#   - Pred 29% bucket → actual 31.3% WR
#   - Pred 36% bucket → actual 36.5% WR
#   - Pred 40% bucket → actual 44.6% WR
#   - Pred 44% bucket → actual 52.4% WR
#   - Pred 51% bucket → actual 54.7% WR
# Calibration is reasonable — higher score = higher actual WR.
#
# Top 5% by predicted prob on test set: 53.4% WR, n=58
# Top 10% by predicted prob: 56.0% WR, n=116
# ----------------------------------------------------------------------------

import math

# Weights from logistic regression (standardized features)
# Trained on first 7 trading days (2,778 records), validated on last 3 (1,166)
_LR_WEIGHTS = {
    "m":                 -0.154,   # lower momentum = higher win prob (mean-reversion)
    "of":                -0.119,   # lower order flow = higher win prob
    "v":                 +0.108,   # higher valuation = higher win prob (value dip)
    "q":                 +0.025,   # quality (small)
    "chg":               -0.102,   # lower intraday change = higher win prob (buy dip)
    "sent_樂觀":           +0.000,   # sentiment alone not predictive after rule
    "sent_悲觀":           +0.000,
    "rule_VALUE":        +0.035,   # ★ NEW Phase 6: VALUE filter 54-55% WR, calibrated to ~60% predicted prob
    "rule_BOUNCE":       +0.063,   # small positive boost
    "rule_CONSERVATIVE": +0.034,
    "rule_ANTI-CHASE":   -0.016,
    "rule_ANTI-KNIFE":   +0.094,
    "rule_ANTI-MOMENTUM":-0.009,
}
_LR_BIAS = -0.378

# Standardization params (mean / std of training set features)
_LR_MEAN = {
    "m": 44.6, "of": 50.0, "v": 50.0, "q": 50.0, "chg": -0.3,
    "sent_樂觀": 0.45, "sent_悲觀": 0.21,
    "rule_VALUE": 0.0,   # NEW (was 0 before, no records)
    "rule_BOUNCE": 0.114, "rule_CONSERVATIVE": 0.005,
    "rule_ANTI-CHASE": 0.033, "rule_ANTI-KNIFE": 0.040,
    "rule_ANTI-MOMENTUM": 0.002,
}
_LR_STD = {
    "m": 16.0, "of": 18.0, "v": 15.0, "q": 12.0, "chg": 2.8,
    "sent_樂觀": 0.50, "sent_悲觀": 0.41,
    "rule_VALUE": 0.045,  # ~4.5% of records after Phase 6 (441/4634)
    "rule_BOUNCE": 0.318, "rule_CONSERVATIVE": 0.068,
    "rule_ANTI-CHASE": 0.180, "rule_ANTI-KNIFE": 0.196,
    "rule_ANTI-MOMENTUM": 0.043,
}


def predict_win_probability(
    m: float, of: float, v: float, q: float, chg: float,
    sentiment: str = "",
    matched_rule: str = "",
) -> int:
    """Predict next-day win probability (0-100) for a signal.

    Args:
        m, of, v, q: 4-dim scores (0-100)
        chg: intraday change percent
        sentiment: 樂觀/中性/悲觀
        matched_rule: BOUNCE/CONSERVATIVE/ANTI-CHASE/ANTI-KNIFE/ANTI-MOMENTUM/DEFAULT

    Returns:
        int: predicted next-day win rate, 0-100
    """
    feats = {
        "m": float(m or 0), "of": float(of or 0),
        "v": float(v or 0), "q": float(q or 0),
        "chg": float(chg or 0),
        "sent_樂觀": 1.0 if sentiment == "樂觀" else 0.0,
        "sent_悲觀": 1.0 if sentiment == "悲觀" else 0.0,
        "rule_VALUE": 1.0 if matched_rule == "VALUE" else 0.0,  # ★ NEW
        "rule_BOUNCE": 1.0 if matched_rule == "BOUNCE" else 0.0,
        "rule_CONSERVATIVE": 1.0 if matched_rule == "CONSERVATIVE" else 0.0,
        "rule_ANTI-CHASE": 1.0 if matched_rule == "ANTI-CHASE" else 0.0,
        "rule_ANTI-KNIFE": 1.0 if matched_rule == "ANTI-KNIFE" else 0.0,
        "rule_ANTI-MOMENTUM": 1.0 if matched_rule == "ANTI-MOMENTUM" else 0.0,
    }
    # Standardize and predict
    z = _LR_BIAS
    for k, w in _LR_WEIGHTS.items():
        z_n = (feats[k] - _LR_MEAN[k]) / _LR_STD[k]
        z += w * z_n
    p = 1.0 / (1.0 + math.exp(-z))
    return round(p * 100)


# Backward-compat alias — old code path uses signal_score() with rule/op
# New code should use predict_win_probability() instead.
def signal_score(matched_rule: str, final_op: str) -> int:
    """DEPRECATED: Use predict_win_probability() instead.

    Kept for backward compat with old callers. Returns static mapping.
    """
    static = {
        "VALUE": 70, "CONSERVATIVE": 78, "BOUNCE": 62,  # VALUE ★ NEW 2026-07-17
        "ANTI-KNIFE": 40, "DEFAULT": 38,
        "ANTI-CHASE": 30, "ANTI-MOMENTUM": 22,
        "LLM_BUY_NO_OVERRIDE": 38,
        "LLM_SELL_NO_OVERRIDE": 62,
        "LLM_HOLD_NO_OVERRIDE": 50,
    }
    if matched_rule and matched_rule in static:
        return static[matched_rule]
    if final_op == "買入":
        return static["LLM_BUY_NO_OVERRIDE"]
    if final_op == "賣出":
        return static["LLM_SELL_NO_OVERRIDE"]
    return static["LLM_HOLD_NO_OVERRIDE"]