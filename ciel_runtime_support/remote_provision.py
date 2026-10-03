"""Pre-session provisioning from an HTTP manifest.

Before a runtime launches, the workspace can be prepared from a manifest
(``remote_provision.manifest_url``): files are placed inside the workspace and
scripts (PowerShell, bash or Python) run in it, for example to install the
programs a session needs.  Every file and script must carry a sha256 that
matches what was downloaded; nothing unverified is written or run.  A failed
download, check or script blocks the launch.

Manifest version 1::

    {"version": 1,
     "files": [{"path": "tools/setup.zip", "url": "...", "sha256": "..."}],
     "steps": [{"id": "install-node", "shell": "powershell", "url": "...",
                "sha256": "...", "platform": "windows", "timeout_s": 600,
                "run": "once"}]}

``run: once`` reruns a step only when its sha256 changes or its last run
failed; ``every_launch`` runs it on every launch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Any, Callable, Mapping
import urllib.request
import uuid

from .event_settings_cli import (
    EventSettingsCliError,
    _validated_url,
    parse_flag,
    split_assignment,
)
from .remote_instructions import expand_environment_references
from .remote_memory import _bounded_int, _http_url, _origin, _safe_relative_path


DEFAULT_MAX_MANIFEST_BYTES = 1_048_576
DEFAULT_MAX_FILE_BYTES = 67_108_864
DEFAULT_MAX_TOTAL_BYTES = 268_435_456
DEFAULT_STEP_TIMEOUT_SECONDS = 600
SHELL_EXTENSIONS = {"powershell": ".ps1", "bash": ".sh", "python": ".py"}
PLATFORMS = frozenset({"windows", "linux", "macos", "any"})
RUN_MODES = frozenset({"once", "every_launch"})
REMOTE_PROVISION_KEYS = (
    "enabled",
    "manifest_url",
    "authorization",
    "timeout_seconds",
    "max_manifest_bytes",
    "max_file_bytes",
    "max_total_bytes",
)
REMOTE_PROVISION_LIMITS = {
    "timeout_seconds": (1, 120),
    "max_manifest_bytes": (1_024, 4_194_304),
    "max_file_bytes": (1_024, 1_073_741_824),
    "max_total_bytes": (1_024, 4_294_967_296),
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def settings(config: Mapping[str, Any]) -> dict[str, Any]:
    value = config.get("remote_provision")
    return dict(value) if isinstance(value, dict) else {}


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _sha256(value: Any, *, field: str) -> str:
    digest = str(value or "").strip().lower()
    if not _SHA256.fullmatch(digest):
        raise ValueError(f"{field} must be a 64-character hex sha256")
    return digest


@dataclass(frozen=True, slots=True)
class ProvisionFile:
    path: PurePosixPath
    url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ProvisionStep:
    id: str
    shell: str
    url: str
    sha256: str
    platform: str = "any"
    timeout_s: int = DEFAULT_STEP_TIMEOUT_SECONDS
    run: str = "once"


@dataclass(frozen=True, slots=True)
class ProvisionManifest:
    files: tuple[ProvisionFile, ...]
    steps: tuple[ProvisionStep, ...]


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    status: str
    detail: str = ""
    lines: tuple[str, ...] = ()


def parse_manifest(payload: Any, *, manifest_url: str) -> ProvisionManifest:
    if not isinstance(payload, dict):
        raise ValueError("provision manifest must be a JSON object")
    if payload.get("version", 1) != 1:
        raise ValueError(f"unsupported provision manifest version: {payload.get('version')}")
    raw_files = payload.get("files") or []
    raw_steps = payload.get("steps") or []
    if not isinstance(raw_files, list) or not isinstance(raw_steps, list):
        raise ValueError("provision manifest files and steps must be arrays")
    if not raw_files and not raw_steps:
        raise ValueError("provision manifest has no files and no steps")
    files: list[ProvisionFile] = []
    seen_paths: set[PurePosixPath] = set()
    for index, raw in enumerate(raw_files):
        name = f"files[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{name} must be an object")
        path = _safe_relative_path(raw.get("path"), field=f"{name}.path")
        if path in seen_paths:
            raise ValueError(f"duplicate provision path: {path.as_posix()}")
        seen_paths.add(path)
        files.append(ProvisionFile(
            path,
            _http_url(raw.get("url"), base=manifest_url, field=f"{name}.url"),
            _sha256(raw.get("sha256"), field=f"{name}.sha256"),
        ))
    steps: list[ProvisionStep] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_steps):
        name = f"steps[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{name} must be an object")
        step_id = str(raw.get("id") or "").strip()
        if not _STEP_ID.fullmatch(step_id):
            raise ValueError(f"{name}.id must be 1-64 letters, digits, '.', '_' or '-'")
        if step_id in seen_ids:
            raise ValueError(f"duplicate provision step id: {step_id}")
        seen_ids.add(step_id)
        shell = str(raw.get("shell") or "").strip().lower()
        if shell not in SHELL_EXTENSIONS:
            raise ValueError(f"{name}.shell must be one of {', '.join(SHELL_EXTENSIONS)}")
        platform = str(raw.get("platform") or "any").strip().lower()
        if platform not in PLATFORMS:
            raise ValueError(f"{name}.platform must be one of {', '.join(sorted(PLATFORMS))}")
        run = str(raw.get("run") or "once").strip().lower()
        if run not in RUN_MODES:
            raise ValueError(f"{name}.run must be once or every_launch")
        steps.append(ProvisionStep(
            step_id,
            shell,
            _http_url(raw.get("url"), base=manifest_url, field=f"{name}.url"),
            _sha256(raw.get("sha256"), field=f"{name}.sha256"),
            platform,
            _bounded_int(raw.get("timeout_s"), DEFAULT_STEP_TIMEOUT_SECONDS, 1, 86_400),
            run,
        ))
    return ProvisionManifest(tuple(files), tuple(steps))


def _terminate_tree(process: subprocess.Popen[Any]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, 9)
        except OSError:
            process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


@dataclass(frozen=True, slots=True)
class RemoteProvisioner:
    load_config: Callable[[], dict[str, Any]]
    workspace: Callable[[], Path]
    state_dir: Path
    log: Callable[[str, str], None]
    urlopen: Callable[..., Any] = urllib.request.urlopen
    platform: Callable[[], str] = current_platform
    python: str = sys.executable
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    @property
    def root(self) -> Path:
        return self.state_dir / "provision"

    def enabled(self) -> bool:
        return bool(settings(self.load_config()).get("enabled", False))

    def provision(self, *, reason: str = "launch") -> ProvisionResult:
        remote = settings(self.load_config())
        if not bool(remote.get("enabled", False)):
            return ProvisionResult("disabled")
        manifest_url = str(remote.get("manifest_url") or "").strip()
        try:
            manifest_url = _http_url(manifest_url, base="", field="remote_provision.manifest_url")
        except ValueError as exc:
            return self._failed(reason, str(exc))
        authorization, missing = expand_environment_references(
            str(remote.get("authorization") or ""), self.environ
        )
        if missing:
            return self._failed(
                reason,
                "missing authorization environment variable: " + ", ".join(sorted(set(missing))),
            )
        timeout = _bounded_int(remote.get("timeout_seconds"), 30, 1, 120)
        file_limit = _bounded_int(remote.get("max_file_bytes"), DEFAULT_MAX_FILE_BYTES, 1_024, 1_073_741_824)
        total_limit = _bounded_int(remote.get("max_total_bytes"), DEFAULT_MAX_TOTAL_BYTES, 1_024, 4_294_967_296)
        def download(url: str, maximum: int) -> bytes:
            return self._download(
                url, timeout=timeout, maximum=maximum,
                authorization=authorization, authorization_origin=_origin(manifest_url),
            )

        lines: list[str] = []
        state = self._read_state()
        try:
            manifest = parse_manifest(
                json.loads(download(manifest_url, _bounded_int(
                    remote.get("max_manifest_bytes"), DEFAULT_MAX_MANIFEST_BYTES, 1_024, 4_194_304
                )).decode("utf-8")),
                manifest_url=manifest_url,
            )
            workspace = self.workspace().resolve()
            total = 0
            for item in manifest.files:
                total += self._place_file(item, workspace, download, file_limit, lines)
                if total > total_limit:
                    raise ValueError(f"provision files exceed {total_limit} bytes")
            steps = dict(state.get("steps") or {})
            state["steps"] = steps
            for step in manifest.steps:
                self._run_step(step, workspace, download, file_limit, steps, lines)
        except Exception as exc:
            state["last_error"] = str(exc)
            self._write_state(state, manifest_url, reason, "failed")
            return self._failed(reason, str(exc), lines)
        state.pop("last_error", None)
        self._write_state(state, manifest_url, reason, "ok")
        self.log("INFO", f"remote_provision_ok reason={reason} " + "; ".join(lines))
        return ProvisionResult("ok", lines=tuple(lines))

    def status_lines(self) -> list[str]:
        state = self._read_state()
        if not state:
            return ["no provisioning has run in this workspace"]
        lines = [
            f"last={state.get('status', '-')} at {state.get('updated_at', '-')} "
            f"reason={state.get('reason', '-')} manifest={state.get('manifest_url', '-')}"
        ]
        if state.get("last_error"):
            lines.append(f"error: {state['last_error']}")
        for step_id, record in sorted((state.get("steps") or {}).items()):
            lines.append(
                f"step {step_id}: exit={record.get('exit_code')} at {record.get('finished_at')} "
                f"sha256={str(record.get('sha256', ''))[:12]} log={record.get('log', '')}"
            )
        return lines

    def _place_file(self, item: ProvisionFile, workspace: Path, download: Callable[[str, int], bytes],
                    limit: int, lines: list[str]) -> int:
        target = workspace.joinpath(*item.path.parts).resolve()
        if workspace not in target.parents:
            raise ValueError(f"provision path leaves the workspace: {item.path.as_posix()}")
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == item.sha256:
            lines.append(f"file {item.path.as_posix()} unchanged")
            return 0
        raw = download(item.url, limit)
        if hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ValueError(f"sha256 mismatch for file {item.path.as_posix()}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(raw)
        os.replace(temporary, target)
        lines.append(f"file {item.path.as_posix()} written")
        return len(raw)

    def _run_step(self, step: ProvisionStep, workspace: Path, download: Callable[[str, int], bytes],
                  limit: int, steps: dict[str, Any], lines: list[str]) -> None:
        if step.platform not in {"any", self.platform()}:
            lines.append(f"step {step.id} skipped (platform {step.platform})")
            return
        previous = steps.get(step.id) or {}
        if step.run == "once" and previous.get("sha256") == step.sha256 and previous.get("exit_code") == 0:
            lines.append(f"step {step.id} already done")
            return
        script = self.root / "scripts" / f"{step.sha256}{SHELL_EXTENSIONS[step.shell]}"
        if not (script.is_file() and hashlib.sha256(script.read_bytes()).hexdigest() == step.sha256):
            raw = download(step.url, limit)
            if hashlib.sha256(raw).hexdigest() != step.sha256:
                raise ValueError(f"sha256 mismatch for step {step.id}")
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_bytes(raw)
        log_dir = self.root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{step.id}.log"
        env = dict(self.environ)
        env.update(CIEL_PROVISION_DIR=str(self.root), CIEL_WORKSPACE=str(workspace), CIEL_PROVISION_STEP=step.id)
        with log_path.open("wb") as output:
            process = subprocess.Popen(
                self._command(step.shell, script),
                cwd=str(workspace),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
            try:
                exit_code = process.wait(timeout=step.timeout_s)
            except subprocess.TimeoutExpired:
                _terminate_tree(process)
                exit_code = None
        steps[step.id] = {
            "sha256": step.sha256,
            "exit_code": exit_code,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "log": str(log_path),
        }
        if exit_code is None:
            raise ValueError(f"step {step.id} timed out after {step.timeout_s}s (log {log_path})")
        if exit_code != 0:
            raise ValueError(f"step {step.id} exited {exit_code} (log {log_path})")
        lines.append(f"step {step.id} ran")

    def _command(self, shell: str, script: Path) -> list[str]:
        if shell == "powershell":
            executable = "powershell.exe" if os.name == "nt" else "pwsh"
            return [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        if shell == "bash":
            return ["bash", str(script)]
        return [self.python, str(script)]

    def _download(self, url: str, *, timeout: int, maximum: int, authorization: str,
                  authorization_origin: tuple[str, str, int | None]) -> bytes:
        headers = {"Accept": "*/*"}
        if authorization.strip() and _origin(url) == authorization_origin:
            headers["Authorization"] = authorization.strip()
        request = urllib.request.Request(url, headers=headers, method="GET")
        with self.urlopen(request, timeout=timeout) as response:
            _http_url(str(response.geturl() or url), base="", field="download redirect")
            raw = response.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError(f"download exceeds {maximum} bytes: {url}")
        return raw

    def _state_path(self) -> Path:
        return self.root / "provision-state.json"

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_state(self, state: dict[str, Any], manifest_url: str, reason: str, status: str) -> None:
        state.update(
            manifest_url=manifest_url,
            reason=reason,
            status=status,
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _failed(self, reason: str, detail: str, lines: list[str] | None = None) -> ProvisionResult:
        self.log("WARN", f"remote_provision_failed reason={reason} error={detail}")
        return ProvisionResult("failed", detail, tuple(lines or ()))


def provision_before_launch(result: Any, *, reason: str, provisioner: Callable[[], RemoteProvisioner],
                            output: Callable[[str], None] = print) -> Any:
    """Run provisioning after the launch assets; a failure stops the launch."""

    if reason != "launch":
        return result
    outcome = provisioner().provision(reason=reason)
    if outcome.status == "disabled":
        return result
    for line in outcome.lines:
        output(f"Provisioning: {line}")
    if outcome.status == "failed":
        output("Ciel Runtime launch blocked:")
        output(f"- Provisioning failed: {outcome.detail}")
        raise SystemExit(2)
    return result


def remote_provision_command(
    load_config: Callable[[], dict[str, Any]],
    save_config: Callable[[dict[str, Any]], None],
    provisioner: Callable[[], RemoteProvisioner],
    output: Callable[[str], None] = print,
) -> Callable[[Any], None]:
    """``remote-provision [KEY=VALUE ...] [run] [status]``."""

    def handle(args: Any) -> None:
        tokens = [str(value) for value in (getattr(args, "values", None) or [])]
        try:
            updates: dict[str, Any] = {}
            actions: list[str] = []
            for token in tokens:
                key, value = split_assignment(token, bare_keys=("run", "status"))
                if key in {"run", "status"}:
                    actions.append(key)
                elif key not in REMOTE_PROVISION_KEYS:
                    raise EventSettingsCliError(
                        f"unsupported remote provision option: {key}; expected one of "
                        f"{', '.join(REMOTE_PROVISION_KEYS)}, run, status"
                    )
                elif key == "enabled":
                    updates[key] = parse_flag(key, value)
                elif key == "manifest_url":
                    updates[key] = _validated_url(key, value)
                elif key in REMOTE_PROVISION_LIMITS:
                    minimum, maximum = REMOTE_PROVISION_LIMITS[key]
                    if not value.strip().isdigit() or not minimum <= int(value) <= maximum:
                        raise EventSettingsCliError(f"{key} must be a whole number from {minimum} to {maximum}")
                    updates[key] = int(value)
                else:
                    updates[key] = value
        except EventSettingsCliError as exc:
            raise SystemExit(str(exc)) from None
        if updates:
            config = load_config()
            stored = config.get("remote_provision")
            if not isinstance(stored, dict):
                stored = {}
                config["remote_provision"] = stored
            stored.update(updates)
            save_config(config)
            shown = ", ".join(f"{key} (stored)" if key == "authorization" else key for key in updates)
            output(f"remote-provision updated: {shown}")
        if not tokens:
            current = settings(load_config())
            output("remote-provision:")
            output(f"  enabled={bool(current.get('enabled', False))}")
            output(f"  manifest_url={current.get('manifest_url') or 'unset'}")
            output(f"  authorization={'stored' if current.get('authorization') else 'unset'}")
            for key, (minimum, _maximum) in REMOTE_PROVISION_LIMITS.items():
                default = {
                    "timeout_seconds": 30,
                    "max_manifest_bytes": DEFAULT_MAX_MANIFEST_BYTES,
                    "max_file_bytes": DEFAULT_MAX_FILE_BYTES,
                    "max_total_bytes": DEFAULT_MAX_TOTAL_BYTES,
                }[key]
                output(f"  {key}={current.get(key) or default}")
        for action in actions:
            if action == "status":
                for line in provisioner().status_lines():
                    output(f"  {line}")
                continue
            outcome = provisioner().provision(reason="manual")
            for line in outcome.lines:
                output(f"  {line}")
            if outcome.status == "disabled":
                output("  remote-provision is disabled; set enabled=true first")
            elif outcome.status == "failed":
                raise SystemExit(f"remote-provision failed: {outcome.detail}")
            else:
                output("  remote-provision complete")

    return handle


__all__ = [
    "ProvisionManifest",
    "ProvisionResult",
    "REMOTE_PROVISION_KEYS",
    "RemoteProvisioner",
    "current_platform",
    "parse_manifest",
    "provision_before_launch",
    "remote_provision_command",
    "settings",
]
