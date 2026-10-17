"""Sprint 2 integrity kit — research only, all flags default OFF.

Nothing in this package is imported by a production build or deploy path.
See `scripts/sprint2_selftests.py` for the one-command self-test entry point
and `docs/sprint2-runbook.md` for what each alert means.
"""
__all__ = ["flags", "hashing", "receipts", "gate", "fingerprints",
           "freshness", "crossval", "restatement", "gap_receipts"]
