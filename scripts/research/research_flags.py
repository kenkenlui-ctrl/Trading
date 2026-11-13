"""Research flags for the 2026-11-11 credibility work — all default OFF.

Mirrors scripts/integrity/flags.py's contract exactly, in a research-local
file so that no production build path ever mentions these names:

  * a missing or corrupt config file means EVERY flag is OFF (fail closed)
  * only a literal JSON `true` turns a flag on
  * `write_config` refuses unknown flag names, so a typo cannot silently
    create a flag that nothing reads

The four flags are the ones the spec names. Item 5 (directional coverage) is
NOT here: the spec marks it PROPOSED and explicitly says "Kenneth to approve
or drop", so it is not implemented and not registered.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CONFIG = REPO / "config" / "research_flags.json"

FLAG_MEANING = {
    "executability_tiers_flag": (
        "Item 1: label every signal FILLABLE / NOT_TRIGGERED / UNFILLABLE / "
        "UNJUDGED and drop UNFILLABLE from the published win-rate denominator"),
    "confirmation_cost_flag": (
        "Item 2: print the confirmation-period cost (overnight-gap "
        "distribution) next to the close-hold filter benefit on /methodology"),
    "failure_criteria_flag": (
        "Item 3: publish preregistered numeric thresholds for declaring a "
        "strategy dead"),
    "gate_independence_flag": (
        "Item 4: A2 read-back verifies the LIVE page (not the build manifest); "
        "three-layer receipt; hard gate on entry-count vs executed-count"),
}

ALL_FLAGS = tuple(FLAG_MEANING)
_DEFAULTS = {name: False for name in ALL_FLAGS}


def load_config() -> dict:
    """Read the flag file. Any problem -> every flag OFF."""
    cfg = dict(_DEFAULTS)
    if not CONFIG.exists():
        return cfg
    try:
        raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for name in ALL_FLAGS:
        cfg[name] = raw.get(name) is True
    return cfg


def flag(name: str) -> bool:
    if name not in ALL_FLAGS:
        raise KeyError(f"unknown research flag {name!r}; known: {ALL_FLAGS}")
    return load_config()[name]


def enabled_flags() -> list[str]:
    return [n for n in ALL_FLAGS if flag(n)]


def write_config(**kw) -> dict:
    """Only used by the operator to turn a flag on."""
    bad = set(kw) - set(ALL_FLAGS)
    if bad:
        raise KeyError(f"unknown flag(s): {sorted(bad)}")
    cfg = load_config()
    cfg.update({k: (v is True) for k, v in kw.items()})
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return cfg


def require(name: str, what: str) -> None:
    """Raise unless the named flag is on. Used to refuse to emit anything that
    would read as a production artefact while the flag is OFF."""
    if not flag(name):
        raise RuntimeError(
            f"{name} is OFF — refusing to {what}. The spec requires every item "
            f"to sit behind its flag, default OFF, until Kenneth approves it.")
