"""A3 — process fingerprints.

Every pipeline run appends one JSON line to logs/fingerprints.jsonl. The file is
append-only: a run is never rewritten or deleted, because the question this
answers is "what produced the number I am looking at", and an editable history
cannot answer that.

Record fields (one line each):

  run_id          unique per run, sortable by timestamp
  started_at      ISO-8601 UTC
  finished_at     ISO-8601 UTC
  terminal_state  pass | fail | blocked   (blocked = a guard stopped it)
  git_commit      commit at run time
  config_hash     digest of effective config
  engine_versions {engine: version} as executed
  input_hashes    {repo-relative path: sha256} of vendor/script inputs
  flags           which sprint2 flags were on for this run
  note            free text: what blocked it, if blocked

Backfill is NOT required (spec A3.3) — the first record after this sprint is
the beginning of the series.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .hashing import config_hash

REPO = Path(__file__).resolve().parent.parent.parent
LOG = REPO / "logs" / "fingerprints.jsonl"

TERMINAL_STATES = ("pass", "fail", "blocked")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def new_run_id() -> str:
    return f"{_now().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:8]}"


def make_record(*, run_id: str, started_at: str, finished_at: str | None = None,
                terminal_state: str = "pass", git_commit: str = "unknown",
                engine_versions: dict[str, str] | None = None,
                input_hashes: dict[str, str] | None = None,
                cfg: dict | None = None, flags: dict[str, bool] | None = None,
                note: str = "") -> dict:
    if terminal_state not in TERMINAL_STATES:
        raise ValueError(f"terminal_state must be one of {TERMINAL_STATES}")
    return {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at or _now(),
        "terminal_state": terminal_state,
        "git_commit": git_commit,
        "config_hash": config_hash(cfg or {}),
        "engine_versions": dict(engine_versions or {}),
        "input_hashes": dict(input_hashes or {}),
        "flags": dict(flags or {}),
        "note": note,
    }


def append(record: dict, log: Path = LOG) -> dict:
    """Append one record. Creates the log dir if needed.

    Append-only by construction: 'a' mode with a single write of one line, so
    two concurrent runs cannot interleave a partial record on this platform."""
    if record.get("terminal_state") not in TERMINAL_STATES:
        raise ValueError("refusing to append a record with no valid terminal_state")
    log.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, ensure_ascii=False)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return record


def read_all(log: Path = LOG) -> list[dict]:
    if not log.exists():
        return []
    out = []
    for ln in log.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            # A corrupt line must not make the whole history unreadable; it is
            # itself a finding, so it is reported rather than hidden.
            out.append({"_corrupt_line": ln[:200]})
    return out


def find(run_id: str, log: Path = LOG) -> dict | None:
    for r in read_all(log):
        if r.get("run_id") == run_id:
            return r
    return None


def joinable(receipt: dict, log: Path = LOG) -> dict | None:
    """A2.2's "joinable" requirement: given an export's receipt, return the
    fingerprint record of the run that produced it.

    Match is on (git_commit, config_hash) plus, when the receipt names its run,
    run_id. A receipt produced by a run that left no fingerprint is a finding —
    the caller gets None and decides."""
    if not receipt:
        return None
    if receipt.get("run_id"):
        r = find(receipt["run_id"], log)
        if r:
            return r
    cands = [r for r in read_all(log)
             if r.get("git_commit") == receipt.get("git_commit")
             and r.get("config_hash") == receipt.get("config_hash")]
    return cands[-1] if cands else None