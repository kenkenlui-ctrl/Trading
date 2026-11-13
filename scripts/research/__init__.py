"""Research-only package for the 2026-11-11 website credibility work.

Nothing in here is imported by a production build path. The spec
(docs/website-improvements-2026-11-11.md) requires:

  * no production change until Kenneth approves each flag
  * every flag default OFF
  * production code untouched — "grep for new identifiers in build paths
    returns nothing"

So the flags live HERE rather than in scripts/integrity/flags.py. The sprint2
kit is production: deploy_gate.py imports it on every publish, and
sprint2_selftests.py asserts that exactly the two approved flags are on and
that only the four A2-wired files may mention the kit. Adding four more names
to that registry would put new identifiers inside the production flag path for
no benefit — nothing in production reads them yet.

The fail-closed semantics are copied deliberately: a missing or corrupt config
must mean OFF, never ON. A research flag that fails open is worse than no flag.
"""
