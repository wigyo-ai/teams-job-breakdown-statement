"""
Generates placeholder Teams app icons:
  color.png   — 192x192 px  (Certis navy background, white text)
  outline.png — 32x32 px    (white on transparent background)

Run from the teams/ directory:
  python teams/create_icons.py

Requires Pillow:
  pip install Pillow
Replace the generated placeholders with production-quality artwork before
submitting to the Teams Admin Center.
"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Color icon — 192x192, navy background ────────────────────────────────────
size = 192
img = Image.new("RGB", (size, size), color=(26, 58, 92))   # #1A3A5C
draw = ImageDraw.Draw(img)

# White rounded rectangle as card
margin = 24
draw.rounded_rectangle(
    [margin, margin, size - margin, size - margin],
    radius=18,
    fill=(255, 255, 255),
)

# Navy "JBS" text centred
text = "JBS"
try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 52)
except Exception:
    font = ImageFont.load_default()

bbox = draw.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
draw.text(
    ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
    text,
    fill=(26, 58, 92),
    font=font,
)

color_path = os.path.join(OUT_DIR, "color.png")
img.save(color_path)
print(f"Saved → {color_path}")

# ── Outline icon — 32x32, white on transparent ───────────────────────────────
size = 32
img2 = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw2 = ImageDraw.Draw(img2)

try:
    font2 = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
except Exception:
    font2 = ImageFont.load_default()

text2 = "J"
bbox2 = draw2.textbbox((0, 0), text2, font=font2)
tw2, th2 = bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]
draw2.text(
    ((size - tw2) / 2 - bbox2[0], (size - th2) / 2 - bbox2[1]),
    text2,
    fill=(255, 255, 255, 255),
    font=font2,
)

outline_path = os.path.join(OUT_DIR, "outline.png")
img2.save(outline_path)
print(f"Saved → {outline_path}")
