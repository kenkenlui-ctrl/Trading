"""Sprint 2 feature flags — ALL DEFAULT OFF.

Spec rule: "every item behind a `<feature>_flag` config flag, default OFF. No
production changes until Kenneth approves each flag."

Nothing in this package may be imported by a production build or deploy path
while any flag is OFF. `self_test.py` in this package asserts that
independently — see `test_flags_off_leaves_production_untouched`.

The config file is committed with every flag false on purpose: a missing or
corrupt config must mean OFF, never ON.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CONFIG = REPO / "config" / "integrity_flags.json"

# Flag name -> one-line meaning. The operator runbook (docs/sprint2-runbook.md)
# repeats these; keep them in sync.
FLAG_MEANING = {
    "staleness_banner_flag": "A1: render FRESH/STALE/NOT_RUN/SOURCE_DOWN state + banner on pages",
    "deploy_gate_flag": "A2: fail-closed pre-publish gate (receipts, engine version, self-test)",
    "fingerprint_flag": "A3: append a run fingerprint record to logs/fingerprints.jsonl",
    "crossval_flag": "B1: run the independent second v2 implementation and gate on exact agreement",
    "restatement_flag": "B2: hash vendor bar files per session and log RESTATEMENT on re-issue",
    "gap_receipt_flag": "B3: bind missing T-1 bars to receipts, exclude from candidates AND denominators",
}

ALL_FLAGS = tuple(FLAG_MEANING)

_DEFAULTS = {name: False for name in ALL_FLAGS}


def load_config() -> dict:
    """Read the flag file. Any problem → every flag OFF.

    Fail-closed means the safe direction. A typo'd path or malformed JSON must
    not silently enable a guard nobody reviewed."""
    cfg = dict(_DEFAULTS)
    if not CONFIG.exists():
        return cfg
    try:
        raw = json.loads(CONFIG.read_text())
    except Exception:
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for name in ALL_FLAGS:
        cfg[name] = raw.get(name) is True   # only a literal true turns it on
    return cfg


def flag(name: str) -> bool:
    if name not in ALL_FLAGS:
        raise KeyError(f"unknown sprint2 flag {name!r}; known: {ALL_FLAGS}")
    return load_config()[name]


def enabled_flags() -> list[str]:
    return [n for n in ALL_FLAGS if flag(n)]


def write_config(**kw) -> dict:
    """Only used by the operator to turn a flag on. Refuses unknown names so a
    typo cannot silently create a flag that nothing reads."""
    bad = set(kw) - set(ALL_FLAGS)
    if bad:
        raise KeyError(f"unknown flag(s): {sorted(bad)}")
    cfg = load_config()
    cfg.update({k: (v is True) for k, v in kw.items()})
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
    return cfg