"""Derive owned_main_review_probe_installed.py from v2 (345a7ebe). v2 is only read."""
import hashlib
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
raw = src.read_bytes()
assert hashlib.sha256(raw).hexdigest().startswith("345a7ebe"), "v2 is not 345a7ebe"
s = raw.decode("utf-8")


def rep(a, b):
    global s
    assert s.count(a) == 1, (s.count(a), a[:120])
    s = s.replace(a, b)


# Docstring: what the snapshot is taken from.
rep('''Creates only owned state: an immutable snapshot of the frozen source, a temp''',
    '''Installed-build variant: the product source is an INSTALLED npm package root
given as the only argument (never the worktree). It is copied into a per-run
owned snapshot under .local and verified file by file against the installed
manifest; the installed path itself is never an owned-kill marker.

Creates only owned state: an immutable snapshot of the frozen source, a temp''')

# 1. Installed source from argv; the worktree keeps only harness tools and .local.
rep('''SOURCE = Path(__file__).resolve().parents[8]
TOOLS = SOURCE / "docs/journal/2026/11/03/features/work-parallel/evidence/menu-ui"''',
'''# Worktree: harness tools (console driver, screenshot helper) and owned .local only.
SOURCE = Path(__file__).resolve().parents[8]
if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
    raise SystemExit("usage: owned_main_review_probe_installed.py <installed ciel-runtime package root>")
# Product source: the installed package root. It is read, never written, and never
# used as an owned marker (a live session may later run from it).
INSTALLED = Path(sys.argv[1]).resolve()
if INSTALLED == SOURCE or SOURCE in INSTALLED.parents:
    raise SystemExit("installed path must not be the worktree or inside it: " + str(INSTALLED))
TOOLS = SOURCE / "docs/journal/2026/11/03/features/work-parallel/evidence/menu-ui"''')

# 3. Committed LF blob hashes (aafbe1c) named by the team lead.
start = s.index("# Current integrated tree named by the team lead")
end = s.index("}\n", s.index("KEY_HASHES = {", start)) + 2
s = s[:start] + '''# Committed LF blob hashes of the published build (commit 34966b6 code, aafbe1c
# docs), named by the team lead for the installed npm package root.
KEY_HASHES = {
    "ciel_runtime_support/work_parallel_finalization.py":
        "79a573b67ac69a2649df9960841fbd026876ad7684d2584d6c9d63ba8000664d",
    "ciel_runtime_support/claude_mods/ciel-work/hooks/register.js":
        "ea7768f5348e61883cfe6833420abf0e98e1e001576c5868fb1b4005d526271a",
    "ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js":
        "dcf8fbbcf33cfeadd9e87890f2c62b790400e4aaca6105138ed55db03f7cac32",
    "ciel_runtime_support/claude_mods/ciel-work/types/index.d.ts":
        "4110db606785f32159b68bf2e77fdfb0e35ee46533853caab5917e9ce048f314",
    "ciel_runtime_support/work_parallel_mcp.py":
        "ccf397c62c084241253723db5a9bbba09240bec09e2dba37ca14de83ebb7c03f",
}
''' + s[end:]

# Driver copy keeps the executed runner under its own name.
rep('''DRIVER_SOURCES = {"owned_main_review_probe_v2.py": Path(__file__).resolve(),''',
    '''DRIVER_SOURCES = {Path(__file__).name: Path(__file__).resolve(),''')

# 2. tests is dropped only when the installed tree has none; recorded in result.json.
rep('''SNAP_DIRS = ("ciel_runtime_support", "scripts", "tests", "npm-bin")''',
'''SNAP_DIRS_FULL = ("ciel_runtime_support", "scripts", "tests", "npm-bin")
# The npm package root has no tests/ dir. Only tests may be dropped, and only when
# the installed tree lacks it; every other dir must exist or copytree fails.
TESTS_DROPPED = not (INSTALLED / "tests").is_dir()
SNAP_DIRS = tuple(name for name in SNAP_DIRS_FULL if not (TESTS_DROPPED and name == "tests"))''')

rep('''def build_snapshot() -> dict:
    """Per-run byte copy of the current worktree, verified file by file."""
    if SNAP.exists():
        raise RuntimeError("snapshot already exists")
    source_before = tree_manifest(SOURCE)
    key_source = {rel: source_before.get(rel, "") for rel in KEY_HASHES}
    if key_source != KEY_HASHES:
        raise RuntimeError(f"current worktree key hashes differ: {key_source}")
    SNAP.mkdir()
    ignored = shutil.ignore_patterns(*IGNORED, "*.pyc")
    for name in SNAP_DIRS:
        shutil.copytree(SOURCE / name, SNAP / name, ignore=ignored)
    for path in SOURCE.iterdir():
        if path.is_file() and path.suffix != ".pyc":
            shutil.copy2(path, SNAP / path.name)
    snap = tree_manifest(SNAP)
    source_after = tree_manifest(SOURCE)
    if source_after != source_before:
        changed = sorted(k for k in set(source_before) | set(source_after)
                         if source_before.get(k) != source_after.get(k))
        raise RuntimeError(f"worktree changed while snapshotting: {changed[:20]}")
    mismatch = sorted(k for k in set(snap) | set(source_before) if snap.get(k) != source_before.get(k))
    if mismatch:
        raise RuntimeError(f"snapshot differs from worktree: {mismatch[:20]}")''',
'''def build_snapshot() -> dict:
    """Per-run byte copy of the INSTALLED package root, verified file by file
    against the installed manifest. The installed tree is only read."""
    if SNAP.exists():
        raise RuntimeError("snapshot already exists")
    source_before = tree_manifest(INSTALLED)
    key_source = {rel: source_before.get(rel, "") for rel in KEY_HASHES}
    if key_source != KEY_HASHES:
        raise RuntimeError(f"installed tree key hashes differ: {key_source}")
    SNAP.mkdir()
    ignored = shutil.ignore_patterns(*IGNORED, "*.pyc")
    for name in SNAP_DIRS:
        shutil.copytree(INSTALLED / name, SNAP / name, ignore=ignored)
    for path in INSTALLED.iterdir():
        if path.is_file() and path.suffix != ".pyc":
            shutil.copy2(path, SNAP / path.name)
    snap = tree_manifest(SNAP)
    source_after = tree_manifest(INSTALLED)
    if source_after != source_before:
        changed = sorted(k for k in set(source_before) | set(source_after)
                         if source_before.get(k) != source_after.get(k))
        raise RuntimeError(f"installed tree changed while snapshotting: {changed[:20]}")
    mismatch = sorted(k for k in set(snap) | set(source_before) if snap.get(k) != source_before.get(k))
    if mismatch:
        raise RuntimeError(f"snapshot differs from installed tree: {mismatch[:20]}")
    (OUT / "installed-manifest.json").write_text(json.dumps(source_before, indent=1, sort_keys=True),
                                                 encoding="utf-8")''')
rep('''    return {"files": len(snap), "manifest_sha256": digest, "key_hashes": {k: snap[k] for k in KEY_HASHES},
            "snapshot": str(SNAP), "mismatch": mismatch}''',
'''    installed_digest = hashlib.sha256(json.dumps(source_before, sort_keys=True).encode()).hexdigest()
    return {"files": len(snap), "manifest_sha256": digest, "key_hashes": {k: snap[k] for k in KEY_HASHES},
            "snapshot": str(SNAP), "mismatch": mismatch, "installed": str(INSTALLED),
            "installed_files": len(source_before), "installed_manifest_sha256": installed_digest,
            "snap_dirs": list(SNAP_DIRS), "tests_dropped": TESTS_DROPPED}''')

# 1b. Owned-kill matching never matches the shared installed path.
rep('''def register_discovered_owned(row: dict, label: str, markers: list[str]) -> None:
    command = str(row.get("CommandLine") or "")
    if not any(marker in command for marker in markers):
        raise RuntimeError(f"{label}: process command line has no owned marker")''',
'''def _path_text(value) -> str:
    return str(value).replace("/", "\\\\").lower()


INSTALLED_TEXT = _path_text(INSTALLED)


def references_installed(command) -> bool:
    """True when a command line names the shared installed package path (any
    slash form, any case). Such a process may be a live session: never owned."""
    return bool(command) and INSTALLED_TEXT in _path_text(command)


def register_discovered_owned(row: dict, label: str, markers: list[str]) -> None:
    command = str(row.get("CommandLine") or "")
    if references_installed(command):
        raise RuntimeError(f"{label}: command line references the shared installed path; never owned")
    if not any(marker in command for marker in markers):
        raise RuntimeError(f"{label}: process command line has no owned marker")''')
rep('''    for row in roots:
        pid = int(row["ProcessId"])
        try:
            register_discovered_owned(row, "final-owned-command", markers)''',
'''    for row in roots:
        pid = int(row["ProcessId"])
        if references_installed(row.get("CommandLine")):
            diag({"final_kill_skipped_installed": identity(row)})
            continue
        try:
            register_discovered_owned(row, "final-owned-command", markers)''')
rep('''    if (pinned.pid, pinned.created) in PROTECTED_IDENTITIES:
        record.update(refused=True, reason="identity appears in protected snapshot")
        write_kill(record)
        raise RuntimeError(f"refused protected identity {(pinned.pid, pinned.created)}")''',
'''    if (pinned.pid, pinned.created) in PROTECTED_IDENTITIES:
        record.update(refused=True, reason="identity appears in protected snapshot")
        write_kill(record)
        raise RuntimeError(f"refused protected identity {(pinned.pid, pinned.created)}")
    if references_installed(row.get("CommandLine")):
        record.update(refused=True, reason="command line references the shared installed path")
        write_kill(record)
        raise RuntimeError(f"refused installed-path identity {(pinned.pid, pinned.created)}")''')
rep('''                if key in protected:
                    refused_pins.append(key)
                    diag({"kill_refused_protected": label, "identity": key,
                          "owned_chain": chain(owned_now, key),
                          "protected_chain": chain(protected_now, key)
                                             or chain(live_before, key)})
                    continue''',
'''                if key in protected:
                    refused_pins.append(key)
                    diag({"kill_refused_protected": label, "identity": key,
                          "owned_chain": chain(owned_now, key),
                          "protected_chain": chain(protected_now, key)
                                             or chain(live_before, key)})
                    continue
                if references_installed(row.get("CommandLine")):
                    refused_pins.append(key)
                    diag({"kill_refused_installed": label, "identity": key,
                          "owned_chain": chain(owned_now, key)})
                    continue''')
rep('''        if refused_pins:
            raise RuntimeError(f"{label}: protected identity inside owned tree: {refused_pins}")''',
'''        if refused_pins:
            raise RuntimeError(f"{label}: protected or installed-path identity inside owned tree: {refused_pins}")''')

# Owned markers (BASE, SNAP, scratch) and the installed path must not overlap.
rep('''scratch = Path(tempfile.mkdtemp(prefix="ciel-owned-main-review-"))''',
'''scratch = Path(tempfile.mkdtemp(prefix="ciel-owned-main-review-"))
for _marker in (BASE, SNAP, scratch):
    if _path_text(_marker) in INSTALLED_TEXT or INSTALLED_TEXT in _path_text(_marker):
        raise RuntimeError(f"owned marker {_marker} overlaps the installed path {INSTALLED}")''')

rep('''result = {"out": str(OUT), "base": str(BASE), "driver_hashes": DRIVER_HASHES, "snapshot": snapshot_proof, "session": SESSION,''',
'''result = {"out": str(OUT), "base": str(BASE), "driver_hashes": DRIVER_HASHES, "snapshot": snapshot_proof, "session": SESSION,
          "installed_source": {"path": str(INSTALLED), "snap_dirs": list(SNAP_DIRS), "tests_dropped": TESTS_DROPPED,
                               "installed_files": snapshot_proof["installed_files"],
                               "installed_manifest_sha256": snapshot_proof["installed_manifest_sha256"]},''')

dst.write_bytes(s.encode("utf-8"))
print("written", dst, hashlib.sha256(s.encode("utf-8")).hexdigest())
