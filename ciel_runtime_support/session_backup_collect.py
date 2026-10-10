"""Collect one workspace's agent session into a snapshot while the agent runs.

Roots are named so a restore can place them elsewhere:
``cwd`` (the working folder), ``claude`` (Claude config dir), ``home``
(for ``~/.claude.json``), ``codex`` (CODEX_HOME), ``ciel`` (Ciel config dir)
and ``ciel_ws`` (this workspace's Ciel state dir).

Consistency without stopping the agent: append-only ``.jsonl`` files are read
up to their size at that moment and cut back to the last complete line; SQLite
databases are copied through the ``sqlite3`` backup API; everything else Ciel
and the CLIs write is replaced atomically, so a plain read sees one version.
"""

from __future__ import annotations

import fnmatch
import getpass
import json
import os
import platform
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from ciel_runtime_support.session_backup_secrets import SecretBundle, split_secret_fields
from ciel_runtime_support.session_backup_store import FORMAT_VERSION, ChunkWriter
from ciel_runtime_support.workspace_router_selection import workspace_digest, workspace_identity

READ_BLOCK = 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 1024 * 1024 * 1024
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

    def as_dict(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in ("cwd", "claude", "home", "codex", "ciel", "ciel_ws")}


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


@dataclass
class Collection:
    entries: list[dict[str, Any]] = field(default_factory=list)
    secrets: SecretBundle = field(default_factory=SecretBundle)
    skipped: list[dict[str, str]] = field(default_factory=list)
    sessions: dict[str, Any] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)


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
    with path.open("rb") as stream:
        while block := stream.read(READ_BLOCK):
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

    # ---- primitives -------------------------------------------------------------------------

    def _rel(self, root: str, path: Path) -> str:
        return Path(path).relative_to(getattr(self.roots, root)).as_posix()

    def _claim(self, path: Path) -> bool:
        key = _norm(path)
        if key in self.result.seen:
            return False
        self.result.seen.add(key)
        return True

    def _entry(self, root: str, path: Path, kind: str, chunks: list[str], size: int) -> None:
        try:
            stat = path.stat()
            mtime, mode = stat.st_mtime, stat.st_mode & 0o777
        except OSError:
            mtime, mode = time.time(), 0o644
        self.result.entries.append(
            {"root": root, "path": self._rel(root, path), "kind": kind, "size": size,
             "mtime": mtime, "mode": mode, "chunks": chunks}
        )

    def _skip(self, root: str, path: Path, reason: str) -> None:
        try:
            rel = self._rel(root, path)
        except ValueError:
            rel = str(path)
        self.result.skipped.append({"root": root, "path": rel, "reason": reason})

    def add_file(self, root: str, path: Path) -> None:
        path = Path(path)
        if not path.is_file() or not self._claim(path):
            return
        if any(_norm(path) == _norm(Path(skip)) for skip in self.options.skip_paths):
            self._skip(root, path, "backup key file")
            return
        name = path.name
        try:
            size = path.stat().st_size
            if size > self.options.max_file_bytes:
                self._skip(root, path, f"larger than {self.options.max_file_bytes} bytes")
                return
            if self._is_secret_file(root, path):
                self.result.secrets.files[f"{root}/{self._rel(root, path)}"] = path.read_bytes()
                self._entry(root, path, "secret-file", [], size)
                return
            if name in JSON_SPLIT_NAMES and root in ("ciel", "ciel_ws", "home"):
                self._add_split_json(root, path)
                return
            if path.suffix.lower() in (".sqlite", ".sqlite3", ".db") and _looks_like_sqlite(path):
                data = sqlite_snapshot(path)
                self._entry(root, path, "sqlite", self.writer.write_stream([data]), len(data))
                return
            if path.suffix.lower() == ".jsonl":
                total = 0

                def counted() -> Iterator[bytes]:
                    nonlocal total
                    for block in _jsonl_stream(path):
                        total += len(block)
                        yield block

                chunks = self.writer.write_stream(counted())
                self._entry(root, path, "jsonl", chunks, total)
                return
            total = 0

            def counted_file() -> Iterator[bytes]:
                nonlocal total
                for block in _file_stream(path):
                    total += len(block)
                    yield block

            chunks = self.writer.write_stream(counted_file())
            self._entry(root, path, "file", chunks, total)
        except (OSError, sqlite3.Error, ValueError) as error:
            self._skip(root, path, f"{type(error).__name__}: {error}")

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

    def add_tree(self, root: str, base: Path, *, skip_dirs: set[str] = frozenset(), excludes: tuple[str, ...] = ()) -> None:
        base = Path(base)
        if not base.is_dir():
            return
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
                kept.append(name)
            dirs[:] = kept
            for name in sorted(files):
                if _matches(name, excludes) or name.startswith(".part-") or name.endswith((".tmp", ".lock")):
                    continue
                self.add_file(root, current_path / name)

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
            self.add_tree("cwd", self.roots.cwd, excludes=(*self.options.excludes, *self.options.extra_excludes))

    def collect(self) -> Collection:
        self.collect_claude()
        self.collect_codex()
        self.collect_ciel()
        self.collect_cwd()
        return self.result


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
    "CollectOptions",
    "CollectRoots",
    "Collection",
    "DEFAULT_CWD_EXCLUDES",
    "SessionCollector",
    "build_manifest",
    "claude_project_key",
    "codex_threads_for_cwd",
    "default_roots",
    "plain_path",
    "same_path",
    "sqlite_snapshot",
    "workspace_label",
]
