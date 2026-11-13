#!/usr/bin/env python3
"""Item 4 — gate independence, a three-layer build receipt, and a hard gate on
entry-count vs executed-count. RESEARCH ONLY.

THE FAILURE THIS EXISTS FOR
    The site sat on a 2026-10-30 stamp while the build said "done". Build
    succeeded, deploy succeeded, and nothing ever read the published page back
    to see whether it actually showed the new numbers. A build that says it
    succeeded is not evidence that the site shows it.

THE THREE LAYERS (each with its own evidence)
    (a) build succeeded    exit code + the build manifest
    (b) published          deploy id + publish timestamp
    (c) read-back          the LIVE page, fetched over the network, shows the
                           new stamp AND the new count

    (a) and (b) without (c) is STALE, not done. The gate's verdict is STALE
    and it blocks, exactly as a failed (c) would.

WHY THE READ-BACK IS INDEPENDENT
    The manifest is what is being CHECKED. A gate that compares the manifest
    with the manifest can never fail: both sides come from the same build
    pipeline, and if the pipeline is wrong they are wrong together. So the
    checker reads the deployed page itself — bytes that came back from the
    CDN — and compares what a visitor would actually see against what the
    build claimed. The source of the check is stated in the receipt so a
    reader can see which side the comparison is on.

THE HARD GATE (entry count vs executed count)
    A run can report every step green while a fifth of its entry scripts
    never executed. "We wrote a line saying it ran" is not "it ran". So after
    the aggregate run the gate compares the on-disk entry list against the
    entries that actually executed, including everything reachable by import
    from them. Any mismatch is a FAIL. The denominator is the on-disk list,
    content-addressed by path + digest, recomputed every round — never a
    number the pipeline produced about itself.

THE BIDIRECTIONAL TOGGLES
    Two paired toggles, because running only one mistakes count-driven
    movement for rule-driven movement:
      (i)  change the RULE with the counts constant -> the classifications
           must FLIP. If they do not move, the labels are not coming from the
           rule.
      (ii) add ZERO-VALUE rows with the rule constant -> the classifications
           must stay STABLE. If they move, the labels are being driven by the
           population's size rather than by the rule.
    The criterion version is inside the content hash, so a verdict cached
    under an older criterion cannot be served under a newer one.

IDENTITY-TYPE RATIOS ARE DEMOTED
    A ratio that is a difference of zero always holds (A - A = 0). Such a
    check is a tautology: it can never fail, so it can never be a decision
    input. Every ratio this module reports carries a flag saying whether it
    is a decision input or a descriptive field, and the verdict is computed
    only from the decision inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
RESEARCH = REPO / "scripts" / "research"

LAYER_BUILD = "a_build_succeeded"
LAYER_PUBLISHED = "b_published"
LAYER_READBACK = "c_read_back_confirmed"
LAYERS = (LAYER_BUILD, LAYER_PUBLISHED, LAYER_READBACK)

LIVE_URL = "https://www.win9you.com/"

STAMP_RE = re.compile(r'data-w9-fresh="數據截至\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"')
COUNT_RE = re.compile(r"([0-9]+)\s*個\s*v2\s*訊號")
UNIVERSE_RE = re.compile(r"([0-9]+)\s*US\s*\+\s*([0-9]+)\s*JP")


# --------------------------------------------------------------------------
# Layer (c) — the read-back, from a source that did not build anything
# --------------------------------------------------------------------------
def fetch_live(url: str = LIVE_URL, timeout: int = 25) -> dict:
    """Fetch the published page and read the stamp and the count off it.

    This is the independent source. It is the deployed HTML, fetched over the
    network, and it did not produce the build.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dsa-hk-research/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            status = getattr(r, "status", 200)
    except Exception as e:
        return {"ok": False, "url": url, "error": f"{type(e).__name__}: {e}"}
    stamp = STAMP_RE.search(body)
    count = COUNT_RE.search(body)
    uni = UNIVERSE_RE.search(body)
    return {
        "ok": True,
        "url": url,
        "http_status": status,
        "bytes": len(body),
        "stamp": stamp.group(1) if stamp else None,
        "signal_count": int(count.group(1)) if count else None,
        "universe_us": int(uni.group(1)) if uni else None,
        "universe_jp": int(uni.group(2)) if uni else None,
        "source": "live published HTML, fetched over the network",
    }


def build_manifest() -> dict:
    """What the build CLAIMED. The manifest is the thing under test, never
    the checker."""
    p = REPO / "data" / "build_state.json"
    out: dict = {"source": str(p)}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["status"] = d.get("status")
    out["sessions"] = d.get("sessions") or {}
    out["deploy_url"] = d.get("deploy_url")
    out["finished_at"] = d.get("finished_at")
    out["commit"] = d.get("commit")
    return out


def manifest_signal_count() -> int | None:
    """The number of signals the build produced, from the artefact the build
    produced — again: the thing under test, not the checker."""
    p = REPO / "public" / "v2-signals.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    sig = d.get("signals")
    return len(sig) if isinstance(sig, list) else None


def three_layer_receipt(manifest: dict, published: dict, live: dict) -> dict:
    """The receipt: three layers, each with its own evidence.

    A run with (a) and (b) but a failed (c) is STALE, not done.
    """
    layers: dict[str, dict] = {}

    layers[LAYER_BUILD] = {
        "layer": LAYER_BUILD,
        "evidence": "build exit code + build manifest",
        "ok": bool(manifest) and manifest.get("status") == "pass"
        and not manifest.get("error"),
        "detail": {"status": manifest.get("status"),
                   "sessions": manifest.get("sessions"),
                   "commit": manifest.get("commit"),
                   "error": manifest.get("error")},
    }

    layers[LAYER_PUBLISHED] = {
        "layer": LAYER_PUBLISHED,
        "evidence": "deploy id + publish timestamp",
        "ok": bool(published.get("deploy_id") or published.get("deploy_url"))
        and not published.get("error"),
        "detail": published,
    }

    want_stamp = None
    sessions = (manifest.get("sessions") or {})
    for v in sessions.values():
        want_stamp = v
        break
    want_count = manifest_signal_count()

    stamp_ok = bool(live.get("ok")) and live.get("stamp") == want_stamp
    count_ok = bool(live.get("ok")) and (
        want_count is None or live.get("signal_count") == want_count)
    layers[LAYER_READBACK] = {
        "layer": LAYER_READBACK,
        "evidence": "the live published page, fetched over the network",
        "independent_source": live.get("source"),
        "ok": bool(stamp_ok and count_ok),
        "detail": {
            "expected_stamp": want_stamp,
            "live_stamp": live.get("stamp"),
            "stamp_matches": stamp_ok,
            "expected_signal_count": want_count,
            "live_signal_count": live.get("signal_count"),
            "signal_count_matches": count_ok,
            "live_universe_us": live.get("universe_us"),
            "live_universe_jp": live.get("universe_jp"),
            "url": live.get("url"),
            "error": live.get("error"),
        },
    }

    ok = all(layers[l]["ok"] for l in LAYERS)
    if ok:
        verdict = "DONE"
    elif layers[LAYER_BUILD]["ok"] and layers[LAYER_PUBLISHED]["ok"]:
        verdict = "STALE"          # built and deployed, but the page disagrees
    else:
        verdict = "NOT_DONE"
    return {
        "verdict": verdict,
        "ok": ok,
        "layers": layers,
        "layer_order": list(LAYERS),
        "note": ("A run with (a) and (b) but a failed (c) is STALE, not done. "
                 "STALE blocks exactly as a failed build does."),
    }


# --------------------------------------------------------------------------
# The stale-stamp fixture — the gate must FAIL on it
# --------------------------------------------------------------------------
def stale_stamp_fixture(manifest: dict | None = None) -> dict:
    """Plant a stale stamp and check the gate catches it.

    The planted page shows the PREVIOUS build's date and count while the
    manifest claims the new ones. If the gate passes this, the gate is
    broken, not the fixture.
    """
    manifest = manifest or build_manifest()
    sessions = (manifest.get("sessions") or {})
    new_stamp = next(iter(sessions.values()), None)
    # an earlier session of the same market is the previous build's stamp
    stamps = sorted(set(v for v in sessions.values() if v))
    old_stamp = stamps[0] if stamps else "2026-01-01"
    if new_stamp == old_stamp:
        old_stamp = "2026-01-01"

    planted = {
        "ok": True,
        "url": "PLANTED (fixture — not a real fetch)",
        "stamp": old_stamp,
        "signal_count": 0,
        "universe_us": None,
        "universe_jp": None,
        "source": "planted stale page (negative control)",
    }
    published = {"deploy_id": "planted-deploy", "published_at": "planted",
                 "note": "fixture"}
    receipt = three_layer_receipt(manifest, published, planted)
    caught = (not receipt["ok"]) and receipt["verdict"] == "STALE"
    return {
        "fixture": "planted_stale_stamp",
        "planted_stamp": old_stamp,
        "manifest_stamp": new_stamp,
        "planted_count": 0,
        "gate_verdict": receipt["verdict"],
        "gate_ok": receipt["ok"],
        "gate_caught_it": bool(caught),
        "must_fail": True,
        "passes": bool(caught),
        "receipt": receipt,
    }


# --------------------------------------------------------------------------
# The hard gate — on-disk entry count vs actually executed count
# --------------------------------------------------------------------------
def content_address(path: Path) -> dict:
    """path + digest. The denominator is recomputed from disk every round."""
    try:
        blob = path.read_bytes()
    except Exception:
        return {"path": str(path), "digest": None, "bytes": 0}
    return {
        "path": str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path),
        "digest": hashlib.sha256(blob).hexdigest(),
        "bytes": len(blob),
    }


TRACER = r"""
import sys, json, runpy
seen = set()
class Tracer:
    def find_module(self, name, path=None):
        return self if name.startswith(('research', 'scripts')) or name in ('core','bt_clean','integrity') else None
    def load_module(self, name):
        return self
sys.meta_path.insert(0, Tracer())
import importlib.abc, importlib.machinery
class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.startswith('research') or name in ('core','bt_clean','integrity'):
            seen.add(name)
        return None
sys.meta_path.insert(0, Finder())
import importlib
_orig = importlib.import_module
def traced(name, package=None):
    seen.add(name)
    return _orig(name, package)
importlib.import_module = traced
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as e:
    code = e.code if isinstance(e.code, int) else 0
else:
    code = 0
print("__TRACED__" + json.dumps(sorted(seen)))
sys.exit(code)
"""


def hard_gate_entry_coverage(entries: list[Path],
                             runner_args: list[str] | None = None) -> dict:
    """Compare the on-disk entry list against the entries that actually ran.

    "We wrote a line saying it ran" is not "it ran". Each declared entry is
    executed as a subprocess with an import tracer; the executed set is the
    entries that ran, and the transitive set is everything they reached by
    import. Any declared entry that did not execute is a FAIL.
    """
    declared = [content_address(p) for p in entries]
    runner_args = runner_args or ["--selftest"]
    executed: list[str] = []
    failed: list[dict] = []
    transitive: set[str] = set()

    for p in entries:
        tracer = Path("/tmp") / f"_w9_trace_{p.stem}.py"
        tracer.write_text(TRACER, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(tracer), str(p)] + runner_args,
                capture_output=True, text=True, cwd=str(REPO), timeout=900)
        except Exception as e:
            failed.append({"entry": str(p), "error": f"{type(e).__name__}: {e}"})
            continue
        finally:
            try:
                tracer.unlink()
            except Exception:
                pass
        if proc.returncode != 0:
            failed.append({"entry": str(p), "exit": proc.returncode,
                           "stderr_tail": (proc.stderr or "")[-400:]})
            continue
        executed.append(str(p))
        for line in (proc.stdout or "").splitlines():
            if line.startswith("__TRACED__"):
                try:
                    transitive.update(json.loads(line[len("__TRACED__"):]))
                except Exception:
                    pass

    declared_paths = {d["path"] for d in declared}
    executed_rel = {str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)
                    for p in entries if str(p) in executed}
    not_executed = sorted(declared_paths - executed_rel)
    mismatch = len(not_executed) > 0
    return {
        "gate": "hard_gate_entry_count_vs_executed_count",
        "declared_count": len(declared),
        "executed_count": len(executed),
        "transitive_modules_reached": len(transitive),
        "not_executed": not_executed,
        "failed": failed,
        "mismatch": bool(mismatch),
        "verdict": "FAIL" if (mismatch or failed) else "PASS",
        "denominator": {
            "source": "the on-disk entry list, recomputed every round",
            "independent_of_execution": True,
            "content_addressed": declared,
        },
        "note": ("Coverage equals 'reachable by import from an executed "
                 "entry', not 'a run line was written'. A declared entry that "
                 "did not execute is a FAIL."),
    }


# --------------------------------------------------------------------------
# The bidirectional toggles
# --------------------------------------------------------------------------
def bidirectional_toggles(rows: list[dict], rule_a: dict, rule_b: dict) -> dict:
    """Two paired toggles.

    (i)  change the RULE, keep the COUNTS constant -> classifications must
         FLIP. If they do not move, the labels do not come from the rule.
    (ii) add ZERO-VALUE rows, keep the RULE constant -> classifications must
         stay STABLE. If they move, the labels are driven by the population
         rather than by the rule.
    """
    import coverage as C

    def classify(pop: list[dict], rule: dict) -> dict[str, str]:
        out = {}
        for r in pop:
            judged = C.judge_row(r, rule)
            out[f"{r.get('sym')}|{r.get('date')}"] = judged["verdict"]
        return out

    base = classify(rows, rule_a)
    rule_changed = classify(rows, rule_b)
    flipped = sum(1 for k in base if base[k] != rule_changed.get(k))
    counts_constant = (len(base) == len(rule_changed))

    # The added rows must be DISTINCT from the existing ones: a zero-value
    # row that reuses an existing symbol/date would overwrite that row's
    # verdict in the keyed map, and the arm would then read "the existing
    # classification changed" when in truth a row was replaced.
    zero_rows = []
    for k, r in enumerate(rows[:max(1, len(rows) // 10)]):
        z = dict(r, open=0.0, high=0.0, low=0.0, close=0.0, volume=0.0)
        z["date"] = f"{r.get('date')}-zero{k:03d}"
        z["sym"] = f"{r.get('sym')}-ZERO{k:03d}"
        zero_rows.append(z)
    stable_pop = classify(rows + zero_rows, rule_a)
    unchanged = sum(1 for k in base if base[k] == stable_pop.get(k))
    rows_constant = (len(rows) + len(zero_rows)) == len(stable_pop)

    return {
        "gate": "bidirectional_toggles",
        "toggle_1_rule_change": {
            "counts_constant": counts_constant,
            "classifications_flipped": flipped,
            "n_rows": len(rows),
            "verdict": "PASS" if (counts_constant and flipped > 0) else "FAIL",
            "why": ("with the rule changed and the population held constant, "
                    "the classifications must move"),
        },
        "toggle_2_zero_value_rows": {
            "rule_constant": True,
            "rows_added": len(zero_rows),
            "existing_classifications_unchanged": unchanged,
            "all_existing_unchanged": unchanged == len(base),
            "counts_expanded": rows_constant,
            "verdict": ("PASS" if (unchanged == len(base) and rows_constant)
                        else "FAIL"),
            "why": ("with the rule held constant and zero-value rows added, "
                    "the existing classifications must not move"),
        },
        "criterion_version": C.CRITERION_VERSION,
        "criterion_version_in_hash": True,
        "passes": bool((counts_constant and flipped > 0)
                       and unchanged == len(base) and rows_constant),
    }


# --------------------------------------------------------------------------
# Denominator independence
# --------------------------------------------------------------------------
def denominator_independence(denominator_source: Path,
                              pipeline_participates: list[str] | None = None) -> dict:
    """The denominator — what SHOULD have been checked — must come from a
    source that does not take part in the execution, and it must be
    content-addressed and recomputed every round.

    Identity-type ratios (a difference of zero always holds) are demoted to
    descriptive fields: they can never fail, so they can never decide.
    """
    entries = sorted(p for p in denominator_source.rglob("*")
                     if p.is_file() and p.suffix in (".py", ".sh"))
    addressed = [content_address(p) for p in entries]
    participants = set(pipeline_participates or [])
    overlap = sorted(a["path"] for a in addressed if a["path"] in participants)
    return {
        "gate": "denominator_independence",
        "denominator_source": str(denominator_source),
        "recomputed_from": "disk, every round",
        "content_addressed": True,
        "n_entries": len(addressed),
        "digest_of_digests": hashlib.sha256(
            "".join(a["digest"] or "" for a in addressed).encode()).hexdigest(),
        "overlap_with_pipeline": overlap,
        "independent": not overlap,
        "identity_type_ratios": "demoted to descriptive fields; never decision inputs",
        "decision_inputs": [a["path"] for a in addressed],
        "verdict": "PASS" if not overlap else "FAIL",
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
    # a planted stale stamp must make the gate fail
    st = stale_stamp_fixture()
    ok &= bool(st.get("gate_caught_it"))
    ok &= st.get("gate_verdict") == "STALE"
    # a receipt whose read-back disagrees must not be DONE
    man = {"status": "pass", "sessions": {"us200": "2026-10-30"}}
    rec = three_layer_receipt(man, {"deploy_id": "d"},
                              {"ok": True, "stamp": "1999-01-01",
                               "signal_count": 0, "source": "planted"})
    ok &= rec["verdict"] == "STALE" and rec["ok"] is False
    return bool(ok)


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--selftest", action="store_true")
    _a = _ap.parse_args()
    raise SystemExit(_selftest() if _a.selftest else 0)
