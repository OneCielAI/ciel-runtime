"""Restore scratch/snapshot files that an accidental `ruff check --select F401 --fix .` rewrote.

Content-addressed: an original candidate is accepted only when running the exact same
ruff fix over it reproduces the current (rewritten) bytes. Nothing is written in
--dry-run. Candidates come only from trees the accidental run never touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

WORKTREE = Path(r"C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel")
POOL_ROOTS = [
    Path(r"C:\Users\djlov\AppData\Local\Temp"),
    Path(r"C:\Users\djlov\ciel-runtime"),
    Path(r"C:\Users\djlov\.claude"),
]
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
KEEP_FIXED = {  # product files whose F401 fix is intended
    "ciel_runtime_support/work_parallel_native.py", "ciel_runtime_support/work_parallel_stranded.py",
    "tests/test_ctl_launch_lease.py", "tests/test_side_retry_pins.py",
    "tests/test_work_operator_receipt_bounds.py", "tests/test_work_parallel_gate_presence.py",
    "tests/test_work_parallel_owner_offline.py", "tests/test_work_parallel_review_pin.py",
    "tests/test_work_state_unavailable.py",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("touched")
    parser.add_argument("report")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    touched = []
    for line in Path(args.touched).read_text(encoding="utf-8").splitlines():
        rel = line.strip().removeprefix("./")
        if rel and rel not in KEEP_FIXED:
            touched.append(WORKTREE / rel)
    names = {path.name for path in touched}
    current = {path: path.read_bytes() for path in touched if path.is_file()}
    wanted = {sha(data) for data in current.values()}

    # 1. collect untouched candidates by basename
    candidates: dict[str, bytes] = {}
    worktree_norm = os.path.normcase(str(WORKTREE))
    for root in POOL_ROOTS:
        for dirpath, dirnames, filenames in os.walk(root):
            norm = os.path.normcase(dirpath)
            if norm == worktree_norm or norm.startswith(worktree_norm + os.sep):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for filename in filenames:
                if filename in names:
                    try:
                        data = (Path(dirpath) / filename).read_bytes()
                    except OSError:
                        continue
                    candidates.setdefault(sha(data), data)
    print("candidate contents (untouched trees):", len(candidates), flush=True)
    # (a) worktree files the accidental run did not rewrite (docs/journal, other copies)
    touched_norm = {os.path.normcase(str(path)) for path in touched}
    touched_norm |= {os.path.normcase(str(WORKTREE / rel)) for rel in KEEP_FIXED}
    for dirpath, dirnames, filenames in os.walk(WORKTREE):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            path = Path(dirpath) / filename
            if filename in names and os.path.normcase(str(path)) not in touched_norm:
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                candidates.setdefault(sha(data), data)
    print("candidate contents (+worktree untouched):", len(candidates), flush=True)
    # (b) every blob in the repository object store whose path has a wanted basename
    listing = subprocess.run(["git", "rev-list", "--all", "--objects"], cwd=WORKTREE,
                             capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    blobs = {line.split(" ", 1)[0] for line in listing.splitlines()
             if " " in line and line.rsplit("/", 1)[-1] in names}
    for blob in blobs:
        data = subprocess.run(["git", "cat-file", "blob", blob], cwd=WORKTREE, capture_output=True).stdout
        candidates.setdefault(sha(data), data)
    print("candidate contents (+git blobs):", len(candidates), flush=True)
    # (c) whole-file Write tool inputs recorded in Claude transcripts
    for transcript in Path(r"C:\Users\djlov\.claude\projects").rglob("*.jsonl"):
        try:
            handle = transcript.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                if '"Write"' not in line or not any(name in line for name in names):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                content = (row.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Write":
                        continue
                    data_in = block.get("input") or {}
                    file_path = str(data_in.get("file_path") or "")
                    text = data_in.get("content")
                    if isinstance(text, str) and Path(file_path.replace("\\", "/")).name in names:
                        for variant in (text, text.replace("\r\n", "\n").replace("\n", "\r\n")):
                            data = variant.encode("utf-8")
                            candidates.setdefault(sha(data), data)
    print("candidate contents (+transcript writes):", len(candidates), flush=True)

    # 2. apply the same fix to every candidate in an isolated tree
    ruffed_to_original: dict[str, set[str]] = defaultdict(set)
    with tempfile.TemporaryDirectory(prefix="ruff-restore-") as tmp:
        tmp_root = Path(tmp)
        shutil.copy2(WORKTREE / "ruff.toml", tmp_root / "ruff.toml")
        index = {}
        for number, (digest, data) in enumerate(candidates.items()):
            target = tmp_root / f"c{number:06d}" / "mod.py"
            target.parent.mkdir()
            target.write_bytes(data)
            index[target] = digest
        subprocess.run(["uvx", "ruff@0.15.22", "check", "--select", "F401", "--fix", "--no-cache", "."],
                       cwd=tmp_root, capture_output=True, text=True)
        for target, digest in index.items():
            fixed = target.read_bytes()
            if fixed != candidates[digest]:
                ruffed_to_original[sha(fixed)].add(digest)

    # 3. restore
    report = {"restored": [], "ambiguous": [], "missing": []}
    for path, data in current.items():
        origins = ruffed_to_original.get(sha(data), set())
        rel = str(path.relative_to(WORKTREE)).replace("\\", "/")
        if len(origins) == 1:
            original = candidates[next(iter(origins))]
            report["restored"].append({"path": rel, "sha256": sha(original)})
            if args.apply:
                path.write_bytes(original)
        elif origins:
            report["ambiguous"].append({"path": rel, "origins": sorted(origins)})
        else:
            report["missing"].append(rel)
    Path(args.report).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print({key: len(value) for key, value in report.items()}, "wanted", len(wanted))
    return 0


if __name__ == "__main__":
    sys.exit(main())
