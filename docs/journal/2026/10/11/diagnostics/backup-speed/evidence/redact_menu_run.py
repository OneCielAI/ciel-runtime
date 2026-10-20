"""Keep only the backup panel of the menu-run screens (the menu also shows provider URL and key hints).

usage: python redact_menu_run.py RAW_DIR OUT_DIR
PNG: crops of the panel and the progress/result lines. TXT: lines from 'Session backup options' on,
without the provider status block.
"""
import sys
from pathlib import Path

from PIL import Image

raw, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
SECRET = ("Base URL", "API key", "base_url", "sk-", "fp ")

progress = Image.open(raw / "screen-06-progress.png")
panel = progress.crop((0, 380, 1129, 505))
line = progress.crop((0, 598, 1129, 625))
joined = Image.new("RGB", (1129, panel.height + line.height), (12, 12, 12))
joined.paste(panel, (0, 0))
joined.paste(line, (0, panel.height))
joined.save(out / "menu-backup-progress.png")
Image.open(raw / "screen-08-result.png").crop((0, 270, 1129, 518)).save(out / "menu-backup-result.png")

for name in ("screen-03-progress.txt", "screen-06-progress.txt", "screen-08-result.txt"):
    lines = (raw / name).read_text(encoding="utf-8").splitlines()
    start = next((i for i, text in enumerate(lines) if "Session backup options" in text), 0)
    kept = [lines[0]] + [text for text in lines[start:] if text.strip() and not any(word in text for word in SECRET)]
    (out / name.replace("screen-", "menu-")).write_text("\n".join(kept) + "\n", encoding="utf-8")
print("written", sorted(path.name for path in out.iterdir()))
