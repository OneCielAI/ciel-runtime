"""Render a captured console text buffer to PNG (not a pixel screenshot).

usage: render_screen.py <screen.txt> <out.png> <caption>
PrintWindow of the owned pseudoconsole returned rect=0x0, so the native screen
text captured by console_watch.py is rendered here with its provenance caption.
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
text = Path(sys.argv[1]).read_text(encoding="utf-8")
lines = [sys.argv[3], "-" * 120] + text.splitlines()
font = ImageFont.truetype("C:/Windows/Fonts/CascadiaMono.ttf", 15)
box = font.getbbox("M")
cw, ch = box[2] - box[0] + 1, 20
image = Image.new("RGB", (cw * 122 + 20, ch * len(lines) + 20), (12, 12, 12))
draw = ImageDraw.Draw(image)
for i, line in enumerate(lines):
    colour = (255, 214, 102) if i < 2 else (230, 230, 230)
    draw.text((10, 10 + i * ch), line, font=font, fill=colour)
image.save(sys.argv[2])
print(sys.argv[2], image.size)
