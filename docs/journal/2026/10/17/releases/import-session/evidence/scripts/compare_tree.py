"""Compare every regular file under --tree with git --commit, LF-normalized (data only, nothing executed).

Usage: py -3.11 -I compare_tree.py --repo R --commit C --tree T [--prefix SUBDIR] [--only PATH ...]
Prints counts, mismatches and the hashes of --only paths.
"""

import argparse
import hashlib
import subprocess
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True)
ap.add_argument("--commit", required=True)
ap.add_argument("--tree", required=True)
ap.add_argument("--prefix", default="", help="repository subdirectory that --tree mirrors")
ap.add_argument("--only", nargs="*", default=None, help="tree-relative paths to compare (default: all files)")
args = ap.parse_args()
tree = Path(args.tree)


def lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def git_blob(path: str) -> bytes | None:
    proc = subprocess.run(["git", "-C", args.repo, "show", f"{args.commit}:{path}"], capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


rels = args.only if args.only is not None else sorted(
    p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
same = differ = missing = 0
for rel in rels:
    local = (tree / rel).read_bytes()
    repo_path = f"{args.prefix}/{rel}" if args.prefix else rel
    blob = git_blob(repo_path)
    if blob is None:
        missing += 1
        print(f"NOT-IN-GIT {rel}")
        continue
    a, b = hashlib.sha256(lf(local)).hexdigest(), hashlib.sha256(lf(blob)).hexdigest()
    if a == b:
        same += 1
        if args.only is not None:
            print(f"MATCH {rel} {a}")
    else:
        differ += 1
        print(f"DIFF {rel} tree={a} git={b}")
print(f"compared={len(rels)} identical={same} different={differ} not_in_git={missing} commit={args.commit}")
