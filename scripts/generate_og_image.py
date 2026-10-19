"""generate_og_image.py — v2.3 dark terminal OG image (1200x630).

EVERY FIGURE ON THIS CARD IS READ FROM AN ARTIFACT.

It used to carry "+3.05% T+10 swing avg (net)" — a sum of per-trade
percentage returns, which is the same arithmetic the site withdrew on
2026-10-18 and now refuses to publish anywhere else (the /backtest page's
"Risk metrics" table was removed for it, and its Headline table's "Total"
column was removed for the identical quantity). An OG image is the one piece
of the site that gets scraped into other people's pages, so a retracted
figure on it travels further than the page it came from.

The cards are therefore rendered from:
  - data/monthly_backtest/latest.md — the per-trade averages the /backtest
    page itself publishes
  - data/ab_close_hold.json — the US per-trade out-of-sample win rate
and the generator REFUSES to fall back to a literal when a source is
missing: a missing artifact should stop the build, not put a stale number
on the one image nobody re-reads before shipping.
"""
from PIL import Image, ImageDraw, ImageFont
import json
import pathlib
import random
import re

REPO = pathlib.Path("/Users/kenken/dev/dsa-hk")


def _headline_rows() -> dict:
    """The per-trade table the /backtest page publishes, from the artifact."""
    md = (REPO / "data" / "monthly_backtest" / "latest.md").read_text(encoding="utf-8")
    i = md.find("## Headline")
    if i < 0:
        raise SystemExit("generate_og_image: latest.md has no Headline section")
    block = md[i: md.find("\n## ", i + 5) if md.find("\n## ", i + 5) > 0 else len(md)]
    rows = {}
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5 or set(cells[0]) <= set("|-: "):
            continue
        rows[cells[0]] = {"n": cells[1], "wr": cells[2], "avg": cells[3]}
    return rows


def _oos_win_pct() -> str:
    """The US per-trade out-of-sample win rate, from the A/B artifact."""
    d = json.loads((REPO / "data" / "ab_close_hold.json").read_text(encoding="utf-8"))
    return f"{d['windows']['us200']['B_close-hold']['test']['win%']}%"


def _cards() -> list:
    hl = _headline_rows()
    t10 = hl.get("T+10 (10-day swing)")
    t1 = hl.get("T+1 (day-trade)")
    if not t10 or not t1:
        raise SystemExit("generate_og_image: the Headline table lost a horizon")
    # "200+200" is the brand line (HK + US universe), not a performance figure.
    return [
        ("200+200", "HK + US universe", "#e8eaf0"),
        (t10["avg"], "T+10 avg/trade", "#10b981"),
        (t1["avg"], "T+1 avg/trade", DIM),
        (_oos_win_pct(), "US win rate, OOS", DIM),
    ]


W, H = 1200, 630
BG = (10, 14, 26)
PANEL = (19, 24, 38)
BORDER = (31, 39, 53)
FG = (232, 234, 240)
FG2 = (197, 202, 214)
DIM = (136, 146, 164)
DIM2 = (107, 114, 128)
AMBER = (245, 158, 11)
BULL = (16, 185, 129)
BEAR = (244, 63, 94)
OUT = "/Users/kenken/dev/dsa-hk/public/og-image.png"

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

f_kicker = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 22)
f_title = ImageFont.truetype("/System/Library/Fonts/NewYork.ttf", 76)
f_sub = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", 36)
f_stat = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 52)
f_statl = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 21)
f_foot = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 22)
f_foot2 = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 22)

# Background + frames
d.rectangle([0, 0, W, H], fill=BG)
d.rectangle([16, 16, W - 17, H - 17], outline=BORDER, width=1)
d.rectangle([0, 0, W, 4], fill=AMBER)

# Kicker
d.text((70, 62), "FIRST-PARTY DATA · T-1 OHLC ONLY", font=f_kicker, fill=AMBER)

# Title
d.text((66, 100), "Leeks Terminal", font=f_title, fill=FG)
d.rectangle([70, 118, 230, 126], fill=BULL)
d.text((70, 195), "HK + US 即日鮮 AI 交易決策儀表板", font=f_sub, fill=FG2)

# Stat cards — every figure from an artifact (see _cards).
cards = _cards()
x0, y0 = 90, 290
for i, (big, sub, col) in enumerate(cards):
    x = x0 + i * 265
    d.rounded_rectangle([x - 14, y0 - 16, x + 230, y0 + 140], radius=8,
                        fill=PANEL, outline=BORDER, width=1)
    d.text((x + 16, y0 + 10), big, font=f_stat, fill=col)
    d.text((x + 2, y0 + 80), sub, font=f_statl, fill=DIM)

# Candlestick decoration
import random
random.seed(20260828)
for i in range(16):
    cx = 700 + i * 26
    o = 455 + random.randint(-18, 18)
    c = 470 + random.randint(-35, 20)
    col = "#26a69a" if c <= o else "#ef5350"
    d.line([cx, min(o, c) - 12, cx, max(o, c) + 10], fill=col, width=2)
    d.rectangle([cx - 7, min(o, c), cx + 7, max(o, c)], fill=col)

# Footer
d.text((90, 552), "10-step price-action framework · Python deterministic · zero LLM numbers", font=f_foot, fill=DIM)
d.text((90, 585), "www.win9you.com  ·  Educational use only · Not investment advice", font=f_foot, fill=DIM2)

img.save(OUT, "PNG", optimize=True)
print(f"OG image: {img.size[0]}x{img.size[1]} saved to {OUT}")
