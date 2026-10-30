"""Extract exact tracked test bytes at the release commit; never use dirty test sources."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[8]
OUT = Path(__file__).resolve().parent
commit = sys.argv[1]
assert len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)
tests = Path(tempfile.mkdtemp(prefix="ciel-sse-" + commit[:7] + "-tests-"))
files = subprocess.check_output(["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", commit, "tests"], encoding="utf-8").splitlines()
for name in files:
    target = tests / Path(name).relative_to("tests")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(subprocess.check_output(["git", "-C", str(ROOT), "show", commit + ":" + name]))
(OUT / "exact-tests-path.txt").write_text(str(tests), encoding="utf-8")
(OUT / "exact-test-sources.json").write_text(json.dumps({"commit": commit, "files": len(files), "path": str(tests)}, indent=2), encoding="utf-8")
print(tests)
