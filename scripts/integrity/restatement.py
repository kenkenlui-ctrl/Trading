"""B2 — vendor restatement tracking.

The hazard (spec Background): a vendor re-issues an already-ingested daily file
with corrected values. Per-row or per-day hashes look stable because the session
dates are unchanged, so signal-distance percentiles silently drift and nothing
notices. This module makes the re-issue loud.

Ledger at logs/restatements.jsonl, append-only, one record per ingest attempt:

  observed_at   when this ingest was seen
  session       the vendor session/tag the file claims to cover
  vendor_file   repo-relative path of the ingested file
  sha256        full-file digest at this ingest
  prev_sha256   the digest recorded for this session before, or null on first sight
  event         NEW_SESSION | RESTATEMENT | UNCHANGED
  vendor_markers  any version/date markers the vendor put in the file, if present
  dependents    repo-relative paths whose derived quantities consumed this file

`event` is the whole point: a RESTATEMENT must be distinguishable from a
genuinely new bar, and both distinguishable from a byte-identical re-ingest.

Blast radius (B2.3): `dependents_of()` reads a declared dependency map so a
restatement can name everything that has to be recomputed, instead of leaving an
analyst to grep for it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Iterable

from .hashing import file_sha256

REPO = Path(__file__).resolve().parent.parent.parent
LEDGER = REPO / "logs" / "restatements.jsonl"
DEPMAP = REPO / "config" / "ingest_dependencies.json"

NEW_SESSION = "NEW_SESSION"
RESTATEMENT = "RESTATEMENT"
UNCHANGED = "UNCHANGED"

# Vendor version/date markers, if the file carries any. Kept as a small ordered
# list of regexes so adding a vendor means adding one line, not a rewrite.
_MARKER_PATTERNS = (
    ("exported_at", re.compile(r'"(?:exported_at|exportedAt|last_updated|as_of)"\s*:\s*"([^"]+)"')),
    ("file_version", re.compile(r'"(?:file_version|version|schema_version)"\s*:\s*"?([^",}]+)"')),
)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def vendor_markers(path: Path) -> dict:
    """Best-effort extraction of the vendor's own version/date markers.

    Deliberately tolerant: a file with no markers is normal, and a marker we
    fail to parse must not fail the ingest."""
    out: dict = {}
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:8192]
    except Exception:
        return out
    for name, pat in _MARKER_PATTERNS:
        m = pat.search(head)
        if m:
            out[name] = m.group(1)
    return out


def _last_for(log: Path, vendor_file: str, session: str) -> dict | None:
    if not log.exists():
        return None
    rec = None
    for ln in log.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("vendor_file") == vendor_file and r.get("session") == session:
            rec = r
    return rec


def _append(rec: dict, log: Path) -> dict:
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return rec


def dependents_of(vendor_file: str, depmap: Path = DEPMAP) -> list[str]:
    """Declared consumers of a vendor file, read from config/ingest_dependencies.json.

    Shape: {"charts/hk200/AAPL.json": ["data/v2_signals.json", ...]}.
    A missing file means 'nobody declared dependents', which is reported as an
    empty list rather than an error — an undeclared dependency is a gap in the
    map, not a broken tracker."""
    if not depmap.exists():
        return []
    try:
        m = json.loads(depmap.read_text())
    except Exception:
        return []
    v = m.get(vendor_file)
    return list(v) if isinstance(v, list) else []


def ingest(path: Path, session: str, *, repo: Path = REPO, log: Path = LEDGER,
           write: bool = True) -> dict:
    """Record one ingest of `path` claiming to cover `session`.

    Returns the ledger record. `write=False` makes it a dry run, used by the
    probe tests so demonstrating the guard does not pollute the real ledger."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        key = str(path.relative_to(repo))
    except ValueError:
        key = str(path)
    sha = file_sha256(path)
    prev = _last_for(log, key, session)
    prev_sha = (prev or {}).get("sha256")

    if prev is None:
        event = NEW_SESSION
    elif prev_sha == sha:
        event = UNCHANGED
    else:
        event = RESTATEMENT

    rec = {
        "observed_at": _now(),
        "session": session,
        "vendor_file": key,
        "sha256": sha,
        "prev_sha256": prev_sha,
        "event": event,
        "vendor_markers": vendor_markers(path),
        "dependents": dependents_of(key) if event == RESTATEMENT else [],
    }
    return _append(rec, log) if write else rec


def restatements(log: Path = LEDGER) -> list[dict]:
    """All RESTATEMENT events, oldest first."""
    if not log.exists():
        return []
    out = []
    for ln in log.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("event") == RESTATEMENT:
            out.append(r)
    return out