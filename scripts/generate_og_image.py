"""generate_og_image.py — v2.3 dark terminal OG image (1200x630)."""
from PIL import Image, ImageDraw, ImageFont
import random

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

# Stat cards
cards = [
    ("200+200", "HK + US universe", "#e8eaf0"),
    ("+3.05%", "T+10 swing avg (net)", "#10b981"),
    ("+0.38%", "T+1 day-trade avg", DIM),
    ("16", "configs · deflated", DIM),
]
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
