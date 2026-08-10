"""Background Colab speech deployment jobs launched by Web Chat."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime_paths import CONFIG_DIR


_ACTIONS = {"start", "deploy", "recreate", "status"}
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SECRET_PATTERN = re.compile(r"(?:tskey-[A-Za-z0-9_-]+|Bearer\s+\S+)", re.IGNORECASE)


@dataclass(slots=True)
class _Job:
    job_id: str
    action: str
    profile: str
    process: subprocess.Popen[bytes]
    log_path: Path
    redactions: tuple[str, ...]


class ColabSpeechJobManager:
    def __init__(self, script_path: Path, state_dir: Path) -> None:
        self.script_path = script_path
        self.state_dir = state_dir
        self._jobs: dict[str, _Job] = {}
        self._latest = ""
        self._lock = threading.Lock()

    @staticmethod
    def _safe_name(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not _SAFE_NAME.fullmatch(text):
            raise ValueError(f"invalid Colab {label}")
        return text

    def login_command(self, settings: dict[str, Any], *, reset: bool = False) -> str:
        profile = self._safe_name(settings.get("profile") or "default", "profile")
        distribution = self._safe_name(settings.get("distribution") or "Ubuntu-26.04", "distribution")
        auth = str(settings.get("auth") or "oauth2").strip().lower()
        if auth not in {"adc", "oauth2"}:
            raise ValueError("Colab auth must be adc or oauth2")
        suffix = " -ResetAuthentication" if reset else ""
        return (
            "powershell -ExecutionPolicy Bypass -File "
            f'"{self.script_path}" -Action Login -Distribution "{distribution}" '
            f'-ColabAuth "{auth}" -Profile "{profile}"{suffix}'
        )

    @staticmethod
    def _redact(text: str, redactions: tuple[str, ...]) -> str:
        result = _SECRET_PATTERN.sub("<redacted>", text)
        for secret in redactions:
            result = result.replace(secret, "<redacted>")
        return result

    def _scrub_completed_log(self, job: _Job) -> None:
        job.process.wait()
        try:
            original = job.log_path.read_text(encoding="utf-8", errors="replace")
            redacted = self._redact(original, job.redactions)
            if redacted != original:
                job.log_path.write_text(redacted, encoding="utf-8")
        except OSError:
            pass

    def launch(
        self,
        action: str,
        settings: dict[str, Any],
        secrets: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        if normalized == "login":
            return {
                "ok": True,
                "requires_terminal": True,
                "command": self.login_command(settings, reset=bool((secrets or {}).get("reset_authentication"))),
            }
        if normalized not in _ACTIONS:
            raise ValueError("Colab action must be start, deploy, recreate, status, or login")
        if os.name != "nt":
            raise RuntimeError("Web-managed Colab deployment currently requires Windows with WSL")
        if not self.script_path.is_file():
            raise RuntimeError(f"Colab deployment script is missing: {self.script_path}")
        profile = self._safe_name(settings.get("profile") or "default", "profile")
        with self._lock:
            for job in self._jobs.values():
                if job.profile == profile and job.process.poll() is None:
                    raise RuntimeError(f"Colab profile '{profile}' already has a running job")
            self.state_dir.mkdir(parents=True, exist_ok=True)
            job_id = uuid.uuid4().hex[:16]
            log_path = self.state_dir / f"{job_id}.log"
            command = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script_path),
                "-Action",
                normalized.capitalize(),
                "-Profile",
                profile,
            ]
            environment = os.environ.copy()
            supplied = secrets or {}
            for source, target in (("tailscale_auth_key", "TAILSCALE_AUTHKEY"), ("speech_api_key", "CIEL_SPEECH_API_KEY")):
                value = str(supplied.get(source) or "").strip()
                if value:
                    environment[target] = value
            with log_path.open("wb") as output:
                process = subprocess.Popen(
                    command,
                    cwd=str(self.script_path.parent.parent),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            redactions = tuple(str(supplied.get(key) or "").strip() for key in ("tailscale_auth_key", "speech_api_key") if str(supplied.get(key) or "").strip())
            job = _Job(job_id, normalized, profile, process, log_path, redactions)
            self._jobs[job_id] = job
            self._latest = job_id
            threading.Thread(target=self._scrub_completed_log, args=(job,), daemon=True).start()
        return self.status(job_id)

    def status(self, job_id: str = "") -> dict[str, Any]:
        selected = str(job_id or "").strip() or self._latest
        with self._lock:
            job = self._jobs.get(selected)
            if job is None:
                return {"ok": True, "job": None}
            return_code = job.process.poll()
            try:
                output = job.log_path.read_text(encoding="utf-8", errors="replace")[-16000:]
            except OSError:
                output = ""
        redacted_output = self._redact(output, job.redactions)
        return {
            "ok": return_code in {None, 0},
            "job": {
                "id": job.job_id,
                "action": job.action,
                "profile": job.profile,
                "running": return_code is None,
                "return_code": return_code,
                "output": redacted_output,
            },
        }


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy_colab_speech.ps1"
_MANAGER = ColabSpeechJobManager(_SCRIPT_PATH, CONFIG_DIR / "colab-jobs")


def launch_colab_speech_job(action: str, settings: dict[str, Any], secrets: dict[str, str] | None = None) -> dict[str, Any]:
    return _MANAGER.launch(action, settings, secrets)


def colab_speech_job_status(job_id: str = "") -> dict[str, Any]:
    return _MANAGER.status(job_id)


__all__ = ["ColabSpeechJobManager", "colab_speech_job_status", "launch_colab_speech_job"]
