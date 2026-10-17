"""B3 — data-gap receipts.

The hazard (spec Background): "It is currently unknown whether the pipeline
carries a stale bar forward when a vendor is late. Either answer must be
explicit and receipted." Two failure modes, both silent:

  1. carrying a stale bar forward  → a look-ahead-adjacent number presented as
     this session's, and
  2. dropping a ticker from the candidate set but leaving it in the denominator
     → win rates and counts silently shrink or inflate.

Receipt fields (one line each):

  session        the session the gap applies to
  market/symbol  who is missing
  observed_at    when the gap was noticed
  query_time     when the vendor was actually queried (distinct from noticed)
  rows_returned  0 — asserted, not assumed
  reason         MISSING_BAR | SOURCE_LATE | RULE_REJECTION | QUARANTINED
  action         EXCLUDED_CANDIDATES | EXCLUDED_DENOMINATORS | BOTH | QUARANTINED

B3.3's separation is enforced structurally, not by convention: 'source late' and
'value not met' are different reasons and travel through different functions,
and `count_reasons` proves a rule rejection can never be counted as a gap.

A gap EXCLUDES from both the candidate set and every denominator. That is the
point of the module — `apply_gaps()` is the only supported way to fold a gap set
into a session, and it takes both sets as arguments so forgetting the
denominators is visible at the call site.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Iterable, Sequence

MISSING_BAR = "MISSING_BAR"
SOURCE_LATE = "SOURCE_LATE"
RULE_REJECTION = "RULE_REJECTION"
QUARANTINED = "QUARANTINED"

GAP_REASONS = (MISSING_BAR, SOURCE_LATE)
NON_GAP_REASONS = (RULE_REJECTION, QUARANTINED)
ALL_REASONS = GAP_REASONS + NON_GAP_REASONS

EXCLUDED_CANDIDATES = "EXCLUDED_CANDIDATES"
EXCLUDED_DENOMINATORS = "EXCLUDED_DENOMINATORS"
BOTH = "BOTH"
QUARANTINE = "QUARANTINE"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class GapReceipt:
    session: str
    market: str
    symbol: str
    reason: str = MISSING_BAR
    action: str = BOTH
    query_time: str = field(default_factory=_now)
    observed_at: str = field(default_factory=_now)
    rows_returned: int = 0

    def __post_init__(self):
        if self.reason not in ALL_REASONS:
            raise ValueError(f"reason must be one of {ALL_REASONS}")
        # Only SOURCE-side gaps must be empty. A RULE_REJECTION is the opposite
        # case: the vendor answered fine and the rules said no, so it legitimately
        # carries rows. Enforcing rows==0 on it (as the first version of this
        # dataclass did) would make B3.3's channel separation impossible to
        # express at all.
        if self.reason in GAP_REASONS and self.rows_returned != 0:
            raise ValueError(
                f"a {self.reason} receipt must carry rows_returned == 0")

    def as_dict(self) -> dict:
        return {"session": self.session, "market": self.market,
                "symbol": self.symbol, "reason": self.reason,
                "action": self.action, "query_time": self.query_time,
                "observed_at": self.observed_at,
                "rows_returned": self.rows_returned}


def is_gap(receipt: GapReceipt) -> bool:
    """The channel separation, as a predicate. Only source-side reasons count
    as gaps; a rule rejection never enters a gap counter."""
    return receipt.reason in GAP_REASONS


def receipts_for(session: str, gaps: Iterable[GapReceipt]) -> list[dict]:
    return [r.as_dict() for r in gaps if r.session == session]


def count_reasons(gaps: Iterable[GapReceipt]) -> dict:
    """Counts by reason, so a test can prove rule rejections and gaps land in
    different buckets."""
    out = {r: 0 for r in ALL_REASONS}
    for g in gaps:
        out[g.reason] += 1
    return out


def apply_gaps(gaps: Sequence[GapReceipt], candidates: Iterable[str],
               denominator: Iterable[str]) -> tuple[list[str], list[str], list[dict]]:
    """Fold gaps into a session.

    Returns (candidates, denominator, receipts). A symbol with a gap receipt is
    removed from BOTH sets. Removal is by symbol, so the two lists cannot drift
    apart — the failure mode this module exists to prevent."""
    gapped = {g.symbol for g in gaps if is_gap(g)}
    cand = [c for c in candidates if c not in gapped]
    den = [d for d in denominator if d not in gapped]
    recs = [g.as_dict() for g in gaps]
    return cand, den, recs


def quarantine_expired(gaps: Sequence[GapReceipt], *,
                       refilled: Iterable[str] = ()) -> list[dict]:
    """B3.2: a missing bar not refilled by the NEXT session's build is
    quarantined. Already-published sessions are never backfilled — this returns
    receipts to log, it does not rewrite history.

    Call this with the still-open gaps from the previous build and the symbols
    that have since been refilled. Only gap receipts are considered: a rule
    rejection is not a gap and must never be quarantined as one."""
    ref = set(refilled)
    return [{**g.as_dict(), "reason": QUARANTINED, "action": QUARANTINE}
            for g in gaps if is_gap(g) and g.symbol not in ref]