#!/usr/bin/env python3
"""A2 — fail-closed pre-publish deploy gate (the production entry point).

    python3 scripts/deploy_gate.py

Exit 0 = publish allowed. Any non-zero exit = nothing deploys.

This is the only place a deploy is allowed to be gated, and it is gated by
looking at the artefacts that are actually about to be published — not at a
summary of what somebody believes was published. Four things must hold:

  1. every published JSON artefact carries a receipt naming the engine, the
     engine version, the commit, the config hash, every input's hash, and the
     time of generation
  2. the receipt's engine version equals the version this site is PINNED to in
     config/engine_versions.json
  3. every file the artefact was built from still hashes to what the receipt
     recorded — nothing rewrote an input after the fact
  4. the site's own self-test suite passes, right now, run as a subprocess

and, when the cross-validation flag is on, a fifth:

  5. the independent second implementation agrees exactly

Any one of these failing blocks the whole publish. There is no partial
publish, no warning-only severity, and no environment variable that turns a
failure into a pass.

The ONE thing that can stop this gate from enforcing is the flag being turned
off. That is deliberate: the flag is the operator's decision, it lives in a
committed file, and the sprint2 guardrail test asserts that exactly one flag
is on and that it is this one. A gate nobody can switch off is not a policy
decision, it is a bug waiting to happen.
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _kit():
    """Import the integrity kit from inside a function.

    The sprint2 guardrail requires production code to reach the kit only from
    inside a function body. A module-level import survives a flag being
    turned off and makes the kit's import cost unconditional — and the whole
    point of the kit is that it is opt-in.
    """
    scripts = REPO / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from integrity import flags, gate
    return flags, gate

# engine name -> the artefact it produced, relative to the repo root. The
# engine name is what the receipt inside the artefact has to claim, and what
# the pin file has to name.
ARTEFACT_PATHS: dict[str, tuple[str, ...]] = {
    "v2_signals": ("public", "v2-signals.json"),
}

LOG = REPO / "logs" / "deploy_gate.json"


def expected_versions(repo: Path = REPO) -> dict[str, str]:
    """The pinned version per engine, read from the operator's pin file.

    The pin is deliberately a separate file from the producer. If the producer
    read its own version out of the pin, the gate's version check could never
    fail and would be decoration.
    """
    pin = repo / "config" / "engine_versions.json"
    raw = json.loads(pin.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{pin} is empty or not an object")
    return {str(k): str(v) for k, v in raw.items()}


def collect_artefacts(repo: Path = REPO) -> list[tuple[str, object]]:
    """Load the artefacts that are about to be published, in the shape the
    gate consumes. Raises if one is missing — a missing artefact is a build
    fault, not a reason to publish without checking."""
    out: list[tuple[str, object]] = []
    for engine, rel in ARTEFACT_PATHS.items():
        path = repo.joinpath(*rel)
        if not path.is_file():
            raise FileNotFoundError(f"{engine}: {path} does not exist")
        out.append((engine, json.loads(path.read_text(encoding="utf-8"))))
    return out


def evaluate(artefacts: list[tuple[str, object]], expected: dict[str, str],
             self_test_passed: bool, crossval_ok: bool = True,
             repo: Path = REPO):
    """The gate expression.

    Every probe and the deploy step call THIS function, so a probe can never
    pass against a different expression than the one that actually guards the
    publish.
    """
    _, gate = _kit()
    return gate.run_gate(artefacts, expected, self_test_passed, crossval_ok,
                          repo=repo)


def run_self_test(repo: Path = REPO) -> tuple[bool, str]:
    """Run the site's own self-test suite and report whether it passed.

    The gate runs it itself rather than trusting a "the pipeline said it was
    green" boolean: a boolean handed to the gate is exactly the kind of thing
    a broken pipeline hands over.
    """
    script = repo / "scripts" / "self_test.py"
    if not script.is_file():
        return False, f"{script} is missing"
    p = subprocess.run([sys.executable, str(script)], capture_output=True,
                       text=True, cwd=str(repo))
    out = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
    detail = " | ".join(l.strip() for l in out[-3:] if l.strip())
    return p.returncode == 0, detail or f"exit {p.returncode}"


def crossval_state(repo: Path = REPO) -> tuple[bool, str]:
    """(ok, note) for the B1 cross-validation.

    B1 is not wired into this deploy path yet, so an ON crossval flag is a
    fail-closed condition rather than something to paper over: the gate will
    not claim a cross-validation it did not run.
    """
    flags, _ = _kit()
    if not flags.flag("crossval_flag"):
        return True, "crossval_flag off — no second implementation in force"
    return False, ("crossval_flag is ON but this deploy path does not run B1 "
                   "— refusing to publish rather than claim a check that did "
                   "not happen")


def _head(repo: Path = REPO) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _receipt_summary(payload: object) -> dict:
    r = payload.get("__receipt__") if isinstance(payload, dict) else None
    if not isinstance(r, dict):
        return {"present": False}
    return {
        "present": True,
        "engine": r.get("engine"),
        "engine_version": r.get("engine_version"),
        "git_commit": r.get("git_commit"),
        "generated_at": r.get("generated_at"),
        "n_inputs": len(r.get("input_hashes") or {}),
    }


def _log(report: dict, repo: Path = REPO) -> None:
    try:
        log = repo / "logs" / "deploy_gate.json"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    except Exception:  # noqa: BLE001
        # A log that cannot be written must not turn a PASS into a pass-with-
        # silence: the gate's verdict is the return code, the log is the
        # record. If the record cannot be kept, say so on stdout.
        print("  !! could not write logs/deploy_gate.json — the verdict below "
              "is the only record of this run")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    flags, _ = _kit()
    if not flags.flag("deploy_gate_flag"):
        print("A2 deploy gate: deploy_gate_flag is OFF — this gate is not in "
              "force. Everything else about the publish is unaffected.")
        return 0

    try:
        artefacts = collect_artefacts()
        expected = expected_versions()
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ A2 deploy gate: could not load the artefacts to check — {e}")
        _log({"ok": False, "failures": [f"load_error:{e}"],
              "checked_at": _dt.datetime.now(_dt.timezone.utc)
              .replace(microsecond=0).isoformat()})
        return 1

    self_test_ok, self_test_detail = run_self_test()
    crossval_ok, crossval_note = crossval_state()
    result = evaluate(artefacts, expected, self_test_ok, crossval_ok)

    report = {
        "checked_at": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0).isoformat(),
        "ok": result.ok,
        "failures": result.failures,
        "git_commit": _head(),
        "flags": flags.load_config(),
        "expected_versions": expected,
        "self_test": {"ok": self_test_ok, "detail": self_test_detail},
        "crossval": {"ok": crossval_ok, "note": crossval_note},
        "receipts": {engine: _receipt_summary(payload)
                     for engine, payload in artefacts},
    }
    _log(report)

    if not result.ok:
        print("  ✗ A2 deploy gate FAILED — nothing deploys:")
        for f in result.failures:
            print(f"      - {f}")
        if not self_test_ok:
            print(f"      (self-test: {self_test_detail})")
        if not crossval_ok:
            print(f"      (crossval: {crossval_note})")
        return 1

    print("  ✓ A2 deploy gate green — receipts present, versions pinned, "
          "inputs unchanged, self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
