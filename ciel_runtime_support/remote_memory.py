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
from .prompt_injection import PromptInjector, append_anthropic_system_texts
from .remote_instructions import (
    RUNTIME_FILES,
    configured_url as configured_instruction_url,
    expand_environment_references,
    normalized_instruction_sha256,
    settings as instruction_settings,
    target_file,
)


MEMORY_POINTER_BEGIN = "<!-- ciel-runtime:remote-memory:begin -->"
MEMORY_POINTER_END = "<!-- ciel-runtime:remote-memory:end -->"
MEMORY_REFERENCE_INSTRUCTION = (
    "Memory guidance: Resolve these paths from the current workspace root. "
    "Read the memory index first and use the relevant files under the memory "
    "root as context for your work."
)
DEFAULT_DIRECTORY = ".ciel/memory"
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
_MEMORY_POINTER_PATTERN = re.compile(
    re.escape(MEMORY_POINTER_BEGIN)
    + r".*?"
    + re.escape(MEMORY_POINTER_END),
    re.DOTALL,
)


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


def memory_directory(workspace: Path, config: Mapping[str, Any]) -> Path:
    configured = str(settings(config).get("directory") or DEFAULT_DIRECTORY).strip()
    relative = _safe_relative_path(
        configured,
        field="remote_memory.directory",
    )
    root = workspace.resolve()
    destination = root.joinpath(*relative.parts).resolve()
    if destination == root or root not in destination.parents:
        raise ValueError("remote_memory.directory must stay inside the workspace")
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


def _managed_pointer_block(index_address: str, root_address: str = "") -> str:
    resolved_root = str(root_address or "").strip()
    if not resolved_root:
        resolved_root = PurePosixPath(index_address).parent.as_posix()
    return (
        f"{MEMORY_POINTER_BEGIN}\n"
        f"Memory root: {resolved_root}\n"
        f"Memory index: {index_address}\n"
        f"{MEMORY_REFERENCE_INSTRUCTION}\n"
        f"{MEMORY_POINTER_END}"
    )


def without_memory_pointer(value: str) -> str:
    """Remove Ciel's managed memory block from trusted instruction text."""

    return _MEMORY_POINTER_PATTERN.sub("", value).strip()


def _without_pointer_from_content(value: Any) -> tuple[Any, str]:
    """Scrub managed blocks from a privileged text/content-part value."""

    if isinstance(value, str):
        matches = tuple(_MEMORY_POINTER_PATTERN.finditer(value))
        return without_memory_pointer(value), matches[-1].group(0) if matches else ""
    if isinstance(value, dict):
        copied = dict(value)
        text_key = next(
            (
                key
                for key in ("text", "input_text", "output_text")
                if isinstance(copied.get(key), str)
            ),
            "",
        )
        if not text_key:
            return copied, ""
        copied[text_key], pointer = _without_pointer_from_content(copied[text_key])
        if not copied[text_key] and str(copied.get("type") or "") in {
            "",
            "text",
            "input_text",
            "output_text",
        }:
            return "", pointer
        return copied, pointer
    if not isinstance(value, list):
        return value, ""
    cleaned: list[Any] = []
    pointer = ""
    for part in value:
        if isinstance(part, str):
            next_part, found = _without_pointer_from_content(part)
            if found:
                pointer = found
            if next_part:
                cleaned.append(next_part)
            continue
        if not isinstance(part, dict):
            cleaned.append(part)
            continue
        copied = dict(part)
        text_key = next(
            (
                key
                for key in ("text", "input_text", "output_text")
                if isinstance(copied.get(key), str)
            ),
            "",
        )
        if text_key:
            copied[text_key], found = _without_pointer_from_content(copied[text_key])
            if found:
                pointer = found
            if not copied[text_key] and str(copied.get("type") or "") in {
                "",
                "text",
                "input_text",
                "output_text",
            }:
                continue
        cleaned.append(copied)
    return cleaned, pointer


def _append_pointer_to_content(value: Any, pointer: str) -> Any:
    if isinstance(value, list):
        return [*value, {"type": "text", "text": pointer}]
    current = str(value or "").rstrip()
    return f"{current}\n\n{pointer}" if current else pointer


def _messages_without_memory_pointer(
    messages: Any,
) -> tuple[list[Any], str, list[int]]:
    if not isinstance(messages, list):
        return [], "", []
    projected: list[Any] = []
    pointer = ""
    privileged: list[int] = []
    for message in messages:
        if not isinstance(message, dict):
            projected.append(message)
            continue
        copied = dict(message)
        if str(copied.get("role") or "") in {"system", "developer"}:
            if "content" in copied:
                copied["content"], found = _without_pointer_from_content(
                    copied.get("content")
                )
            elif isinstance(copied.get("text"), str):
                copied["text"], found = _without_pointer_from_content(copied["text"])
            else:
                found = ""
            if found:
                pointer = found
            privileged.append(len(projected))
        projected.append(copied)
    return projected, pointer, privileged


def _anthropic_system_with_pointer_last(system: Any, prompt: str) -> Any:
    cleaned, _pointer = _without_pointer_from_content(system)
    return append_anthropic_system_texts(cleaned, [prompt] if prompt else [])


def move_memory_pointer_to_system_end(
    messages: list[dict[str, Any]],
    fallback_pointer: str | None = None,
) -> list[dict[str, Any]]:
    """Keep the managed pointer at the tail of the final wire system text.

    ``fallback_pointer`` restores the trusted pointer when an intermediate
    projection truncated the tail of a long system prompt.
    """

    fallback_match = _MEMORY_POINTER_PATTERN.search(fallback_pointer or "")
    pointer = fallback_match.group(0) if fallback_match else ""
    projected, existing_pointer, system_indexes = _messages_without_memory_pointer(
        messages
    )
    if not pointer and fallback_pointer is None:
        pointer = existing_pointer
    if not pointer:
        return projected
    if not system_indexes:
        return [{"role": "system", "content": pointer}, *projected]
    target = system_indexes[-1]
    projected[target]["content"] = _append_pointer_to_content(
        projected[target].get("content"),
        pointer,
    )
    return projected


def _current_memory_addresses(
    state_dir: Path,
    config: Mapping[str, Any],
    workspace: Path | None = None,
) -> tuple[str, str]:
    """Return verified workspace-relative root and index paths."""

    if not bool(settings(config).get("enabled", False)):
        return "", ""
    try:
        state = json.loads((state_dir / "remote-memory.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return "", ""
    if not isinstance(state, dict):
        return "", ""
    try:
        raw_workspace = str(state.get("workspace") or "").strip()
        if not raw_workspace:
            return "", ""
        stored_workspace = Path(raw_workspace).resolve()
        root_relative = _safe_relative_path(state.get("root"), field="root")
        index_relative = _safe_relative_path(state.get("index"), field="index")
    except (OSError, ValueError):
        return "", ""
    if workspace is not None and stored_workspace != workspace.resolve():
        return "", ""
    root = stored_workspace.joinpath(*root_relative.parts).resolve()
    if stored_workspace not in root.parents:
        return "", ""
    path = root.joinpath(*index_relative.parts).resolve()
    if root not in path.parents:
        return "", ""
    if not root.is_dir() or not path.is_file():
        return "", ""
    return root_relative.as_posix(), root_relative.joinpath(index_relative).as_posix()


def current_memory_root_address(
    state_dir: Path,
    config: Mapping[str, Any],
    workspace: Path | None = None,
) -> str:
    """Return the verified workspace-relative memory root for prompt injection."""

    root_address, _index_address = _current_memory_addresses(
        state_dir,
        config,
        workspace,
    )
    return root_address


def current_memory_index_address(
    state_dir: Path,
    config: Mapping[str, Any],
    workspace: Path | None = None,
) -> str:
    """Return a verified workspace-relative index path for prompt injection."""

    _root_address, index_address = _current_memory_addresses(
        state_dir,
        config,
        workspace,
    )
    return index_address


def current_memory_prompt(
    state_dir: Path,
    config: Mapping[str, Any],
    workspace: Path | None = None,
) -> str:
    root_address, index_address = _current_memory_addresses(
        state_dir,
        config,
        workspace,
    )
    return (
        _managed_pointer_block(index_address, root_address)
        if root_address and index_address
        else ""
    )


def inject_current_memory_prompt(
    body: dict[str, Any],
    protocol: MessageProtocol,
    state_dir: Path,
    config: Mapping[str, Any],
    workspace: Path | None = None,
) -> dict[str, Any]:
    prompt = current_memory_prompt(state_dir, config, workspace)
    if protocol == "anthropic_messages":
        projected = dict(body)
        if "system" in body or prompt:
            projected["system"] = _anthropic_system_with_pointer_last(
                body.get("system"),
                prompt,
            )
        messages, _pointer, _privileged = _messages_without_memory_pointer(
            body.get("messages")
        )
        if isinstance(body.get("messages"), list):
            projected["messages"] = messages
        return body if projected == body else projected
    if protocol == "openai_responses":
        projected = dict(body)
        instructions = body.get("instructions")
        current = (
            without_memory_pointer(instructions)
            if isinstance(instructions, str)
            else instructions
        )
        if current or prompt or "instructions" in body:
            projected["instructions"] = (
                f"{current}\n\n{prompt}" if current and prompt else current or prompt
            )
        inputs, _pointer, _privileged = _messages_without_memory_pointer(
            body.get("input")
        )
        if isinstance(body.get("input"), list):
            projected["input"] = inputs
        elif isinstance(body.get("input"), dict):
            projected["input"] = _messages_without_memory_pointer(
                [body["input"]]
            )[0][0]
        return body if projected == body else projected
    if protocol in {"openai_chat", "ollama_chat"}:
        messages = body.get("messages")
        projected = dict(body)
        projected["messages"] = move_memory_pointer_to_system_end(
            messages if isinstance(messages, list) else [],
            prompt,
        )
        return body if projected == body else projected
    if protocol == "google_generative":
        projected = dict(body)
        key = "system_instruction" if "system_instruction" in body else "systemInstruction"
        current = body.get(key)
        if isinstance(current, dict):
            system_instruction = dict(current)
            parts, _pointer = _without_pointer_from_content(current.get("parts"))
            system_instruction["parts"] = parts
            projected[key] = system_instruction
        elif isinstance(current, str):
            projected[key] = without_memory_pointer(current)
        if prompt:
            projected = PromptInjector().inject(projected, protocol, [prompt])
        return body if projected == body else projected
    return PromptInjector().inject(body, protocol, [prompt]) if prompt else body


def update_memory_pointer(
    path: Path,
    index_address: str = "",
    root_address: str = "",
) -> bool:
    """Replace Ciel's memory pointer at the bottom without changing user text."""

    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    if not index_address and not _MEMORY_POINTER_PATTERN.search(current):
        return False
    current = without_memory_pointer(current)
    next_text = current
    if index_address:
        block = _managed_pointer_block(index_address, root_address)
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
    root_address: str = "",
) -> bool:
    if runtime not in RUNTIME_FILES:
        raise ValueError(f"unsupported instruction runtime: {runtime}")
    return update_memory_pointer(
        target_file(workspace, runtime),
        index_address,
        root_address,
    )


def _can_project_native_pointer(
    config: Mapping[str, Any],
    state_dir: Path,
    workspace: Path,
    runtime: str,
) -> bool:
    instruction = instruction_settings(dict(config))
    configured = configured_instruction_url(dict(config), runtime)
    target = target_file(workspace, runtime)
    if not bool(instruction.get("enabled", False)) or not configured or not target.is_file():
        return False
    safe_runtime = runtime.replace("-", "_")
    try:
        state = json.loads(
            (state_dir / f"remote-instructions-{safe_runtime}.json").read_text(
                encoding="utf-8"
            )
        )
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(state, dict)
        or str(state.get("url") or "") != configured
        or str(state.get("target") or "") != target.name
    ):
        return False
    try:
        raw = target.read_bytes()
    except OSError:
        return False
    downloaded_digest = str(state.get("sha256") or "")
    if downloaded_digest and hashlib.sha256(raw).hexdigest() == downloaded_digest:
        return True
    normalized_digest = str(state.get("normalized_sha256") or "")
    if not normalized_digest:
        return False
    try:
        managed_content = without_memory_pointer(raw.decode("utf-8"))
    except UnicodeError:
        return False
    return normalized_instruction_sha256(managed_content) == normalized_digest


def _shared_target_has_projectable_pointer(
    config: Mapping[str, Any],
    state_dir: Path,
    workspace: Path,
    runtime: str,
) -> bool:
    filename = RUNTIME_FILES[runtime]
    return any(
        _can_project_native_pointer(config, state_dir, workspace, candidate)
        for candidate, candidate_filename in RUNTIME_FILES.items()
        if candidate_filename == filename
    )


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
        if not bool(remote.get("enabled", False)):
            self._remove_legacy_pointer(runtime, workspace)
            return RemoteMemoryResult(manifest_url, None, None, "", "disabled")
        try:
            manifest_url = _http_url(
                manifest_url,
                base="",
                field="remote_memory.manifest_url",
            )
            root = memory_directory(workspace, config)
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
            def commit_state(index_address: str) -> None:
                self._write_state(
                    {
                        "manifest_url": manifest_url,
                        "workspace": str(workspace),
                        "root": root.relative_to(workspace).as_posix(),
                        "index": manifest.index.as_posix(),
                        "index_address": index_address,
                        "files": [
                            {"path": item.path.as_posix(), "format": item.format}
                            for item in manifest.files
                        ],
                        "reason": reason,
                    }
                )

            index_address = self._replace_tree(
                root,
                manifest,
                manifest_url=manifest_url,
                timeout=timeout,
                file_limit=file_limit,
                total_limit=total_limit,
                authorization=authorization,
                commit=commit_state,
            )
            index_path = root.joinpath(*manifest.index.parts)
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
        projection_detail = ""
        try:
            if _shared_target_has_projectable_pointer(
                config,
                self.state_dir,
                workspace,
                runtime,
            ):
                project_memory_pointer(
                    workspace,
                    runtime,
                    index_path.relative_to(workspace).as_posix(),
                    root.relative_to(workspace).as_posix(),
                )
            else:
                project_memory_pointer(workspace, runtime, "")
        except (OSError, UnicodeError, ValueError) as exc:
            projection_detail = (
                "memory pointer projection failed: "
                f"{type(exc).__name__}: {exc}"
            )
            self.log(
                "WARN",
                f"remote_memory_pointer_failed runtime={runtime} "
                f"error={type(exc).__name__}: {exc}",
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
            projection_detail,
        )

    def current_index_address(self) -> str:
        return current_memory_index_address(
            self.state_dir,
            self.load_config(),
            self.workspace().resolve(),
        )

    def current_prompt_text(self) -> str:
        return current_memory_prompt(
            self.state_dir,
            self.load_config(),
            self.workspace().resolve(),
        )

    def project_current_pointer(self, runtime: str) -> bool:
        """Project the verified current memory location into native instructions."""

        config = self.load_config()
        workspace = self.workspace().resolve()
        if not _shared_target_has_projectable_pointer(
            config,
            self.state_dir,
            workspace,
            runtime,
        ):
            return project_memory_pointer(workspace, runtime, "")
        root_address, index_address = _current_memory_addresses(
            self.state_dir,
            config,
            workspace,
        )
        return project_memory_pointer(
            workspace,
            runtime,
            index_address,
            root_address,
        )

    def _remove_legacy_pointer(self, runtime: str, workspace: Path) -> None:
        try:
            project_memory_pointer(workspace, runtime, "")
        except (OSError, UnicodeError, ValueError) as exc:
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
        commit: Callable[[str], None],
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
            index_address = str(root.joinpath(*manifest.index.parts).resolve())
            try:
                commit(index_address)
            except Exception:
                os.replace(root, staging)
                if moved_previous and backup.exists():
                    os.replace(backup, root)
                raise
            if backup.exists():
                try:
                    shutil.rmtree(backup)
                except OSError as exc:
                    self.log(
                        "WARN",
                        "remote_memory_backup_cleanup_failed "
                        f"path={backup} error={type(exc).__name__}: {exc}",
                    )
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return index_address

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
    """Refresh native instructions and restore the verified memory pointer."""

    result = instruction_synchronizer().sync(runtime, reason=reason)
    try:
        memory_synchronizer().project_current_pointer(runtime)
    except (OSError, UnicodeError, ValueError) as exc:
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
    """Download once and project the verified pointer for every runtime."""

    result = synchronizer.sync("codex", reason="manual")
    for runtime in ("codex-app-server", "claude", "agy", "kimi", "grok"):
        try:
            synchronizer.project_current_pointer(runtime)
        except (OSError, UnicodeError, ValueError) as exc:
            synchronizer.log(
                "WARN",
                f"remote_memory_pointer_failed runtime={runtime} "
                f"error={type(exc).__name__}: {exc}",
            )
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
    "MEMORY_REFERENCE_INSTRUCTION",
    "RemoteMemoryFile",
    "RemoteMemoryManifest",
    "RemoteMemoryResult",
    "RemoteMemorySynchronizer",
    "current_memory_index_address",
    "current_memory_prompt",
    "current_memory_root_address",
    "inject_current_memory_prompt",
    "memory_directory",
    "move_memory_pointer_to_system_end",
    "parse_manifest",
    "project_memory_pointer",
    "settings",
    "sync_all_memory_pointers",
    "sync_instruction_with_memory_pointer",
    "sync_launch_assets",
    "update_memory_pointer",
    "without_memory_pointer",
]
