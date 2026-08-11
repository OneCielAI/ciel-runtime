"""Background Colab speech deployment jobs launched by Web Chat."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets as crypto_secrets
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
_SAVED_SECRET_NAMES = ("tailscale_auth_key", "speech_api_key")


class PortableEncryptedSecretStore:
    """Portable authenticated vault with one credential slot per profile."""

    def __init__(self, path: Path, key_path: Path | None = None, *, master_key: bytes | None = None) -> None:
        self.path = path
        self.key_path = key_path or path.with_suffix(".key")
        self._master_key = master_key
        self._lock = threading.Lock()

    @staticmethod
    def _chmod_private(path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _key(self) -> bytes:
        if self._master_key is not None:
            return self._master_key
        configured = str(os.environ.get("CIEL_RUNTIME_SECRET_MASTER_KEY") or "").strip()
        if configured:
            try:
                key = base64.urlsafe_b64decode(configured.encode("ascii"))
            except (ValueError, UnicodeError) as exc:
                raise RuntimeError("CIEL_RUNTIME_SECRET_MASTER_KEY must be URL-safe base64") from exc
            if len(key) != 32:
                raise RuntimeError("CIEL_RUNTIME_SECRET_MASTER_KEY must decode to exactly 32 bytes")
            self._master_key = key
            return key
        try:
            key = base64.urlsafe_b64decode(self.key_path.read_text(encoding="ascii").strip().encode("ascii"))
        except FileNotFoundError:
            key = crypto_secrets.token_bytes(32)
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.key_path.with_suffix(self.key_path.suffix + ".tmp")
            temporary.write_text(base64.urlsafe_b64encode(key).decode("ascii"), encoding="ascii")
            self._chmod_private(temporary)
            os.replace(temporary, self.key_path)
        except (OSError, ValueError, UnicodeError) as exc:
            raise RuntimeError(f"Ciel secret-vault key is unreadable: {exc}") from exc
        if len(key) != 32:
            raise RuntimeError("Ciel secret-vault key has an invalid length")
        self._master_key = key
        return key

    @staticmethod
    def _derive(master: bytes, purpose: bytes) -> bytes:
        return hmac.new(master, b"ciel-runtime-colab-v1:" + purpose, hashlib.sha256).digest()

    def _protect(self, value: str) -> str:
        master = self._key()
        encryption_key = self._derive(master, b"encryption")
        mac_key = self._derive(master, b"authentication")
        nonce = crypto_secrets.token_bytes(16)
        plain = value.encode("utf-8")
        stream = bytearray()
        for counter in range((len(plain) + 31) // 32):
            stream.extend(hmac.new(encryption_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        cipher = bytes(left ^ right for left, right in zip(plain, stream))
        payload = b"CRV1" + nonce + cipher
        tag = hmac.new(mac_key, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + tag).decode("ascii")

    def _unprotect(self, value: str) -> str:
        try:
            payload = base64.urlsafe_b64decode(value.encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("Saved Colab worker credential is not valid base64") from exc
        if len(payload) < 52 or not payload.startswith(b"CRV1"):
            raise RuntimeError("Saved Colab worker credential has an invalid format")
        body, supplied_tag = payload[:-32], payload[-32:]
        master = self._key()
        expected_tag = hmac.new(self._derive(master, b"authentication"), body, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied_tag, expected_tag):
            raise RuntimeError("Saved Colab worker credential failed authentication")
        nonce, cipher = body[4:20], body[20:]
        encryption_key = self._derive(master, b"encryption")
        stream = bytearray()
        for counter in range((len(cipher) + 31) // 32):
            stream.extend(hmac.new(encryption_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        try:
            return bytes(left ^ right for left, right in zip(cipher, stream)).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("Saved Colab worker credential could not be decoded") from exc

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "profiles": {}}
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Saved Colab worker credentials are unreadable: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
            raise RuntimeError("Saved Colab worker credentials have an invalid format")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)

    def save(self, profile: str, secrets: dict[str, str]) -> None:
        values = {name: str(secrets.get(name) or "").strip() for name in _SAVED_SECRET_NAMES}
        values = {name: value for name, value in values.items() if value}
        if not values:
            return
        with self._lock:
            data = self._read()
            profiles = data.setdefault("profiles", {})
            current = profiles.get(profile) if isinstance(profiles.get(profile), dict) else {}
            for name, value in values.items():
                current[name] = self._protect(value)
            profiles[profile] = current
            self._write(data)

    def load(self, profile: str) -> dict[str, str]:
        with self._lock:
            data = self._read()
            encrypted = data.get("profiles", {}).get(profile, {})
            if not isinstance(encrypted, dict):
                return {}
            return {
                name: self._unprotect(str(encrypted.get(name)))
                for name in _SAVED_SECRET_NAMES
                if str(encrypted.get(name) or "").strip()
            }

    def clear(self, profile: str) -> None:
        with self._lock:
            data = self._read()
            profiles = data.get("profiles", {})
            if not isinstance(profiles, dict) or profile not in profiles:
                return
            del profiles[profile]
            if profiles:
                self._write(data)
            else:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass

    def status(self, profile: str) -> dict[str, bool]:
        with self._lock:
            data = self._read()
            encrypted = data.get("profiles", {}).get(profile, {})
            if not isinstance(encrypted, dict):
                encrypted = {}
            return {f"stored_{name}": bool(str(encrypted.get(name) or "").strip()) for name in _SAVED_SECRET_NAMES}


@dataclass(slots=True)
class _Job:
    job_id: str
    action: str
    profile: str
    process: subprocess.Popen[bytes]
    log_path: Path
    redactions: tuple[str, ...]


class ColabSpeechJobManager:
    def __init__(self, script_path: Path, state_dir: Path, secret_store: PortableEncryptedSecretStore | None = None) -> None:
        self.script_path = script_path
        self.state_dir = state_dir
        self.secret_store = secret_store or PortableEncryptedSecretStore(state_dir.parent / "colab-worker-credentials.vault.json")
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
        supplied = secrets or {}
        if str(supplied.get("forget_saved_credentials") or "").strip() in {"1", "true", "yes"}:
            self.secret_store.clear(profile)
        entered = {name: str(supplied.get(name) or "").strip() for name in _SAVED_SECRET_NAMES}
        entered = {name: value for name, value in entered.items() if value}
        if entered:
            self.secret_store.save(profile, entered)
        effective = self.secret_store.load(profile)
        effective.update(entered)
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
            environment.pop("CIEL_RUNTIME_SECRET_MASTER_KEY", None)
            if normalized in {"deploy", "recreate"}:
                for source, target in (("tailscale_auth_key", "TAILSCALE_AUTHKEY"), ("speech_api_key", "CIEL_SPEECH_API_KEY")):
                    value = str(effective.get(source) or "").strip()
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
            redactions = tuple(str(effective.get(key) or "").strip() for key in _SAVED_SECRET_NAMES if str(effective.get(key) or "").strip())
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
        failure_kind = ""
        if return_code not in {None, 0}:
            if re.search(r"(?i)(TooManyAssignments|Precondition Failed|allocation limit)", redacted_output):
                failure_kind = "colab_capacity"
            elif re.search(r"(?i)(Tailscale authentication failed|TAILSCALE_AUTHKEY is required)", redacted_output):
                failure_kind = "tailscale_auth"
            elif re.search(r"(?i)(appears to be lost|404/401|session.+not found)", redacted_output):
                failure_kind = "colab_session_lost"
            else:
                failure_kind = "deployment_failed"
        return {
            "ok": return_code in {None, 0},
            "job": {
                "id": job.job_id,
                "action": job.action,
                "profile": job.profile,
                "running": return_code is None,
                "return_code": return_code,
                "failure_kind": failure_kind,
                "output": redacted_output,
            },
        }

    def credential_status(self, profile: str) -> dict[str, bool]:
        return self.secret_store.status(self._safe_name(profile or "default", "profile"))


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy_colab_speech.ps1"
_MANAGER = ColabSpeechJobManager(_SCRIPT_PATH, CONFIG_DIR / "colab-jobs")


def launch_colab_speech_job(action: str, settings: dict[str, Any], secrets: dict[str, str] | None = None) -> dict[str, Any]:
    return _MANAGER.launch(action, settings, secrets)


def colab_speech_job_status(job_id: str = "") -> dict[str, Any]:
    return _MANAGER.status(job_id)


def colab_speech_credential_status(profile: str = "default") -> dict[str, bool]:
    return _MANAGER.credential_status(profile)


__all__ = ["ColabSpeechJobManager", "PortableEncryptedSecretStore", "colab_speech_credential_status", "colab_speech_job_status", "launch_colab_speech_job"]
