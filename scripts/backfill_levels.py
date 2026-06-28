"""Backfill entry_zone / stop_loss / target_price / confidence by parsing full_md
for all 2026-06-27 reports.

Why: original DB schema never had these as columns, so the report page
always showed '—'. The LLM emits them as **bold** lines in the 操作建議
section. We regex them out and write to data_snapshot_json (which already
exists and is loosely typed).

Pattern (zh):
- **入場區間**: <value>
- **止損位**: <value>
- **目標價**: <value>
- 信心 <value>  (in summary)
"""
import json
import re
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "dsa_hk.db"
DATE = "2026-06-27"


def parse_levels(full_md: str, summary_md: str) -> dict:
    """Extract entry / stop / target / confidence from LLM markdown."""
    out = {}
    if full_md:
        for label, key in [
            (r"入場區間", "entry_zone"),
            (r"止損位", "stop_loss"),
            (r"目標價", "target_price"),
            (r"支持區", "support_zone"),
            (r"阻力區", "resistance_zone"),
        ]:
            m = re.search(rf"\*\*{label}\*\*\s*[:：]\s*([^\n]+)", full_md)
            if m:
                out[key] = m.group(1).strip()
    # Confidence: appears in summary "信心 高/中/低" or "信心 8" etc.
    for src in (summary_md, full_md):
        if "confidence" in out:
            break
        m = re.search(r"信心\s*(高|中|低|[0-9]+(?:\.[0-9]+)?)", src or "")
        if m:
            out["confidence"] = m.group(1).strip()
    return out


def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, code, summary_md, full_md, data_snapshot_json, support_zone, resistance_zone FROM daily_report WHERE report_date=?",
        (DATE,),
    ).fetchall()
    print(f"Scanning {len(rows)} reports for {DATE}")

    updates = []
    for row_id, code, summary_md, full_md, snapshot_raw, sup, res in rows:
        # If support/resistance already exist in their own columns, keep them.
        # Otherwise (likely NULL) try to parse from full_md.
        parsed = parse_levels(full_md, summary_md)

        # Merge with existing snapshot
        snap = {}
        if snapshot_raw:
            try:
                snap = json.loads(snapshot_raw)
            except Exception:
                snap = {}
        snap["entry_zone"] = parsed.get("entry_zone") or snap.get("entry_zone")
        snap["stop_loss"] = parsed.get("stop_loss") or snap.get("stop_loss")
        snap["target_price"] = parsed.get("target_price") or snap.get("target_price")
        snap["confidence"] = parsed.get("confidence") or snap.get("confidence")
        if sup:
            snap["support_zone"] = sup
        elif parsed.get("support_zone"):
            snap["support_zone"] = parsed["support_zone"]
        if res:
            snap["resistance_zone"] = res
        elif parsed.get("resistance_zone"):
            snap["resistance_zone"] = parsed["resistance_zone"]

        new_snapshot = json.dumps(snap, ensure_ascii=False)
        updates.append((new_snapshot, row_id))

        # Print sample for first 5
        if row_id <= 5 or code in ("KO", "LLY", "00700.HK"):
            print(f"  {code}: entry={snap.get('entry_zone', '—')[:30]} stop={snap.get('stop_loss', '—')[:30]} target={snap.get('target_price', '—')[:30]} conf={snap.get('confidence', '—')}")

    cur.executemany(
        "UPDATE daily_report SET data_snapshot_json=? WHERE id=?",
        updates,
    )
    conn.commit()
    print(f"Updated {len(updates)} snapshots")
    conn.close()


if __name__ == "__main__":
    main()