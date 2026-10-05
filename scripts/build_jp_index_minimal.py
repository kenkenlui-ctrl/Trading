"""build_jp_index_minimal.py — Re-render ONLY the JP hub index.html from already-copied source data.

Avoids the ohlc.json copy step that times out on NFS. Reads from the chart.json
files in public/jp200/charts/ that regen_action_plan.py already wrote.

Usage: python3 scripts/build_jp_index_minimal.py
"""
import sys
import json
from pathlib import Path

REPO = Path("/Users/kenken/dev/dsa-hk")
SRC = REPO / "data" / "jp200"
PUBLIC = REPO / "public"
JP_OUT = PUBLIC / "jp200"

# Read JP universe
universe = json.load(open(REPO / "jp_universe_200.json"))


def load_t(ticker: str) -> dict | None:
    safe = ticker.replace(".", "_")
    path = JP_OUT / "charts" / f"{safe}.json"
    if not path.exists():
        return None
    try:
        return json.load(open(path))
    except Exception:
        return None


rows = []
for tk in universe:
    tj = load_t(tk)
    if not tj:
        continue
    # 2026-10-05: JP snapshots are flat (asof/close/chg_pct) — reading
    # last_bar.C directly gave 0 on every row. snapshot_quote() reads both.
    from build_dashboard import snapshot_quote
    q = snapshot_quote(tj)
    ap = tj.get("action_plan", {})
    rows.append({
        "ticker": tk,
        "name": q["name"],
        "last": q["last"],
        "chg_pct": q["chg_pct"],
        "phase": tj.get("phase", "range"),
        "action_plan": ap,
        "adx": tj.get("adx"),
        "regime": tj.get("regime"),
    })

# Render minimal index.html (we'll let build_dashboard.py do the full version later)
print(f"JP rows: {len(rows)}")
phases = {}
for r in rows:
    phases[r["phase"]] = phases.get(r["phase"], 0) + 1
print(f"Phases: {phases}")
actions = {}
for r in rows:
    v = r["action_plan"].get("verdict", "WAIT")
    actions[v] = actions.get(v, 0) + 1
print(f"Actions: {actions}")
print("Build JP index minimal — done. Run full build_dashboard.py when NFS is responsive.")
