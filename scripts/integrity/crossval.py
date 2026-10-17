"""B1 — an independent second implementation of the v2 signal decision.

Why a second implementation exists (spec Background): the VIX regime-filter
test on 2026-10-08 was falsified by a bug in the VERIFICATION path, not the
signal path — a placebo-grouping error flipped the conclusion. Nothing in the
pipeline recomputes the answer a second way, so a verification bug and a signal
bug look identical from outside.

This module is that second way. It is written from the published v2 contract,
not by transcribing the primary implementation:

    S1 entry   = 20-day low, as of T-1
    trend gate = prev_close > MA200 and MA200 rising
    guards     = ATR% < 6% and (R1/S1 - 1) < 35%
    stop       = S1 * (1 - 3 * ATR%)
    horizon    = 10 sessions, no price target

Implementation note — and the reason this is a real second opinion rather than a
re-run: the primary path is pandas `.rolling()`/`.shift()` on a DataFrame. This
one is explicit slicing over plain Python lists. The shift-by-one convention
("value at row t uses information up to t-1") becomes, here, the statement
"everything reads indices up to last = n-1 and never touches n".

No-look-ahead is therefore structural, not a promise: `independent_plan()` has
no access to bar n at all. Dropping a T-day bar on the end changes nothing,
which is exactly what the no-look-ahead probe asserts.

Agreement is EXACT MATCH on the decision fields, not a statistical test — the
spec's multiple-comparisons guardrail forbids claiming p-values here. Two
implementations agreeing is a consistency check, and nothing more.
"""
from __future__ import annotations

import datetime as _dt
from typing import Sequence

from .hashing import obj_sha256

IMPL_NAME = "sprint2.independent"
IMPL_VERSION = "1.0.0"

# v2 contract constants. Kept as module-level named values and echoed into every
# result so a threshold change is visible in the receipt rather than buried in
# code (B1.3).
S1_LOOKBACK = 20
R2_LOOKBACK = 40
MA_LONG = 200
MA_SLOPE_LOOKBACK = 20
ATR_WINDOW = 14
ATR_MAX = 0.06
BOX_MAX = 0.35
STOP_ATR = 3.0
HOLD = 10

PARAMS = {
    "s1_lookback": S1_LOOKBACK,
    "r2_lookback": R2_LOOKBACK,
    "ma_long": MA_LONG,
    "ma_slope_lookback": MA_SLOPE_LOOKBACK,
    "atr_window": ATR_WINDOW,
    "atr_max": ATR_MAX,
    "box_max": BOX_MAX,
    "stop_atr": STOP_ATR,
    "hold": HOLD,
}

# Fields compared for agreement. Deliberately excludes sizing: share counts
# depend on the equity base and lot table, which are shared config, not signal
# logic. Including them would make the comparison fail for reasons that have
# nothing to do with the entry decision.
COMPARED_FIELDS = ("decision", "entry_s1", "stop", "trend_gate", "guard_rail")


class InsufficientHistory(Exception):
    """Raised when there is not enough T-1 history to decide. Distinct from a
    rule rejection: this is a data condition, and B3 requires it to travel on
    the gap channel rather than the rule channel."""


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def independent_plan(bars: Sequence[dict], t1_date: str | None = None) -> dict:
    """Decide v2 for one symbol from its daily bars, using only T-1 information.

    `bars` must be ascending by date, each {date, high, low, close, volume}.
    `t1_date` is the T-1 session. Bars after it are DISCARDED, not ignored by
    convention — that is the whole point of the parameter, and it was added
    because the first version of this function inferred T-1 as "the last row
    in the list", which meant a caller who passed today's bar got today's
    answer and a green self-test. Selection by date is what makes look-ahead
    impossible rather than merely discouraged.

    With no `t1_date`, T-1 is the last bar (convenient for fixtures).

    Returns a decision dict, or raises InsufficientHistory.
    """
    if t1_date:
        cutoff = str(t1_date)
        bars = [b for b in bars if str(b.get("date")) <= cutoff]
    if len(bars) < MA_LONG + MA_SLOPE_LOOKBACK + 1:
        raise InsufficientHistory(
            f"need > {MA_LONG + MA_SLOPE_LOOKBACK} bars, have {len(bars)}")

    last = len(bars) - 1
    hi = [float(b["high"]) for b in bars]
    lo = [float(b["low"]) for b in bars]
    cl = [float(b["close"]) for b in bars]

    # ---- S1 / R1: the 20-day ladder, T-1 inclusive ----
    s1 = min(lo[last - S1_LOOKBACK + 1: last + 1])
    r1 = max(hi[last - S1_LOOKBACK + 1: last + 1])
    # ---- R2: the 40 sessions immediately before the S1/R1 window ----
    r2 = max(hi[last - S1_LOOKBACK - R2_LOOKBACK + 1: last - S1_LOOKBACK + 1])

    prev_close = cl[last]

    # ---- MA200 and its slope, both T-1 inclusive ----
    ma_now = sum(cl[last - MA_LONG + 1: last + 1]) / MA_LONG
    ma_then = sum(cl[last - MA_LONG - MA_SLOPE_LOOKBACK + 1:
                     last - MA_SLOPE_LOOKBACK + 1]) / MA_LONG
    ma_slope = (ma_now / ma_then - 1) if ma_then else float("nan")

    # ---- ATR% : mean true range over ATR_WINDOW sessions, divided by T-1 close ----
    tr_sum = 0.0
    for i in range(last - ATR_WINDOW + 1, last + 1):
        tr_sum += _true_range(hi[i], lo[i], cl[i - 1])
    atr_pct = tr_sum / ATR_WINDOW / prev_close

    box_w = r1 / s1 - 1 if s1 else float("nan")

    trend_gate = bool(prev_close > ma_now and ma_slope > 0)
    guard_rail = bool(atr_pct < ATR_MAX and box_w < BOX_MAX)

    if not trend_gate:
        decision, reason = "NO_SIGNAL", "not in uptrend"
    elif not guard_rail:
        decision, reason = "NO_SIGNAL", "guard rail (ATR/box)"
    else:
        decision, reason = "SIGNAL", "ok"

    stop = s1 * (1 - STOP_ATR * atr_pct)

    return {
        "impl": IMPL_NAME,
        "impl_version": IMPL_VERSION,
        "symbol": bars[last].get("symbol"),
        "asof": str(bars[last].get("date")),
        "decision": decision,
        "reason": reason,
        "entry_s1": round(s1, 6),
        "r2": round(r2, 6),
        "stop": round(stop, 6),
        "atr_pct": atr_pct,
        "box_w": box_w,
        "prev_close": prev_close,
        "ma200": ma_now,
        "ma200_slope": ma_slope,
        "trend_gate": trend_gate,
        "guard_rail": guard_rail,
        "bars_used": last + 1,
    }


def decision_only(plan: dict) -> dict:
    """The comparable slice, rounded identically on both sides so agreement is
    about the decision and not about float formatting."""
    return {k: plan.get(k) for k in COMPARED_FIELDS}


def compare(primary: dict, secondary: dict) -> dict:
    """Exact-match comparison of the two implementations' decisions.

    Returns a record with `agree`, the differing fields, and both hashes. A
    disagreement is a finding to publish in the receipt, never something to
    reconcile."""
    a, b = decision_only(primary), decision_only(secondary)
    diff = {k: {"primary": a.get(k), "independent": b.get(k)}
            for k in COMPARED_FIELDS if a.get(k) != b.get(k)}
    return {
        "agree": not diff,
        "differing_fields": diff,
        "primary_hash": obj_sha256(a),
        "independent_hash": obj_sha256(b),
        "compared_fields": list(COMPARED_FIELDS),
        "agreement_mode": "exact_match_not_statistical",
    }


def rejection_receipt(session: str, symbol: str, primary: dict,
                      secondary: dict, comparison: dict,
                      observed_at: str | None = None) -> dict:
    """B1.4 — auditable receipt for a rejected signal.

    Carries both outputs' hashes so the disagreement can be reconstructed later
    without storing either full output."""
    return {
        "kind": "crossval_rejection",
        "session": session,
        "symbol": symbol,
        "observed_at": observed_at or _dt.datetime.now(_dt.timezone.utc)
                                .replace(microsecond=0).isoformat(),
        "reason": "implementations_disagree",
        "primary_hash": comparison["primary_hash"],
        "independent_hash": comparison["independent_hash"],
        "primary_decision": primary.get("decision"),
        "independent_decision": secondary.get("decision"),
        "differing_fields": comparison["differing_fields"],
        "params": PARAMS,
    }