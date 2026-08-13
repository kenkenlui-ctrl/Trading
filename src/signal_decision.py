"""Rule-based signal decision engine — next-day long/short.

The LLM is trained on investing content (trend-following) which is often the
OPPOSITE of day-trading 1D mean-reversion. Final 買入/賣出/觀望 is decided by
deterministic rules; the LLM is a feature/narrative extractor only.

Phase 10 (2026-07-30) — next-day board for BUY *and* SHORT
=========================================================
Holdout-validated on backtest_results (train ≤2026-07-10, test >2026-07-10):

  GOLD_LONG  (v≥70, m<40, 0<pe<12, chg<1):
    full-sample n=44  WR=77.3%  avg=+1.92%
    train n=36 WR=75.0% | test n=8 WR=87.5%

  FADE_SHORT (chg≥+3, m≥60, pe>20)  — short extended expensive names:
    full-sample n=192 WR=62.0%  avg_short_pnl=+1.63%
    train n=147 WR=60.5% | test n=45 WR=66.7%

  Anti-patterns (do NOT long these; short sleeve uses the chase cluster):
    chg≥3 + m≥60 long WR ~40% train / short WR ~59–62%

Rules (priority, first match wins):
  1. FADE_SHORT   chg≥3 + m≥60 + pe>20 → 賣出
  2. GOLD_LONG    v≥70 + m<40 + 0<pe<12 + chg<1 → 買入
                  (blocked to 觀望 if HSI signal-day ≤ -1.5%)
  3. VALUE        v≥70 + pe<15 + chg<+1.5 + m<60 → 買入 (SILVER)
                  + ANTI-REBOUND / ANTI-MOM-EXT stops
  4. ANTI-CHASE   (LLM BUY path) 樂觀+m≥60+chg≥3 → 觀望 if not already shorted
  5. ANTI-KNIFE   LLM SELL + 悲觀 + chg≤-3 → 觀望 (don't short knives)
  6. ANTI-MOMENTUM LLM BUY + m≥80 → 觀望
  7. CONSERVATIVE slight dip mean-reversion BUY (weaker on pure 1D)
  8. BOUNCE       DISABLED
  9. DEFAULT      觀望

Also exposes:
  next_day_long_score / next_day_short_score / next_day_bias
  → single 0-100 scores where higher = higher chance next day moves that way.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import math


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

# Phase 9 Step 1.5: Disabled rules (kept visible for audit).
_DISABLED_RULES = {"BOUNCE"}  # 14d: 46.8% WR, -0.13% avg — anti-edge

# Phase 9 Step 2: HSI bear-day threshold — suppresses BUY only (shorts still OK).
HSI_BEAR_THRESHOLD = -1.5

# Phase 10 thresholds (holdout-validated)
GOLD_LONG_V_MIN = 70
GOLD_LONG_M_MAX = 40
GOLD_LONG_PE_MAX = 12.0
GOLD_LONG_CHG_MAX = 1.0

FADE_SHORT_CHG_MIN = 3.0
FADE_SHORT_M_MIN = 60
FADE_SHORT_PE_MIN = 20.0


@dataclass
class Decision:
    op: str           # 買入 / 觀望 / 賣出
    reason: str       # why this op
    matched_rule: str  # GOLD_LONG / FADE_SHORT / VALUE / ...
    original_op: str  # what LLM said (for audit)


def _safe_float(x, default=None):
    if x is None:
        return default
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def _safe_int(x, default: int = 0) -> int:
    v = _safe_float(x, None)
    if v is None:
        return default
    return int(round(v))


def _extract_features(score_breakdown: dict, data_snapshot: dict) -> dict:
    sb = score_breakdown or {}
    ds = data_snapshot or {}
    pe = _safe_float(ds.get("pe_ttm"))
    if pe is not None and pe <= 0:
        pe = None
    price = _safe_float(ds.get("last_price"))
    hi = _safe_float(ds.get("52w_high"))
    lo = _safe_float(ds.get("52w_low"))
    dist_hi = _safe_float(sb.get("dist_52w_high_pct"))
    dist_lo = _safe_float(sb.get("dist_52w_low_pct"))
    if dist_hi is None and price and hi and hi > 0:
        dist_hi = (price - hi) / hi * 100.0
    if dist_lo is None and price and lo and lo > 0:
        dist_lo = (price - lo) / lo * 100.0
    return {
        "m": _safe_int(sb.get("momentum_score"), 0),
        "of": _safe_int(sb.get("order_flow_score"), 0),
        "v": _safe_int(sb.get("value_score"), 0),
        "q": _safe_int(sb.get("quality_score"), 0),
        "pe": pe,
        "chg": _safe_float(ds.get("change_pct"), 0.0) or 0.0,
        "chg_5d": _safe_float(sb.get("chg_5d")),
        "dist_hi": dist_hi,
        "dist_lo": dist_lo,
        "turnover_5d_ratio": _safe_float(sb.get("turnover_5d_ratio"), 1.0) or 1.0,
        "pe_relative": _safe_float(sb.get("pe_relative"), 1.0) or 1.0,
    }


def decide(
    llm_op: str,
    sentiment: str,
    score_breakdown: dict,
    data_snapshot: dict,
    sector: str = "",
    hsi_yesterday_chg: float = None,
) -> Decision:
    """Apply deterministic rules to override LLM's operation_advice.

    Returns Decision with rule-based op + reason + LLM's original.
    """
    f = _extract_features(score_breakdown, data_snapshot)
    m, of, v, q = f["m"], f["of"], f["v"], f["q"]
    pe, chg = f["pe"], f["chg"]
    pe_str = f"{pe:.1f}" if pe is not None else "n/a"
    sent = sentiment or ""
    hsi_bear = (
        hsi_yesterday_chg is not None
        and hsi_yesterday_chg <= HSI_BEAR_THRESHOLD
    )

    # ------------------------------------------------------------------
    # 0. Data quality gate (2026-08-02 P0 fix)
    # If critical technical indicators are all missing, force HOLD.
    # Otherwise we get "MA missing + RS missing + 高信心 100% 下日勝率"
    # which is dangerous miscalibration (Grok audit 2026-08-02).
    # ------------------------------------------------------------------
    ma20 = data_snapshot.get("ma20") if data_snapshot else None
    ma50 = data_snapshot.get("ma50") if data_snapshot else None
    rsi14 = data_snapshot.get("rsi14") if data_snapshot else None
    if ma20 is None and ma50 is None and rsi14 is None:
        return Decision(
            op="觀望",
            reason=(
                "DATA_GUARD: MA20/MA50/RSI14 全部缺失，禁止 actionable signal。"
                "避免 LLM 喺冇技術指標嘅情況下標記高信心。"
                "等下次有完整 snapshot 先再 trade。"
            ),
            matched_rule="DATA_GUARD",
            original_op=llm_op,
        )

    # ------------------------------------------------------------------
    # 1. FADE_SHORT — short extension into expensive names (next-day fade)
    # Holdout: test WR 66.7% (n=45), full 62.0% (n=192), avg short +1.6%
    # Fires BEFORE anti-chase so we monetize the chase cluster instead of
    # only blocking the long.
    # ------------------------------------------------------------------
    if (
        chg >= FADE_SHORT_CHG_MIN
        and m >= FADE_SHORT_M_MIN
        and pe is not None
        and pe > FADE_SHORT_PE_MIN
    ):
        return Decision(
            op="賣出",
            reason=(
                f"FADE_SHORT: chg={chg:+.1f}%≥+{FADE_SHORT_CHG_MIN:.0f} + m={m}≥{FADE_SHORT_M_MIN} "
                f"+ pe={pe_str}>{FADE_SHORT_PE_MIN:.0f} (extended expensive). "
                f"Holdout short WR≈67% (n=45), full-sample 62% (n=192). Day-trade short only."
            ),
            matched_rule="FADE_SHORT",
            original_op=llm_op,
        )

    # ------------------------------------------------------------------
    # 2. GOLD_LONG — strict value + cold momentum (best next-day long)
    # Holdout: test WR 87.5% (n=8), full 77.3% (n=44), avg +1.92%
    # ------------------------------------------------------------------
    gold = (
        v >= GOLD_LONG_V_MIN
        and m < GOLD_LONG_M_MAX
        and pe is not None
        and 0 < pe < GOLD_LONG_PE_MAX
        and chg < GOLD_LONG_CHG_MAX
    )
    if gold:
        if hsi_bear:
            return Decision(
                op="觀望",
                reason=(
                    f"HSI_REGIME: GOLD_LONG blocked — HSI {hsi_yesterday_chg:+.2f}% ≤ "
                    f"{HSI_BEAR_THRESHOLD}% (bear day washes out long edge)."
                ),
                matched_rule="HSI_REGIME",
                original_op=llm_op,
            )
        return Decision(
            op="買入",
            reason=(
                f"GOLD_LONG: v={v}≥{GOLD_LONG_V_MIN} + m={m}<{GOLD_LONG_M_MAX} "
                f"+ pe={pe_str}<{GOLD_LONG_PE_MAX:.0f} + chg={chg:+.1f}%<{GOLD_LONG_CHG_MAX:.0f}. "
                f"Best next-day long sleeve (full n=44 WR≈77%, +1.9% avg)."
            ),
            matched_rule="GOLD_LONG",
            original_op=llm_op,
        )

    # ------------------------------------------------------------------
    # 3. VALUE (SILVER) — looser value long with anti-chase stops
    # ------------------------------------------------------------------
    value_would_fire = (
        v >= 70
        and pe is not None
        and pe < 15
        and pe > 0
    )
    if value_would_fire:
        if hsi_bear:
            return Decision(
                op="觀望",
                reason=(
                    f"HSI_REGIME: VALUE blocked — HSI {hsi_yesterday_chg:+.2f}% ≤ "
                    f"{HSI_BEAR_THRESHOLD}%."
                ),
                matched_rule="HSI_REGIME",
                original_op=llm_op,
            )
        if chg is not None and chg >= 1.5:
            return Decision(
                op="觀望",
                reason=(
                    f"ANTI-REBOUND: VALUE met (v={v}, pe={pe_str}) BUT chg={chg:+.1f}% "
                    f"is rebound chase (7/17 live 0/16; backtest chg≥+2% ~47.5% WR)."
                ),
                matched_rule="ANTI-REBOUND",
                original_op=llm_op,
            )
        if m >= 60:
            return Decision(
                op="觀望",
                reason=(
                    f"ANTI-MOM-EXT: VALUE met (v={v}, pe={pe_str}) BUT m={m}≥60 "
                    f"(extended; prefer GOLD m<40). Backtest m≥70 in VALUE ~47% WR."
                ),
                matched_rule="ANTI-MOM-EXT",
                original_op=llm_op,
            )
        return Decision(
            op="買入",
            reason=(
                f"VALUE BUY (SILVER): v={v}≥70 + pe={pe_str}<15 + chg<+1.5% + m<60. "
                f"Weaker than GOLD_LONG; full VALUE ~64% 1D WR."
            ),
            matched_rule="VALUE",
            original_op=llm_op,
        )

    # ------------------------------------------------------------------
    # 4–6. Toxic LLM path blockers
    # ------------------------------------------------------------------
    if llm_op == "買入" and sent == "樂觀" and m >= 60 and chg >= 3:
        return Decision(
            op="觀望",
            reason=(
                f"ANTI-CHASE: LLM 買入 but 樂觀+m={m}+chg={chg:+.1f}% toxic long "
                f"(~35% long WR). Prefer FADE_SHORT when pe>20."
            ),
            matched_rule="ANTI-CHASE",
            original_op=llm_op,
        )

    if llm_op == "賣出" and sent == "悲觀" and chg <= -3:
        return Decision(
            op="觀望",
            reason=(
                f"ANTI-KNIFE: LLM 賣出 but 悲觀+chg={chg:+.1f}% panic day — "
                f"next day often bounces. Do not short knives."
            ),
            matched_rule="ANTI-KNIFE",
            original_op=llm_op,
        )

    if llm_op == "買入" and m >= 80:
        return Decision(
            op="觀望",
            reason=(
                f"ANTI-MOMENTUM: m={m}≥80 extended; long anti-edge (~17–39% WR). "
                f"Consider short only if pe>20 via FADE_SHORT."
            ),
            matched_rule="ANTI-MOMENTUM",
            original_op=llm_op,
        )

    # ------------------------------------------------------------------
    # 7. CONSERVATIVE BUY — slight dip, non-tech (weaker pure-1D; OK multi-day)
    # ------------------------------------------------------------------
    if (
        llm_op == "買入"
        and -3 < chg < 0
        and sent != "樂觀"
        and 30 <= m <= 60
        and sector not in TECH_SECTORS_AVOID
    ):
        if hsi_bear:
            return Decision(
                op="觀望",
                reason=f"HSI_REGIME: CONSERVATIVE blocked — HSI {hsi_yesterday_chg:+.2f}%.",
                matched_rule="HSI_REGIME",
                original_op=llm_op,
            )
        return Decision(
            op="買入",
            reason=(
                f"CONSERVATIVE BUY: chg={chg:+.1f}% dip + sent={sent} + m={m} + non-tech. "
                f"Stronger on 2–3d hold/paper than pure 1D (1D backtest ~43%, paper ~81%)."
            ),
            matched_rule="CONSERVATIVE",
            original_op=llm_op,
        )

    # ------------------------------------------------------------------
    # 8. BOUNCE — DISABLED
    # ------------------------------------------------------------------
    if "BOUNCE" not in _DISABLED_RULES:
        if (
            llm_op in ("買入", "觀望")
            and -5 <= chg <= -2
            and sent in ("悲觀", "中性")
            and m < 60
        ):
            return Decision(
                op="買入",
                reason=f"BOUNCE BUY: chg={chg:+.1f}% + sent={sent} + m={m}<60",
                matched_rule="BOUNCE",
                original_op=llm_op,
            )

    # ------------------------------------------------------------------
    # 9. DEFAULT
    # ------------------------------------------------------------------
    return Decision(
        op="觀望",
        reason=(
            f"DEFAULT: LLM said {llm_op}; outside GOLD_LONG / VALUE / FADE_SHORT / "
            f"CONSERVATIVE edges → 觀望"
        ),
        matched_rule="DEFAULT",
        original_op=llm_op,
    )


def apply_to_snapshot(
    llm_op: str,
    llm_sentiment: str,
    llm_trend: str,
    score_breakdown: dict,
    data_snapshot: dict,
    sector: str = "",
    hsi_yesterday_chg: float = None,
) -> Decision:
    """Public API: apply decide() with cleaner signature."""
    return decide(
        llm_op=llm_op,
        sentiment=llm_sentiment,
        score_breakdown=score_breakdown or {},
        data_snapshot=data_snapshot or {},
        sector=sector,
        hsi_yesterday_chg=hsi_yesterday_chg,
    )


# ----------------------------------------------------------------------------
# Phase 10: Next-day directional scores (0-100)
# Higher long_score  → higher P(next close up)
# Higher short_score → higher P(next close down)
# bias = long_score - short_score  (positive → lean long)
# Calibrated from train Pearson signs + holdout rule lifts (2026-07-30).
# ----------------------------------------------------------------------------

def next_day_long_score(
    score_breakdown: dict,
    data_snapshot: dict,
    sentiment: str = "",
    matched_rule: str = "",
) -> int:
    """0-100 score: higher ⇒ better next-day LONG candidate."""
    f = _extract_features(score_breakdown, data_snapshot)
    p = 50.0
    chg, m, v, pe = f["chg"], f["m"], f["v"], f["pe"]
    dist_hi, dist_lo = f["dist_hi"], f["dist_lo"]
    sent = sentiment or ""

    # Day change (mean-reversion long)
    if -3 <= chg <= -0.5:
        p += 12
    elif -5 <= chg < -3:
        p += 6
    elif chg >= 3:
        p -= 18
    elif chg >= 2:
        p -= 12
    elif chg >= 1:
        p -= 6

    # Momentum: cold helps longs
    if m < 40:
        p += 12
    elif m < 50:
        p += 6
    elif m >= 80:
        p -= 18
    elif m >= 60:
        p -= 10

    # Value / PE
    if v >= 70:
        p += 10
    elif v < 40:
        p -= 6
    if pe is not None:
        if 0 < pe < 12:
            p += 10
        elif pe < 15:
            p += 4
        elif pe > 30:
            p -= 8

    # Sentiment
    if sent == "悲觀":
        p += 8
    elif sent == "樂觀":
        p -= 10

    # 52w context
    if dist_hi is not None and dist_hi > -5:
        p -= 12
    elif dist_hi is not None and dist_hi > -10:
        p -= 5
    if dist_lo is not None and dist_lo <= 15:
        p += 6

    # Rule bump (if already decided)
    rule_bump = {
        "GOLD_LONG": 18,
        "VALUE": 10,
        "CONSERVATIVE": 4,
        "FADE_SHORT": -25,
        "ANTI-CHASE": -15,
        "ANTI-MOMENTUM": -18,
        "HSI_REGIME": -12,
        "BOUNCE": -5,
    }
    p += rule_bump.get(matched_rule or "", 0)

    return int(max(0, min(100, round(p))))


def next_day_short_score(
    score_breakdown: dict,
    data_snapshot: dict,
    sentiment: str = "",
    matched_rule: str = "",
) -> int:
    """0-100 score: higher ⇒ better next-day SHORT candidate."""
    f = _extract_features(score_breakdown, data_snapshot)
    p = 50.0
    chg, m, v, pe = f["chg"], f["m"], f["v"], f["pe"]
    dist_hi = f["dist_hi"]
    sent = sentiment or ""

    # Extension
    if chg >= 4:
        p += 16
    elif chg >= 3:
        p += 14
    elif chg >= 2:
        p += 8
    elif chg <= -3:
        p -= 16  # knife — do not short
    elif chg <= -1:
        p -= 8

    if m >= 80:
        p += 14
    elif m >= 65:
        p += 10
    elif m >= 60:
        p += 6
    elif m < 40:
        p -= 12

    if pe is not None:
        if pe > 25:
            p += 8
        elif pe > 20:
            p += 6
        elif 0 < pe < 12:
            p -= 10  # don't short cheap value

    if v >= 70:
        p -= 10
    elif v < 40:
        p += 4

    if sent == "樂觀":
        p += 8
    elif sent == "悲觀":
        p -= 8

    if dist_hi is not None and dist_hi > -5:
        p += 8
    elif dist_hi is not None and dist_hi > -8:
        p += 4

    rule_bump = {
        "FADE_SHORT": 20,
        "ANTI-CHASE": 8,       # chase cluster = short research interest
        "ANTI-MOMENTUM": 10,
        "GOLD_LONG": -25,
        "VALUE": -15,
        "ANTI-KNIFE": -20,     # blocked short
        "CONSERVATIVE": -8,
    }
    p += rule_bump.get(matched_rule or "", 0)

    return int(max(0, min(100, round(p))))


def direction_score(
    score_breakdown: dict,
    data_snapshot: dict,
    sentiment: str = "",
    matched_rule: str = "",
) -> int:
    """Single 0–100 next-day direction confidence (THE score to look at).

    Scale (by design):
      0   → high chance SHORT wins next day
      50  → no clear edge (hold / skip)
      100 → high chance LONG wins next day

    Built from long_score vs short_score mass, then anchored by matched rule
    so GOLD_LONG clusters high and FADE_SHORT clusters low.
    """
    lg = float(next_day_long_score(score_breakdown, data_snapshot, sentiment, matched_rule))
    sh = float(next_day_short_score(score_breakdown, data_snapshot, sentiment, matched_rule))
    total = lg + sh
    if total <= 1e-6:
        raw = 50.0
    else:
        # Share of "long mass" → 0..100. Equal long/short → 50.
        raw = 100.0 * lg / total

    rule = matched_rule or ""
    # Hard anchors so actionable rules are unambiguous on the dial
    if rule == "GOLD_LONG":
        raw = max(raw, 88.0)
    elif rule == "VALUE":
        raw = max(raw, 72.0)
    elif rule == "CONSERVATIVE":
        raw = max(min(raw, 80.0), 62.0)
    elif rule == "FADE_SHORT":
        raw = min(raw, 12.0)
    elif rule in ("ANTI-CHASE", "ANTI-MOMENTUM"):
        # Chase cluster: lean short / avoid long (not a hard short unless FADE)
        raw = min(raw, 32.0)
    elif rule == "ANTI-KNIFE":
        # Do not short panic — pin near neutral
        raw = max(min(raw, 58.0), 42.0)
    elif rule == "HSI_REGIME":
        # Bear day: suppress long confidence toward neutral
        raw = min(raw, 48.0)
    elif rule == "ANTI-REBOUND" or rule == "ANTI-MOM-EXT":
        raw = min(max(raw, 35.0), 55.0)

    return int(max(0, min(100, round(raw))))


def direction_label(score: int) -> str:
    """Human band for direction_score."""
    # IMPORTANT: score 0 is valid (strong short). Do not use `score or 50`.
    s = 50 if score is None else int(score)
    if s >= 85:
        return "STRONG_LONG"
    if s >= 70:
        return "LEAN_LONG"
    if s <= 15:
        return "STRONG_SHORT"
    if s <= 30:
        return "LEAN_SHORT"
    return "NEUTRAL"


def direction_action(score: int) -> str:
    """Suggested side from direction_score alone."""
    # IMPORTANT: score 0 is valid (strong short). Do not use `score or 50`.
    s = 50 if score is None else int(score)
    if s >= 70:
        return "BUY"
    if s <= 30:
        return "SHORT"
    return "HOLD"


def next_day_bias(
    score_breakdown: dict,
    data_snapshot: dict,
    sentiment: str = "",
    matched_rule: str = "",
) -> dict:
    """Return long/short poles + single direction_score for the board."""
    lg = next_day_long_score(score_breakdown, data_snapshot, sentiment, matched_rule)
    sh = next_day_short_score(score_breakdown, data_snapshot, sentiment, matched_rule)
    d = direction_score(score_breakdown, data_snapshot, sentiment, matched_rule)
    return {
        "long_score": lg,
        "short_score": sh,
        "bias": lg - sh,  # >0 lean long, <0 lean short
        "direction_score": d,  # 0=short … 100=long  ← primary
        "label": direction_label(d),
        "action": direction_action(d),
    }


# ----------------------------------------------------------------------------
# signal_score column / badge — NOW = direction_score semantics
# 0=short … 50=neutral … 100=long
# ----------------------------------------------------------------------------

_SIGNAL_SCORE_TABLE = {
    # Bidirectional anchors (static fallback when features missing)
    "HSI_REGIME": 45,
    "GOLD_LONG": 92,
    "FADE_SHORT": 8,
    "VALUE": 78,
    "ANTI-REBOUND": 48,
    "ANTI-MOM-EXT": 48,
    "CONSERVATIVE": 68,
    "BOUNCE": 45,
    "ANTI-CHASE": 28,
    "ANTI-KNIFE": 50,
    "ANTI-MOMENTUM": 22,
    "DEFAULT": 50,
    "LLM_BUY_NO_OVERRIDE": 55,
    "LLM_SELL_NO_OVERRIDE": 40,
    "LLM_HOLD_NO_OVERRIDE": 50,
}


def signal_score(
    matched_rule: str,
    final_op: str,
    score_breakdown: Optional[dict] = None,
    data_snapshot: Optional[dict] = None,
    sentiment: str = "",
) -> int:
    """Primary badge score = direction_score when features available.

    0 = short confidence, 100 = long confidence.
    """
    if score_breakdown is not None and data_snapshot is not None:
        return direction_score(
            score_breakdown, data_snapshot, sentiment, matched_rule or ""
        )
    if matched_rule and matched_rule in _SIGNAL_SCORE_TABLE:
        return _SIGNAL_SCORE_TABLE[matched_rule]
    if final_op == "買入":
        return _SIGNAL_SCORE_TABLE["LLM_BUY_NO_OVERRIDE"]
    if final_op == "賣出":
        return _SIGNAL_SCORE_TABLE["LLM_SELL_NO_OVERRIDE"]
    return _SIGNAL_SCORE_TABLE["LLM_HOLD_NO_OVERRIDE"]


def extract_matched_rule(decision_reason: str) -> str:
    """Extract matched_rule from decision_reason string '[RULE] ...'."""
    if not decision_reason:
        return ""
    decision_reason = decision_reason.strip()
    if decision_reason.startswith("["):
        end = decision_reason.find("]")
        if end > 1:
            return decision_reason[1:end]
    # Fallback: leading TOKEN:
    for tag in (
        "GOLD_LONG", "FADE_SHORT", "HSI_REGIME", "VALUE", "ANTI-REBOUND",
        "ANTI-MOM-EXT", "ANTI-CHASE", "ANTI-KNIFE", "ANTI-MOMENTUM",
        "CONSERVATIVE", "BOUNCE", "DEFAULT",
    ):
        if decision_reason.startswith(tag) or f"{tag}:" in decision_reason[:40]:
            return tag
    return ""


# Phase 4/9 LR weights (extended Phase 10 with GOLD_LONG / FADE_SHORT dummies)
_LR_WEIGHTS = {
    "m": +0.150,
    "of": -0.050,
    "v": +0.100,
    "q": -0.100,
    "chg": -0.150,
    "chg_5d": -0.050,
    "turnover_5d_ratio": -0.050,
    "dist_52w_low": -0.030,
    "dist_52w_high": -0.050,
    "pe_relative": -0.100,
    "sent_樂觀": -0.100,
    "sent_悲觀": +0.100,
    "rule_HSI_REGIME": -0.300,
    "rule_GOLD_LONG": +0.650,   # Phase 10
    "rule_FADE_SHORT": +0.400,  # Phase 10 — for short win-prob context use short_score
    "rule_VALUE": +0.450,
    "rule_ANTI-REBOUND": -0.100,
    "rule_ANTI-MOM-EXT": -0.100,
    "rule_BOUNCE": -0.060,
    "rule_CONSERVATIVE": -0.050,
    "rule_ANTI-CHASE": -0.400,
    "rule_ANTI-KNIFE": +0.150,
    "rule_ANTI-MOMENTUM": -0.500,
}
_LR_BIAS = 0.0
_LR_MEAN = {
    "m": 47.0, "of": 56.0, "v": 50.0, "q": 50.0,
    "chg": 0.0, "chg_5d": -0.1,
    "turnover_5d_ratio": 1.04,
    "dist_52w_low": 87.7, "dist_52w_high": -20.4,
    "pe_relative": 1.0,
    "sent_樂觀": 0.10, "sent_悲觀": 0.26,
}
_LR_STD = {
    "m": 18.6, "of": 8.2, "v": 20.1, "q": 18.0,
    "chg": 5.4, "chg_5d": 6.6,
    "turnover_5d_ratio": 0.26,
    "dist_52w_low": 307.0, "dist_52w_high": 14.3,
    "pe_relative": 0.10,
    "sent_樂觀": 0.30, "sent_悲觀": 0.44,
}


def predict_win_probability(
    m: float, of: float, v: float, q: float, chg: float,
    sentiment: str = "",
    matched_rule: str = "",
    chg_5d: float = 0.0,
    turnover_5d_ratio: float = 1.0,
    dist_52w_low: float = 30.0,
    dist_52w_high: float = -20.0,
    pe_relative: float = 1.0,
) -> int:
    """Predict next-day *direction-aligned* win probability (0-100).

    For 買入 rules this is P(up). For FADE_SHORT this is P(down) approximated
    via the FADE_SHORT dummy (use next_day_short_score for pure short rank).
    """
    feats = {
        "m": float(m or 0), "of": float(of or 0),
        "v": float(v or 0), "q": float(q or 0),
        "chg": float(chg or 0),
        "chg_5d": float(chg_5d or 0),
        "turnover_5d_ratio": float(turnover_5d_ratio or 1.0),
        "dist_52w_low": float(dist_52w_low or 30.0),
        "dist_52w_high": float(dist_52w_high or -20.0),
        "pe_relative": float(pe_relative or 1.0),
        "sent_樂觀": 1.0 if sentiment == "樂觀" else 0.0,
        "sent_悲觀": 1.0 if sentiment == "悲觀" else 0.0,
        "rule_HSI_REGIME": 1.0 if matched_rule == "HSI_REGIME" else 0.0,
        "rule_GOLD_LONG": 1.0 if matched_rule == "GOLD_LONG" else 0.0,
        "rule_FADE_SHORT": 1.0 if matched_rule == "FADE_SHORT" else 0.0,
        "rule_VALUE": 1.0 if matched_rule == "VALUE" else 0.0,
        "rule_ANTI-REBOUND": 1.0 if matched_rule == "ANTI-REBOUND" else 0.0,
        "rule_ANTI-MOM-EXT": 1.0 if matched_rule == "ANTI-MOM-EXT" else 0.0,
        "rule_BOUNCE": 1.0 if matched_rule == "BOUNCE" else 0.0,
        "rule_CONSERVATIVE": 1.0 if matched_rule == "CONSERVATIVE" else 0.0,
        "rule_ANTI-CHASE": 1.0 if matched_rule == "ANTI-CHASE" else 0.0,
        "rule_ANTI-KNIFE": 1.0 if matched_rule == "ANTI-KNIFE" else 0.0,
        "rule_ANTI-MOMENTUM": 1.0 if matched_rule == "ANTI-MOMENTUM" else 0.0,
    }
    z = _LR_BIAS
    rule_keys = {k for k in _LR_WEIGHTS if k.startswith("rule_")}
    for k, w in _LR_WEIGHTS.items():
        if k in rule_keys:
            z += w * feats.get(k, 0.0)
        else:
            z_n = (feats[k] - _LR_MEAN.get(k, 0)) / max(_LR_STD.get(k, 1), 1e-6)
            z += w * z_n
    p = 1.0 / (1.0 + math.exp(-z))
    return max(0, min(100, round(p * 100)))
