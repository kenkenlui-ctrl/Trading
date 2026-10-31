"""staleness.py — the SITE-FACING adapter for the A1 freshness contract.

    The contract itself lives in scripts/integrity/freshness.py (Sprint 2,
    Phase A1): the four states, the 36-hour limit, the banner. That module is
    the single implementation and it is guarded by the sprint2 self-test
    suite. This file does NOT re-implement any of it — it supplies the
    INPUTS that module demands and puts the result on the pages.

    2026-10-25: an earlier draft of this file implemented its own state
    machine. That was duplication of a guarded module, and it was removed the
    same day. One implementation, one place to be wrong.

WHAT THE CONTRACT NEEDS AND DOES NOT KNOW
    integrity/freshness.py is deliberately told things it cannot guess:

      asof        the timestamp of the newest input actually consumed
      session     the session the page claims to be about — "MUST be supplied
                  by the caller — never inferred here", because a guessed
                  session date is how "stale" silently becomes "fresh"
      source_error  set when the vendor could not be reached

    So this adapter's whole job is those three inputs:

      asof          the close of the session our published bars cover, read
                    from public/<mkt>/ohlc/ (the modal last date — the same
                    rule the nav badge uses)
      session       the market's latest CLOSED session, measured
                    independently of our 200 names, written by
                    scripts/market_sessions.py from the benchmark index's
                    last close
      source_error  the vendor error, when the check could not run

    One extra rule is applied here, on top of the contract's age rule:
    a market that has closed a session we do not have is STALE even when
    our input is younger than 36 hours. The age limit alone would call that
    FRESH (one session behind, fetched yesterday = young), which is exactly
    the case the dispatch named.

THE FLAG
    config/integrity_flags.json -> staleness_banner_flag decides whether the
    banner is RENDERED. The state is always computed. A flag that hides the
    banner cannot change what the state is.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from integrity import freshness as FR  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
PUBLIC = REPO / "public"
FLAGS = REPO / "config" / "integrity_flags.json"
SESSIONS = REPO / "data" / "market_sessions.json"

# The freshness contract (Sprint 2 A1.1) — kept here so the adapter and the
# module agree on what the limit is.
MAX_AGE_HOURS = FR.MAX_AGE_HOURS

# market label -> the directory the site publishes that market under
MARKET_DIR = {"HK": "hk200", "US": "us200", "JP": "jp200"}
MARKET_LABEL = {"HK": "港股", "US": "美股", "JP": "日股"}
# session close, as (IANA timezone, local hour) — zoneinfo handles the DST
# difference between New York and Hong Kong, which a hardcoded HKT offset
# gets wrong by an hour for half the year.
SESSION_CLOSE = {
    "HK": ("Asia/Hong_Kong", 16),
    "US": ("America/New_York", 16),
    "JP": ("Asia/Tokyo", 15),
}

FRESH = FR.FRESH
STALE = FR.STALE
NOT_RUN = FR.NOT_RUN
SOURCE_DOWN = FR.SOURCE_DOWN


def flags() -> dict:
    try:
        return json.loads(FLAGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def enabled() -> bool:
    """Whether the banner is rendered. The STATE is always computed."""
    return bool(flags().get("staleness_banner_flag"))


def reference() -> dict:
    """The vendor reference: what each market has most recently closed."""
    try:
        return json.loads(SESSIONS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _modal_last_date(market_dir: str) -> str:
    """The session the published bars cover.

    Modal (most common) last date, for the same reason the nav badge uses
    the modal: a handful of suspended names cannot drag the market's session
    backwards.
    """
    from collections import Counter

    d = PUBLIC / market_dir / "ohlc"
    if not d.is_dir():
        return ""
    c: Counter = Counter()
    for p in d.glob("*_ohlc.json"):
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rows, list) and rows:
            last = rows[-1]
            if isinstance(last, dict) and last.get("date"):
                c[str(last["date"])[:10]] += 1
    if not c:
        return ""
    return c.most_common(1)[0][0]


_CACHE: dict = {}


def data_session(market: str) -> str:
    key = MARKET_DIR.get(market, market.lower())
    if key not in _CACHE:
        _CACHE[key] = _modal_last_date(key)
    return _CACHE[key]


def _session_close_iso(market: str, session_date: str) -> str:
    """The session's close as a timestamp the contract can age.

    The close is computed in the market's own timezone and then converted to
    HKT, so a US session that closed at 16:00 New York is aged from
    04:00 HKT in summer and 05:00 HKT in winter.
    """
    if not session_date:
        return ""
    try:
        from zoneinfo import ZoneInfo

        tzname, hour = SESSION_CLOSE.get(market, ("Asia/Hong_Kong", 16))
        close = datetime.fromisoformat(f"{session_date}T{hour:02d}:00:00").replace(
            tzinfo=ZoneInfo(tzname))
        return close.astimezone(ZoneInfo("Asia/Hong_Kong")).isoformat()
    except Exception:
        h = SESSION_CLOSE.get(market, ("Asia/Hong_Kong", 16))[1]
        return f"{session_date}T{h:02d}:00:00+08:00"


def _ref_age_hours(entry: dict, now: datetime) -> float | None:
    checked_at = str(entry.get("checked_at") or "")
    if not checked_at:
        return None
    try:
        ca = datetime.fromisoformat(checked_at)
    except ValueError:
        return None
    if ca.tzinfo is None:
        ca = ca.replace(tzinfo=now.astimezone().tzinfo)
    elif now.tzinfo is None:
        now = now.astimezone()
    return (now - ca).total_seconds() / 3600.0


def compute(market: str, now: datetime | None = None) -> FR.Freshness:
    """The freshness state of one market. Never raises."""
    now = now or datetime.now()
    key = MARKET_DIR.get(market, market.lower())
    label = MARKET_LABEL.get(market, market)

    data_sess = data_session(market)
    ref = reference()
    entry = (ref.get("markets") or {}).get(key) or {}
    latest = str(entry.get("latest_closed_session") or "")[:10]
    status = str(entry.get("status") or "")
    error = str(entry.get("error") or "")

    # 1. the vendor could not be reached — its own state, checked first
    if status == "source_down":
        return FR.classify(None, latest or (data_sess or "—"), market=label,
                            source_error=error or "vendor unreachable")

    # 2. the market's session check did not run, or ran too long ago to stand
    #    behind -> we cannot say what the market has done since
    if not entry:
        return FR.classify(None, latest or (data_sess or "—"), market=label)
    ref_age = _ref_age_hours(entry, now)
    if ref_age is None or ref_age > MAX_AGE_HOURS:
        return FR.classify(None, latest or (data_sess or "—"), market=label)

    # 3. no input at all
    if not data_sess:
        return FR.classify(None, latest or "—", market=label)

    # 4. the contract's own rule: age of the newest input vs the 36h limit
    f = FR.classify(_session_close_iso(market, data_sess),
                    latest or data_sess, market=label)

    # 5. the extra rule: we are missing a session the market has closed.
    #    The age limit cannot see this — one session behind and fetched
    #    yesterday is still a young input.
    missing = bool(latest and data_sess < latest)
    if missing:
        reason = (f"市場最新已收市 session 係 {latest}，但本站數據只到 {data_sess}"
                  f"（缺咗 {latest} 嘅數據）")
        return FR.Freshness(STALE, f.asof, f.age_hours, latest, label, None, reason)

    # 6. The age limit fired, but we have JUST confirmed (the reference is
    #    inside the limit) that the market has not closed a session since
    #    our data's session. Nothing is missing — the age limit exists to
    #    catch a MISSED session, and there is none. Without this exemption
    #    the site would cry wolf every Saturday and every Monday morning:
    #    Friday's close is more than 36 hours old by Saturday morning, but
    #    no session has closed since.
    if f.state == STALE and latest and data_sess == latest:
        return FR.Freshness(FRESH, f.asof, f.age_hours, latest, label)

    return f


def banner_html(market: str) -> str:
    """The banner for one market. Empty when there is nothing to say."""
    if not enabled():
        return ""
    f = compute(market)
    if f.state == FRESH:
        return ""
    b = FR.banner(f, "zh")
    # the library's banner is a bare div; wrap it so it reads as a banner
    return ('<div class="container" style="margin:18px 0 0">'
            f'<div class="info-card" style="padding:0;border-left:none">{b}</div></div>')


def affected_markets() -> list[str]:
    """Markets whose state is not FRESH."""
    return [m for m in ("HK", "US", "JP") if compute(m).state != FRESH]


def source_claim(session_label: str = "") -> str:
    """What the SHIPPED HTML is allowed to say about its own freshness.

    The dispatch is explicit: when a market's data is older than that market's
    latest closed session, the page must not present the signals as "Live" or
    "今日 actionable" without the stale flag attached. So the word itself
    changes — a banner BELOW the claim is not enough, because the claim is
    what a reader skims.

    2026-10-31: this used to return the literal word "Live" whenever the
    BUILD-TIME state was fresh, and the word was written into the HTML. But
    a build-time state is frozen: the site was built on 2026-10-29 with fresh
    data and sat there until 2026-10-31, so the shipped source still claimed
    "Live" three sessions later. The request-time check caught it in a
    browser, and the source kept claiming it to everyone who does not run
    JavaScript — a crawler, a link-preview scraper, a text fetcher, a reader
    with JS off, and "view source". Kenneth checked the source on 2026-10-31
    and saw "Live" with no notice, and he was right to.

    So the SOURCE states the one thing it knows for certain — the session its
    data covers — and the visitor's clock decides whether that may be called
    "Live". A page that cannot run the script says the DATE, which is true,
    instead of "Live", which might not be.
    """
    if not enabled():
        return "Live"
    if affected_markets():
        return "數據未更新"
    return f"數據截至 {session_label}" if session_label else "數據截至 —"


def actionable_note() -> str:
    """The stale flag that has to travel with the '今日 actionable' claim."""
    if not enabled():
        return ""
    bad = affected_markets()
    if not bad:
        return ""
    parts = []
    for m in bad:
        f = compute(m)
        parts.append(f"{MARKET_LABEL[m]}數據截至 {data_session(m) or '—'}")
    return "（" + "；".join(parts) + "，未更新）"


def banner_html_all() -> str:
    """One banner for a page that serves several markets (the home page).

    Each affected market gets its own line: the visitor has to know WHICH
    market is behind, not merely that something is.
    """
    if not enabled():
        return ""
    rows = []
    for m in ("HK", "US", "JP"):
        f = compute(m)
        if f.state == FRESH:
            continue
        # The age line only makes sense when the age is the reason. When the
        # reason is a MISSED session, printing "9.0h old (limit 36h)" under a
        # stale heading reads as a limit that was not applied.
        age_txt = ""
        if f.state == STALE and f.age_hours is not None and not f.reason:
            age_txt = (f"最後輸入 {f.asof}，已過 {f.age_hours:.1f} 小時"
                       f"（上限 {MAX_AGE_HOURS} 小時）")
        rows.append(
            f'<li><b>{MARKET_LABEL[m]}</b>：{f.reason or age_txt}'
            f'受影響 session：{f.session or "—"}。</li>'
        )
    if not rows:
        return ""
    return (
        '<div class="container" style="margin:18px 0 0">'
        '<div class="info-card" style="border-left:3px solid var(--amber)">'
        '<h2 style="margin-top:0;font-size:1rem">⚠ 數據新鮮度提示</h2>'
        '<ul style="font-size:0.85rem;margin:6px 0 0;padding-left:1.1rem">'
        + "".join(rows) +
        '</ul>'
        '<p style="font-size:0.8rem;opacity:0.75;margin:8px 0 0">'
        '頁面下面嘅數字係截至上面列出嘅日期，唔係最新。'
        '落單前請以券商或官方 App 嘅報價為準。</p></div></div>'
    )


def _next_expected_close(market: str, session_date: str) -> str:
    """When the NEXT session was due to close, given this session's date.

    Weekday rule only — a trading calendar is not knowable from a session
    date, and guessing one is how "stale" silently becomes "fresh". The
    page's client-side check rolls this forward over weekends; a market
    holiday can therefore flag a session that never happened, which is the
    safe direction to be wrong in.
    """
    if not session_date:
        return ""
    try:
        d = date.fromisoformat(session_date) + timedelta(days=1)
    except ValueError:
        return ""
    while d.weekday() >= 5:          # Saturday, Sunday
        d += timedelta(days=1)
    return _session_close_iso(market, d.isoformat())


def client_payload(market: str) -> dict | None:
    """The facts the page needs to judge freshness at REQUEST time.

    The build-time state is frozen into the HTML; a site that sat for four
    days cannot know that from a banner computed when the data was fresh.
    So the page carries the dates and the visitor's clock decides.
    """
    sess = data_session(market)
    if not sess:
        return None
    return {
        "market": market,
        "session": sess,
        "close": _session_close_iso(market, sess),
        "next": _next_expected_close(market, sess),
        "limit": MAX_AGE_HOURS,
    }


def client_payload_all() -> list:
    out = []
    for m in ("HK", "US", "JP"):
        p = client_payload(m)
        if p:
            out.append(p)
    return out


def client_meta(markets: list | None = None) -> str:
    """The <meta name="w9-freshness"> content for a page.

    JSON with single quotes as the attribute delimiter, because the payload
    contains double quotes.
    """
    import json as _json

    if markets is None:
        payload = client_payload_all()
    else:
        payload = [p for p in (client_payload(m) for m in markets) if p]
    # Valid JSON, double quotes and all — the HTML attribute that carries it
    # uses single quotes instead. Rewriting the quotes here produced
    # {'market':'HK'} which JSON.parse rejects, so the page's own check
    # would have silently done nothing.
    return _json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def summary_json() -> dict:
    """Machine-readable state, for the pipeline summary and for tests."""
    now = datetime.now()
    return {m: compute(m, now).as_dict() for m in ("HK", "US", "JP")}
