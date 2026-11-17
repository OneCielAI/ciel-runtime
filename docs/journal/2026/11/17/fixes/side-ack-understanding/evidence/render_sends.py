"""Render the source-side sends of both E2E runs to one PNG (rendered text, not pixels).

usage: render_sends.py <evidence dir> <out.png>
"""
import json, sys, textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
base = Path(sys.argv[1])
font = ImageFont.truetype("C:/Windows/Fonts/malgun.ttf", 15)
lines = []
for run, label in (("e2e-before", "BEFORE 35ba17e"), ("e2e-after", "AFTER working tree")):
    r = json.loads((base / run / "result.json").read_text(encoding="utf-8"))
    lines.append((label + " · real Claude Code " + r["claude_version"] + " · Walkie stand-in sends to the source", (255, 214, 102)))
    events = [("event 300001 (request)", r["sends_ok_event"]), ("event 300002 (forced failure)", r["sends_failed_event"])]
    if "sends_inbound_ack_event" in r:
        events.append(("event 300003 (another session's receipt ACK)", r["sends_inbound_ack_event"]))
    for name, rows in events:
        lines.append(("  " + name + ": " + str(len(rows)) + " sends", (230, 230, 230)))
        for index, row in enumerate(rows, 1):
            text = (row["text"] or "").replace("\n", " // ")
            for k, part in enumerate(textwrap.wrap(text, 80) or [""]):
                prefix = ("    " + str(index) + ". [" + str(row["client_id"])[:8] + "] ") if k == 0 else " " * 21
                colour = (98, 230, 167) if run == "e2e-after" else (255, 140, 150)
                lines.append((prefix + part, colour))
    for key, value in r["checks"].items():
        lines.append(("  " + ("PASS " if value else "FAIL ") + key, (98, 230, 167) if value else (255, 100, 124)))
    lines.append(("", (0, 0, 0)))
image = Image.new("RGB", (1180, 22 * len(lines) + 20), (12, 12, 12))
draw = ImageDraw.Draw(image)
for i, (text, colour) in enumerate(lines):
    draw.text((10, 10 + i * 22), text, font=font, fill=colour)
image.save(sys.argv[2]); print(sys.argv[2], image.size)
