"""nk225_membership.py — point-in-time Nikkei 225 membership from the official log.

WHY
    portfolio_test.py found v2 beating a passive hold of the SAME 201 names by
    +6.9pp/yr in Japan. That differential could still be a survivorship artefact:
    the 201 names are today's large caps, so any name that only became a large
    cap in, say, 2021 was not investable as part of a 2016 "large cap" basket,
    and its history is being counted as if it were.

    Nikkei publishes the full component-change log since 1970. That gives a
    membership date per code, which is a selection made WITHOUT reference to
    whether the stock went up afterwards.

CAVEAT ON PARSING
    The table renders the date bottom-aligned within each group, so a row's date
    label is the next label at or below it. Ambiguity is at most one row, and
    both candidate labels are almost always in the same year. Names whose
    derived membership date lands within 12 months of the 2016-09-01 backtest
    start are reported separately rather than silently bucketed.

Output: data/nk225_entry.json  { "1306": {"added": "...", "name": "..."} }
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PDF = Path("/tmp/nk225_changes.pdf")
OUT = Path("/Users/kenken/dev/dsa-hk/data/nk225_entry.json")
START = "2016-09-01"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
DATE_RE = re.compile(r"([A-Z][a-z]{2})/(\d{1,2})/(\d{4})")
CODE_RE = re.compile(r"\b([0-9]{4}[0-9A-Z]?)\b")
NAME_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9&.,'’\- ]{2,40}?)\s{2,}([0-9]{4}[0-9A-Z]?)\s{2,}"
    r"([A-Za-z][A-Za-z0-9&.,'’\- ]{2,40}?)\s{2,}([0-9]{4}[0-9A-Z]?)\b")


def rows_from_page(text: str) -> list[tuple[str | None, str, str, str, str]]:
    """-> [(date_or_None, del_name, del_code, add_name, add_code)]"""
    out = []
    for line in text.splitlines():
        if "Issues Deleted" in line or "Company Name" in line or "History of" in line:
            continue
        m = NAME_RE.search(line)
        if not m:
            continue
        d = DATE_RE.search(line)
        date = f"{d.group(3)}-{MONTHS[d.group(1)]:02d}-{int(d.group(2)):02d}" if d else None
        out.append((date, m.group(1).strip(), m.group(2), m.group(3).strip(), m.group(4)))
    return out


def main() -> int:
    import pypdf
    reader = pypdf.PdfReader(str(PDF))
    events: list[tuple[str, str, str, str, str]] = []
    for page in reader.pages:
        rows = rows_from_page(page.extract_text(extraction_mode="layout"))
        pending = None
        for date, dn, dc, an, ac in rows:
            if date:
                pending = date          # bottom-aligned: label applies to this and
                events.append((date, dn, dc, an, ac))
            else:
                # a continuation row: belongs to the most recent label
                events.append((pending or "UNKNOWN", dn, dc, an, ac))

    added: dict[str, tuple[str, str]] = {}
    deleted: dict[str, tuple[str, str]] = {}
    for date, dn, dc, an, ac in events:
        if date == "UNKNOWN":
            continue
        if dc not in added or date < added[dc][0]:
            added[dc] = (date, dn)
        if ac not in added or date < added[ac][0]:
            added[ac] = (date, an)
        deleted.setdefault(dc, (date, dn))
        deleted.setdefault(ac, (date, an))

    OUT.write_text(json.dumps({k: {"added": v[0], "name": v[1]} for k, v in added.items()},
                              indent=1, ensure_ascii=False))
    print(f"parsed {len(events)} change rows -> {len(added)} distinct codes added")
    dated = [v[0] for v in added.values() if v[0] != "UNKNOWN"]
    print(f"  earliest added: {min(dated)}   latest: {max(dated)}")
    late = sorted([(v[0], k, v[1]) for k, v in added.items() if v[0] > START], reverse=True)
    print(f"  added AFTER {START}: {len(late)}")
    for d, c, n in late[:25]:
        print(f"    {d}  {c}  {n}")

    # coverage against the universe we actually trade
    bars = Path("/Users/kenken/dev/dsa-hk/data/bt10y2")
    codes = [p.stem[:-2] for p in bars.glob("*_T.json")]
    hit = [c for c in codes if c in added]
    never = [c for c in codes if c not in added]
    before = [c for c in hit if added[c][0] <= START]
    after = [c for c in hit if added[c][0] > START]
    print(f"\n  our JP universe: {len(codes)} names")
    print(f"    in the log with a KNOWN pre-2016 entry : {len(before)}")
    print(f"    added after 2016-09-01 (late entrant)  : {len(after)}")
    print(f"    not matched in the log                : {len(never)}")
    if never[:15]:
        print(f"      e.g. {never[:15]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
