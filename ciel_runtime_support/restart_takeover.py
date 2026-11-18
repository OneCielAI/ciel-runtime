"""Non-destructive worktree snapshots at a CLI restart, and the takeover note.

User requirement 2026-11-17: a restart command backs up and records the
worktrees being worked on, and the relaunched session takes the work over.
User decision: a non-destructive snapshot ref, never a commit on a branch.

This runs after the CLI child has exited and before the relaunch, so no agent
writes the files meanwhile. For every worktree of the launch folder's
repository (main checkout, linked worktrees, ``.claude/worktrees/*`` including
Work fork worktrees) that has changes, a snapshot commit is built with a
temporary index (``read-tree HEAD``, ``add -A``, ``write-tree``,
``commit-tree -p HEAD``) and stored at
``refs/ciel/restart/<request id>/<worktree slug>``. The real index, the working
files and every branch stay exactly as they were; tracked changes and new
untracked files are captured, ignored files are not.

The manifest (``restart-takeover.json`` in the router instance folder) lists
each worktree and where the session was actually working (the ``cwd`` of the
conversation ``--continue`` resumes); the restart notice carries a one-line
summary. Snapshot refs of the newest ``KEEP_RESTARTS`` restarts are kept.
Every failure is recorded and never blocks the restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

REF_PREFIX = "refs/ciel/restart/"
KEEP_RESTARTS = 10
MANIFEST = "restart-takeover.json"
GIT_TIMEOUT = 120
# A worktree whose untracked set is this large is recorded, not snapshotted:
# hashing it would stall the relaunch and grow the object store.
MAX_UNTRACKED_FILES = 5000
MAX_UNTRACKED_BYTES = 512 * 1024 * 1024
TRANSCRIPT_TAIL_BYTES = 262144
_IDENTITY = {"GIT_AUTHOR_NAME": "ciel-runtime", "GIT_AUTHOR_EMAIL": "ciel-runtime@localhost",
             "GIT_COMMITTER_NAME": "ciel-runtime", "GIT_COMMITTER_EMAIL": "ciel-runtime@localhost"}


class TakeoverError(RuntimeError):
    pass


def _trust(roots) -> list[str]:
    # Exact roots only (Stage 1, 2026-11-17): a sandbox account runs in a
    # repository another account owns; never a wildcard.
    arguments: list[str] = []
    for root in roots:
        value = str(root)
        if not value or "*" in value or not Path(value).is_absolute():
            continue
        # git prints worktree paths with "/" on Windows; both spellings name one root.
        for spelling in dict.fromkeys((value, value.replace("\\", "/"))):
            arguments += ["-c", "safe.directory=" + spelling]
    return arguments


def _find_root(folder: Path) -> Path | None:
    """The folder holding ``.git``, found on disk: discovering it with git would need trust first."""
    for candidate in (folder, *folder.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _git(cwd: Path, *args: str, trust=(), env: Mapping[str, str] | None = None) -> str:
    command = ["git", *_trust(trust), "-C", str(cwd), *args]
    merged = {**os.environ, **(env or {}), "GIT_TERMINAL_PROMPT": "0"}
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=GIT_TIMEOUT, env=merged)
    if done.returncode != 0:
        raise TakeoverError((done.stderr or done.stdout or "git failed").strip().splitlines()[-1][:300])
    return done.stdout


def worktrees(repo: Path, trust) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in _git(repo, "worktree", "list", "--porcelain", trust=trust).splitlines() + [""]:
        if not line:
            if current.get("path"):
                rows.append(current)
            current = {}
        elif line.startswith("worktree "):
            current = {"path": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].removeprefix("refs/heads/")
        elif line == "detached":
            current["branch"] = None
        elif line == "bare":
            current["bare"] = True
    return rows


def slug(path: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(path).name).strip("-.")[:40] or "root"
    return name + "-" + hashlib.sha256(path.replace("\\", "/").lower().encode()).hexdigest()[:8]


def _excludes(path: Path, nested: list[Path]) -> list[str]:
    """Pathspecs that leave out what is not this worktree's own work.

    ``nested`` holds other worktrees living inside this one (.claude/worktrees/*)
    and the CLIs' own state folders when they sit inside it: Celly runs with its
    home folder as the repository, so ``.claude`` (transcripts), ``.claude.json``
    and ``.codex`` are untracked, unignored files there (Celly 2026-11-17).
    """
    specs = ["--", "."]
    for inner in nested:
        try:
            relative = inner.resolve().relative_to(path.resolve())
        except ValueError:
            continue
        specs.append(":(exclude)" + relative.as_posix())
    return specs


def _status(path: Path, trust, nested: list[Path] = ()) -> tuple[int, int, int]:
    """(modified, untracked, untracked bytes) outside the excluded folders."""
    out = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all", *_excludes(path, list(nested)),
               trust=trust)
    entries = [entry for entry in out.split("\0") if entry]
    untracked_paths = [entry[3:] for entry in entries if entry.startswith("??")]
    # A rename record carries its source path as the next entry.
    modified = sum(1 for entry in entries if len(entry) > 3 and entry[:2] != "??" and entry[2] == " ")
    size = 0
    for relative in untracked_paths[:MAX_UNTRACKED_FILES + 1]:
        try:
            size += (path / relative).stat().st_size
        except OSError:
            continue
    return modified, len(untracked_paths), size


def snapshot_worktree(path: Path, head: str | None, ref: str, message: str, trust, nested: list[Path] = ()) -> str:
    """Store the worktree's current files at ``ref`` without touching its index or branch."""
    handle, index = tempfile.mkstemp(prefix="ciel-restart-index-")
    os.close(handle)
    os.unlink(index)  # git creates it; an empty file is not a valid index
    env = {"GIT_INDEX_FILE": index, **_IDENTITY}
    try:
        if head:
            _git(path, "read-tree", head, trust=trust, env=env)
        _git(path, "add", "-A", *_excludes(path, list(nested)), trust=trust, env=env)
        tree = _git(path, "write-tree", trust=trust, env=env).strip()
        parents = ["-p", head] if head else []
        commit = _git(path, "commit-tree", tree, *parents, "-m", message, trust=trust, env=env).strip()
        _git(path, "update-ref", ref, commit, trust=trust)
        return commit
    finally:
        try:
            os.unlink(index)
        except OSError:
            pass


def session_cwd(claude_config: Path, launch_folder: Path) -> str | None:
    """The cwd recorded last in the newest transcript of the launch folder's project."""
    key = re.sub(r"[^A-Za-z0-9._-]", "-", str(launch_folder).rstrip("\\/"))
    folder = Path(claude_config) / "projects" / key
    try:
        newest = max(folder.glob("*.jsonl"), key=lambda path: path.stat().st_mtime)
    except (OSError, ValueError):
        return None
    try:
        with newest.open("rb") as stream:
            stream.seek(max(0, newest.stat().st_size - TRANSCRIPT_TAIL_BYTES))
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("cwd"), str) and row["cwd"]:
            return row["cwd"]
    return None


def _prune(repo: Path, trust, keep: int) -> int:
    out = _git(repo, "for-each-ref", "--sort=-committerdate", "--format=%(refname)", REF_PREFIX, trust=trust)
    seen: list[str] = []
    removed = 0
    for ref in out.splitlines():
        request = ref[len(REF_PREFIX):].split("/", 1)[0]
        if request not in seen:
            seen.append(request)
        if seen.index(request) >= keep:
            _git(repo, "update-ref", "-d", ref, trust=trust)
            removed += 1
    return removed


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def snapshot_all(launch_folder: Path, request_id: str, *, claude_config: Path | None,
                 state_dir: Path, now: Callable[[], float] = time.time,
                 exclude: list[Path] | tuple[Path, ...] = ()) -> dict[str, Any]:
    launch = Path(launch_folder).resolve()
    manifest: dict[str, Any] = {"schema": "ciel-runtime.restart-takeover/v1", "request_id": request_id,
                                "at": now(), "launch_folder": str(launch), "worktrees": []}
    manifest["session_cwd"] = session_cwd(claude_config, launch) if claude_config else None
    repo = _find_root(launch)
    if repo is None:
        manifest["repository"] = None
        manifest["note"] = "launch folder is not inside a git repository"
        _write(state_dir, manifest)
        return manifest
    manifest["repository"] = str(repo)
    trust = [repo]
    try:
        rows = worktrees(repo, trust)
    except (OSError, subprocess.SubprocessError, TakeoverError) as error:
        manifest["error"] = type(error).__name__ + ": " + str(error)[:300]
        _write(state_dir, manifest)
        return manifest
    trust = trust + [Path(row["path"]) for row in rows]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(manifest["at"]))
    for row in rows:
        entry = {"path": row["path"], "branch": row.get("branch"), "head": row.get("head"),
                 "modified": 0, "untracked": 0, "snapshot_ref": None, "snapshot_commit": None}
        path = Path(row["path"])
        if row.get("bare") or not path.is_dir():
            entry["skipped"] = "bare" if row.get("bare") else "missing"
            manifest["worktrees"].append(entry)
            continue
        nested = [Path(other["path"]) for other in rows if other is not row and not _same(other["path"], row["path"])]
        nested += [Path(item) for item in exclude if item]
        try:
            entry["modified"], entry["untracked"], size = _status(path, trust, nested)
            if entry["untracked"] > MAX_UNTRACKED_FILES or size > MAX_UNTRACKED_BYTES:
                entry["skipped"] = f"too large to snapshot ({entry['untracked']} untracked files, {size} bytes)"
            elif entry["modified"] or entry["untracked"]:
                ref = REF_PREFIX + request_id + "/" + slug(row["path"])
                entry["snapshot_commit"] = snapshot_worktree(
                    path, row.get("head"), ref, f"ciel-runtime restart snapshot {stamp} ({row['path']})", trust,
                    nested)
                entry["snapshot_ref"] = ref
        except (OSError, subprocess.SubprocessError, TakeoverError) as error:
            entry["error"] = type(error).__name__ + ": " + str(error)[:300]
        manifest["worktrees"].append(entry)
    cwd = manifest["session_cwd"]
    if cwd:
        holding = [entry for entry in manifest["worktrees"]
                   if _same(cwd, entry["path"]) or os.path.normcase(os.path.normpath(cwd)).startswith(
                       os.path.normcase(os.path.normpath(entry["path"])) + os.sep)]
        # The deepest matching worktree is the one the session was working in.
        holding.sort(key=lambda entry: len(entry["path"]), reverse=True)
        manifest["session_worktree"] = holding[0]["path"] if holding else None
    try:
        manifest["pruned_refs"] = _prune(repo, trust, KEEP_RESTARTS)
    except (OSError, subprocess.SubprocessError, TakeoverError) as error:
        manifest["prune_error"] = str(error)[:200]
    _write(state_dir, manifest)
    return manifest


def _write(state_dir: Path, manifest: Mapping[str, Any]) -> None:
    target = Path(state_dir) / MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


NOTICE_NAMED_WORKTREES = 5


def _state(row: Mapping[str, Any]) -> str:
    """What happened to one worktree's uncommitted work, in the notice's words."""
    if row.get("snapshot_ref"):
        return "snapshot " + str(row["snapshot_ref"])
    if row.get("error"):
        return "snapshot failed"
    if row.get("skipped"):
        return "snapshot skipped: " + str(row["skipped"]).split(" (", 1)[0]
    return "no uncommitted changes"


def _detail(row: Mapping[str, Any]) -> str:
    return " (" + ", ".join((
        "branch " + str(row["branch"]) if row.get("branch") else "detached HEAD",
        "HEAD " + str(row.get("head") or "")[:10],
        _state(row),
    )) + ")"


def notice_suffix(manifest: Mapping[str, Any] | None) -> str:
    """One line appended to the restart notice (it is typed into the prompt).

    It names where the conversation was (the transcript cwd: the project
    folder, or the worktree EnterWorktree moved it to) and every worktree that
    still holds uncommitted work, so the resumed session can take each over.
    A Bash ``cd`` is not recorded in the transcript (E2E 2026-11-17), so the
    changed worktrees are listed by name rather than guessed from it.
    """
    if not manifest:
        return ""
    parts: list[str] = []
    rows = manifest.get("worktrees") or []
    where = manifest.get("session_worktree") or manifest.get("session_cwd")
    if where:
        row = next((entry for entry in rows if _same(str(where), entry["path"])), None)
        parts.append("재시작 전 작업 위치: " + str(manifest.get("session_cwd") or where) + (_detail(row) if row else "")
                     + " — 그 위치에서 이어서 작업")
    # Snapshotted, failed (its changes are unknown) or too large to snapshot.
    changed = [entry for entry in rows
               if (entry.get("snapshot_ref") or entry.get("error")
                   or str(entry.get("skipped") or "").startswith("too large"))
               and not (where and _same(str(where), entry["path"]))]
    if changed:
        named = "; ".join(str(entry["path"]) + _detail(entry) for entry in changed[:NOTICE_NAMED_WORKTREES])
        more = len(changed) - NOTICE_NAMED_WORKTREES
        parts.append("변경이 남은 워크트리: " + named + (f" 외 {more}곳" if more > 0 else "")
                     + " — 각 워크트리에서 이어서 작업")
    if parts:
        parts.append("목록: " + MANIFEST)
    return " · ".join(parts)


__all__ = ["KEEP_RESTARTS", "MANIFEST", "REF_PREFIX", "TakeoverError", "notice_suffix", "session_cwd",
           "slug", "snapshot_all", "snapshot_worktree", "worktrees"]
