"""Compare every file of an extracted published package with the same path at a git commit.

Usage: compare_package.py <repo> <commit> <package_dir> <out_json>
Differences are classified as line-ending-only or version stamp; anything else is reported.
"""
import json
import subprocess
import sys
from pathlib import Path

repo, commit, package, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
identical, line_endings, other, missing = 0, [], [], []
for path in sorted(p for p in package.rglob("*") if p.is_file()):
    rel = path.relative_to(package).as_posix()
    shown = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{rel}"], capture_output=True)
    if shown.returncode:
        missing.append(rel)
        continue
    data = path.read_bytes()
    if data == shown.stdout:
        identical += 1
    elif data.replace(b"\r\n", b"\n") == shown.stdout.replace(b"\r\n", b"\n"):
        line_endings.append(rel)
    else:
        other.append(rel)
record = {"commit": commit, "package_files": identical + len(line_endings) + len(other) + len(missing),
          "identical": identical, "line_endings_only": line_endings, "other_differences": other,
          "not_in_commit": missing}
out.write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
