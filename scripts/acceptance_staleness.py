#!/usr/bin/env python3
"""acceptance_staleness.py — the old-vs-fresh acceptance test for A1.

The dispatch (2026-10-29, item 3) asks for exactly this:

    "附 acceptance test：用舊 snapshot 同新 snapshot 各跑一次，
     證明 STALE 出現同消失都正確。"

WHY THE SITE HAS TWO LAYERS, AND WHY BOTH ARE TESTED

    1. BUILD TIME (server). scripts/integrity/freshness.py decides the state
       and scripts/staleness.py puts it on the page: the banner, the "Live"
       claim, the stale flag on "今日 actionable". The dispatch asks for this
       layer ("狀態喺 build time 計算").

    2. REQUEST TIME (client). public/static/staleness-core.js decides again
       from the visitor's own clock, using the dates the page carries. This
       layer exists because a build-time banner is frozen into the HTML: the
       site was built on 2026-10-25 with fresh data and then sat there for
       four days, so its frozen banner still said everything was fine. That
       is the failure this whole change is about.

    A STALE notice that only ever appears at build time would have said
    nothing on 10-26, 10-27 and 10-28 — which is exactly the night the
    failure happened.

THE TWO SNAPSHOTS

    OLD  the snapshot the site was still publishing on 2026-10-29 morning:
         every market on 2026-10-23, while the market had already closed
         2026-10-28. This is the state the site sat in for the three missed
         nights, reconstructed from the session dates the pipeline was given
         (ASOF_* = 2026-10-28) — not from a number typed into this file.

    NEW  the snapshot this build published: every market on 2026-10-28.

    Both are evaluated at the SAME instant — the moment the page was built —
    so the only thing that differs is the snapshot. That is what makes the
    comparison a test rather than a demonstration.

WHAT IS ASSERTED

    server layer  old -> STALE (and the "Live" claim becomes "數據未更新",
                 and the stale flag travels with "今日 actionable")
                 new -> FRESH (and "Live" stays "Live", no banner)
    client layer  old payload -> the shipped core.js flags all three markets
                 new payload -> the shipped core.js flags none
    shipped page  the built homepage carries the facts, shows no stale
                 banner, and still claims "Live"

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


def record(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))


# ---------------------------------------------------------------------------
# the two snapshots
# ---------------------------------------------------------------------------
# The OLD session is a documented fixture: 2026-10-23 is what the site was
# publishing on 2026-10-25, and it is what it was still publishing on
# 2026-10-29 morning (verified against the live home page). It is history,
# not a number this script invents.
OLD_SESSION = "2026-10-23"

# The NEW session is READ OUT of the built page, not typed: the payload the
# page carries is the snapshot under test.
def _new_session() -> str:
    import re as _re
    home = (PUBLIC / "index.html").read_text(encoding="utf-8", errors="replace")
    m = _re.search(r"<meta name=\"w9-freshness\" content='([^']*)'", home)
    if not m:
        return ""
    try:
        facts = json.loads(m.group(1))
    except Exception:
        return ""
    sessions = {str(f.get("session")) for f in facts if f.get("session")}
    return sorted(sessions)[0] if len(sessions) == 1 else ""


NEW_SESSION = _new_session()


def old_reference() -> dict:
    """The vendor's view for the OLD snapshot.

    We checked the market NOW (as the nightly job would) and the market has
    closed 2026-10-28; our published bars stop at 2026-10-23. That is the
    10-26/27/28 failure, reconstructed — the check is recent, so the state
    is STALE for the right reason (a missed session) and not merely because
    the reference itself is too old to stand behind.
    """
    now = dt.datetime.now().astimezone()
    return {
        "generated_at": now.isoformat(),
        "markets": {
            "hk200": {"latest_closed_session": NEW_SESSION, "status": "ok",
                      "checked_at": now.isoformat(), "error": ""},
            "us200": {"latest_closed_session": NEW_SESSION, "status": "ok",
                      "checked_at": now.isoformat(), "error": ""},
            "jp200": {"latest_closed_session": NEW_SESSION, "status": "ok",
                      "checked_at": now.isoformat(), "error": ""},
        },
    }


def with_old_snapshot(fn):
    """Run fn() as if the site were still publishing OLD_SESSION."""
    saved_cache = dict(S._CACHE)
    saved_ref = S.reference
    S._CACHE.update({"hk200": OLD_SESSION, "us200": OLD_SESSION,
                     "jp200": OLD_SESSION})
    S.reference = old_reference
    try:
        return fn()
    finally:
        S._CACHE.clear()
        S._CACHE.update(saved_cache)
        S.reference = saved_ref


# ---------------------------------------------------------------------------
# 1. the server (build-time) layer
# ---------------------------------------------------------------------------
def server_layer(now: dt.datetime) -> None:
    old = with_old_snapshot(lambda: {
        "states": {m: S.compute(m, now).state for m in ("HK", "US", "JP")},
        "banner": S.banner_html_all(),
        "claim": S.source_claim(NEW_SESSION),
        "actionable": S.actionable_note(),
        "reason": S.compute("HK", now).reason,
    })
    fresh = {
        "states": {m: S.compute(m, now).state for m in ("HK", "US", "JP")},
        "banner": S.banner_html_all(),
        "claim": S.source_claim(NEW_SESSION),
        "actionable": S.actionable_note(),
    }

    # --- OLD: every market must be STALE, and the claim must change -------
    record(all(v == "STALE" for v in old["states"].values()),
           "old snapshot: every market is STALE",
           f"states={old['states']}")
    record(old["banner"].strip() != "",
           "old snapshot: the STALE banner is rendered",
           "no banner was produced for a stale snapshot")
    record(OLD_SESSION in old["banner"] and NEW_SESSION in old["banner"],
           "old snapshot: the banner names the data date AND the missed session",
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
    record(fresh["claim"] == f"數據截至 {NEW_SESSION}",
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
def client_layer(build_moment: dt.datetime) -> None:
    core = PUBLIC / "static" / "staleness-core.js"
    if not core.exists():
        record(False, "the shipped decision core exists", str(core))
        return
    node = shutil.which("node")
    if not node:
        record(False, "node is available to run the shipped decision code",
               "node is not installed on this machine")
        return

    # FRESH payload: read out of the page that was actually built.
    home = (PUBLIC / "index.html").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<meta name=\"w9-freshness\" content='([^']*)'", home)
    if not m:
        record(False, "the built homepage carries the freshness facts",
               "no w9-freshness meta tag")
        return
    fresh_facts = json.loads(m.group(1))
    record(bool(fresh_facts),
           "the built homepage carries the freshness facts",
           "the meta tag is empty")

    # The claim is the browser's to earn: the script must be the only thing
    # that can write "Live", and it must write "數據未更新" instead when the
    # data is behind.
    js = PUBLIC / "static" / "staleness.js"
    if js.exists():
        jstext = js.read_text(encoding="utf-8", errors="replace")
        record("w9-fresh-claim" in jstext and "'Live'" in jstext
               and "數據未更新" in jstext,
               "the shipped script owns the 'Live' claim",
               "the script does not write the claim")
    else:
        record(False, "the shipped script owns the 'Live' claim",
               "public/static/staleness.js is missing")

    # OLD payload: the same shape, the session the site used to publish.
    old_facts = []
    for f in fresh_facts:
        o = dict(f)
        o["session"] = OLD_SESSION
        o["close"] = S._session_close_iso(f["market"], OLD_SESSION)
        o["next"] = S._next_expected_close(f["market"], OLD_SESSION)
        old_facts.append(o)

    run = (
        "const fs=require('fs'),vm=require('vm');"
        "const s={globalThis:{}};s.globalThis=s;vm.createContext(s);"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(core))},'utf8'),s);"
        "const W9=s.W9Freshness||s.globalThis.W9Freshness;"
        "process.stdout.write(JSON.stringify("
        "W9.decide(JSON.parse(process.argv[1]),Number(process.argv[2]))));"
    )
    now_ms = int(build_moment.timestamp() * 1000)

    def decide(facts):
        r = subprocess.run([node, "-e", run, json.dumps(facts), str(now_ms)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-400:])
        return json.loads(r.stdout)

    fresh_stale = decide(fresh_facts)
    old_stale = decide(old_facts)

    record(len(fresh_stale) == 0,
           "new snapshot: the visitor's clock finds nothing stale",
           f"stale={fresh_stale}")
    record(len(old_stale) == len(old_facts) and len(old_stale) > 0,
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
def page_layer() -> None:
    pages = [("home", PUBLIC / "index.html"),
             ("HK hub", PUBLIC / "hk200" / "index.html"),
             ("US hub", PUBLIC / "us200" / "index.html"),
             ("JP hub", PUBLIC / "jp200" / "index.html")]
    for name, p in pages:
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
        record("sprint2-banner" not in text,
               f"{name}: shows no stale banner",
               "a stale banner is baked into a fresh page")

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
    # show that this predicate REJECTS a source that does claim it — so the
    # control asserts the predicate is TRUE on the tampered source (i.e. the
    # main check would fail on it). Asserting the predicate still passes
    # would prove nothing: it would pass on ANY source, tampered or not.
    tainted = re.sub(r'(<span class="w9-fresh-claim"[^>]*>)[^<]*(</span>)',
                     r'\1Live\2', kicker)
    record("Live" in tainted,
           "MUST-FAIL a source that asserts 'Live' is rejected",
           "the control does not discriminate: the tampered source does not "
           "actually claim Live, so the check would not have rejected it")


# ---------------------------------------------------------------------------
def main() -> int:
    home = PUBLIC / "index.html"
    if not home.exists():
        print("FAIL: public/index.html does not exist — build the site first")
        return 1
    # The instant the page was built: the mtime of the file the build wrote.
    build_moment = dt.datetime.fromtimestamp(home.stat().st_mtime)

    print(f"acceptance: old snapshot = {OLD_SESSION}, "
          f"new snapshot = {NEW_SESSION}")
    print(f"evaluated at the build instant: {build_moment.isoformat()}\n")

    if not NEW_SESSION:
        print("FAIL: the built page carries no single freshness session — "
              "the payload is missing or the markets disagree")
        return 1

    server_layer(build_moment)
    client_layer(build_moment)
    page_layer()

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
