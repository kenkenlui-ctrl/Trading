"""A1 — freshness contract and visitor-visible pipeline state.

The failure this exists for (spec Background): on 2026-10-17 ~04:24 HKT the site
still showed "Updated 2026-10-14 18:24" — 58h old, past the 36h limit — and an
external cron was the only thing that noticed. A visitor saw dead numbers
presented as live. Counts looked healthy.

States (spec A1.2), one line each:

  FRESH       input is within the age limit for its session
  STALE       input is older than the limit; banner renders, numbers stay visible
              but are marked degraded
  NOT_RUN     the pipeline did not run at all for this session; the page says so
              and NEVER reuses the previous session's numbers
  SOURCE_DOWN the vendor was unreachable; distinct from NOT_RUN because the
              cause and the on-call action differ

Contract: MAX_AGE_HOURS = 36, kept from the existing monitor. Monday/holiday
handling is explicitly out of scope for now (spec A1.1), so `classify()` reports
what it measured and takes the expected session date from the caller rather than
guessing a trading calendar — a guessed session date is how "stale" silently
becomes "fresh".
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

MAX_AGE_HOURS = 36

FRESH = "FRESH"
STALE = "STALE"
NOT_RUN = "NOT_RUN"
SOURCE_DOWN = "SOURCE_DOWN"
STATES = (FRESH, STALE, NOT_RUN, SOURCE_DOWN)


def _parse(s: str) -> _dt.datetime:
    s = str(s).strip().replace("Z", "+00:00")
    d = _dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


@dataclass
class Freshness:
    state: str
    asof: str | None
    age_hours: float | None
    session: str | None
    market: str | None = None
    source_error: str | None = None
    # 2026-10-25: an optional extra line for the banner. The age limit is not
    # the only way to be stale: the site's newest input can be younger than
    # 36h and still be MISSING a session the market has already closed. That
    # case cannot be expressed by the age number, and the banner must say
    # which of the two it is — otherwise the reader sees "9.0h old" under a
    # STALE heading and concludes the limit was not applied.
    reason: str | None = None

    @property
    def is_fresh(self) -> bool:
        return self.state == FRESH

    def as_dict(self) -> dict:
        return {"state": self.state, "asof": self.asof,
                "age_hours": self.age_hours, "session": self.session,
                "market": self.market, "source_error": self.source_error,
                "reason": self.reason}


def classify(asof: str | None, session: str, *, now: str | None = None,
             market: str | None = None, source_error: str | None = None,
             max_age_hours: float = MAX_AGE_HOURS) -> Freshness:
    """Decide the state for one market/session.

    `asof` is the timestamp of the newest input actually consumed (None if the
    pipeline produced nothing). `session` is the session the page claims to be
    about and MUST be supplied by the caller — never inferred here.

    Precedence: a source error is SOURCE_DOWN even if a previous snapshot is
    present, because the two have different causes and different fixes. An
    absent input with no source error is NOT_RUN, not STALE: there is nothing
    stale to point at, and reporting STALE would imply a number exists.
    """
    if source_error:
        return Freshness(SOURCE_DOWN, asof, None, session, market, source_error)
    if not asof:
        return Freshness(NOT_RUN, None, None, session, market)
    now_dt = _parse(now) if now else _dt.datetime.now(_dt.timezone.utc)
    age = (now_dt - _parse(asof)).total_seconds() / 3600.0
    state = FRESH if age <= max_age_hours else STALE
    return Freshness(state, asof, age, session, market)


_CN = {
    FRESH: ("資料新鮮", "var(--ok)"),
    STALE: ("資料過期", "var(--amber)"),
    NOT_RUN: ("本次冇跑", "var(--red)"),
    SOURCE_DOWN: ("數據源唔通", "var(--red)"),
}


def banner(f: Freshness, lang: str = "zh") -> str:
    """Render the visitor-visible banner.

    Shows as-of, how stale, and which market/session is affected (spec A1.3).
    Returns "" for FRESH — a fresh page carries no banner, so a page that is
    merely FRESH can never be mistaken for a guarded one in the test suite.
    """
    if f.state == FRESH:
        return ""
    if lang == "en":
        head = {"STALE": "Stale data", "NOT_RUN": "Not run for this session",
                "SOURCE_DOWN": "Data source unreachable"}[f.state]
        bits = [f"{head} — {f.market or ''} {f.session}".strip()]
        if f.state == STALE and f.age_hours is not None:
            bits.append(f"last input {f.asof}, {f.age_hours:.1f}h old (limit "
                        f"{MAX_AGE_HOURS}h)")
        elif f.state == NOT_RUN:
            bits.append("this session's pipeline produced no data; the figures "
                        "below are NOT this session's")
        elif f.state == SOURCE_DOWN and f.source_error:
            bits.append(f"vendor error: {f.source_error}")
        if f.reason:
            bits.append(f.reason)
        return ('<div class="sprint2-banner sprint2-banner--stale" role="status" '
                'data-state="%s" data-session="%s">%s</div>'
                % (f.state, f.session, " · ".join(bits)))
    label, colour = _CN[f.state]
    bits = [f"{label} · {f.market or ''} {f.session}".strip()]
    # The age line describes WHY it is stale when the age is the reason. When
    # a `reason` is present the staleness has another cause (a session the
    # market closed and we do not have), and printing "9.0h old (limit 36h)"
    # under a STALE heading reads as a limit that was not applied.
    if f.state == STALE and f.age_hours is not None and not f.reason:
        bits.append(f"最後輸入 {f.asof}，已過 {f.age_hours:.1f} 小時（上限 "
                    f"{MAX_AGE_HOURS} 小時）")
    elif f.state == NOT_RUN:
        bits.append("今次 session 冇跑出數據，下面嘅數字唔係今次嘅")
    elif f.state == SOURCE_DOWN and f.source_error:
        bits.append(f"數據源錯誤：{f.source_error}")
    if f.reason:
        bits.append(f.reason)
    return ('<div class="sprint2-banner sprint2-banner--stale" role="status" '
            'style="border-left:4px solid %s" data-state="%s" data-session="%s">'
            '%s</div>' % (colour, f.state, f.session, " · ".join(bits)))


def annotate(payload: dict, f: Freshness, lang: str = "zh") -> dict:
    """Attach the state (machine-readable) and its banner to an exported page
    payload. Returns a copy; does not mutate the caller's dict."""
    out = dict(payload)
    out["pipeline_state"] = f.as_dict()
    b = banner(f, lang)
    if b:
        out["staleness_banner"] = b
    return out