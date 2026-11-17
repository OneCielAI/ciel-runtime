"""Render the E2E console captures and checks to one PNG (rendered text, not a pixel screenshot).

usage: render_e2e.py <evidence dir> <out.png>
The owned ConPTY session gives a text tail, not pixels. Each run's lines that
name a Work fork row (agent list) and the result checks are drawn side by side.
"""
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

base = Path(sys.argv[1])
font = ImageFont.truetype("C:/Windows/Fonts/CascadiaMono.ttf", 15)
box = font.getbbox("M")
cw, ch = box[2] - box[0] + 1, 20
lines = []
for run, label in (("e2e-before", "BEFORE  HEAD 6622029"), ("e2e-after", "AFTER   ec4e527")):
    result = json.loads((base / run / "result.json").read_text(encoding="utf-8"))
    console = (base / run / "console.txt").read_text(encoding="utf-8").splitlines()
    rows = [line.strip() for line in console if "work-auto-" in line][-2:]
    lines.append((label + " · real Claude Code " + result["claude_version"]
                  + " · git told every repo is another account's", (255, 214, 102)))
    lines.append(("  agent list rows naming a Work fork: " + (str(len(rows)) if rows else "none"), (230, 230, 230)))
    for row in rows:
        lines.append(("    " + row[row.find("◯"):][:110] if "◯" in row else "    " + row[:110], (98, 230, 167)))
    for key, value in result["checks"].items():
        lines.append(("  " + ("PASS " if value else "FAIL ") + key, (98, 230, 167) if value else (255, 100, 124)))
    lines.append(("  leases " + json.dumps([[r["state"], r["bound"], (r["workspace"] or {}).get("exists")]
                                             for r in result["leases"]]), (230, 230, 230)))
    lines.append(("  coordination runs " + json.dumps(result["coordination_runs"]), (230, 230, 230)))
    lines.append(("", (0, 0, 0)))
image = Image.new("RGB", (cw * 122 + 20, ch * len(lines) + 20), (12, 12, 12))
draw = ImageDraw.Draw(image)
for index, (text, colour) in enumerate(lines):
    draw.text((10, 10 + index * ch), text, font=font, fill=colour)
image.save(sys.argv[2])
print(sys.argv[2], image.size)
