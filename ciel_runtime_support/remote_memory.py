"""HTTP-managed, workspace-scoped memory trees for interactive runtimes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any, Callable, Mapping
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .architecture import MessageProtocol
from .prompt_injection import PromptInjector
from .remote_instructions import (
    RUNTIME_FILES,
    expand_environment_references,
    target_file,
)


MEMORY_POINTER_BEGIN = "<!-- ciel-runtime:remote-memory:begin -->"
MEMORY_POINTER_END = "<!-- ciel-runtime:remote-memory:end -->"
DEFAULT_DIRECTORY = "memory"
LEGACY_DIRECTORY = ".ciel/memory"
DEFAULT_MAX_MANIFEST_BYTES = 1_048_576
DEFAULT_MAX_FILE_BYTES = 4_194_304
DEFAULT_MAX_TOTAL_BYTES = 33_554_432
DEFAULT_MAX_FILES = 256

_FORMAT_EXTENSIONS = {
    "okf": frozenset({".okf"}),
    "markdown": frozenset({".md", ".markdown"}),
    "json": frozenset({".json"}),
    "yaml": frozenset({".yaml", ".yml"}),
    "toml": frozenset({".toml"}),
    "text": frozenset({".txt", ".text"}),
}
_FORMAT_ALIASES = {
    "md": "markdown",
    "yml": "yaml",
    "txt": "text",
}
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def settings(config: Mapping[str, Any]) -> dict[str, Any]:
    value = config.get("remote_memory")
    return dict(value) if isinstance(value, dict) else {}


def _bounded_int(
    value: Any,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _safe_relative_path(value: Any, *, field: str) -> PurePosixPath:
    raw = str(value or "").strip()
    if not raw or "\\" in raw or ":" in raw:
        raise ValueError(f"{field} must be a non-empty portable relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{field} must stay inside the memory directory")
    return path


def memory_directory(state_dir: Path, config: Mapping[str, Any]) -> Path:
    configured = str(settings(config).get("directory") or DEFAULT_DIRECTORY).strip()
    # 0.2.22 briefly interpreted this setting relative to the launch directory.
    # Preserve that release's default as an alias while moving the owned data
    # into the Ciel workspace-state boundary.
    if configured.replace("\\", "/") == LEGACY_DIRECTORY:
        configured = DEFAULT_DIRECTORY
    relative = _safe_relative_path(
        configured,
        field="remote_memory.directory",
    )
    root = state_dir.resolve()
    destination = root.joinpath(*relative.parts).resolve()
    if destination == root or root not in destination.parents:
        raise ValueError("remote_memory.directory must stay inside the workspace state directory")
    return destination


def _normalized_format(value: Any, path: PurePosixPath) -> str:
    declared = str(value or "").strip().lower()
    normalized = _FORMAT_ALIASES.get(declared, declared)
    if not normalized:
        suffix = path.suffix.lower()
        normalized = next(
            (
                name
                for name, extensions in _FORMAT_EXTENSIONS.items()
                if suffix in extensions
            ),
            "",
        )
    extensions = _FORMAT_EXTENSIONS.get(normalized)
    if extensions is None:
        raise ValueError(f"unsupported memory format: {declared or path.suffix}")
    if path.suffix.lower() not in extensions:
        expected = ", ".join(sorted(extensions))
        raise ValueError(
            f"memory file {path.as_posix()} format {normalized} requires one of: {expected}"
        )
    return normalized


def _http_url(value: Any, *, base: str, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field} is required")
    url = urllib.parse.urljoin(base, raw)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must resolve to an http:// or https:// URL")
    return url


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


@dataclass(frozen=True, slots=True)
class RemoteMemoryFile:
    path: PurePosixPath
    url: str
    format: str
    sha256: str = ""


@dataclass(frozen=True, slots=True)
class RemoteMemoryManifest:
    index: PurePosixPath
    files: tuple[RemoteMemoryFile, ...]


@dataclass(frozen=True, slots=True)
class RemoteMemoryResult:
    manifest_url: str
    root: Path | None
    index_path: Path | None
    index_address: str
    status: str
    file_count: int = 0
    detail: str = ""


def parse_manifest(
    payload: Any,
    *,
    manifest_url: str,
    max_files: int = DEFAULT_MAX_FILES,
) -> RemoteMemoryManifest:
    if not isinstance(payload, dict):
        raise ValueError("memory manifest must be a JSON object")
    version = payload.get("version", payload.get("schema_version", 1))
    if version != 1:
        raise ValueError(f"unsupported memory manifest version: {version}")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("memory manifest files must be a non-empty array")
    if len(raw_files) > max_files:
        raise ValueError(f"memory manifest exceeds {max_files} files")

    files: list[RemoteMemoryFile] = []
    seen: set[PurePosixPath] = set()
    for index, raw in enumerate(raw_files):
        if not isinstance(raw, dict):
            raise ValueError(f"memory manifest files[{index}] must be an object")
        path = _safe_relative_path(raw.get("path"), field=f"files[{index}].path")
        if path in seen:
            raise ValueError(f"duplicate memory path: {path.as_posix()}")
        seen.add(path)
        file_format = _normalized_format(raw.get("format"), path)
        url = _http_url(
            raw.get("url", raw.get("download_url")),
            base=manifest_url,
            field=f"files[{index}].url",
        )
        digest = str(raw.get("sha256") or "").strip().lower()
        if digest and not _SHA256.fullmatch(digest):
            raise ValueError(f"files[{index}].sha256 must contain 64 hexadecimal characters")
        files.append(RemoteMemoryFile(path, url, file_format, digest))

    index_path = _safe_relative_path(payload.get("index"), field="index")
    if index_path not in seen:
        raise ValueError("memory manifest index must name one of the downloaded files")
    return RemoteMemoryManifest(index_path, tuple(files))


def _managed_pointer_block(index_address: str) -> str:
    return (
        f"{MEMORY_POINTER_BEGIN}\n"
        f"Memory index: {index_address}\n"
        f"{MEMORY_POINTER_END}"
    )


def current_memory_index_address(
    state_dir: Path,
    config: Mapping[str, Any],
) -> str:
    """Return a verified workspace-state index path for prompt injection."""

    if not bool(settings(config).get("enabled", False)):
        return ""
    try:
        state = json.loads((state_dir / "remote-memory.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return ""
    if not isinstance(state, dict):
        return ""
    try:
        root_relative = _safe_relative_path(state.get("root"), field="root")
        index_relative = _safe_relative_path(state.get("index"), field="index")
    except ValueError:
        return ""
    state_root = state_dir.resolve()
    root = state_root.joinpath(*root_relative.parts).resolve()
    if state_root not in root.parents:
        return ""
    path = root.joinpath(*index_relative.parts).resolve()
    if root not in path.parents:
        return ""
    return str(path) if path.is_file() else ""


def current_memory_prompt(state_dir: Path, config: Mapping[str, Any]) -> str:
    address = current_memory_index_address(state_dir, config)
    return _managed_pointer_block(address) if address else ""


def inject_current_memory_prompt(
    body: dict[str, Any],
    protocol: MessageProtocol,
    state_dir: Path,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    prompt = current_memory_prompt(state_dir, config)
    if not prompt or MEMORY_POINTER_BEGIN in json.dumps(
        body, ensure_ascii=False, default=str
    ):
        return body
    return PromptInjector().inject(body, protocol, [prompt])


def update_memory_pointer(path: Path, index_address: str = "") -> bool:
    """Replace Ciel's memory pointer at the bottom without changing user text."""

    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    start = current.find(MEMORY_POINTER_BEGIN)
    end = (
        current.find(MEMORY_POINTER_END, start + len(MEMORY_POINTER_BEGIN))
        if start >= 0
        else -1
    )
    if not index_address and (start < 0 or end < 0):
        return False
    if start >= 0 and end >= 0:
        current = (
            current[:start] + current[end + len(MEMORY_POINTER_END) :]
        ).strip()
    next_text = current
    if index_address:
        block = _managed_pointer_block(index_address)
        next_text = f"{current}\n\n{block}".strip() if current else block
    if next_text:
        next_text += "\n"
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    if existing == next_text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".ciel-memory.tmp")
    temporary.write_text(next_text, encoding="utf-8")
    os.replace(temporary, path)
    return True


def project_memory_pointer(
    workspace: Path,
    runtime: str,
    index_address: str = "",
) -> bool:
    if runtime not in RUNTIME_FILES:
        raise ValueError(f"unsupported instruction runtime: {runtime}")
    return update_memory_pointer(target_file(workspace, runtime), index_address)


@dataclass(frozen=True, slots=True)
class RemoteMemorySynchronizer:
    load_config: Callable[[], dict[str, Any]]
    workspace: Callable[[], Path]
    state_dir: Path
    log: Callable[[str, str], None]
    urlopen: Callable[..., Any] = urllib.request.urlopen

    def sync(self, runtime: str, *, reason: str = "launch") -> RemoteMemoryResult:
        config = self.load_config()
        remote = settings(config)
        manifest_url = str(remote.get("manifest_url") or "").strip()
        workspace = self.workspace().resolve()
        self._remove_legacy_pointer(runtime, workspace)
        if not bool(remote.get("enabled", False)):
            return RemoteMemoryResult(manifest_url, None, None, "", "disabled")
        try:
            manifest_url = _http_url(
                manifest_url,
                base="",
                field="remote_memory.manifest_url",
            )
            root = memory_directory(self.state_dir, config)
        except ValueError as exc:
            return self._failed(runtime, manifest_url, str(exc))

        authorization, missing = expand_environment_references(
            str(remote.get("authorization") or "")
        )
        if missing:
            return self._failed(
                runtime,
                manifest_url,
                "missing authorization environment variable: "
                + ", ".join(sorted(set(missing))),
            )
        timeout = _bounded_int(remote.get("timeout_seconds"), 5, 1, 30)
        manifest_limit = _bounded_int(
            remote.get("max_manifest_bytes"),
            DEFAULT_MAX_MANIFEST_BYTES,
            1_024,
            4_194_304,
        )
        file_limit = _bounded_int(
            remote.get("max_file_bytes"),
            DEFAULT_MAX_FILE_BYTES,
            1_024,
            16_777_216,
        )
        total_limit = _bounded_int(
            remote.get("max_total_bytes"),
            DEFAULT_MAX_TOTAL_BYTES,
            1_024,
            134_217_728,
        )
        max_files = _bounded_int(
            remote.get("max_files"), DEFAULT_MAX_FILES, 1, 2_048
        )

        try:
            manifest_raw = self._download(
                manifest_url,
                timeout=timeout,
                maximum=manifest_limit,
                authorization=authorization,
                authorization_origin=_origin(manifest_url),
                accept="application/json",
            )
            manifest = parse_manifest(
                json.loads(manifest_raw.decode("utf-8")),
                manifest_url=manifest_url,
                max_files=max_files,
            )
            index_address = self._replace_tree(
                root,
                manifest,
                manifest_url=manifest_url,
                timeout=timeout,
                file_limit=file_limit,
                total_limit=total_limit,
                authorization=authorization,
            )
            index_path = root.joinpath(*manifest.index.parts)
            self._write_state(
                {
                    "manifest_url": manifest_url,
                    "root": root.relative_to(self.state_dir.resolve()).as_posix(),
                    "index": manifest.index.as_posix(),
                    "index_address": index_address,
                    "files": [
                        {"path": item.path.as_posix(), "format": item.format}
                        for item in manifest.files
                    ],
                    "reason": reason,
                }
            )
        except (
            json.JSONDecodeError,
            OSError,
            UnicodeError,
            urllib.error.URLError,
            ValueError,
        ) as exc:
            return self._failed(
                runtime,
                manifest_url,
                f"{type(exc).__name__}: {exc}",
            )
        self.log(
            "INFO",
            f"remote_memory_updated runtime={runtime} files={len(manifest.files)} "
            f"index={index_address} reason={reason}",
        )
        return RemoteMemoryResult(
            manifest_url,
            root,
            index_path,
            index_address,
            "updated",
            len(manifest.files),
        )

    def current_index_address(self) -> str:
        return current_memory_index_address(self.state_dir, self.load_config())

    def current_prompt_text(self) -> str:
        return current_memory_prompt(self.state_dir, self.load_config())

    def project_current_pointer(self, runtime: str) -> bool:
        """Remove the obsolete native-file projection left by 0.2.22."""

        return project_memory_pointer(self.workspace().resolve(), runtime, "")

    def _remove_legacy_pointer(self, runtime: str, workspace: Path) -> None:
        try:
            project_memory_pointer(workspace, runtime, "")
        except (OSError, ValueError) as exc:
            self.log(
                "WARN",
                f"remote_memory_legacy_pointer_cleanup_failed runtime={runtime} "
                f"error={type(exc).__name__}: {exc}",
            )

    def _download(
        self,
        url: str,
        *,
        timeout: int,
        maximum: int,
        authorization: str,
        authorization_origin: tuple[str, str, int | None],
        accept: str,
    ) -> bytes:
        headers = {"Accept": accept}
        if authorization.strip() and _origin(url) == authorization_origin:
            headers["Authorization"] = authorization.strip()
        request = urllib.request.Request(url, headers=headers, method="GET")
        with self.urlopen(request, timeout=timeout) as response:
            final_url = str(response.geturl() or url)
            _http_url(final_url, base="", field="download redirect")
            raw = response.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError(f"download exceeds {maximum} bytes: {url}")
        raw.decode("utf-8")
        return raw

    def _replace_tree(
        self,
        root: Path,
        manifest: RemoteMemoryManifest,
        *,
        manifest_url: str,
        timeout: int,
        file_limit: int,
        total_limit: int,
        authorization: str,
    ) -> str:
        parent = root.parent
        parent.mkdir(parents=True, exist_ok=True)
        if root.exists() and (not root.is_dir() or root.is_symlink()):
            raise ValueError("remote memory target must be a real directory")
        identity = uuid.uuid4().hex
        staging = parent / f".{root.name}.ciel-stage-{identity}"
        backup = parent / f".{root.name}.ciel-backup-{identity}"
        staging.mkdir()
        total = 0
        try:
            for item in manifest.files:
                raw = self._download(
                    item.url,
                    timeout=timeout,
                    maximum=file_limit,
                    authorization=authorization,
                    authorization_origin=_origin(manifest_url),
                    accept="text/plain, application/json;q=0.9, application/yaml;q=0.8",
                )
                total += len(raw)
                if total > total_limit:
                    raise ValueError(
                        f"memory downloads exceed total limit of {total_limit} bytes"
                    )
                digest = hashlib.sha256(raw).hexdigest()
                if item.sha256 and digest != item.sha256:
                    raise ValueError(f"sha256 mismatch for {item.path.as_posix()}")
                destination = staging.joinpath(*item.path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)

            moved_previous = False
            if root.exists():
                os.replace(root, backup)
                moved_previous = True
            try:
                os.replace(staging, root)
            except Exception:
                if moved_previous and backup.exists() and not root.exists():
                    os.replace(backup, root)
                raise
            if backup.exists():
                shutil.rmtree(backup)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return str(root.joinpath(*manifest.index.parts).resolve())

    def _state_path(self) -> Path:
        return self.state_dir / "remote-memory.json"

    def _write_state(self, value: Mapping[str, Any]) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(dict(value), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _failed(
        self,
        runtime: str,
        manifest_url: str,
        detail: str,
    ) -> RemoteMemoryResult:
        self.log("WARN", f"remote_memory_failed runtime={runtime} error={detail}")
        return RemoteMemoryResult(
            manifest_url,
            None,
            None,
            "",
            "failed",
            detail=detail,
        )


def sync_instruction_with_memory_pointer(
    runtime: str,
    *,
    reason: str,
    instruction_synchronizer: Callable[[], Any],
    memory_synchronizer: Callable[[], RemoteMemorySynchronizer],
    log: Callable[[str, str], Any],
) -> Any:
    """Refresh native instructions and remove the obsolete file pointer."""

    result = instruction_synchronizer().sync(runtime, reason=reason)
    try:
        memory_synchronizer().project_current_pointer(runtime)
    except (OSError, ValueError) as exc:
        log(
            "WARN",
            f"remote_memory_pointer_failed runtime={runtime} "
            f"error={type(exc).__name__}: {exc}",
        )
    return result


def sync_launch_assets(
    runtime: str,
    *,
    reason: str,
    instruction_sync: Callable[..., Any],
    memory_sync: Callable[..., RemoteMemoryResult],
) -> RemoteMemoryResult:
    """Synchronize instructions before replacing launch-time memory."""

    instruction_sync(runtime, reason=reason)
    return memory_sync(runtime, reason=reason)


def sync_all_memory_pointers(
    synchronizer: RemoteMemorySynchronizer,
) -> list[str]:
    """Download once and remove obsolete native-file pointers."""

    result = synchronizer.sync("codex", reason="manual")
    for runtime in ("claude", "agy", "kimi", "grok"):
        synchronizer.project_current_pointer(runtime)
    if result.status == "disabled":
        return ["Remote memory is disabled."]
    detail = (
        f" files={result.file_count} index={result.index_address}"
        if result.status == "updated"
        else ""
    )
    suffix = f" ({result.detail})" if result.detail else ""
    return [f"remote-memory: {result.status}{detail}{suffix}"]


__all__ = [
    "DEFAULT_DIRECTORY",
    "MEMORY_POINTER_BEGIN",
    "MEMORY_POINTER_END",
    "RemoteMemoryFile",
    "RemoteMemoryManifest",
    "RemoteMemoryResult",
    "RemoteMemorySynchronizer",
    "current_memory_index_address",
    "current_memory_prompt",
    "inject_current_memory_prompt",
    "memory_directory",
    "parse_manifest",
    "project_memory_pointer",
    "settings",
    "sync_all_memory_pointers",
    "sync_instruction_with_memory_pointer",
    "sync_launch_assets",
    "update_memory_pointer",
]
