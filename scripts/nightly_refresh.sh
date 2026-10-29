#!/bin/bash
# nightly_refresh.sh — the scheduled build that was missing.
#
# WHY THIS FILE EXISTS
#   On 2026-10-22/23 and again on 2026-10-26/27/28 the site sat on old data
#   and nothing said so. The cause was not a failing job: there was no job.
#   The launchd scheduler ran an analysis job and a chanlun scan, the sync
#   job committed the repo, and nothing in the machine ever built the site.
#
#   Two things then had to be true for this to be safe to schedule:
#
#   1. It has to survive a laptop that is closed and asleep. The machine
#      sleeps from roughly 18:00 with the lid shut and runs on battery, so a
#      calendar job at 23:00 would be skipped night after night. launchd
#      coalesces missed calendar jobs and runs them at the next wake, and
#      the plist also sets RunAtLoad — so the build happens when the lid
#      opens, not only when the clock says 07:00.
#
#   2. It has to be silent when there is nothing to do. The data is already
#      current most mornings; rebuilding and redeploying 2,700 files for no
#      reason would be noise. So the job checks first and skips.
#
# THE CHECK (2026-10-29, corrected)
#   The first version compared the published data against data/market_sessions
#   .json — which is written BY THE BUILD. That file was therefore always
#   exactly as old as the data, so the two always agreed and the job always
#   skipped. The three missed nights would have been missed again, silently,
#   by a job that looked like it was working.
#
#   The comparison has to be against the VENDOR'S current state, so the job
#   asks the vendor first (scripts/market_sessions.py — three index bars,
#   seconds) and only then decides:
#
#       vendor's latest closed session  >  our published session  ->  behind
#       otherwise                                                  ->  current
#
#   The dates the build is then told to fetch are the vendor's own latest
#   closed sessions — one per market, because the three do not share a
#   calendar. Nothing here is a hand-typed date, and nothing is inferred
#   from "yesterday": a session that has not closed is not a session.
#
# WHAT IT DOES ON A REAL RUN
#   scripts/refresh.sh — fetch → build → self-test → A2 deploy gate → deploy.
#   refresh.sh records the outcome in data/build_state.json, so the NEXT run
#   (this one or a manual one) reports the previous failure out loud before
#   it starts.
#
# FAILURE
#   A failed build must not be silent. This script sends a macOS notification
#   (the laptop shows it when it wakes) and writes the reason to
#   logs/nightly-*.log. The site itself also judges its own freshness at
#   request time, so a site whose build failed says so to visitors even
#   though no new build could publish the news.
set -uo pipefail
cd "$(dirname "$0")/.."

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/nightly-$STAMP.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "nightly build starting"

# --- what has the vendor closed? -------------------------------------------
# market_sessions.py reads the benchmark index's last CLOSE for each market.
# It refuses to count a session that has not closed yet, so a run at 07:10
# sees yesterday's sessions, not today's.
echo
log "asking the vendor what each market has closed..."
python3 scripts/market_sessions.py 2>&1 | tee -a "$LOG"

# --- are we behind? --------------------------------------------------------
# The vendor's latest closed session against the session our published bars
# actually end on. A market that has closed a session we do not have is a
# market that needs a rebuild.
eval "$(python3 - <<'PY'
import json, pathlib, sys
sys.path.insert(0, "scripts")
import staleness as S

ref = S.reference().get("markets") or {}
behind = []
for m in ("HK", "US", "JP"):
    key = S.MARKET_DIR[m]
    latest = str((ref.get(key) or {}).get("latest_closed_session") or "")[:10]
    ours = S.data_session(m)
    if latest and ours and ours < latest:
        behind.append(m)
print(f'BEHIND="{",".join(behind)}"')
PY
)"

if [ -z "${BEHIND:-}" ]; then
  log "already current — nothing to publish"
  exit 0
fi

log "behind the market's latest closed session: ${BEHIND}"

# --- the dates to fetch ----------------------------------------------------
# One per market, read from the vendor's own answer. DATA_DATE (the generic
# cap) is the latest of the three.
eval "$(python3 - <<'PY'
import pathlib, sys
sys.path.insert(0, "scripts")
import staleness as S

ref = S.reference().get("markets") or {}
def sess(m):
    return str((ref.get(S.MARKET_DIR[m]) or {}).get("latest_closed_session") or "")[:10]
hk, us, jp = sess("HK"), sess("US"), sess("JP")
print(f'ASOF_HK="{hk}"')
print(f'ASOF_US="{us}"')
print(f'ASOF_JP="{jp}"')
print(f'DATA_DATE="{max([d for d in (hk, us, jp) if d] or [""])}"')
PY
)"

if [ -z "${ASOF_HK:-}" ] || [ -z "${ASOF_US:-}" ] || [ -z "${ASOF_JP:-}" ]; then
  log "!!! the vendor did not report a closed session for every market — refusing to guess"
  python3 scripts/build_state.py --record fail --reason "nightly: vendor sessions incomplete" || true
  exit 1
fi

log "fetching: HK=$ASOF_HK US=$ASOF_US JP=$ASOF_JP"

# --- run the pipeline ------------------------------------------------------
ASOF_HK="$ASOF_HK" ASOF_US="$ASOF_US" ASOF_JP="$ASOF_JP" DATA_DATE="$DATA_DATE" \
  bash scripts/refresh.sh >>"$LOG" 2>&1
RC=$?

if [ $RC -ne 0 ]; then
  log "!!! refresh.sh FAILED (exit $RC) — see $LOG"
  python3 scripts/build_state.py --record fail --reason "nightly refresh.sh exited $RC" || true
  /usr/bin/osascript -e "display notification \"Nightly build FAILED (exit $RC). Log: $LOG\" with title \"win9you.com — build failed\"" 2>/dev/null || true
  exit $RC
fi

log "nightly build finished OK"
exit 0
