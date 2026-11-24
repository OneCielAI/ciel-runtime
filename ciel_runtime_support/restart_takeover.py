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
# Stop scanning additional worktrees once this total wall-clock budget elapses
# so a repository with hundreds of old worktrees cannot stall a CLI restart
# past the replay window (Andy 2026-11-24: 1,219 worktrees stalled 5.7 min).
MAX_TOTAL_SECONDS = 30.0
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
    # Each subprocess opens one exact worktree (plus its primary repo root if
    # distinct). Passing all registered roots to every call exceeds the Windows
    # process command-line limit when hundreds of worktrees exist.
    trust_list = list(trust)
    exact = [root for root in trust_list if _same(str(root), str(cwd))]
    if trust_list and not any(_same(str(trust_list[0]), str(item)) for item in exact):
        exact.insert(0, trust_list[0])
    command = ["git", *_trust(exact), "-C", str(cwd), *args]
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


def _resolve_once(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _excludes(path: Path, nested: list[Path], *, resolved_path: Path | None = None,
              resolved_nested: list[Path] | None = None) -> list[str]:
    """Pathspecs that leave out what is not this worktree's own work.

    ``nested`` holds other worktrees living inside this one (.claude/worktrees/*)
    and the CLIs' own state folders when they sit inside it: Celly runs with its
    home folder as the repository, so ``.claude`` (transcripts), ``.claude.json``
    and ``.codex`` are untracked, unignored files there (Celly 2026-11-17).
    """
    base = _resolve_once(path) if resolved_path is None else resolved_path
    base_norm = os.path.normcase(os.path.normpath(str(base)))
    prefix = base_norm if base_norm.endswith(os.sep) else base_norm + os.sep
    candidates = [_resolve_once(inner) for inner in nested] if resolved_nested is None else resolved_nested
    rels: list[str] = []
    for inner in candidates:
        inner_norm = os.path.normcase(os.path.normpath(str(inner)))
        if not inner_norm.startswith(prefix):
            continue
        rel = str(inner)[len(str(base)):].lstrip("\\/").replace("\\", "/")
        if rel:
            rels.append(rel)
    # Drop redundant descendants when an ancestor directory is already excluded.
    rels.sort(key=lambda item: (item.count("/"), item.lower()))
    kept: list[str] = []
    kept_prefixes: list[str] = []
    for rel in rels:
        key = rel.lower() if os.name == "nt" else rel
        if any(key == p[:-1] or key.startswith(p) for p in kept_prefixes):
            continue
        kept.append(rel)
        kept_prefixes.append(key + "/")
    return ["--", ".", *(":(exclude)" + rel for rel in kept)]


def _status(path: Path, trust, nested: list[Path] = (), *, explicit_excludes: list[Path] | tuple[Path, ...] = (),
            resolved_path: Path | None = None, resolved_nested: list[Path] | None = None,
            resolved_explicit: list[Path] | None = None) -> tuple[int, int, int]:
    """(modified, untracked, untracked bytes) outside the excluded folders."""
    specs = _excludes(path, list(nested), resolved_path=resolved_path, resolved_nested=resolved_nested)
    args = ("status", "--porcelain=v1", "-z", "--untracked-files=all")
    if os.name == "nt" and len(subprocess.list2cmdline(specs)) > 24_000:
        # status has no --pathspec-from-file. Keep explicit state excludes on
        # the CLI so git never walks ~/.claude, disable rename pairing so each
        # record carries one path, and filter remaining nested paths in Python.
        cli_specs = _excludes(path, list(explicit_excludes), resolved_path=resolved_path,
                              resolved_nested=resolved_explicit) if (explicit_excludes or resolved_explicit) else []
        if len(cli_specs) > 2 and len(subprocess.list2cmdline(cli_specs)) <= 24_000:
            out = _git(path, *args, "--no-renames", *cli_specs, trust=trust)
        else:
            out = _git(path, *args, "--no-renames", trust=trust)
        excluded = {os.path.normcase(os.path.normpath(spec.removeprefix(":(exclude)")))
                    for spec in specs[2:]}
        entries = [entry for entry in out.split("\0") if entry and not any(
            os.path.normcase(str(candidate)) in excluded
            for candidate in (Path(entry[3:]), *Path(entry[3:]).parents))]
    else:
        out = _git(path, *args, *specs, trust=trust)
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


def snapshot_worktree(path: Path, head: str | None, ref: str, message: str, trust, nested: list[Path] = (), *,
                      resolved_path: Path | None = None, resolved_nested: list[Path] | None = None) -> str:
    """Store the worktree's current files at ``ref`` without touching its index or branch."""
    handle, index = tempfile.mkstemp(prefix="ciel-restart-index-")
    os.close(handle)
    os.unlink(index)  # git creates it; an empty file is not a valid index
    env = {"GIT_INDEX_FILE": index, **_IDENTITY}
    try:
        if head:
            _git(path, "read-tree", head, trust=trust, env=env)
        specs = _excludes(path, list(nested), resolved_path=resolved_path, resolved_nested=resolved_nested)
        if os.name == "nt" and len(subprocess.list2cmdline(specs)) > 24_000:
            with tempfile.TemporaryDirectory(prefix="ciel-restart-pathspec-") as directory:
                pathspec = Path(directory) / "paths"
                pathspec.write_bytes(("\0".join(specs[1:]) + "\0").encode("utf-8"))
                _git(path, "add", "-A", f"--pathspec-from-file={pathspec}", "--pathspec-file-nul",
                     trust=trust, env=env)
        else:
            _git(path, "add", "-A", *specs, trust=trust, env=env)
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
    primary = Path(rows[0]["path"]) if rows else repo
    resolved_rows = [_resolve_once(Path(row["path"])) for row in rows]
    explicit_list = [Path(item) for item in exclude if item]
    resolved_explicit = [_resolve_once(item) for item in explicit_list]
    cwd = manifest["session_cwd"]
    session_wt_index: int | None = None
    if cwd:
        cwd_norm = os.path.normcase(os.path.normpath(cwd))
        holding = [
            (len(row["path"]), index, row["path"])
            for index, row in enumerate(rows)
            if _same(cwd, row["path"])
            or cwd_norm.startswith(os.path.normcase(os.path.normpath(row["path"])) + os.sep)
        ]
        if holding:
            holding.sort(reverse=True)
            session_wt_index = holding[0][1]
            manifest["session_worktree"] = holding[0][2]
        else:
            manifest["session_worktree"] = None

    def _mtime(index: int) -> float:
        wt_path = Path(rows[index]["path"])
        for probe in (wt_path / ".git", wt_path):
            try:
                return probe.stat().st_mtime
            except OSError:
                continue
        return 0.0

    # Snapshot the session's own worktree first, then the main checkout, then
    # the most recently touched worktrees, preserving manifest row order.
    ordered_indices: list[int] = []
    if session_wt_index is not None:
        ordered_indices.append(session_wt_index)
    if rows and 0 not in ordered_indices:
        ordered_indices.append(0)
    rest = [index for index in range(len(rows)) if index not in ordered_indices]
    rest.sort(key=_mtime, reverse=True)
    ordered_indices.extend(rest)

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(manifest["at"]))
    started = time.monotonic()
    entries: list[dict[str, Any] | None] = [None] * len(rows)
    for position, index in enumerate(ordered_indices):
        row = rows[index]
        entry = {"path": row["path"], "branch": row.get("branch"), "head": row.get("head"),
                 "modified": 0, "untracked": 0, "snapshot_ref": None, "snapshot_commit": None}
        path = Path(row["path"])
        if row.get("bare") or not path.is_dir():
            entry["skipped"] = "bare" if row.get("bare") else "missing"
            entries[index] = entry
            continue
        if position > 0 and MAX_TOTAL_SECONDS > 0 and time.monotonic() - started >= MAX_TOTAL_SECONDS:
            entry["skipped"] = f"time budget reached ({int(MAX_TOTAL_SECONDS)}s)"
            entries[index] = entry
            continue
        wt_trust = [primary, path] if not _same(str(primary), str(path)) else [path]
        if not any(_same(str(repo), str(item)) for item in wt_trust):
            wt_trust.insert(0, repo)
        resolved_others = [resolved_rows[other_i] for other_i, other in enumerate(rows)
                           if other_i != index and not _same(other["path"], row["path"])]
        resolved_all_nested = resolved_others + resolved_explicit
        try:
            entry["modified"], entry["untracked"], size = _status(
                path, wt_trust, explicit_excludes=explicit_list,
                resolved_path=resolved_rows[index], resolved_nested=resolved_all_nested,
                resolved_explicit=resolved_explicit)
            if entry["untracked"] > MAX_UNTRACKED_FILES or size > MAX_UNTRACKED_BYTES:
                entry["skipped"] = f"too large to snapshot ({entry['untracked']} untracked files, {size} bytes)"
            elif entry["modified"] or entry["untracked"]:
                ref = REF_PREFIX + request_id + "/" + slug(row["path"])
                entry["snapshot_commit"] = snapshot_worktree(
                    path, row.get("head"), ref, f"ciel-runtime restart snapshot {stamp} ({row['path']})", wt_trust,
                    resolved_path=resolved_rows[index], resolved_nested=resolved_all_nested)
                entry["snapshot_ref"] = ref
        except (OSError, subprocess.SubprocessError, TakeoverError) as error:
            entry["error"] = type(error).__name__ + ": " + str(error)[:300]
        entries[index] = entry
    manifest["worktrees"] = [entry for entry in entries if entry is not None]
    try:
        prune_trust = [repo, primary] if not _same(str(repo), str(primary)) else [repo]
        manifest["pruned_refs"] = _prune(repo, prune_trust, KEEP_RESTARTS)
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
