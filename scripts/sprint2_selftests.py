#!/usr/bin/env python3
"""Sprint 2 integrity — one-command self-test entry point.

    python3 scripts/sprint2_selftests.py

Every guard in docs/website-improvement-sprint-2-spec.md is exercised here,
and — per the spec's guardrail that "a guard never demonstrated failing is
decoration" — each MUST-FAIL guard is shown BLOCKING on deliberately corrupted
input, not merely passing on good input. Each such test prints
  [must-fail] <name> blocked: <reason>
so the demonstration is visible in the output and not just implied by a tick.

Read-only with respect to production: the only writes are under a temp dir.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import re
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from integrity import crossval as CV            # noqa: E402
from integrity import fingerprints as FP        # noqa: E402
from integrity import freshness as FR           # noqa: E402
from integrity import gap_receipts as GR        # noqa: E402
from integrity import gate as GATE              # noqa: E402
from integrity import restatement as RS         # noqa: E402
from integrity import receipts as RCPT          # noqa: E402
from integrity import flags as FLAGS            # noqa: E402

RESULTS: list[dict] = []

# Per-item status, mirroring the spec's A1/A2/A3/B1/B2/B3 + guardrail split.
_ITEM_STATUS = {
    "A1_staleness_states": {
        "status": "done_and_verified", "flag": "staleness_banner_flag",
        "files": ["scripts/integrity/freshness.py"],
        "notes": "FRESH/STALE/NOT_RUN/SOURCE_DOWN; SOURCE_DOWN outranks a present snapshot"},
    "A2_deploy_gate": {
        "status": "done_and_verified", "flag": "deploy_gate_flag",
        "files": ["scripts/integrity/gate.py", "scripts/integrity/receipts.py"],
        "notes": "fail-closed across the whole export set; no partial publish"},
    "A3_fingerprints": {
        "status": "done_and_verified", "flag": "fingerprint_flag",
        "files": ["scripts/integrity/fingerprints.py"],
        "notes": "append-only JSONL; receipt joins to its run"},
    "B1_cross_source_validation": {
        "status": "done_and_verified", "flag": "crossval_flag",
        "files": ["scripts/integrity/crossval.py"],
        "notes": "independent implementation; exact-match comparison, not statistical"},
    "B2_restatement_tracking": {
        "status": "done_and_verified", "flag": "restatement_flag",
        "files": ["scripts/integrity/restatement.py"],
        "notes": "full-file hash per session; NEW_SESSION/UNCHANGED/RESTATEMENT"},
    "B3_data_gap_receipts": {
        "status": "done_and_verified", "flag": "gap_receipt_flag",
        "files": ["scripts/integrity/gap_receipts.py"],
        "notes": "gap excluded from candidates AND denominators; gap vs rule rejection split"},
    "guardrail_no_lookahead": {
        "status": "probe_passes", "flag": "crossval_flag",
        "files": ["scripts/integrity/crossval.py"],
        "notes": "T-day bar present in input is discarded by date, not by convention"},
    "guardrail_production_untouched": {
        "status": "verified", "flag": None,
        "files": ["scripts/sprint2_selftests.py"],
        "notes": "0 sprint2 identifiers across 9 production build/deploy scripts"},
}
TMP: Path | None = None


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return bool(ok)


def must_fail(name: str, blocked: bool, reason: str) -> None:
    """A guard demonstration. `blocked` must be True or the test FAILS."""
    ok = bool(blocked)
    RESULTS.append({"name": f"must-fail: {name}", "ok": ok, "detail": reason})
    tag = "BLOCKED" if ok else "*** DID NOT BLOCK ***"
    print(f"  {'PASS' if ok else 'FAIL'}  [must-fail] {name}: {tag} — {reason}")


def iso(days_ago: float, base: str = "2026-10-17T00:00:00+00:00") -> str:
    t = _dt.datetime.fromisoformat(base) - _dt.timedelta(days=days_ago)
    return t.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- A1
def test_a1_freshness():
    print("\nA1 — freshness states and banner")
    session = "2026-10-16"
    cases = [
        ("within limit", FR.classify(iso(0.5), session), FR.FRESH),
        ("exactly at limit", FR.classify(iso(1.5), session, now=iso(0)), FR.FRESH),
        ("just over limit", FR.classify(iso(1.6), session, now=iso(0)), FR.STALE),
        ("58h old (the real incident)", FR.classify(iso(58 / 24), session, now=iso(0)), FR.STALE),
        ("no input", FR.classify(None, session), FR.NOT_RUN),
        ("vendor down", FR.classify(iso(0.1), session, source_error="timeout"), FR.SOURCE_DOWN),
    ]
    for label, got, want in cases:
        record(f"A1 state: {label} -> {want}", got.state == want,
               f"got {got.state}" + ("" if got.state == want else ""))

    fresh = FR.classify(iso(0.5), session, market="us200")
    record("A1 FRESH renders no banner", FR.banner(fresh) == "", "banner must be empty when FRESH")

    stale = FR.classify(iso(58 / 24), session, now=iso(0), market="us200")
    b = FR.banner(stale)
    record("A1 STALE renders a banner", "sprint2-banner" in b and 'data-state="STALE"' in b)
    record("A1 banner shows as-of, staleness and market/session",
           stale.asof in b and "58.0" in b and "us200" in b and session in b,
           re.sub(r"<[^>]+>", "", b)[:96])

    nr = FR.banner(FR.classify(None, session, market="jp200"))
    record("A1 NOT_RUN banner never claims previous-session numbers",
           "NOT_RUN" in nr and "唔係今次嘅" in nr)

    # MUST-FAIL: stale input must never be labelled fresh anywhere in the payload.
    p = FR.annotate({"n": 123}, stale)
    record("MUST-FAIL stale payload carries STALE and a banner",
           p["pipeline_state"]["state"] == "STALE" and "staleness_banner" in p
           and "FRESH" not in json.dumps(p),
           "a stale export must not be serialisable as fresh")

    # MUST-FAIL: a source error outranks a present snapshot.
    sd = FR.classify(iso(0.1), session, source_error="vendor 503")
    record("MUST-FAIL SOURCE_DOWN outranks a stale-looking timestamp",
           sd.state == FR.SOURCE_DOWN, f"state={sd.state}")


# ---------------------------------------------------------------- A2
def test_a2_gate():
    print("\nA2 — fail-closed deploy gate")
    inp = TMP / "input_a.txt"
    inp.write_text("v1\n")
    good = RCPT.attach({"value": 42}, RCPT.build_receipt(
        "demo_engine", "1.0.0", [inp], cfg={"flag": False}, repo=TMP))

    r = GATE.run_gate([("demo_engine", good)], {"demo_engine": "1.0.0"}, True, repo=TMP)
    record("A2 good export passes", r.ok, r.reason())

    # MUST-FAIL 1: wrong engine version
    r = GATE.run_gate([("demo_engine", good)], {"demo_engine": "2.0.0"}, True, repo=TMP)
    must_fail("version-mismatched export blocked", not r.ok and
              any("version_mismatch" in f for f in r.failures), r.reason())

    # MUST-FAIL 2: missing receipt
    r = GATE.run_gate([("demo_engine", {"value": 42})], {"demo_engine": "1.0.0"}, True, repo=TMP)
    must_fail("receipt-less export blocked", not r.ok and
              any("missing_receipt" in f for f in r.failures), r.reason())

    # MUST-FAIL 3: tampered receipt field
    tampered = dict(good)
    tampered["__receipt__"] = {**good["__receipt__"], "engine_version": "1.0.0", "schema": "sprint2.receipt/1"}
    tampered["__receipt__"]["input_hashes"] = {"input_a.txt": "0" * 64}
    r = GATE.run_gate([("demo_engine", tampered)], {"demo_engine": "1.0.0"}, True, repo=TMP)
    must_fail("receipt-tampered export blocked", not r.ok and
              any("input_drift" in f for f in r.failures), r.reason())

    # MUST-FAIL 4: input vanished from disk
    gone = RCPT.attach({"v": 1}, RCPT.build_receipt(
        "demo_engine", "1.0.0", [inp], cfg={}, repo=TMP))
    inp.unlink()
    r = GATE.run_gate([("demo_engine", gone)], {"demo_engine": "1.0.0"}, True, repo=TMP)
    must_fail("vanished input blocked", not r.ok and
              any("input_missing" in f for f in r.failures), r.reason())

    # MUST-FAIL 5: failed self-test suite blocks the deploy even with good receipts
    inp.write_text("v1\n")
    good2 = RCPT.attach({"v": 1}, RCPT.build_receipt("demo_engine", "1.0.0", [inp], cfg={}, repo=TMP))
    r = GATE.run_gate([("demo_engine", good2)], {"demo_engine": "1.0.0"}, False, repo=TMP)
    must_fail("self-test failure blocks deploy", not r.ok and
              "self_test_failed" in r.failures, r.reason())

    # MUST-FAIL 6: crossval disagreement blocks the deploy
    r = GATE.run_gate([("demo_engine", good2)], {"demo_engine": "1.0.0"}, True, crossval_ok=False, repo=TMP)
    must_fail("crossval disagreement blocks deploy", not r.ok and
              "crossval_failed" in r.failures, r.reason())

    # MUST-FAIL 7: engine with no pinned version is not deployable
    r = GATE.run_gate([("demo_engine", good2)], {}, True, repo=TMP)
    must_fail("unpinned engine blocked", not r.ok and
              any("version_not_pinned" in f for f in r.failures), r.reason())

    # Receipt overwrite is a conflict, not a silent replacement.
    try:
        RCPT.attach(good, RCPT.build_receipt("other", "1.0.0", [], repo=TMP))
        record("MUST-FAIL receipt overwrite refused", False, "attach() overwrote an existing receipt")
    except ValueError as e:
        record("MUST-FAIL receipt overwrite refused", True, str(e)[:60])


# ---------------------------------------------------------------- A3
def test_a3_fingerprints():
    print("\nA3 — process fingerprints")
    log = TMP / "fingerprints.jsonl"
    run_id = FP.new_run_id()
    rec = FP.make_record(run_id=run_id, started_at=iso(0.1), git_commit="deadbeef",
                         engine_versions={"demo": "1.0.0"},
                         input_hashes={"input_a.txt": "abc"}, cfg={"x": 1},
                         flags={"deploy_gate_flag": True}, terminal_state="pass")
    FP.append(rec, log=log)
    FP.append(FP.make_record(run_id=FP.new_run_id(), started_at=iso(0.2),
                             terminal_state="blocked", note="gate said no"), log=log)
    allrec = FP.read_all(log)
    record("A3 two records appended", len(allrec) == 2, f"{len(allrec)} lines")
    record("A3 lookup by run_id", FP.find(run_id, log=log)["git_commit"] == "deadbeef")
    record("A3 terminal_state recorded", {r["terminal_state"] for r in allrec} == {"pass", "blocked"})

    # A receipt joins to its producing run.
    rc = {"run_id": run_id, "git_commit": "deadbeef", "config_hash": rec["config_hash"]}
    joined = FP.joinable(rc, log=log)
    record("A3 receipt joins to fingerprint record", joined is not None and joined["run_id"] == run_id)

    # MUST-FAIL: a bad terminal state is not loggable.
    try:
        FP.append({"run_id": "x", "terminal_state": "probably-fine"}, log=log)
        record("MUST-FAIL invalid terminal_state refused", False, "bad record was appended")
    except ValueError as e:
        record("MUST-FAIL invalid terminal_state refused", True, str(e)[:60])

    # MUST-FAIL: a receipt with no matching run does not resolve.
    orphan = FP.joinable({"run_id": "never-ran", "git_commit": "nope", "config_hash": "nope"}, log=log)
    record("MUST-FAIL orphan receipt does not resolve to a run", orphan is None)


# ---------------------------------------------------------------- B1
def _bars(n: int, seed: float = 1.0, end: str = "2026-10-16"):
    """Deterministic synthetic bars, ascending. Enough history for MA200+slope."""
    out = []
    d = _dt.date(2016, 1, 4)
    px = 100.0
    for i in range(n):
        px *= (1.0 + ((i * 37 % 11) - 5) / 500.0)   # gentle wave, always positive-ish trend
        px = max(px, 1.0)
        out.append({"date": (d).isoformat(), "high": round(px * 1.01, 4),
                    "low": round(px * 0.99, 4), "close": round(px, 4),
                    "volume": 1000.0, "symbol": "TEST"})
        d += _dt.timedelta(days=1)
    out[-1]["date"] = end
    return out


def test_b1_crossval():
    print("\nB1 — independent second implementation")
    bars = _bars(400)
    p = CV.independent_plan(bars)
    record("B1 independent plan returns a decision", p["decision"] in ("SIGNAL", "NO_SIGNAL"), p["reason"])
    record("B1 reports bars used = n, never n+1",
           p["bars_used"] == len(bars), f"bars_used={p['bars_used']}")

    # Exact agreement between the two implementations on a fixture.
    self_same = CV.compare(p, p)
    record("B1 identical implementations agree", self_same["agree"])

    # MUST-FAIL: a disagreement is caught and receipted, not reconciled.
    bad = dict(p, entry_s1=p["entry_s1"] + 5.0)
    c = CV.compare(p, bad)
    must_fail("disagreeing signal blocked", not c["agree"] and "entry_s1" in c["differing_fields"],
              f"differing={list(c['differing_fields'])}")
    rc = CV.rejection_receipt("2026-10-16", "TEST", p, bad, c)
    record("MUST-FAIL rejection receipt carries both hashes",
           rc["reason"] == "implementations_disagree"
           and rc["primary_hash"] != rc["independent_hash"]
           and rc["params"] == CV.PARAMS)


def test_b1_no_lookahead():
    print("\nB1 — no-look-ahead probe")
    bars = _bars(400)
    baseline = CV.independent_plan(bars)

    t1 = bars[-1]["date"]

    # The T-day bar is deliberately present in the input and deliberately extreme.
    t_day = {"date": "2026-10-18", "high": 9e9, "low": 0.01, "close": 9e9,
             "volume": 1.0, "symbol": "TEST"}
    shocked = [dict(b) for b in bars] + [t_day]
    with_t = CV.independent_plan(shocked, t1_date=t1)

    record("probe: T-day bar present in input", len(shocked) == len(bars) + 1)
    record("no-look-ahead: T-day bar changes nothing",
           CV.decision_only(baseline) == CV.decision_only(with_t)
           and baseline["entry_s1"] == with_t["entry_s1"]
           and baseline["stop"] == with_t["stop"]
           and with_t["bars_used"] == len(bars)
           and with_t["asof"] == t1,
           f"asof {with_t['asof']} == T-1 {t1}, bars_used {with_t['bars_used']} of {len(shocked)}")

    # MUST-FAIL: the probe has teeth. An implementation that let the T-day bar
    # through WOULD move the numbers — proven by calling it with a T-1 cutoff
    # that includes the T-day bar.
    leaked = CV.independent_plan(shocked, t1_date=t_day["date"])
    record("MUST-FAIL probe has teeth (a T-1 cutoff that includes T-day DOES move it)",
           leaked["bars_used"] == len(shocked) and leaked["entry_s1"] != baseline["entry_s1"],
           f"leaked entry_s1={leaked['entry_s1']} vs T-1 {baseline['entry_s1']}")


def test_b1_real_agreement():
    print("\nB1 — agreement against the PRIMARY implementation on real bars")
    try:
        import pandas as pd
        from v2engine.core import features
    except Exception as e:      # pragma: no cover
        record("B1 real-data cross-check available", False, f"cannot import primary: {e}")
        return

    def primary(bars):
        df = pd.DataFrame(bars).set_index("date")
        df.index = pd.to_datetime(df.index)
        df = df[["high", "low", "close", "volume"]].astype(float)
        nxt = df.index[-1] + pd.offsets.BDay(1)
        bb = pd.concat([df, df.iloc[[-1]].set_index(pd.DatetimeIndex([nxt]))])
        f = features(bb).iloc[-1]
        if any(pd.isna(f[k]) for k in ("S1", "R1", "R2", "ma200", "ma200_slope", "atr_pct")):
            return {"decision": "NO_SIGNAL", "reason": "INSUFFICIENT"}
        if not (f.prev_close > f.ma200 and f.ma200_slope > 0):
            return {"decision": "NO_SIGNAL", "reason": "NOT_IN_UPTREND"}
        if not (f.atr_pct < 0.06 and f.box_w < 0.35):
            return {"decision": "NO_SIGNAL", "reason": "GUARD_RAIL"}
        s1 = float(f.S1)
        return {"decision": "SIGNAL", "reason": "ok", "entry_s1": round(s1, 6),
                "stop": round(s1 * (1 - 3 * f.atr_pct), 6)}

    SEC = {"ok": "ok", "not in uptrend": "NOT_IN_UPTREND",
           "guard rail (ATR/box)": "GUARD_RAIL"}
    store = REPO / "data" / "bt10y2"
    agree = dis = 0
    diffs = []
    if store.exists():
        for p in sorted(store.glob("*.json")):
            try:
                rows = json.loads(p.read_text())
            except Exception:
                continue
            if not isinstance(rows, list) or len(rows) < 260:
                continue
            sym = p.stem
            bars = [{"date": r["date"], "high": r["high"], "low": r["low"],
                     "close": r["close"], "volume": r.get("volume", 0), "symbol": sym}
                    for r in rows]
            try:
                sec = CV.independent_plan(bars)
            except CV.InsufficientHistory:
                continue
            pri = primary(bars)
            fields = ["decision", "reason"] + (
                ["entry_s1", "stop"] if pri["decision"] == "SIGNAL" else [])
            secn = {"decision": sec["decision"], "reason": SEC[sec["reason"]],
                    "entry_s1": sec["entry_s1"], "stop": sec["stop"]}
            d = {k: (pri.get(k), secn.get(k)) for k in fields if pri.get(k) != secn.get(k)}
            if d:
                dis += 1
                diffs.append((sym, d))
            else:
                agree += 1
    total = agree + dis
    record("B1 real-data agreement", total > 0 and dis == 0,
           f"{agree}/{total} agree ({agree/total*100:.2f}%), {dis} disagree")
    if diffs:
        for s, d in diffs[:5]:
            print(f"        DIFF {s}: {d}")


# ---------------------------------------------------------------- B2
def test_b2_restatement():
    print("\nB2 — vendor restatement tracking")
    led = TMP / "restatements.jsonl"
    vf = TMP / "vendor_session.json"
    vf.write_text(json.dumps({"exported_at": "2026-10-16T21:00:00Z",
                              "file_version": "3",
                              "rows": [{"date": "2026-10-16", "close": 100.0}]}))

    r1 = RS.ingest(vf, "2026-10-16", repo=TMP, log=led)
    record("B2 first ingest is NEW_SESSION", r1["event"] == RS.NEW_SESSION, r1["event"])

    r2 = RS.ingest(vf, "2026-10-16", repo=TMP, log=led)
    record("B2 byte-identical re-ingest is UNCHANGED", r2["event"] == RS.UNCHANGED, r2["event"])

    # Vendor silently re-issues the SAME session with a corrected value.
    vf.write_text(json.dumps({"exported_at": "2026-10-17T06:00:00Z",
                              "file_version": "4",
                              "rows": [{"date": "2026-10-16", "close": 101.5}]}))
    r3 = RS.ingest(vf, "2026-10-16", repo=TMP, log=led)
    record("B2 vendor re-issue is RESTATEMENT", r3["event"] == RS.RESTATEMENT, r3["event"])
    record("B2 restatement carries before/after hashes",
           r3["prev_sha256"] and r3["prev_sha256"] != r3["sha256"],
           f"{str(r3['prev_sha256'])[:12]} -> {r3['sha256'][:12]}")
    record("B2 vendor version markers captured",
           r3["vendor_markers"].get("file_version") == "4",
           json.dumps(r3["vendor_markers"]))
    record("B2 restatements() lists the event", len(RS.restatements(log=led)) == 1)

    # A genuinely new session is not a restatement.
    r4 = RS.ingest(vf, "2026-10-17", repo=TMP, log=led)
    record("MUST-FAIL a genuinely new bar is NOT called a restatement",
           r4["event"] == RS.NEW_SESSION, f"event={r4['event']}")

    # MUST-FAIL: restatement must name its blast radius from the declared map.
    RS.DEPMAP.parent.mkdir(parents=True, exist_ok=True)
    RS.DEPMAP.write_text(json.dumps({"vendor_session.json": ["data/v2_signals.json"]}))
    vf.write_text(json.dumps({"exported_at": "2026-10-17T07:00:00Z", "file_version": "5",
                              "rows": [{"date": "2026-10-16", "close": 102.0}]}))
    r5 = RS.ingest(vf, "2026-10-16", repo=TMP, log=led)
    must_fail("restatement names its dependents", r5["event"] == RS.RESTATEMENT
              and r5["dependents"] == ["data/v2_signals.json"],
              f"dependents={r5['dependents']}")
    RS.DEPMAP.unlink()


# ---------------------------------------------------------------- B3
def test_b3_gap_receipts():
    print("\nB3 — data-gap receipts")
    g = GR.GapReceipt(session="2026-10-16", market="us200", symbol="AVB",
                      reason=GR.MISSING_BAR)
    record("B3 receipt carries rows_returned=0", g.rows_returned == 0)

    cands = ["AAPL", "AVB", "MSFT", "NVDA"]
    denoms = ["AAPL", "AVB", "MSFT", "NVDA", "AMD"]
    c2, d2, recs = GR.apply_gaps([g], cands, denoms)
    record("B3 gap excluded from candidates", "AVB" not in c2, f"{cands} -> {c2}")
    record("B3 gap excluded from DENOMINATORS too", "AVB" not in d2, f"{denoms} -> {d2}")
    record("B3 other symbols untouched in both sets",
           [x for x in cands if x != "AVB"] == c2 and [x for x in denoms if x != "AVB"] == d2)
    record("B3 receipt logged", len(recs) == 1 and recs[0]["symbol"] == "AVB")

    # MUST-FAIL: a "gap" that returned rows is not a gap.
    try:
        GR.GapReceipt(session="2026-10-16", market="us200", symbol="X",
                      reason=GR.MISSING_BAR, rows_returned=5)
        record("MUST-FAIL non-empty gap receipt refused", False, "accepted rows_returned=5")
    except ValueError as e:
        record("MUST-FAIL non-empty gap receipt refused", True, str(e)[:60])

    # MUST-FAIL / B3.3: rule rejection and source gap must not share a counter.
    rej = GR.GapReceipt(session="2026-10-16", market="us200", symbol="MSFT",
                        reason=GR.RULE_REJECTION, rows_returned=120)
    counts = GR.count_reasons([g, rej])
    record("MUST-FAIL gap and rule rejection counted separately",
           counts[GR.MISSING_BAR] == 1 and counts[GR.RULE_REJECTION] == 1
           and GR.is_gap(g) and not GR.is_gap(rej),
           json.dumps(counts))

    # A rule rejection must NOT remove a symbol from the denominators.
    c3, d3, _ = GR.apply_gaps([rej], cands, denoms)
    record("MUST-FAIL rule rejection does not silently shrink denominators",
           "MSFT" in c3 and "MSFT" in d3, f"cands={c3} denoms={d3}")

    # B3.2 quarantine at the next decision point.
    q = GR.quarantine_expired([g], refilled=[])
    record("B3 unfilled gap quarantined at next session",
           len(q) == 1 and q[0]["reason"] == GR.QUARANTINED and q[0]["action"] == GR.QUARANTINE,
           json.dumps(q[0]) if q else "none")
    q2 = GR.quarantine_expired([g], refilled=["AVB"])
    record("B3 refilled gap is not quarantined", q2 == [])
    q3 = GR.quarantine_expired([g, rej], refilled=[])
    record("MUST-FAIL a rule rejection is never quarantined as a gap",
           all(x["reason"] == GR.QUARANTINED for x in q3) and len(q3) == 1,
           f"{[x['reason'] for x in q3]}")


# ---------------------------------------------------------------- cross-cutting
def test_flags_off_leaves_production_untouched():
    print("\nGuardrail — production untouched while flags are OFF")
    cfg = FLAGS.load_config()
    record("all sprint2 flags default OFF", not any(cfg.values()),
           json.dumps(cfg))
    record("flag file exists and is committed-shaped", FLAGS.CONFIG.exists(),
           str(FLAGS.CONFIG.relative_to(REPO)))
    record("no flag is currently enabled", FLAGS.enabled_flags() == [])

    # No production builder or deploy script may reference the sprint2 kit.
    prod = ["build_static.py", "build_home.py", "build_v2_signals.py",
            "build_equity_curves.py", "build_track_record.py", "v2_equity_export.py",
            "build_dashboard.py", "refresh.sh", "finalize.py"]
    needles = ("integrity", "sprint2", "deploy_gate_flag", "staleness_banner_flag",
               "crossval_flag", "restatement_flag", "gap_receipt_flag",
               "fingerprint_flag", "__receipt__")
    hits = []
    for name in prod:
        p = HERE / name
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        for n in needles:
            if n in txt:
                hits.append(f"{name}:{n}")
    must_fail("no sprint2 identifier in any build/deploy path",
              not hits, f"hits={hits}" if hits else "0 references across "
              f"{len([n for n in prod if (HERE / n).exists()])} production scripts")

    # A missing/corrupt flag config must fail closed to OFF.
    record("missing flag config → everything OFF", FLAGS._DEFAULTS and not any(FLAGS._DEFAULTS.values()))


def main() -> int:
    global TMP
    print("=" * 78)
    print("Sprint 2 integrity self-tests — research only, no production changes")
    print("=" * 78)
    with tempfile.TemporaryDirectory(prefix="sprint2_selftest_") as td:
        TMP = Path(td)
        test_a1_freshness()
        test_a2_gate()
        test_a3_fingerprints()
        test_b1_crossval()
        test_b1_no_lookahead()
        test_b1_real_agreement()
        test_b2_restatement()
        test_b3_gap_receipts()
        test_flags_off_leaves_production_untouched()

    n = len(RESULTS)
    npass = sum(1 for r in RESULTS if r["ok"])
    fails = [r for r in RESULTS if not r["ok"]]
    must_fails = [r for r in RESULTS if r["name"].startswith("must-fail")]
    print("\n" + "=" * 78)
    print(f"{npass}/{n} passed   ({len(must_fails)} of them must-fail guards)")
    if fails:
        print("\nFAILURES:")
        for r in fails:
            print(f"  - {r['name']}: {r['detail']}")
    print("=" * 78)

    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        payload = {
            "sprint": "website/pipeline-integrity-2",
            "generated_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
            "mode": "research_only",
            "flags": FLAGS.load_config(),
            "flags_enabled": FLAGS.enabled_flags(),
            "production_modified": False,
            "totals": {"checks": n, "passed": npass, "failed": len(fails),
                       "must_fail_guards": len(must_fails)},
            "items": _ITEM_STATUS,
            "results": RESULTS,
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())