"""Hashing helpers shared by receipts (A2), fingerprints (A3) and
restatement tracking (B2).

One implementation so "same input -> same hash" cannot mean different things in
two subsystems. All hashes are sha256 hex over raw bytes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def file_sha256(path: Path) -> str:
    """Full-file digest. B2 deliberately hashes the WHOLE vendor file rather
    than per-row: a per-row scheme can miss a reordered-but-identical-column
    file, and the spec asks for file-level immutability."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    """Stable serialisation: sorted keys, no whitespace drift, UTF-8 kept as-is.

    Used for hashing structures (config hashes, export payloads) where byte-level
    formatting must not change the digest but key order must not matter."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def obj_sha256(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def hash_files(paths: Iterable[Path], base: Path | None = None) -> dict[str, str]:
    """Map repo-relative path -> sha256, for paths that exist.

    Missing files are simply absent from the map. That absence is what the
    deploy gate treats as a broken receipt: a receipt promising an input that
    is no longer there is not internally consistent."""
    out: dict[str, str] = {}
    for p in sorted(paths, key=lambda x: str(x)):
        if not p.is_file():
            continue
        key = str(p.relative_to(base)) if base else str(p)
        out[key] = file_sha256(p)
    return out


def config_hash(cfg: dict) -> str:
    """Digest of the flag config plus any caller-supplied tunables.

    Kept separate from the flag file's own bytes so a caller can fold in e.g.
    PARAMS without having to write them to disk first."""
    return obj_sha256(cfg)