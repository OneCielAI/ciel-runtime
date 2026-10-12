"""Collect one workspace's agent session into a snapshot while the agent runs.

Roots are named so a restore can place them elsewhere:
``cwd`` (the working folder), ``claude`` (Claude config dir), ``home``
(for ``~/.claude.json``), ``codex`` (CODEX_HOME), ``ciel`` (Ciel config dir)
and ``ciel_ws`` (this workspace's Ciel state dir).

Consistency without stopping the agent: append-only ``.jsonl`` files are read
up to their size at that moment and cut back to the last complete line; SQLite
databases are copied through the ``sqlite3`` backup API; everything else Ciel
and the CLIs write is replaced atomically, so a plain read sees one version.

Speed: a file whose size, modification time and file id are what the newest
snapshot on every target recorded is not read again; its chunks are named again.
Files of a folder tree are read on several threads (opening a file the first time
cost about 10 ms on Windows, 1 ms with eight opens at once; measured 2026-10-11),
and their entries still come out in the order the tree is walked.
"""

from __future__ import annotations

import fnmatch
import getpass
import json
import os
import platform
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from stat import S_ISREG
from typing import Any, Callable, Iterable, Iterator, Mapping

from ciel_runtime_support.session_backup_secrets import SecretBundle, split_secret_fields
from ciel_runtime_support.session_backup_store import CHUNK_SIZE, FORMAT_VERSION, ChunkWriter
from ciel_runtime_support.workspace_router_selection import workspace_digest, workspace_identity

# Files are read in whole chunks, so most blocks become a chunk without being copied again.
READ_BLOCK = CHUNK_SIZE
DEFAULT_MAX_FILE_BYTES = 1024 * 1024 * 1024
# Files of a tree queued ahead of the one whose entry is written next (a queued file holds no data).
READ_AHEAD = 256
# A file modified this close to the start of the snapshot that recorded it is read again next time.
REUSE_MARGIN_NS = 2_000_000_000
DEFAULT_CWD_EXCLUDES = (
    "node_modules", ".npm-global", ".npm", ".cache", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "AppData", "Temp", "$Recycle.Bin",
    "*.lock", ".part-*",
)
CLAUDE_SKIP_DIRS = {
    "projects", "sessions", "shell-snapshots", "statsig", "cache", "debug", "ide", "telemetry",
    "paste-cache", "image-cache", "todos", "file-history", "downloads", "logs", "session-env",
}
CLAUDE_PLUGIN_FILES = ("installed_plugins.json", "known_marketplaces.json", "config.json")
CODEX_KEEP_DIRS = ("prompts", "skills", "rules", "memories", "agents")
CODEX_SECRET_FILES = {"auth.json"}
CIEL_TOP_FILES = ("config.json", "launch-state.json", "model-registry.json", "log-level")
CIEL_SECRET_SUFFIXES = (".vault.json", ".vault.key", ".pepper")
CIEL_GLOBAL_SECRET_FILES = ("colab-worker-credentials.vault.json", "colab-worker-credentials.vault.key")
JSON_SPLIT_NAMES = {"config.json", ".claude.json"}


def plain_path(value: object) -> str:
    r"""A Windows extended-length path (``\\?\C:\...``, as Codex stores cwd) in its ordinary form."""

    text = str(value or "")
    if text.startswith(("\\\\?\\UNC\\", "//?/UNC/")):
        return "\\\\" + text[8:]
    if text.startswith(("\\\\?\\", "//?/")):
        return text[4:]
    return text


def same_path(a: object, b: object) -> bool:
    return workspace_identity(plain_path(a)) == workspace_identity(plain_path(b))


def claude_project_key(cwd: str) -> str:
    """Claude Code's projects/<key> folder name for a cwd."""

    return re.sub(r"[^A-Za-z0-9._-]", "-", str(cwd).rstrip("\\/"))


@dataclass(frozen=True)
class CollectRoots:
    cwd: Path
    claude: Path
    home: Path
    codex: Path
    ciel: Path
    ciel_ws: Path
    # Git worktrees outside the work folder, backed up as roots "worktree-1", "worktree-2", ...
    worktrees: tuple[Path, ...] = ()

    def extra(self) -> dict[str, Path]:
        return {f"worktree-{index}": Path(path) for index, path in enumerate(self.worktrees, start=1)}

    def path(self, name: str) -> Path:
        extra = self.extra()
        return extra[name] if name in extra else Path(getattr(self, name))

    def as_dict(self) -> dict[str, str]:
        fixed = {name: str(getattr(self, name)) for name in ("cwd", "claude", "home", "codex", "ciel", "ciel_ws")}
        return {**fixed, **{name: str(path) for name, path in self.extra().items()}}


WORKTREE_MODES = ("off", "inside", "all")


def is_linked_worktree(folder: Path) -> bool:
    """A git linked worktree: ``.git`` is a file pointing into <repo>/.git/worktrees/ (submodules point into modules/)."""

    marker = Path(folder) / ".git"
    try:
        if not marker.is_file():
            return False
        text = marker.read_text(encoding="utf-8", errors="replace")[:1024]
    except OSError:
        return False
    return text.startswith("gitdir:") and "/worktrees/" in text.replace("\\", "/")


def git_worktrees(cwd: Path, runner: Callable[..., Any] = subprocess.run) -> list[Path]:
    """Linked worktrees of the repository at ``cwd`` (git worktree list), main worktree left out."""

    try:
        result = runner(["git", "-C", str(cwd), "worktree", "list", "--porcelain"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if getattr(result, "returncode", 1) != 0:
        return []
    paths = [Path(line[len("worktree "):].strip()) for line in str(result.stdout).splitlines() if line.startswith("worktree ")]
    main = _norm(Path(cwd))
    return [path for path in paths if _norm(path) != main]


def outside_worktrees(cwd: Path, runner: Callable[..., Any] = subprocess.run) -> list[Path]:
    base = _norm(Path(cwd)).rstrip("\\/") + os.sep
    return [path for path in git_worktrees(cwd, runner) if not (_norm(path) + os.sep).startswith(base) and path.is_dir()]


def default_roots(cwd: Path, *, environ: Mapping[str, str], home: Path, asset_home: Path, config_dir: Path) -> CollectRoots:
    claude = environ.get("CLAUDE_CONFIG_DIR") or str(home / ".claude")
    codex = environ.get("CODEX_HOME") or str(asset_home / ".codex")
    return CollectRoots(
        cwd=Path(cwd),
        claude=Path(claude),
        home=Path(home),
        codex=Path(codex),
        ciel=Path(config_dir),
        ciel_ws=Path(config_dir) / "workspaces" / workspace_digest(cwd),
    )


def workspace_label(cwd: Path, host: str, user: str) -> str:
    """Folder name for this workspace's snapshots on a target."""

    def clean(text: str, empty: str = "x") -> str:
        return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:40] or empty

    return f"{clean(host)}--{clean(user)}--{clean(Path(cwd).name, 'root')}-{workspace_digest(cwd)}"


@dataclass
class CollectOptions:
    include_files: bool = True
    session_ids: tuple[str, ...] = ()
    excludes: tuple[str, ...] = DEFAULT_CWD_EXCLUDES
    extra_excludes: tuple[str, ...] = ()
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    label: str = ""
    trigger: str = "manual"
    # Files never collected wherever they are (the backup key file).
    skip_paths: tuple[str, ...] = ()
    # First-level folders of the work folder left out (chosen in the menu, this depth only).
    skip_top: tuple[str, ...] = ()
    # "off": git linked worktrees are left out; "inside": those in the work folder are kept;
    # "all": also the ones elsewhere (CollectRoots.worktrees).
    worktrees: str = "off"
    # Called about once a second with {"files", "read", "uploaded", "current"}.
    progress: Callable[[dict[str, Any]], None] | None = None
    # (root, path) -> entry of files the newest snapshot holds on every target (previous_files);
    # empty reads every file.
    previous: Mapping[tuple[str, str], Mapping[str, Any]] = field(default_factory=dict)


@dataclass
class Collection:
    entries: list[dict[str, Any]] = field(default_factory=list)
    secrets: SecretBundle = field(default_factory=SecretBundle)
    skipped: list[dict[str, str]] = field(default_factory=list)
    sessions: dict[str, Any] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    started: float = 0.0


@dataclass
class _FileJob:
    root: str
    path: Path
    kind: str  # "file" / "jsonl" read on a reader thread, "reused", or "inline" (secrets, JSON, SQLite)
    stat: os.stat_result
    future: Future | None = None
    previous: Mapping[str, Any] | None = None

    def ready(self) -> bool:
        return self.future is None or self.future.done()


def file_signature(stat: os.stat_result) -> str:
    """Size, modification time and file id (plus the inode change time off Windows).

    A file replaced by a rename (how editors and Ciel save) gets a new id."""

    parts = [stat.st_size, stat.st_mtime_ns, stat.st_ino]
    if os.name != "nt":
        parts.append(stat.st_ctime_ns)
    return ":".join(str(part) for part in parts)


def _norm(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve(strict=False)))


def _jsonl_stream(path: Path) -> Iterator[bytes]:
    """The file up to its current size, without a trailing partial line."""

    with path.open("rb") as stream:
        remaining = os.fstat(stream.fileno()).st_size
        tail = b""
        while remaining > 0:
            block = stream.read(min(READ_BLOCK, remaining))
            if not block:
                break
            remaining -= len(block)
            data = tail + block
            cut = data.rfind(b"\n")
            if cut < 0:
                tail = data
                continue
            yield data[: cut + 1]
            tail = data[cut + 1 :]


def _file_stream(path: Path) -> Iterator[bytes]:
    """The file up to its size when opened, in blocks no larger than what is left.

    read(n) allocates n bytes first: asking a 20 KB file for 8 MiB doubled the
    time of an incremental backup over 3000 small files (measured 2026-10-10)."""

    with path.open("rb") as stream:
        remaining = os.fstat(stream.fileno()).st_size
        while remaining > 0:
            block = stream.read(min(READ_BLOCK, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


def sqlite_snapshot(path: Path) -> bytes:
    """A consistent copy of a live SQLite database (WAL included)."""

    handle, temporary = tempfile.mkstemp(suffix=".sqlite")
    os.close(handle)
    try:
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            target = sqlite3.connect(temporary)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        return Path(temporary).read_bytes()
    finally:
        Path(temporary).unlink(missing_ok=True)


class SessionCollector:
    def __init__(self, roots: CollectRoots, writer: ChunkWriter, options: CollectOptions) -> None:
        self.roots = roots
        self.writer = writer
        self.options = options
        self.result = Collection()
        self.files_done = 0
        self.bytes_read = 0
        self._reported_at = 0.0
        self._lock = threading.Lock()
        self._readers: ThreadPoolExecutor | None = None

    def _report(self, path: Path, force: bool = False) -> None:
        if self.options.progress is None:
            return
        with self._lock:
            now = time.monotonic()
            if not force and now - self._reported_at < 1.0:
                return
            self._reported_at = now
            self.options.progress({
                "files": self.files_done,
                "read": self.bytes_read,
                "unchanged": self.writer.stats.reused_files,
                "uploaded": self.writer.stats.bytes_uploaded,
                "sent": self.writer.bytes_sent(),
                "current": str(path)[-90:],
            })

    def report_upload(self) -> None:
        """While the last chunks upload (reading is done): chunks stored so far and bytes sent."""

        if self.options.progress is None:
            return
        stats = self.writer.stats
        self.options.progress({
            "phase": "uploading",
            "files": self.files_done,
            "read": self.bytes_read,
            "unchanged": stats.reused_files,
            "uploaded": stats.bytes_uploaded,
            "sent": self.writer.bytes_sent(),
            "in_flight": self.writer.in_flight,
            "stored_chunks": stats.uploaded_chunks,
        })

    def _counted(self, path: Path, blocks: Iterator[bytes]) -> Iterator[bytes]:
        for block in blocks:
            with self._lock:
                self.bytes_read += len(block)
            self._report(path)
            yield block

    # ---- primitives -------------------------------------------------------------------------

    def _rel(self, root: str, path: Path) -> str:
        return Path(path).relative_to(self.roots.path(root)).as_posix()

    def _claim(self, path: Path) -> bool:
        key = _norm(path)
        if key in self.result.seen:
            return False
        self.result.seen.add(key)
        return True

    def _entry(self, root: str, path: Path, kind: str, chunks: list[str], size: int,
               before: os.stat_result | None = None, *, unchanged: bool = False) -> None:
        """``before``: the file as it was before reading; its signature lets the next snapshot skip it."""

        with self._lock:
            self.files_done += 1
        self._report(path)
        try:
            stat = before if unchanged and before is not None else path.stat()
            mtime, mode = stat.st_mtime, stat.st_mode & 0o777
        except OSError:
            mtime, mode = time.time(), 0o644
        entry = {"root": root, "path": self._rel(root, path), "kind": kind, "size": size,
                 "mtime": mtime, "mode": mode, "chunks": chunks}
        if before is not None and kind in ("file", "jsonl"):
            entry["sig"] = file_signature(before)
        self.result.entries.append(entry)

    def _skip(self, root: str, path: Path, reason: str) -> None:
        try:
            rel = self._rel(root, path)
        except ValueError:
            rel = str(path)
        self.result.skipped.append({"root": root, "path": rel, "reason": reason})

    def add_file(self, root: str, path: Path) -> None:
        job = self._start(root, Path(path))
        if job is not None:
            self._finish(job)

    def _add_files(self, files: Iterable[tuple[str, Path]]) -> None:
        """add_file for each, several read at once on the reader threads, entries in the order given."""

        queue: deque[_FileJob] = deque()
        for root, path in files:
            job = self._start(root, path)
            if job is None:
                continue
            queue.append(job)
            while queue and (len(queue) > READ_AHEAD or queue[0].ready()):
                self._finish(queue.popleft())
        while queue:
            self._finish(queue.popleft())

    def _start(self, root: str, path: Path) -> _FileJob | None:
        """Checks that need no file contents; a plain file starts reading on a reader thread."""

        try:
            stat = path.stat()
        except OSError:
            return None  # gone, or not a file this user can see
        if not S_ISREG(stat.st_mode) or not self._claim(path):
            return None
        if any(_norm(path) == _norm(Path(skip)) for skip in self.options.skip_paths):
            self._skip(root, path, "backup key file")
            return None
        try:
            rel = self._rel(root, path)
        except ValueError as error:
            self._skip(root, path, f"{type(error).__name__}: {error}")
            return None
        if stat.st_size > self.options.max_file_bytes:
            self._skip(root, path, f"{stat.st_size / 1048576:.0f} MB, over the {self.options.max_file_bytes / 1048576:.0f} MB file limit")
            return None
        suffix = path.suffix.lower()
        if (self._is_secret_file(root, path) or (path.name in JSON_SPLIT_NAMES and root in ("ciel", "ciel_ws", "home"))
                or suffix in (".sqlite", ".sqlite3", ".db")):
            return _FileJob(root, path, "inline", stat)
        kind = "jsonl" if suffix == ".jsonl" else "file"
        previous = self.options.previous.get((root, rel))
        if previous is not None and previous.get("kind") == kind and previous.get("sig") == file_signature(stat):
            return _FileJob(root, path, "reused", stat, previous=previous)
        future = self._readers.submit(self._read, path, kind == "jsonl") if self._readers is not None else None
        return _FileJob(root, path, kind, stat, future=future)

    def _read(self, path: Path, jsonl: bool) -> tuple[list[str], int]:
        total = 0

        def counted() -> Iterator[bytes]:
            nonlocal total
            for block in self._counted(path, _jsonl_stream(path) if jsonl else _file_stream(path)):
                total += len(block)
                yield block

        return self.writer.write_stream(counted()), total

    def _finish(self, job: _FileJob) -> None:
        root, path = job.root, job.path
        try:
            if job.kind == "inline":
                self._add_inline(root, path, job.stat)
            elif job.kind == "reused":
                chunks, size = list(job.previous["chunks"]), int(job.previous["size"])
                self.writer.reuse(chunks, size)
                self._entry(root, path, str(job.previous["kind"]), chunks, size, job.stat, unchanged=True)
            else:
                if job.future is None:
                    chunks, total = self._read(path, job.kind == "jsonl")
                else:
                    while not job.future.done():
                        wait([job.future], timeout=1.0)
                        self._report(path)
                    chunks, total = job.future.result()
                self._entry(root, path, job.kind, chunks, total, job.stat)
        except (OSError, sqlite3.Error, ValueError) as error:
            self._skip(root, path, f"{type(error).__name__}: {error}")

    def _add_inline(self, root: str, path: Path, stat: os.stat_result) -> None:
        if self._is_secret_file(root, path):
            self.result.secrets.files[f"{root}/{self._rel(root, path)}"] = path.read_bytes()
            self._entry(root, path, "secret-file", [], stat.st_size)
            return
        if path.name in JSON_SPLIT_NAMES and root in ("ciel", "ciel_ws", "home"):
            self._add_split_json(root, path)
            return
        if _looks_like_sqlite(path):
            data = sqlite_snapshot(path)
            self._entry(root, path, "sqlite", self.writer.write_stream([data]), len(data))
            return
        chunks, total = self._read(path, False)
        self._entry(root, path, "file", chunks, total, stat)

    def _add_split_json(self, root: str, path: Path) -> None:
        raw = path.read_bytes()
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except ValueError:
            self.result.secrets.files[f"{root}/{self._rel(root, path)}"] = raw
            self._entry(root, path, "secret-file", [], len(raw))
            return
        public, hidden = split_secret_fields(value)
        if hidden:
            self.result.secrets.fields[f"{root}/{self._rel(root, path)}"] = hidden
        data = json.dumps(public, ensure_ascii=False, indent=2).encode("utf-8")
        self._entry(root, path, "json-public", self.writer.write_stream([data]), len(data))

    def _is_secret_file(self, root: str, path: Path) -> bool:
        name = path.name
        if root == "claude" and name == ".credentials.json":
            return True
        if root == "codex" and name in CODEX_SECRET_FILES:
            return True
        if root in ("ciel", "ciel_ws") and name.endswith(CIEL_SECRET_SUFFIXES):
            return True
        return root == "ciel_ws" and name in ("router-external-token", "remote-bridge-token")

    def add_tree(self, root: str, base: Path, *, skip_dirs: set[str] = frozenset(), excludes: tuple[str, ...] = (),
                 skip_top: set[str] = frozenset(), skip_worktrees: bool = False) -> None:
        base = Path(base)
        if not base.is_dir():
            return
        self._add_files(self._walk(root, base, skip_dirs, excludes, skip_top, skip_worktrees))

    def _walk(self, root: str, base: Path, skip_dirs: set[str], excludes: tuple[str, ...], skip_top: set[str],
              skip_worktrees: bool) -> Iterator[tuple[str, Path]]:
        others = {_norm(getattr(self.roots, name)) for name in ("claude", "codex", "ciel", "ciel_ws") if name != root}
        for current, dirs, files in os.walk(base):
            current_path = Path(current)
            kept = []
            for name in sorted(dirs):
                child = current_path / name
                if name in skip_dirs and current_path == base:
                    continue
                if _matches(name, excludes) or _norm(child) in others:
                    self._skip(root, child, "excluded")
                    continue
                if name in skip_top and current_path == base:
                    self._skip(root, child, "not selected")
                    continue
                if skip_worktrees and is_linked_worktree(child):
                    self._skip(root, child, "worktree")
                    continue
                kept.append(name)
            dirs[:] = kept
            for name in sorted(files):
                if _matches(name, excludes) or name.startswith(".part-") or name.endswith((".tmp", ".lock")):
                    continue
                yield root, current_path / name

    # ---- sources ----------------------------------------------------------------------------

    def collect_claude(self) -> None:
        claude = self.roots.claude
        project = claude / "projects" / claude_project_key(str(self.roots.cwd))
        session_ids: list[str] = []
        if project.is_dir():
            for transcript in sorted(project.glob("*.jsonl"), key=lambda item: item.stat().st_mtime):
                if self.options.session_ids and transcript.stem not in self.options.session_ids:
                    continue
                session_ids.append(transcript.stem)
                self.add_file("claude", transcript)
                self.add_tree("claude", project / transcript.stem)
            for extra in sorted(project.iterdir()):
                if extra.is_file() and extra.suffix != ".jsonl":
                    self.add_file("claude", extra)
        for session_id in session_ids:
            self.add_tree("claude", claude / "file-history" / session_id)
            for todo in (claude / "todos").glob(f"{session_id}*.json") if (claude / "todos").is_dir() else []:
                self.add_file("claude", todo)
        if claude.is_dir():
            for item in sorted(claude.iterdir()):
                if item.is_file() and not item.name.endswith(".lock"):
                    self.add_file("claude", item)
                elif item.is_dir() and item.name not in CLAUDE_SKIP_DIRS and item.name != "plugins":
                    self.add_tree("claude", item)
            for name in CLAUDE_PLUGIN_FILES:
                self.add_file("claude", claude / "plugins" / name)
        self.add_file("home", self.roots.home / ".claude.json")
        if session_ids:
            self.result.sessions["claude"] = {"project": project.name, "session_ids": session_ids, "latest": session_ids[-1]}

    def collect_codex(self) -> None:
        codex = self.roots.codex
        if not codex.is_dir():
            return
        threads = codex_threads_for_cwd(codex, self.roots.cwd, self.options.session_ids)
        for thread in threads:
            rollout = Path(thread["rollout_path"]) if thread.get("rollout_path") else None
            if rollout is not None and rollout.is_file():
                try:
                    rollout.relative_to(codex)
                except ValueError:
                    self._skip("codex", rollout, "rollout outside CODEX_HOME")
                    continue
                self.add_file("codex", rollout)
        for item in sorted(codex.iterdir()):
            if item.is_file() and not item.name.endswith((".lock", ".tmp")) and not item.name.startswith("logs_"):
                self.add_file("codex", item)
        for name in CODEX_KEEP_DIRS:
            self.add_tree("codex", codex / name)
        if threads:
            self.result.sessions["codex"] = {
                "threads": [{key: thread.get(key) for key in ("id", "title", "updated_at")} for thread in threads],
                "latest": threads[-1]["id"],
            }

    def collect_ciel(self) -> None:
        for name in CIEL_TOP_FILES:
            self.add_file("ciel", self.roots.ciel / name)
        for name in CIEL_GLOBAL_SECRET_FILES:
            self.add_file("ciel", self.roots.ciel / name)
        self.add_tree("ciel_ws", self.roots.ciel_ws)
        try:
            state = json.loads((self.roots.ciel / "launch-state.json").read_text(encoding="utf-8"))
            wanted = workspace_identity(self.roots.cwd)
            launch = next(
                (value for key, value in (state.get("by_cwd") or {}).items()
                 if workspace_identity(plain_path(key)) == wanted and isinstance(value, dict)),
                {},
            )
        except (OSError, ValueError, AttributeError):
            launch = {}
        if launch:
            self.result.sessions["launch"] = {key: launch.get(key) for key in ("mode", "provider", "model", "time")}

    def collect_cwd(self) -> None:
        if self.options.include_files:
            excludes = (*self.options.excludes, *self.options.extra_excludes)
            self.add_tree("cwd", self.roots.cwd, excludes=excludes, skip_top=set(self.options.skip_top),
                          skip_worktrees=self.options.worktrees == "off")
            if self.options.worktrees == "all":
                for name, path in self.roots.extra().items():
                    self.add_tree(name, path, excludes=excludes)

    def collect(self) -> Collection:
        self.result.started = time.time()
        if self.writer.jobs > 1:
            self._readers = ThreadPoolExecutor(self.writer.jobs, thread_name_prefix="ciel-backup-read")
        try:
            self.collect_claude()
            self.collect_codex()
            self.collect_ciel()
            self.collect_cwd()
        finally:
            if self._readers is not None:
                self._readers.shutdown(wait=True, cancel_futures=True)
                self._readers = None
        self._report(self.roots.cwd, force=True)
        return self.result


def measure_tree(
    base: Path,
    excludes: tuple[str, ...],
    skip: tuple[Path, ...] = (),
    *,
    deadline: float | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    skip_top: tuple[str, ...] = (),
    skip_worktrees: bool = False,
) -> tuple[int, int, dict[str, int], bool]:
    """(bytes, files, bytes per top-level entry, complete) of a work folder as a backup would read it.

    Stops early at ``deadline`` (time.monotonic) and reports complete=False."""

    base = Path(base)
    skipped = {_norm(path) for path in skip}
    total = files = 0
    top: dict[str, int] = {}
    reported = time.monotonic()
    for current, dirs, names in os.walk(base):
        current_path = Path(current)
        dirs[:] = [name for name in dirs if not _matches(name, excludes) and _norm(current_path / name) not in skipped
                   and not (current_path == base and name in skip_top)
                   and not (skip_worktrees and is_linked_worktree(current_path / name))]
        relative = current_path.relative_to(base).parts
        for name in names:
            if _matches(name, excludes) or name.startswith(".part-") or name.endswith((".tmp", ".lock")):
                continue
            try:
                size = (current_path / name).stat().st_size
            except OSError:
                continue
            if size > max_file_bytes:
                continue  # the backup leaves it out too
            total += size
            files += 1
            key = relative[0] if relative else name
            top[key] = top.get(key, 0) + size
        now = time.monotonic()
        if progress is not None and now - reported >= 1.0:
            reported = now
            progress({"phase": "measuring", "files": files, "read": 0, "uploaded": 0, "measured": total, "current": str(current_path)[-90:]})
        if deadline is not None and now > deadline:
            return total, files, top, False
    return total, files, top, True


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _looks_like_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def codex_threads_for_cwd(codex_home: Path, cwd: Path, session_ids: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Codex threads of this cwd (oldest first) with their rollout files."""

    wanted = workspace_identity(cwd)
    rows: list[dict[str, Any]] = []
    for database in sorted(codex_home.glob("state_*.sqlite")):
        try:
            connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=10)
        except sqlite3.Error:
            continue
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
            if not {"id", "cwd"} <= columns:
                continue
            fields = [name for name in ("id", "cwd", "title", "rollout_path", "updated_at", "updated_at_ms") if name in columns]
            for values in connection.execute(f"SELECT {', '.join(fields)} FROM threads"):
                row = dict(zip(fields, values))
                if workspace_identity(plain_path(row.get("cwd"))) != wanted:
                    continue
                if session_ids and row["id"] not in session_ids:
                    continue
                row["updated_at"] = row.get("updated_at_ms") or row.get("updated_at") or 0
                rows.append(row)
        except sqlite3.Error:
            continue
        finally:
            connection.close()
    if not any(row.get("rollout_path") for row in rows):
        rows = _threads_from_rollouts(codex_home, wanted, session_ids) or rows
    return sorted(rows, key=lambda row: row.get("updated_at") or 0)


def _threads_from_rollouts(codex_home: Path, wanted: str, session_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for folder in ("sessions", "archived_sessions"):
        for rollout in (codex_home / folder).rglob("rollout-*.jsonl") if (codex_home / folder).is_dir() else []:
            try:
                with rollout.open("r", encoding="utf-8", errors="replace") as stream:
                    first = json.loads(stream.readline() or "{}")
            except (OSError, ValueError):
                continue
            payload = first.get("payload") if isinstance(first, dict) else None
            if not isinstance(payload, dict) or workspace_identity(plain_path(payload.get("cwd"))) != wanted:
                continue
            thread_id = str(payload.get("id") or "")
            if session_ids and thread_id not in session_ids:
                continue
            rows.append({"id": thread_id, "rollout_path": str(rollout), "updated_at": rollout.stat().st_mtime})
    return rows


def previous_files(manifests: list[Mapping[str, Any] | None], roots: CollectRoots) -> dict[tuple[str, str], dict[str, Any]]:
    """Files a new snapshot may take from the newest snapshot on each target, by (root, path).

    Only files every target's newest snapshot recorded alike (same signature, same chunks)
    under the same root folder, modified at least REUSE_MARGIN_NS before that snapshot
    started; a target without a snapshot means every file is read."""

    current = roots.as_dict()
    shared: dict[tuple[str, str], dict[str, Any]] | None = None
    for manifest in manifests:
        if not manifest or not isinstance(manifest.get("started"), (int, float)):
            return {}
        limit = int(manifest["started"] * 1e9) - REUSE_MARGIN_NS
        old_roots = manifest.get("roots") or {}
        same_roots = {name for name, path in current.items() if old_roots.get(name) and same_path(old_roots[name], path)}
        files: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in manifest.get("entries") or []:
            parts = str(entry.get("sig") or "").split(":")
            if entry.get("kind") not in ("file", "jsonl") or entry.get("root") not in same_roots or len(parts) < 3:
                continue
            if not parts[1].isdigit() or int(parts[1]) >= limit:
                continue
            files[(entry["root"], entry["path"])] = entry
        shared = files if shared is None else {
            key: entry for key, entry in shared.items()
            if key in files and files[key].get("sig") == entry.get("sig") and files[key].get("chunks") == entry.get("chunks")
        }
    return shared or {}


def build_manifest(
    snapshot_id: str,
    workspace: str,
    roots: CollectRoots,
    collection: Collection,
    options: CollectOptions,
    *,
    secrets_ref: dict[str, Any] | None,
    versions: Mapping[str, str],
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    return {
        "format": FORMAT_VERSION,
        "id": snapshot_id,
        "workspace": workspace,
        "created": clock(),
        "started": collection.started,
        "label": options.label,
        "trigger": options.trigger,
        "host": platform.node(),
        "user": getpass.getuser(),
        "platform": platform.platform(),
        "roots": roots.as_dict(),
        "sessions": collection.sessions,
        "versions": dict(versions),
        "entries": collection.entries,
        "skipped": collection.skipped,
        "secrets": secrets_ref or {"included": False, "names": collection.secrets.names()},
    }


__all__ = [
    "WORKTREE_MODES",
    "CollectOptions",
    "CollectRoots",
    "Collection",
    "DEFAULT_CWD_EXCLUDES",
    "SessionCollector",
    "build_manifest",
    "claude_project_key",
    "codex_threads_for_cwd",
    "default_roots",
    "file_signature",
    "git_worktrees",
    "is_linked_worktree",
    "measure_tree",
    "outside_worktrees",
    "plain_path",
    "previous_files",
    "same_path",
    "sqlite_snapshot",
    "workspace_label",
]
