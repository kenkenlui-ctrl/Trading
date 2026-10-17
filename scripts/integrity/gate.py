"""A2 — fail-closed pre-publish deploy gate.

Contract (spec A2.2): before publishing, assert
  (a) every displayed number resolves to a source receipt,
  (b) the receipt is internally consistent with the files on disk,
  (c) the engine version matches the version this site is pinned to,
  (d) the must-fail self-test suite passes.
ANY failure → build aborts, nothing deploys, alert logged.

The gate is a pure function over a list of artefacts plus an expected-version
map. It has no side effects and does not read a config flag itself — the caller
decides whether to consult it — so the same function is testable directly on
tampered input, which is the only way to demonstrate a guard actually fails.

Field names and what a bad value means:

  missing_receipt   artefact carries no receipt at all
  bad_schema        receipt schema string is not the one this gate understands
  missing_field     a required receipt field is absent or empty
  version_mismatch  engine_version != the version pinned for that engine
  input_drift       an input file's hash no longer matches what was recorded
  input_missing     a recorded input is no longer on disk
  self_test_failed  the must-fail suite did not pass
  crossval_failed   the independent implementation disagreed (B1)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .hashing import file_sha256
from .receipts import SCHEMA, get

REPO = Path(__file__).resolve().parent.parent.parent

REQUIRED_FIELDS = ("schema", "engine", "engine_version", "git_commit",
                   "config_hash", "input_hashes", "generated_at")


@dataclass
class GateResult:
    ok: bool
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "failures": list(self.failures)}

    def reason(self) -> str:
        return "; ".join(self.failures) if self.failures else "ok"


def check_receipt(payload, engine: str, expected_version: str,
                  repo: Path = REPO) -> list[str]:
    """Validate one artefact's receipt. Returns a list of failure codes
    (empty = clean)."""
    fails: list[str] = []
    r = get(payload)
    if r is None:
        return [f"{engine}:missing_receipt"]
    if r.get("schema") != SCHEMA:
        fails.append(f"{engine}:bad_schema")
    for f in REQUIRED_FIELDS:
        if f not in r or r[f] in ("", None):
            fails.append(f"{engine}:missing_field:{f}")
    if r.get("engine") != engine:
        fails.append(f"{engine}:engine_name_mismatch")
    # Version is compared as a string on purpose: "1.10" != "1.1" and a
    # float/int coercion here would let a wrong engine through.
    if str(r.get("engine_version")) != str(expected_version):
        fails.append(f"{engine}:version_mismatch")
    inh = r.get("input_hashes")
    if isinstance(inh, dict):
        for key, want in inh.items():
            p = Path(key) if Path(key).is_absolute() else repo / key
            if want == "MISSING":
                # recorded as missing at generation time: consistent, not a failure
                continue
            if not p.is_file():
                fails.append(f"{engine}:input_missing:{key}")
                continue
            if file_sha256(p) != want:
                fails.append(f"{engine}:input_drift:{key}")
    return fails


def run_gate(artefacts: list[tuple[str, object]], expected_versions: dict[str, str],
             self_test_passed: bool, crossval_ok: bool = True,
             repo: Path = REPO) -> GateResult:
    """Fail-closed across a whole export set.

    `artefacts` is [(engine_name, payload), ...]. Any failure anywhere blocks
    the deploy — there is no partial publish and no warning-only severity.
    """
    fails: list[str] = []
    if not artefacts:
        fails.append("no_artefacts")
    for engine, payload in artefacts:
        want = expected_versions.get(engine)
        if want is None:
            fails.append(f"{engine}:version_not_pinned")
            continue
        fails.extend(check_receipt(payload, engine, want, repo=repo))
    if not self_test_passed:
        fails.append("self_test_failed")
    if not crossval_ok:
        fails.append("crossval_failed")
    return GateResult(ok=not fails, failures=fails)