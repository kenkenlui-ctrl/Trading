"""A2 — export receipts.

Every JSON/HTML producer is supposed to embed a receipt describing exactly what
produced it, so the pre-publish gate can answer "which engine, which inputs,
which commit?" as a lookup instead of archaeology.

Receipt shape (one line each, per spec deliverable 4):

  schema         receipt schema version, so a gate can reject unknown shapes
  engine         logical producer name, e.g. "v2_equity_export"
  engine_version producer's semantic version — the gate pins this per expected engine
  git_commit     commit of the working tree at generation time
  config_hash    digest of the effective config (flags + tunables)
  input_hashes   {repo-relative path: sha256} for every input consumed
  generated_at   ISO-8601 UTC timestamp of generation

Nothing here mutates an existing export. `attach()` returns a NEW payload dict;
production writers are not modified in this sprint (flags OFF).
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
from pathlib import Path
from typing import Any

from .hashing import config_hash, file_sha256, obj_sha256

REPO = Path(__file__).resolve().parent.parent.parent
RECEIPT_KEY = "__receipt__"
SCHEMA = "sprint2.receipt/1"


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def git_commit(repo: Path = REPO) -> str:
    """Current commit. Falls back to 'unknown' rather than raising: a receipt
    with an unknown commit is still a valid receipt, and the gate is where that
    should be judged (it can be pinned if the spec requires it)."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def build_receipt(engine: str, engine_version: str, inputs: list[Path],
                  cfg: dict | None = None,
                  repo: Path = REPO,
                  generated_at: str | None = None) -> dict:
    """Assemble a receipt. `inputs` are hashed as repo-relative paths; a path
    outside the repo is hashed under its absolute string so it still shows up
    (an unhashed input is a hole in the chain)."""
    in_hashes: dict[str, str] = {}
    for p in inputs:
        p = Path(p)
        if p.is_file():
            try:
                key = str(p.relative_to(repo))
            except ValueError:
                key = str(p)
            in_hashes[key] = file_sha256(p)
        else:
            in_hashes[str(p)] = "MISSING"
    return {
        "schema": SCHEMA,
        "engine": engine,
        "engine_version": engine_version,
        "git_commit": git_commit(repo),
        "config_hash": config_hash(cfg or {}),
        "input_hashes": in_hashes,
        "generated_at": generated_at or utc_now(),
    }


def attach(payload: dict, receipt: dict) -> dict:
    """Return a copy of `payload` carrying `receipt`. Refuses to silently
    overwrite an existing receipt — two producers claiming one file is a
    conflict the gate should see, not something to paper over."""
    if RECEIPT_KEY in payload:
        raise ValueError(f"payload already carries a {RECEIPT_KEY!r}; refusing to overwrite")
    out = dict(payload)
    out[RECEIPT_KEY] = receipt
    return out


def get(payload: Any) -> dict | None:
    if isinstance(payload, dict):
        r = payload.get(RECEIPT_KEY)
        if isinstance(r, dict):
            return r
    return None


def receipt_digest(receipt: dict) -> str:
    return obj_sha256(receipt)