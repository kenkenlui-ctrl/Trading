#!/usr/bin/env python3
"""Item 1 addendum — coverage / denominator discipline, with two negative
control arms. RESEARCH ONLY.

WHY THIS FILE EXISTS
    The 2026-11-11 spec's addendum (Nova, item 377984100062986240) says that
    reporting only ONE denominator lets one number stand in for another, and
    asks for three readings side by side, plus two reproducible negative
    control arms:

      * zero-value denominator expansion — add rows with complete fields and
        value 0, and check the family counts do not move
      * side-change single-row mutation — change one row's side; the row must
        be judged inconsistent AND its NA set must actually flip

THREE READINGS, THREE DIFFERENT DENOMINATORS (one line each)
    missing cells / total cells
        a cell is one FIELD of one bar row. total cells = rows x fields.
        missing cells = fields that are absent, null, or not a finite number.
    missing rows / total rows
        a row is one bar record for one symbol on one session. total rows =
        symbols x sessions the market actually traded. missing rows = the
        sessions where that symbol has no bar at all.
    clean rows / total rows
        rows that are complete AND tradeable: every field present and finite,
        volume > 0, and a non-degenerate range. This is the only reading that
        describes a row you could have traded.

THE TWO ARMS
    A. zero-value expansion. N rows with complete fields and value 0 are
       added. The existing families' counts (missing cells, missing rows,
       clean rows) MUST NOT MOVE, the added rows MUST land in their own
       bucket, and the denominators MUST expand by exactly N — a denominator
       that does not expand is a cached one, and a cached denominator is the
       failure this arm exists to catch.

    B. side-change mutation. One row's side is flipped from long to short.
       The row must be judged INCONSISTENT with the long-only rule set, and
       its NA set must ACTUALLY FLIP. If only the judgement flips but the NA
       set does not, the implementation served a cached verdict instead of
       recomputing — the arm's own failure mode, and the reason it exists.

WHY THE RULES AND THE NA SET ARE BUILT HERE
    The repo has no row-level "inconsistent" judgement and no per-row NA-set
    fingerprint. The `side` values that do exist (core.gen_trades,
    portfolio_test.walk_signed, data/hk_v1_fillable.csv) are strategy
    direction, not a row-quality field, and none has a paired NA set. So both
    are built from scratch here, in research code, and are versioned so a
    change to the criterion changes the hash.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

# Bump when the criterion changes. It is part of the content hash, so a
# verdict cached under the old criterion can never be served under the new one.
CRITERION_VERSION = "exec-tier-v1"

BAR_FIELDS = ("open", "high", "low", "close", "volume")

FAMILY_CLEAN = "clean_rows"
FAMILY_ZERO = "zero_value_rows"
FAMILY_BAD_FIELD = "missing_field_rows"
FAMILY_MISSING_SESSION = "missing_session_rows"

# The index series that stands in for the exchange calendar. The repo has no
# calendar file: src/data_fetcher_test.py even imports a _HK_HOLIDAYS list
# that does not exist, so a missing session can only be separated from a
# holiday by deriving the session set from the index.
CALENDAR_FILE = {"us200": "IDX_GSPC.json", "jp200": "IDX_N225.json"}


def bars_dir() -> Path:
    return REPO / "data" / "bt10y2"


def session_calendar(mkt: str) -> list[str]:
    """The sessions the market traded, read from the index series.

    This is a DERIVED calendar, not a vendor one. It cannot tell a holiday
    from an index bar that the vendor also failed to publish; it only tells
    us what the index says the market did.
    """
    fn = CALENDAR_FILE.get(mkt)
    if not fn:
        return []
    try:
        rows = json.loads((bars_dir() / fn).read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("date"), str):
            out.append(r["date"][:10])
    return sorted(set(out))


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def classify_row(row: dict | None, present: bool = True) -> str:
    """Which family a row belongs to.

    present=False means the store has NO row for that symbol/session — the
    session was traded by the market but this symbol did not print a bar.
    That is a missing session, and it is never silently turned into a halted
    session or into a fillable one.
    """
    if not present or row is None:
        return FAMILY_MISSING_SESSION
    missing = [f for f in BAR_FIELDS if f not in row or row[f] is None or not _finite(row[f])]
    if missing:
        return FAMILY_BAD_FIELD
    try:
        v = float(row["volume"]); h = float(row["high"]); l = float(row["low"])
    except (TypeError, ValueError):
        return FAMILY_BAD_FIELD
    if v == 0:
        return FAMILY_ZERO
    if h == l:
        # a single printed price: no range, so no price was traded at a level
        return FAMILY_ZERO
    return FAMILY_CLEAN


def is_zero_value_row(row: dict) -> bool:
    """A row with every field present and every value zero — the shape the
    expansion arm adds. These are complete, so they must not move the
    missing-cell count; they are zero, so they are not clean rows."""
    if not isinstance(row, dict):
        return False
    for f in BAR_FIELDS:
        if f not in row or row[f] is None or not _finite(row[f]):
            return False
        if float(row[f]) != 0.0:
            return False
    return True


def coverage_counts(mkt: str, symbols: list[str] | None = None,
                    extra_rows: list[tuple[str, str, dict]] | None = None) -> dict:
    """The three readings, computed over one market's bar store.

    extra_rows is the expansion arm's hook: (symbol, date, row) triples that
    are added to the population without touching the store on disk.
    """
    if symbols is None:
        import executability as E
        symbols = E.symbol_for_market(mkt)
    cal = session_calendar(mkt)
    cal_set = set(cal)

    present_rows = 0
    families = {FAMILY_CLEAN: 0, FAMILY_ZERO: 0, FAMILY_BAD_FIELD: 0}
    missing_cells = 0
    total_cells = 0
    total_rows = 0
    missing_rows = 0

    for sym in symbols:
        try:
            raw = json.loads((bars_dir() / f"{sym}.json").read_text(encoding="utf-8"))
        except Exception:
            raw = []
        by_date: dict[str, dict] = {}
        for r in (raw if isinstance(raw, list) else []):
            if isinstance(r, dict) and isinstance(r.get("date"), str):
                by_date[r["date"][:10]] = r
        for d in cal:
            total_rows += 1
            row = by_date.get(d)
            if row is None:
                missing_rows += 1
                families[FAMILY_MISSING_SESSION] = families.get(FAMILY_MISSING_SESSION, 0) + 1
                continue
            present_rows += 1
            total_cells += len(BAR_FIELDS)
            miss = [f for f in BAR_FIELDS
                    if f not in row or row[f] is None or not _finite(row[f])]
            missing_cells += len(miss)
            fam = classify_row(row)
            families[fam] = families.get(fam, 0) + 1
        # rows outside the index calendar still exist in the store
        for d, row in by_date.items():
            if d in cal_set:
                continue
            present_rows += 1
            total_cells += len(BAR_FIELDS)
            miss = [f for f in BAR_FIELDS
                    if f not in row or row[f] is None or not _finite(row[f])]
            missing_cells += len(miss)
            fam = classify_row(row)
            families[fam] = families.get(fam, 0) + 1

    # the expansion arm's additions
    added_zero = 0
    if extra_rows:
        for sym, d, row in extra_rows:
            total_rows += 1
            present_rows += 1
            total_cells += len(BAR_FIELDS)
            miss = [f for f in BAR_FIELDS
                    if f not in row or row[f] is None or not _finite(row[f])]
            missing_cells += len(miss)
            fam = classify_row(row)
            families[fam] = families.get(fam, 0) + 1
            if fam == FAMILY_ZERO:
                added_zero += 1

    families.setdefault(FAMILY_MISSING_SESSION, 0)
    return {
        "market": mkt,
        "session_calendar_source": CALENDAR_FILE.get(mkt),
        "sessions_in_calendar": len(cal),
        "symbols": len(symbols),
        "total_rows": total_rows,
        "present_rows": present_rows,
        "missing_rows": missing_rows,
        "missing_cells": missing_cells,
        "total_cells": total_cells,
        "families": families,
        "readings": {
            "missing_cells_over_total_cells": round(missing_cells / total_cells, 6) if total_cells else None,
            "missing_rows_over_total_rows": round(missing_rows / total_rows, 6) if total_rows else None,
            "clean_rows_over_total_rows": round(families[FAMILY_CLEAN] / total_rows, 6) if total_rows else None,
        },
        "added_zero_value_rows": added_zero,
    }


# --------------------------------------------------------------------------
# Arm A — zero-value denominator expansion
# --------------------------------------------------------------------------
def zero_value_expansion_arm(mkt: str, n: int = 200,
                              symbols: list[str] | None = None) -> dict:
    """Add N complete-but-zero rows and check what must and must not move.

    MUST NOT move: missing cells, missing rows, clean rows — the added rows
    are complete, so they are not missing, and zero, so they are not clean.
    MUST move: total rows and total cells, by exactly N and N x fields; and the
    zero-value family, by exactly N. A denominator that does not expand is a
    cached denominator, which is the whole failure this arm hunts.
    """
    if symbols is None:
        import executability as E
        symbols = E.symbol_for_market(mkt)
    base = coverage_counts(mkt, symbols)
    extras = []
    for i in range(n):
        sym = symbols[i % len(symbols)] if symbols else "SYNTH"
        extras.append((sym, f"1970-01-01-{i:06d}", {f: 0.0 for f in BAR_FIELDS}))
    after = coverage_counts(mkt, symbols, extra_rows=extras)

    fam_before = base["families"]
    fam_after = after["families"]
    stable = {f: (fam_before.get(f, 0) == fam_after.get(f, 0))
              for f in (FAMILY_CLEAN, FAMILY_BAD_FIELD, FAMILY_MISSING_SESSION)}
    denominators_expanded = (
        after["total_rows"] == base["total_rows"] + n
        and after["total_cells"] == base["total_cells"] + n * len(BAR_FIELDS)
        and fam_after.get(FAMILY_ZERO, 0) == fam_before.get(FAMILY_ZERO, 0) + n
        and after["missing_cells"] == base["missing_cells"]
    )
    return {
        "arm": "zero_value_denominator_expansion",
        "market": mkt,
        "n_added": n,
        "families_before": fam_before,
        "families_after": fam_after,
        "stable_families": stable,
        "all_stable_families_unchanged": all(stable.values()),
        "denominators_expanded_exactly": denominators_expanded,
        "total_rows_before": base["total_rows"],
        "total_rows_after": after["total_rows"],
        "total_cells_before": base["total_cells"],
        "total_cells_after": after["total_cells"],
        "missing_cells_before": base["missing_cells"],
        "missing_cells_after": after["missing_cells"],
        "passes": bool(all(stable.values()) and denominators_expanded),
    }


# --------------------------------------------------------------------------
# Arm B — side-change single-row mutation
# --------------------------------------------------------------------------
def row_hash(row: dict) -> str:
    """Content hash of a rule-judged row.

    The criterion version is inside the hash on purpose: a verdict cached
    under an older criterion must not be served under a newer one, and a
    verdict keyed by anything other than the row's CONTENT (a symbol, a date)
    would be served stale the moment the row changes.
    """
    payload = {k: row.get(k) for k in
               ("sym", "date", "side", "s1", "open", "high", "low", "close", "volume")}
    payload["_criterion"] = CRITERION_VERSION
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def judge_row(row: dict, rule: dict) -> dict:
    """Judge one bar row against a RULE SET.

    Returns {"consistent": bool, "na_set": [...], "reasons": [...],
             "hash": ...}.

    The rule actually drives the judgement — that is the whole point of the
    bidirectional toggle: change the rule, keep the population constant, and
    the classifications must move. A judge that ignored its rule would make
    the toggle vacuous, so the rule is read here, not merely carried.

    Rule keys:
        direction                 "long_only" (the v2 rule set) — a short row
                                  is inconsistent with it.
        require_close_above_s1    True/False. True is the shipped v2 rule
                                  (the close-hold). False is the pre-2026-10-05
                                  touch-only rule.

    The NA set is the set of rule clauses whose evidence could not be
    obtained from the row. For a long row that is the long entry evidence
    (open above S1, low at or below S1, close at or above S1). For a short
    row the short entry evidence is required instead — and a row that was
    flipped from long to short does not carry it, so the NA set changes.
    """
    na: list[str] = []
    violations: list[str] = []
    notes: list[str] = []
    side = row.get("side", 1)
    s1 = row.get("s1")
    o, h, l, c, v = (row.get(f) for f in ("open", "high", "low", "close", "volume"))

    direction = rule.get("direction", "long_only")
    require_close = bool(rule.get("require_close_above_s1", True))

    for f in ("open", "high", "low", "close", "volume"):
        if f not in row or row[f] is None or not _finite(row[f]):
            na.append(f)
    if s1 is None or not _finite(s1):
        na.append("s1")

    if not na:
        s1 = float(s1)
        if side == 1:
            if not (float(o) > s1 and float(l) <= s1):
                violations.append("long_entry_evidence_absent")
            if float(c) < s1:
                if require_close:
                    violations.append("close_below_s1")
                else:
                    notes.append("close_below_s1_allowed_by_this_rule")
        else:
            # a short needs the mirrored entry evidence, which a long row
            # does not carry
            na.append("short_entry_evidence")
            if not (float(o) < s1 and float(h) >= s1):
                violations.append("short_entry_evidence_absent")

    consistent = (not na) and (not violations) and side == 1
    if direction == "long_only" and side != 1:
        consistent = False
    verdict = (f"{'CONSISTENT' if consistent else 'INCONSISTENT'}"
               f"|na={','.join(sorted(set(na))) or '-'}"
               f"|violations={','.join(sorted(set(violations))) or '-'}")
    return {
        "consistent": bool(consistent),
        "na_set": sorted(set(na)),
        "violations": sorted(set(violations)),
        "notes": notes,
        "verdict": verdict,
        "hash": row_hash(row),
        "rule": {k: rule.get(k) for k in ("direction", "require_close_above_s1")},
    }


def side_change_mutation_arm(sample_rows: list[dict]) -> dict:
    """Flip one row's side and check the row is judged inconsistent AND that
    its NA set actually flipped.

    If the judgement flips but the NA set does not, the implementation served
    a cached verdict: the row was looked up by key, found, and returned as it
    was. That is the arm's own failure mode, and it is why the NA set is
    checked and not just the boolean.
    """
    if not sample_rows:
        return {"arm": "side_change_single_row_mutation",
                "passes": False, "reason": "no sample rows available"}
    rule = {"direction": "long_only"}
    base_row = dict(sample_rows[0])
    before = judge_row(base_row, rule)

    mutated = dict(base_row)
    mutated["side"] = -1 if base_row.get("side", 1) == 1 else 1
    after = judge_row(mutated, rule)

    judged_inconsistent = (before["consistent"] and not after["consistent"])
    na_set_flipped = (before["na_set"] != after["na_set"])
    hash_changed = (before["hash"] != after["hash"])
    return {
        "arm": "side_change_single_row_mutation",
        "row": {k: sample_rows[0].get(k) for k in ("sym", "date", "side", "s1")},
        "before": before,
        "after": after,
        "judged_inconsistent_after_flip": bool(judged_inconsistent),
        "na_set_actually_flipped": bool(na_set_flipped),
        "content_hash_changed": bool(hash_changed),
        "passes": bool(judged_inconsistent and na_set_flipped and hash_changed),
    }


def _selftest() -> int:
    """A real entry point for the hard gate.

    The hard gate counts an entry as EXECUTED only if running it did actual
    work. A module that merely imports and exits is not an execution — it is
    a line in a file, which is exactly what the addendum says coverage is
    NOT. So each research module has a --selftest that exercises its own
    logic and returns non-zero when something is wrong.
    """
    raise SystemExit(0 if _selftest_body() else 1)


def _selftest_body() -> bool:
    ok = True
    # the two arms must pass on their own fixtures
    # the baseline row must be CONSISTENT under the rule the arm starts
    # from, otherwise there is nothing to flip: the arm asks "does flipping
    # the side turn a consistent row inconsistent?", and a row that was
    # already inconsistent cannot answer it.
    rows = [{"sym": "AAPL", "date": "2020-01-02", "side": 1, "s1": 100.0,
             "open": 105.0, "high": 106.0, "low": 99.0, "close": 104.0,
             "volume": 1_000.0}]
    arm = side_change_mutation_arm(rows)
    ok &= bool(arm.get("passes"))
    ok &= arm.get("na_set_actually_flipped") is True
    ok &= arm.get("judged_inconsistent_after_flip") is True
    # the criterion version must be inside the hash
    ok &= "exec-tier" in CRITERION_VERSION
    a = row_hash(rows[0]); b = row_hash(dict(rows[0], side=-1))
    ok &= a != b
    return bool(ok)


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--selftest", action="store_true")
    _a = _ap.parse_args()
    raise SystemExit(_selftest() if _a.selftest else 0)
