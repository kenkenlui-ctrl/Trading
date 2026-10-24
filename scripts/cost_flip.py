"""cost_flip.py — at what round-trip cost does the v2-vs-equal-weight gap
change sign?

WHY
    "v2 beats equal weight by X points a year" is only true at the cost the
    page happens to quote. Move the cost assumption and the sign can move.
    The site already learned this the hard way: Japan flips sign between a
    0.10% and a 0.25% round-trip cost, so quoting one number without the
    other is choosing the flattering half.

    This module turns the three measured cost legs the retest already
    produces (0.00%, 0.10%, 0.25%) into the answer a reader actually wants:
    at what cost does the gap cross zero.

HOW THE CROSSING IS COMPUTED
    Between two MEASURED legs, the crossing is a linear interpolation.
    Beyond the outermost measured leg, it is an EXTRAPOLATION and is
    labelled as one — because the CAGR response to cost is not exactly
    linear (the measured legs prove it: the US slope changes from 34.3 to
    19.9 points per 1% of cost between the two intervals), and pretending
    otherwise would be a made-up number dressed as a measurement.

    A market whose gap is negative even at zero cost has no crossing at any
    positive cost. That is reported as "no crossing", not as a number.

    Nothing here is hand-typed: every gap comes from
    data/hk_retest_summary.json, and every crossing is computed from those
    gaps at build time.
"""
from __future__ import annotations

import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
SOURCE = REPO / "data" / "hk_retest_summary.json"

MARKETS = [("us", "美股"), ("jp", "日股"), ("hk", "港股")]


def load() -> dict:
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def _legs(legs: dict) -> list[tuple[float, float]]:
    """[(cost as a fraction, gap in pp), ...] sorted by cost."""
    out = []
    for key, wins in legs.items():
        try:
            pct = float(key.split("_", 1)[1].rstrip("%"))
        except (IndexError, ValueError):
            continue
        # one window per call; the caller passes a single-window dict
        for _w, blk in wins.items():
            out.append((pct / 100.0, float(blk["v2_minus_ew_pp"])))
            break
    return sorted(out)


def _window_legs(summary: dict, mkt: str, window: str) -> list[tuple[float, float]]:
    legs = summary[mkt]["portfolio"]["v2_vs_ew_pp"]
    out = []
    for key, wins in legs.items():
        try:
            pct = float(key.split("_", 1)[1].rstrip("%"))
        except (IndexError, ValueError):
            continue
        blk = wins.get(window)
        if not blk:
            continue
        out.append((pct / 100.0, float(blk["v2_minus_ew_pp"])))
    return sorted(out)


def crossing(points: list[tuple[float, float]]) -> dict:
    """Where the gap crosses zero, given the measured (cost, gap) points.

    Returns {kind, cost, method}:
      kind   "crossed"      the sign changes between two measured legs
             "extrapolated" no sign change, but the gap is shrinking; the
                            crossing lies beyond the measured range
             "never"        the gap does not cross zero at a positive cost
    """
    if len(points) < 2:
        return {"kind": "never", "cost": None, "method": "只有一個成本腿，無法判斷。"}

    # 1. a sign change between two measured legs -> interpolate
    for (c1, g1), (c2, g2) in zip(points, points[1:]):
        if (g1 > 0) != (g2 > 0):
            cost = c1 + (c2 - c1) * (0.0 - g1) / (g2 - g1)
            return {
                "kind": "crossed",
                "cost": cost,
                "method": f"喺 {c1 * 100:.2f}% 同 {c2 * 100:.2f}% 兩個已量度成本之間線性插值。",
            }

    # 2. no sign change: does the gap shrink as the cost rises?
    (c1, g1), (c2, g2) = points[-2], points[-1]
    slope = (g2 - g1) / (c2 - c1)  # pp per unit cost
    if slope >= 0:
        return {
            "kind": "never",
            "cost": None,
            "method": "差距唔會隨成本上升而縮細，所以喺任何正成本都唔會變號。",
        }
    if g2 < 0:
        return {
            "kind": "never",
            "cost": None,
            "method": "零成本都已經係負數，所以喺任何正成本都唔會變號。",
        }
    # 3. positive at the highest measured cost and shrinking -> extrapolate
    cost = c2 - g2 / slope
    return {
        "kind": "extrapolated",
        "cost": cost,
        "method": (f"由 {c1 * 100:.2f}% 同 {c2 * 100:.2f}% 兩個已量度成本線性外推"
                   f"（已超出已量度範圍，只作參考）。"),
    }


def table() -> dict:
    """Everything the page prints, computed from the retest summary."""
    s = load()
    windows = list(s["us"]["portfolio"]["v2_vs_ew_pp"]["cost_0.25%"].keys())
    rows = []
    for mkt, label in MARKETS:
        for w in windows:
            pts = _window_legs(s, mkt, w)
            if not pts:
                continue
            cr = crossing(pts)
            rows.append({
                "market": mkt,
                "market_label": label,
                "window": w,
                "points": pts,
                "crossing": cr,
                "gap_at": {f"{c * 100:.2f}%": g for c, g in pts},
            })
    return {"windows": windows, "rows": rows, "source": "data/hk_retest_summary.json"}


def fmt_pp(g: float) -> str:
    return f"{g:+.2f}"


def fmt_cost(c: float | None) -> str:
    return "—" if c is None else f"{c * 100:.3f}%"


def render_table() -> str:
    """The HTML table for the methodology page."""
    t = table()
    head = ["市場", "窗口"] + [f"{c * 100:.2f}% 差距" for c, _ in t["rows"][0]["points"]] \
        + ["變號成本", "點計"]
    lines = []
    for r in t["rows"]:
        cells = [
            f'<b>{r["market_label"]}</b>',
            r["window"],
        ]
        for c, g in r["points"]:
            colour = "var(--bull)" if g > 0 else "var(--bear)"
            cells.append(f'<span style="color:{colour}">{fmt_pp(g)} pp</span>')
        cr = r["crossing"]
        if cr["kind"] == "never":
            cells.append("<b>唔會變號</b>")
        elif cr["kind"] == "crossed":
            cells.append(f'<b style="color:var(--amber)">{fmt_cost(cr["cost"])}</b>')
        else:
            cells.append(f'<b style="color:var(--amber)">{fmt_cost(cr["cost"])}</b>（外推）')
        cells.append(cr["method"])
        lines.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (
        '<table style="border-collapse:collapse;width:100%;font-size:.88rem;margin:12px 0">'
        '<thead><tr>' + "".join(
            f'<th style="text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)">{h}</th>'
            for h in head) + "</tr></thead><tbody>"
        + "".join(lines) + "</tbody></table>"
    )


def flip_cost(market: str, which: str = "full") -> str:
    """The crossing cost for one market and window, formatted.

    which: "full" (the whole sample) or "late" (the second sub-window).
    The window keys come from the artifact, never typed in.
    """
    t = table()
    windows = t["windows"]
    want = windows[0] if which == "full" else windows[-1]
    for r in t["rows"]:
        if r["market"] == market and r["window"] == want:
            return fmt_cost(r["crossing"]["cost"])
    return "—"


def flip_sentence() -> str:
    """One sentence for the home page: where US and JP cross zero."""
    t = table()
    full = t["windows"][0]
    out = []
    for mkt, label in (("us", "美股"), ("jp", "日股"), ("hk", "港股")):
        for r in t["rows"]:
            if r["market"] == mkt and r["window"] == full:
                cr = r["crossing"]
                if cr["kind"] == "never":
                    out.append(f"{label}零成本都仲係負數")
                elif cr["kind"] == "crossed":
                    out.append(f"{label}喺 {fmt_cost(cr['cost'])} 變號")
                else:
                    out.append(f"{label}喺 {fmt_cost(cr['cost'])} 變號（外推）")
                break
    return "；".join(out) + "。"
