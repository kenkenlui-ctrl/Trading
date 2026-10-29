"""build_state.py — record how each build ended, and check the previous one.

THE FAILURE THIS EXISTS FOR
    On 2026-10-26, 27 and 28 the site sat on 2026-10-23 data and nothing said
    so. Three nights, no error, no alert, no visible sign. The builds were
    not failing — they were never attempted, because there was no job that
    attempts them. But the second half of that is what hurts: even a failed
    build was invisible, because a failed build cannot publish its own
    failure.

    So this file makes two things true:

      1. Every build's outcome is recorded (pass/fail, the reason, the data
         sessions it covered, the deployment it produced) in
         data/build_state.json. The NEXT build reads that record BEFORE it
         starts and says, loudly, what happened last time. A failure is
         therefore carried forward into the next build instead of evaporating.

      2. The site can publish the last build's state, so a visitor sees it
         too. The page also judges its own freshness at request time (see
         scripts/staleness.py and public/static/staleness.js), which covers
         the window where the last build failed and there is no new build to
         publish anything.

USAGE
    python3 scripts/build_state.py --check-previous      # before a build
    python3 scripts/build_state.py --record pass --deploy-url <url>
    python3 scripts/build_state.py --record fail --reason "<why>"
    python3 scripts/build_state.py --show
"""
from __future__ import annotations

import json
import pathlib
import subprocess
from datetime import datetime

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "build_state.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def load() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def record(status: str, reason: str = "", deploy_url: str = "",
           sessions: dict | None = None, started_at: str = "") -> dict:
    """Write the outcome of the build that just finished."""
    rec = {
        "finished_at": _now(),
        "started_at": started_at or load().get("started_at", "") or _now(),
        "status": status,
        "reason": reason,
        "deploy_url": deploy_url,
        "sessions": sessions or load().get("sessions", {}),
        "commit": _git_commit(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rec


def mark_started() -> None:
    """Note that a build has begun, so a crash mid-build is visible."""
    prev = load()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "started_at": _now(),
        "finished_at": "",
        "status": "running",
        "reason": "",
        "deploy_url": prev.get("deploy_url", ""),
        "sessions": prev.get("sessions", {}),
        "commit": _git_commit(),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_previous() -> int:
    """Loudly report the previous build's outcome. Returns 0 always.

    It never blocks: a failed previous build is information, not a reason to
    refuse to publish a good one. But it is printed in a shape that cannot be
    scrolled past, because the whole point is that it was invisible before.
    """
    prev = load()
    status = prev.get("status", "")
    if not status:
        print("[build-state] no previous build recorded — this is the first "
              "run of the recorder")
        return 0
    if status == "pass":
        print(f"[build-state] last build PASSED at {prev.get('finished_at', '?')}"
              + (f" (deployed {prev.get('deploy_url', '')})" if prev.get("deploy_url") else ""))
        return 0
    banner = "=" * 72
    print(banner)
    print(f"[build-state] !! THE LAST BUILD DID NOT SUCCEED ({status}) !!")
    print(f"[build-state]    finished: {prev.get('finished_at', '?')}")
    if prev.get("reason"):
        print(f"[build-state]    reason  : {prev['reason']}")
    if prev.get("started_at"):
        print(f"[build-state]    started : {prev['started_at']}")
    print("[build-state]    the site was NOT updated by that run")
    print(banner)
    return 0


def state_for_site() -> dict:
    """The last build's state, for the page to publish."""
    prev = load()
    return {
        "status": prev.get("status", "unknown"),
        "finished_at": prev.get("finished_at", ""),
        "reason": prev.get("reason", ""),
        "deploy_url": prev.get("deploy_url", ""),
        "sessions": prev.get("sessions", {}),
    }


def main() -> int:
    import sys

    args = sys.argv[1:]
    if "--check-previous" in args:
        return check_previous()
    if "--record" in args:
        status = args[args.index("--record") + 1] if "--record" in args else "fail"
        reason = ""
        if "--reason" in args:
            reason = args[args.index("--reason") + 1]
        url = ""
        if "--deploy-url" in args:
            url = args[args.index("--deploy-url") + 1]
        sessions = {}
        if "--sessions" in args:
            try:
                sessions = json.loads(args[args.index("--sessions") + 1])
            except Exception:
                sessions = {}
        rec = record(status, reason=reason, deploy_url=url, sessions=sessions)
        print(f"[build-state] recorded {status}" + (f" — {reason}" if reason else ""))
        return 0
    if "--started" in args:
        mark_started()
        print("[build-state] build started")
        return 0
    if "--show" in args:
        print(json.dumps(load(), ensure_ascii=False, indent=2))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
