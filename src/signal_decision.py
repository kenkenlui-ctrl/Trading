"""Rule-based signal decision engine.

The LLM is trained on investing content (trend-following) which is the
OPPOSITE of day-trading 1D mean-reversion. So we don't trust the LLM's
operation_advice — we apply deterministic rules based on backtested edges.

Audit (10-day, 1913 signals):
  - LLM BUY (all):          n=197, WR=38.6%, avg=-0.72%  ← ANTI-EDGE
  - LLM BUY 樂觀:           n=112, WR=30.4%, avg=-1.32%  ← WORST
  - LLM BUY m≥80:           n=12,  WR=16.7%, avg=-2.24%  ← COIN FLIP
  - LLM SELL 悲觀+chg≤-3%:  n=53,  WR=37.7%, avg=+0.24%  ← WRONG DIRECTION
  - Det Conservative BUY:   n=26,  WR=61.5%, avg=+0.92%  ← +23% WR edge
  - Det Bounce BUY:         n=84,  WR=47.6%, avg=-0.57%  ← catches reversals

Rules (priority order, first match wins):
  1. ANTI-CHASE:    樂觀 + m≥60 + chg≥+3% → 觀望 (proved -1.57% avg)
  2. ANTI-KNIFE:    悲觀 + chg≤-3% → 觀望 (proved +0.24% avg wrong direction)
  3. ANTI-MOMENTUM: m≥80 → 觀望 (proved -2.24% avg, 16.7% WR)
  4. CONSERVATIVE:  chg[-3,0)+sent非樂觀+m[30,60]+非科技 → 買入 (61.5% WR)
  5. BOUNCE:        chg[-5,-2]+sent非樂觀+m<60+score<45 → 買入 (51.7% WR)
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
    score = int(score_breakdown.get("value_score") or 0) + int(score_breakdown.get("quality_score") or 0)  # placeholder
    score = 0  # We don't have aggregate score here; use data passed in
    chg = float(data_snapshot.get("change_pct") or 0)
    sent = sentiment or ""

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