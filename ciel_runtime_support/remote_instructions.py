"""HTTP-managed workspace instruction files for interactive runtimes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping
import urllib.parse
import urllib.error
import urllib.request


RUNTIME_FILES = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "codex-app-server": "AGENTS.md",
    "agy": "GEMINI.md",
    "kimi": "AGENTS.md",
    # Grok Build reads the AGENTS.md family, like Codex and Kimi.
    "grok": "AGENTS.md",
    # ZCode discovers workspace instructions from AGENTS.md.
    "zcode": "AGENTS.md",
    # Muse Code loads AGENTS.md before other project instruction files.
    "muse": "AGENTS.md",
}
URL_KEYS = {
    "claude": "claude_url",
    "codex": "codex_url",
    "codex-app-server": "codex_url",
    "agy": "agy_url",
    "kimi": "kimi_url",
    "grok": "grok_url",
    "muse": "muse_url",
}

# Walkie names the company prompt inside each agent's instruction document,
# in response headers and in a comment in the body (checked 2026-10-14):
# "X-Walkie-Company-Prompt-Version: 8", "X-Walkie-Company-Prompt-Sha256: c344…"
# and "<!-- company-prompt version=8 sha256=c344… source=latest -->".
COMPANY_PROMPT_VERSION_HEADER = "X-Walkie-Company-Prompt-Version"
COMPANY_PROMPT_SHA256_HEADER = "X-Walkie-Company-Prompt-Sha256"
_COMPANY_PROMPT_MARKER = re.compile(r"<!--\s*company-prompt\s+version=(\d+)\s+sha256=([0-9a-fA-F]{8,64})")

_ENV_REFERENCE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_environment_references(value: str, environ: Mapping[str, str] = os.environ) -> tuple[str, list[str]]:
    missing: list[str] = []
    def replace(match: re.Match[str]) -> str:
        name = next(group for group in match.groups() if group is not None)
        resolved = environ.get(name)
        if resolved is None:
            missing.append(name)
            return match.group(0)
        return resolved
    return _ENV_REFERENCE.sub(replace, value), missing


def settings(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("remote_instructions")
    return value if isinstance(value, dict) else {}


def auto_reload_enabled(config: dict[str, Any]) -> bool:
    """Reload the session when Walkie announces another company prompt (default on)."""

    remote = settings(config)
    return bool(remote.get("enabled", False)) and bool(remote.get("auto_reload", True))


def company_prompt_in(text: str, headers: Any = None) -> dict[str, Any]:
    """The company prompt an instruction document carries: headers first, then the body comment."""

    version = str((headers.get(COMPANY_PROMPT_VERSION_HEADER) if headers is not None else "") or "").strip()
    sha256 = str((headers.get(COMPANY_PROMPT_SHA256_HEADER) if headers is not None else "") or "").strip().lower()
    if not sha256:
        match = _COMPANY_PROMPT_MARKER.search(text or "")
        if match:
            version, sha256 = match.group(1), match.group(2).lower()
    return {"company_prompt_version": version, "company_prompt_sha256": sha256} if sha256 else {}


def held_company_prompt(state_dir: Path, workspace: Path) -> dict[str, Any]:
    """The company prompt this workspace's instruction files were last written with.

    The newest synchronization state wins; a state written before the
    company prompt was recorded falls back to the comment in the file itself.
    """

    held: list[tuple[float, dict[str, Any]]] = []
    for runtime in ("claude", "codex"):
        path = state_dir / f"remote-instructions-{runtime}.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        found = {key: state.get(key) for key in ("company_prompt_version", "company_prompt_sha256")} if isinstance(state, dict) and state.get("company_prompt_sha256") else {}
        if not found:
            try:
                target = workspace.resolve() / RUNTIME_FILES[runtime]
                found = company_prompt_in(target.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                found = {}
        if found:
            updated = float(state.get("updated_at") or 0.0) if isinstance(state, dict) else 0.0
            held.append((updated, found))
    return max(held, key=lambda item: item[0])[1] if held else {}


# One externally supplied prompt serves both native files: a workspace given
# only claude_url or only codex_url still gets CLAUDE.md and AGENTS.md
# (cindy-ai 2026-09-28 had claude_url alone and a stale AGENTS.md).
_SHARED_URL_FALLBACK = {"claude_url": "codex_url", "codex_url": "claude_url"}
# Every launch refreshes both files, whichever runtime starts, so a workspace
# used by Claude Code and Codex never leaves one of them behind (sarah-ai
# 2026-09-28: a Claude launch rewrote CLAUDE.md while AGENTS.md stayed old).
LAUNCH_COMPANION_RUNTIMES = ("claude", "codex")


def instruction_runtime(runtime: str) -> str:
    """Map persisted app-server launch identities to their instruction runtime."""

    return "codex-app-server" if runtime in {"codex-remote", "codex-desktop"} else runtime


def configured_url(config: dict[str, Any], runtime: str) -> str:
    key = URL_KEYS.get(runtime, "")
    if not key:
        return ""
    remote = settings(config)
    url = str(remote.get(key) or "").strip()
    fallback = _SHARED_URL_FALLBACK.get(key)
    if not url and fallback:
        url = str(remote.get(fallback) or "").strip()
    return url


def launch_instruction_runtimes(runtime: str) -> tuple[str, ...]:
    """The launched runtime plus one runtime per companion instruction file."""

    runtimes = [runtime]
    files = {RUNTIME_FILES.get(runtime)}
    for companion in LAUNCH_COMPANION_RUNTIMES:
        filename = RUNTIME_FILES[companion]
        if filename not in files:
            runtimes.append(companion)
            files.add(filename)
    return tuple(runtimes)


def target_file(workspace: Path, runtime: str) -> Path:
    filename = RUNTIME_FILES.get(runtime)
    if not filename:
        raise ValueError(f"unsupported instruction runtime: {runtime}")
    return workspace.resolve() / filename


def normalized_instruction_sha256(value: str) -> str:
    """Hash managed instruction text independently of platform line endings."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RemoteInstructionResult:
    runtime: str
    url: str
    path: Path | None
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RemoteInstructionSynchronizer:
    load_config: Callable[[], dict[str, Any]]
    workspace: Callable[[], Path]
    state_dir: Path
    log: Callable[[str, str], None]
    urlopen: Callable[..., Any] = urllib.request.urlopen
    now: Callable[[], float] = time.time

    def sync(self, runtime: str, *, reason: str = "launch") -> RemoteInstructionResult:
        config = self.load_config()
        remote = settings(config)
        url = configured_url(config, runtime)
        if not bool(remote.get("enabled", False)):
            return RemoteInstructionResult(runtime, url, None, "disabled")
        if not url:
            return RemoteInstructionResult(runtime, url, None, "not-configured")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return self._failed(runtime, url, "URL must use http or https")
        timeout = self._bounded_int(remote.get("timeout_seconds"), 5, 1, 30)
        maximum = self._bounded_int(remote.get("max_bytes"), 1_048_576, 1_024, 4_194_304)
        path = target_file(self.workspace(), runtime)
        state = self._read_state(runtime)
        headers = {"Accept": "text/markdown, text/plain;q=0.9, */*;q=0.1"}
        authorization, missing = expand_environment_references(str(remote.get("authorization") or ""))
        if missing:
            return self._failed(runtime, url, f"missing authorization environment variable: {', '.join(sorted(set(missing)))}")
        if authorization.strip():
            headers["Authorization"] = authorization.strip()
        # A state without a prompt-metadata check cannot establish what
        # company prompt the file carries.  Refresh it unconditionally once;
        # otherwise a legacy state can keep returning 304 forever while the
        # reloader sees an unknown prompt and repeatedly restarts the session.
        prompt_checked = bool(state.get("company_prompt_checked"))
        if prompt_checked and state.get("normalized_sha256"):
            if state.get("etag"):
                headers["If-None-Match"] = str(state["etag"])
            if state.get("last_modified"):
                headers["If-Modified-Since"] = str(state["last_modified"])
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self.urlopen(request, timeout=timeout) as response:
                final = urllib.parse.urlparse(str(response.geturl() or url))
                if final.scheme not in {"http", "https"}:
                    raise ValueError("redirected instruction URL must use http or https")
                raw = response.read(maximum + 1)
                if len(raw) > maximum:
                    raise ValueError(f"instruction response exceeds {maximum} bytes")
                text = raw.decode("utf-8")
                if not text.strip():
                    raise ValueError("instruction response is empty")
                digest = hashlib.sha256(raw).hexdigest()
                changed = self._write_if_changed(path, text)
                self._write_state(runtime, {
                    "url": url,
                    "target": path.name,
                    "sha256": digest,
                    "normalized_sha256": normalized_instruction_sha256(text),
                    "etag": str(response.headers.get("etag") or ""),
                    "last_modified": str(response.headers.get("last-modified") or ""),
                    "updated_at": self.now(),
                    "reason": reason,
                    "company_prompt_checked": True,
                    **company_prompt_in(text, response.headers),
                })
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and path.is_file():
                self.log("INFO", f"remote_instructions_unchanged runtime={runtime} reason={reason}")
                return RemoteInstructionResult(runtime, url, path, "unchanged")
            return self._failed(runtime, url, f"HTTP {exc.code}: {exc.reason}")
        except (OSError, UnicodeError, ValueError) as exc:
            return self._failed(runtime, url, f"{type(exc).__name__}: {exc}")
        status = "updated" if changed else "unchanged"
        self.log("INFO", f"remote_instructions_{status} runtime={runtime} target={path.name} reason={reason}")
        return RemoteInstructionResult(runtime, url, path, status)

    def _state_path(self, runtime: str) -> Path:
        safe = runtime.replace("-", "_")
        return self.state_dir / f"remote-instructions-{safe}.json"

    def _read_state(self, runtime: str) -> dict[str, Any]:
        try:
            value = json.loads(self._state_path(runtime).read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_state(self, runtime: str, value: dict[str, Any]) -> None:
        path = self._state_path(runtime)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _write_if_changed(path: Path, text: str) -> bool:
        raw = text.encode("utf-8")
        try:
            if path.read_bytes() == raw:
                return False
        except FileNotFoundError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".ciel-runtime.tmp")
        temporary.write_bytes(raw)
        os.replace(temporary, path)
        return True

    def _failed(self, runtime: str, url: str, detail: str) -> RemoteInstructionResult:
        self.log("WARN", f"remote_instructions_failed runtime={runtime} error={detail}")
        return RemoteInstructionResult(runtime, url, None, "failed", detail)

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))


@dataclass(frozen=True, slots=True)
class SynchronizedLaunch:
    delegate: Callable[..., Any]
    synchronize: Callable[..., Any]
    runtime: str
    prepare: Callable[..., Any] | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.prepare is not None:
            self.prepare(*args, **kwargs)
        self.synchronize(self.runtime, reason="launch")
        return self.delegate(*args, **kwargs)


def panel_rows(config: dict[str, Any], held: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
    current = settings(config)
    enabled = bool(current.get("enabled", False))
    held = held or {}
    company = (
        f"held v{held.get('company_prompt_version') or '?'} {str(held.get('company_prompt_sha256'))[:8]}"
        if held.get("company_prompt_sha256") else "no company prompt held"
    )
    def compact(value: Any) -> str:
        text = str(value)
        return text[:73] + "…" if len(text) > 74 else text
    return (
        [
            f"Enabled  [{'on' if enabled else 'off'}]",
            f"Claude URL → CLAUDE.md  [{compact(current.get('claude_url') or 'unset')}]",
            f"Codex URL → AGENTS.md  [{compact(current.get('codex_url') or 'unset')}]",
            f"AGY URL → GEMINI.md  [{compact(current.get('agy_url') or 'unset')}]",
            f"Kimi URL → AGENTS.md  [{compact(current.get('kimi_url') or 'unset')}]",
            f"Grok URL → AGENTS.md  [{compact(current.get('grok_url') or 'unset')}]",
            f"Muse URL → AGENTS.md  [{compact(current.get('muse_url') or 'unset')}]",
            f"Authorization header  [{'configured' if current.get('authorization') else 'unset'}]",
            f"HTTP timeout seconds  [{current.get('timeout_seconds') or 5}]",
            f"Reload session when the company prompt changes  [{'on' if current.get('auto_reload', True) else 'off'} · {company}]",
            "Sync configured instruction files now",
            "Back",
        ],
        [
            "enabled",
            "claude_url",
            "codex_url",
            "agy_url",
            "kimi_url",
            "grok_url",
            "muse_url",
            "authorization",
            "timeout_seconds",
            "auto_reload",
            "sync",
            "back",
        ],
    )


__all__ = [
    "RemoteInstructionResult",
    "RemoteInstructionSynchronizer",
    "SynchronizedLaunch",
    "auto_reload_enabled",
    "company_prompt_in",
    "configured_url",
    "instruction_runtime",
    "held_company_prompt",
    "launch_instruction_runtimes",
    "expand_environment_references",
    "normalized_instruction_sha256",
    "panel_rows",
    "settings",
    "target_file",
]
