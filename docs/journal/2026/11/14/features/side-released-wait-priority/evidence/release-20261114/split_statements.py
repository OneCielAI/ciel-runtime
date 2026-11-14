"""Split E701/E702 one-line compound statements with `ruff format --range`, bottom-up.

Only the statement ranges ruff reports are formatted; the rest of each file is untouched.
Files are backed up under the given backup dir before any write.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
REPORT = Path(sys.argv[2])
BACKUP = Path(sys.argv[3])
RUFF = ["uvx", "ruff@0.15.22"]
PATTERN = re.compile(r"^(?P<path>[^:]+(?::[\\/][^:]+)?):(?P<line>\d+):\d+: (?P<code>E70[12]) ")

lines = defaultdict(set)
for raw in REPORT.read_text(encoding="utf-8").splitlines():
    match = PATTERN.match(raw)
    if match:
        lines[match["path"].replace("\\", "/")].add(int(match["line"]))
for rel, numbers in sorted(lines.items()):
    path = ROOT / rel
    if ".local" in Path(rel).parts:
        raise SystemExit(f"refusing scratch path {rel}")
    target = BACKUP / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(path, target)
    before = ast.dump(ast.parse(path.read_bytes()))
    for number in sorted(numbers, reverse=True):
        result = subprocess.run([*RUFF, "format", "--no-cache", f"--range={number}-{number + 1}", str(path)],
                                cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            raise SystemExit(f"{rel}:{number}: {result.stderr}")
    after = ast.dump(ast.parse(path.read_bytes()))
    if before != after:
        raise SystemExit(f"AST changed for {rel}")
    print(rel, len(numbers), "statements split; AST identical")
