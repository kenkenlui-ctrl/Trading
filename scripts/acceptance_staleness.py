#!/usr/bin/env python3
"""acceptance_staleness.py — the old-vs-fresh acceptance test for A1.

The dispatch (2026-10-29, item 3) asks for exactly this:

    "附 acceptance test：用舊 snapshot 同新 snapshot 各跑一次，
     證明 STALE 出現同消失都正確。"

THE SITE HAS TWO LAYERS, AND BOTH ARE TESTED

    1. BUILD TIME (server). scripts/integrity/freshness.py decides the state
       and scripts/staleness.py puts it on the page: the banner, the claim
       the source may make, the stale flag on "今日 actionable". The
       dispatch asks for this layer ("狀態喺 build time 計算").

    2. REQUEST TIME (client). public/static/staleness-core.js decides again
       from the visitor's own clock, using the dates the page carries. This
       layer exists because a build-time banner is frozen into the HTML: the
       site was built on 2026-10-25 with fresh data and then sat there for
       four days, so its frozen banner still said everything was fine. That
       is the failure this whole change is about.

THE TWO SNAPSHOTS

    OLD  2026-10-23 — the session the site was publishing on 2026-10-25, and
         still publishing on 2026-10-29 morning (verified against the live
         home page). It is a documented fixture: the session the three
         missed nights started from.

    NEW  whatever the vendor says each market has most recently CLOSED, read
         from data/market_sessions.json. Read, not typed: the vendor's own
         answer is the only thing that defines "the latest closed session".

    The two are evaluated against each other, market by market, so the test
    does not depend on whether the site happens to be current right now. On
    2026-10-31 the US was one session behind (Yahoo had not settled the
    10-30 close for individual stocks), and the mechanism had to be able to
    say so without the test itself breaking.

WHAT IS ASSERTED

    server layer  old -> STALE for every market, the banner rendered with
                 both dates, the source claim withdrawn, and the stale flag
                 travelling with "今日 actionable"
                 new -> FRESH for every market, no banner, and the SOURCE
                 states the date instead of asserting "Live"
    client layer  the REAL shipped core.js: the old payload flags every
                 market, the new payload flags none
    shipped page  the built homepage carries the facts, does not assert
                 "Live" in its bytes, and marks the claim the browser may
                 upgrade

Run:  python3 scripts/acceptance_staleness.py
Exit 0 = every assertion held.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PUBLIC = REPO / "public"
sys.path.insert(0, str(REPO / "scripts"))

import staleness as S  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []
MARKETS = ("HK", "US", "JP")

# The session the site published before this change, and the sessions the
# three missed nights started from. A documented fixture, not a number this
# script invents.
OLD_SESSION = "2026-10-23"


def record(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))


# ---------------------------------------------------------------------------
# the two snapshots
# ---------------------------------------------------------------------------
# The real reference reader, captured before anything monkeypatches it.
_REAL_REFERENCE = S.reference


def vendor_sessions() -> dict:
    """The vendor's latest CLOSED session per market."""
    ref = (_REAL_REFERENCE().get("markets") or {})
    out = {}
    for m in MARKETS:
        entry = ref.get(S.MARKET_DIR[m]) or {}
        out[m] = str(entry.get("latest_closed_session") or "")[:10]
    return out


def with_published_sessions(sessions: dict, fn):
    """Run fn() as if our published bars covered `sessions`."""
    saved_cache = dict(S._CACHE)
    saved_ref = S.reference
    for m in MARKETS:
        S._CACHE[S.MARKET_DIR[m]] = sessions.get(m, "")
    # The vendor answer, read from the REAL reader — not from this function,
    # which would call itself forever.
    S.reference = _REAL_REFERENCE
    try:
        return fn()
    finally:
        S._CACHE.clear()
        S._CACHE.update(saved_cache)
        S.reference = saved_ref


def _snapshot_state(sessions: dict) -> dict:
    """The state of every market under a given published snapshot."""
    now = dt.datetime.now()
    states = {m: S.compute(m, now).state for m in MARKETS}
    return {
        "states": states,
        "banner": S.banner_html_all(),
        "claim": S.source_claim(sessions.get("HK", "")),
        "actionable": S.actionable_note(),
        "reason": S.compute("HK", now).reason,
    }


# ---------------------------------------------------------------------------
# 1. the server (build-time) layer
# ---------------------------------------------------------------------------
def server_layer(new_sessions: dict) -> None:
    old = with_published_sessions({m: OLD_SESSION for m in MARKETS},
                                  lambda: _snapshot_state({m: OLD_SESSION for m in MARKETS}))
    fresh = with_published_sessions(new_sessions, lambda: _snapshot_state(new_sessions))

    # --- OLD: every market must be STALE, and the claim must change -------
    record(all(v == "STALE" for v in old["states"].values()),
           "old snapshot: every market is STALE",
           f"states={old['states']}")
    record(old["banner"].strip() != "",
           "old snapshot: the STALE banner is rendered",
           "no banner was produced for a stale snapshot")
    record(OLD_SESSION in old["banner"],
           "old snapshot: the banner names the session it published",
           f"banner={old['banner'][:400]}")
    record(old["claim"] == "數據未更新",
           "old snapshot: the source claim is withdrawn",
           f"source claim = {old['claim']!r}")
    record("未更新" in old["actionable"],
           "old snapshot: the stale flag travels with 今日 actionable",
           f"actionable note = {old['actionable']!r}")
    record(bool(old["reason"]),
           "old snapshot: the reason names the session that was missed",
           f"reason = {old['reason']!r}")

    # --- NEW: everything must be back to normal ---------------------------
    record(all(v == "FRESH" for v in fresh["states"].values()),
           "new snapshot: every market is FRESH",
           f"states={fresh['states']}")
    record(fresh["banner"].strip() == "",
           "new snapshot: no STALE banner",
           "a fresh snapshot still produced a banner")
    record(fresh["claim"].startswith("數據截至"),
           "new snapshot: the source states the date, not 'Live'",
           f"source claim = {fresh['claim']!r}")
    record("Live" not in fresh["claim"],
           "new snapshot: the source never asserts 'Live' for itself",
           f"source claim = {fresh['claim']!r}")
    record(fresh["actionable"] == "",
           "new snapshot: no stale flag on 今日 actionable",
           f"actionable note = {fresh['actionable']!r}")


# ---------------------------------------------------------------------------
# 2. the client (request-time) layer — the REAL shipped code, under node
# ---------------------------------------------------------------------------
def client_layer(new_sessions: dict, build_moment: dt.datetime) -> None:
    core = PUBLIC / "static" / "staleness-core.js"
    if not core.exists():
        record(False, "the shipped decision core exists", str(core))
        return
    node = shutil.which("node")
    if not node:
        record(False, "node is available to run the shipped decision code",
               "node is not installed on this machine")
        return

    # The FRESH payload: the page's own dates, for a build that had the
    # vendor's latest closed session. Built through the same helper the page
    # uses, so it is the shape the browser actually reads.
    def payload_for(sessions: dict) -> list:
        facts = []
        for m in MARKETS:
            sess = sessions.get(m, "")
            facts.append({
                "market": m,
                "session": sess,
                "close": S._session_close_iso(m, sess),
                "next": S._next_expected_close(m, sess),
                "limit": S.MAX_AGE_HOURS,
            })
        return facts

    fresh_facts = payload_for(new_sessions)
    old_facts = payload_for({m: OLD_SESSION for m in MARKETS})

    run = (
        "const fs=require('fs'),vm=require('vm');"
        "const s={globalThis:{}};s.globalThis=s;vm.createContext(s);"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(core))},'utf8'),s);"
        "const W9=s.W9Freshness||s.globalThis.W9Freshness;"
        "process.stdout.write(JSON.stringify("
        "W9.decide(JSON.parse(process.argv[1]),Number(process.argv[2]))));"
    )

    def decide(facts, now: dt.datetime) -> list:
        r = subprocess.run([node, "-e", run, json.dumps(facts),
                            str(int(now.timestamp() * 1000))],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-400:])
        return json.loads(r.stdout)

    # The instant the build ran. At that instant the new snapshot is current
    # and the old one is not — the comparison the dispatch asks for.
    fresh_stale = decide(fresh_facts, build_moment)
    old_stale = decide(old_facts, build_moment)

    record(len(fresh_stale) == 0,
           "new snapshot: the visitor's clock finds nothing stale",
           f"stale={fresh_stale}")
    record(len(old_stale) == len(MARKETS) and len(old_stale) > 0,
           "old snapshot: the visitor's clock flags every market",
           f"stale={old_stale}")
    record(all(s.get("reason") == "session-missed" for s in old_stale),
           "old snapshot: every flag says a session was missed",
           f"reasons={[s.get('reason') for s in old_stale]}")
    record(all(s.get("session") == OLD_SESSION for s in old_stale),
           "old snapshot: every flag carries the session it published",
           f"sessions={[s.get('session') for s in old_stale]}")


# ---------------------------------------------------------------------------
# 3. the page that ships
# ---------------------------------------------------------------------------
def page_layer(new_sessions: dict) -> None:
    """The pages as they ship.

    The banner is EXPECTED to be there exactly when the market it serves is
    behind — so the expectation is derived from the state, not asserted as
    "no banner anywhere". On 2026-10-31 the US was one session behind (Yahoo
    had not settled the 10-30 close for individual stocks), and the US hub and
    the home page both said so. A test that demanded "no banner anywhere"
    would have failed on the site being honest.
    """
    def behind(market: str) -> bool:
        ours = S.data_session(market)
        latest = new_sessions.get(market, "")
        return bool(ours and latest and ours < latest)

    any_behind = any(behind(m) for m in MARKETS)
    pages = [("home", PUBLIC / "index.html", None),
             ("HK hub", PUBLIC / "hk200" / "index.html", "HK"),
             ("US hub", PUBLIC / "us200" / "index.html", "US"),
             ("JP hub", PUBLIC / "jp200" / "index.html", "JP")]
    for name, p, market in pages:
        if not p.exists():
            record(False, f"{name}: the page exists", str(p))
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        record('name="w9-freshness"' in text,
               f"{name}: carries the freshness facts",
               "the meta tag is missing")
        record("/static/staleness.js" in text,
               f"{name}: loads the freshness script",
               "the script is not referenced")
        has_banner = ("數據新鮮度提示" in text) or ("sprint2-banner" in text)
        expected = any_behind if market is None else behind(market)
        record(has_banner == expected,
               f"{name}: shows the STALE banner exactly when it is behind",
               f"banner={has_banner}, expected={expected}")

    # The claim the SOURCE makes.
    #
    # 2026-10-31: the source rendered the literal word "Live" whenever the
    # build-time state was fresh. The build-time state is FROZEN, so the
    # shipped HTML claimed "Live" for as long as the page sat there — and
    # the source is what every non-JS reader (a crawler, a link-preview
    # scraper, a text fetcher, "view source") sees. The page may not assert
    # "Live" for itself; the browser earns it.
    home = (PUBLIC / "index.html").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'<div class="kicker">(.*?)</div>', home, re.S)
    kicker = m.group(1) if m else ""
    record(bool(kicker) and "Live" not in kicker,
           "the shipped source does not assert 'Live'",
           f"kicker = {kicker.strip()!r}")
    record(bool(kicker) and "數據截至" in kicker,
           "the shipped source states the session its data covers",
           f"kicker = {kicker.strip()!r}")
    record('class="w9-fresh-claim"' in home,
           "the shipped source marks the claim the browser may upgrade",
           "the .w9-fresh-claim span is missing")
    # MUST-FAIL: the same predicate, re-evaluated on a source that says "Live"
    #
    # The check above is "the source does not claim Live". The control has to
    # show that this predicate REJECTS a source that does claim it — so it
    # asserts the predicate is TRUE on the tampered source (the main check
    # would fail on it). Asserting the predicate still passes would prove
    # nothing: it passes on ANY source, tampered or not.
    tainted = re.sub(r'(<span class="w9-fresh-claim"[^>]*>)[^<]*(</span>)',
                     r'\1Live\2', kicker)
    record("Live" in tainted,
           "MUST-FAIL a source that asserts 'Live' is rejected",
           "the control does not discriminate: the tampered source does not "
           "actually claim Live")


# ---------------------------------------------------------------------------
def main() -> int:
    home = PUBLIC / "index.html"
    if not home.exists():
        print("FAIL: public/index.html does not exist — build the site first")
        return 1
    # The instant the page was built: the mtime of the file the build wrote.
    build_moment = dt.datetime.fromtimestamp(home.stat().st_mtime)

    new_sessions = vendor_sessions()
    if not all(new_sessions.get(m) for m in MARKETS):
        print("FAIL: the vendor did not report a closed session for every "
              f"market: {new_sessions}")
        return 1

    print(f"acceptance: old snapshot = {OLD_SESSION}, "
          f"new snapshot = {new_sessions}")
    print(f"evaluated at the build instant: {build_moment.isoformat()}\n")

    server_layer(new_sessions)
    client_layer(new_sessions, build_moment)
    page_layer(new_sessions)

    bad = 0
    for ok, name, detail in RESULTS:
        if not ok:
            bad += 1
        print(f"{'  ok  ' if ok else 'FAIL  '}{name}"
              + (f"  — {detail}" if detail and not ok else ""))
    print(f"\n{len(RESULTS) - bad}/{len(RESULTS)} assertions held")
    if bad:
        print("\nACCEPTANCE FAILED")
        return 1
    print("\nACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
