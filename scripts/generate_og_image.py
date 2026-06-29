"""Generate OG image (1200x630) for Leeks Terminal."""
from PIL import Image, ImageDraw, ImageFont
import os

W, H = 1200, 630
img = Image.new("RGB", (W, H), (255, 255, 255))
draw = ImageDraw.Draw(img)

# Load fonts
font_h = font_b = font_s = None
for fp in ["/System/Library/Fonts/STHeiti Medium.ttc",
           "/System/Library/Fonts/STHeiti Light.ttc",
           "/Library/Fonts/Arial Unicode.ttf"]:
    if os.path.exists(fp):
        try:
            font_h = ImageFont.truetype(fp, 84)
            font_b = ImageFont.truetype(fp, 32)
            font_s = ImageFont.truetype(fp, 22)
            break
        except Exception:
            pass
if not font_h:
    font_h = ImageFont.load_default()

# Top accent bar
draw.rectangle([(0, 0), (W, 8)], fill=(37, 99, 235))

# Brand mark
draw.text((80, 80), "◆ Leeks Terminal", fill=(26, 29, 35), font=font_h)
draw.text((80, 200), "HK + US Day-Trade AI", fill=(75, 85, 99), font=font_b)

# Tagline (2 lines, no emoji)
draw.text((80, 280), "200 隻港股 + 200 隻美股 · 4 維度評分 + 交易方向", fill=(26, 29, 35), font=font_b)
draw.text((80, 330), "每日全自動分析 · 入場區間 / 止損 / 目標價齊全", fill=(26, 29, 35), font=font_b)

# Stats badge boxes — English labels for OG, no emoji
box_y = 410
boxes = [
    ("Scoring", "v / q / m / of", (21, 128, 61)),
    ("Signals", "Buy / Hold / Sell", (146, 64, 14)),
    ("Timeframe", "Day trade only", (185, 28, 28)),
    ("Markets", "HKEX + US", (37, 99, 235)),
]
bw = 240
gap = 20
total_w = bw * 4 + gap * 3
start_x = (W - total_w) // 2
for i, (label, value, color) in enumerate(boxes):
    x = start_x + i * (bw + gap)
    draw.rectangle([(x, box_y), (x + bw, box_y + 120)], outline=color, width=3)
    # Vertically center: label at top half, value at bottom half
    draw.text((x + 16, box_y + 18), label, fill=color, font=font_b)
    draw.text((x + 16, box_y + 65), value, fill=(26, 29, 35), font=font_s)

# URL
draw.text((80, 580), "win9you.com", fill=(37, 99, 235), font=font_b)

out_path = os.path.join(os.path.dirname(__file__), "..", "public", "og-image.png")
out_path = os.path.abspath(out_path)
os.makedirs(os.path.dirname(out_path), exist_ok=True)
img.save(out_path, "PNG", optimize=True)
print(f"Saved: {out_path} ({os.path.getsize(out_path) // 1024}KB)")
