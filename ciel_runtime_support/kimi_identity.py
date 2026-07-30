"""Official Kimi Code credential and request identity compatibility."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


KIMI_CODE_OAUTH_CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
KIMI_CODE_OAUTH_HOST = "https://auth.kimi.com"
_OAUTH_REFRESH_LOCK = threading.Lock()


def code_home(home: Path, environ: dict[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    return Path(values.get("KIMI_CODE_HOME") or (home / ".kimi-code"))


def oauth_token_record(home: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(
            (code_home(home) / "credentials" / "kimi-code.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def oauth_access_token(home: Path) -> str | None:
    record = oauth_token_record(home)
    if record is None:
        return None
    token = str(record.get("access_token") or "").strip()
    try:
        expires_at = float(record.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if not token:
        return None
    if expires_at > 0 and expires_at <= time.time() + 30:
        return refresh_oauth_access_token(home, record)
    return token


def refresh_oauth_access_token(
    home: Path, record: dict[str, Any] | None = None
) -> str | None:
    """Refresh an expired official Kimi Code token using its public OAuth contract."""

    with _OAUTH_REFRESH_LOCK:
        current = oauth_token_record(home)
        if current is None:
            return None
        try:
            expires_at = float(current.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        current_token = str(current.get("access_token") or "").strip()
        if current_token and (expires_at <= 0 or expires_at > time.time() + 30):
            return current_token
        refresh_token = str(current.get("refresh_token") or "").strip()
        if not refresh_token:
            return None
        oauth_host = str(
            os.environ.get("KIMI_CODE_OAUTH_HOST")
            or os.environ.get("KIMI_OAUTH_HOST")
            or KIMI_CODE_OAUTH_HOST
        ).rstrip("/")
        body = urllib.parse.urlencode(
            {
                "client_id": KIMI_CODE_OAUTH_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        ).encode("utf-8")
        headers = {
            **identity_headers(home),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        request = urllib.request.Request(
            f"{oauth_host}/api/oauth/token", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        access_token = str(payload.get("access_token") or "").strip()
        try:
            expires_in = int(payload.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        if not access_token or expires_in <= 0:
            return None
        updated = {
            **current,
            **payload,
            "refresh_token": str(payload.get("refresh_token") or refresh_token),
            "expires_at": int(time.time()) + expires_in,
        }
        path = code_home(home) / "credentials" / "kimi-code.json"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temp_name = tempfile.mkstemp(prefix=".kimi-code.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(updated, stream, separators=(",", ":"))
                stream.write("\n")
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        return access_token


def oauth_configured(home: Path) -> bool:
    record = oauth_token_record(home)
    if record is None or not (
        str(record.get("access_token") or "").strip()
        or str(record.get("refresh_token") or "").strip()
    ):
        return False
    try:
        text = (code_home(home) / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    return "managed:kimi-code" in text and ("oauth" in text or 'api_key = ""' in text)


def code_version() -> str:
    cached = getattr(code_version, "_cached", None)
    if isinstance(cached, str) and cached:
        return cached
    executable = shutil.which("kimi")
    version = "unknown"
    if executable:
        result = subprocess.run(
            [executable, "--version"], text=True, capture_output=True, check=False
        )
        match = re.search(
            r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?",
            result.stdout or result.stderr,
        )
        if match:
            version = match.group(0)
    setattr(code_version, "_cached", version)
    return version


def device_id(home: Path) -> str:
    path = code_home(home) / "device_id"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    value = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    return value


def identity_headers(home: Path) -> dict[str, str]:
    version = code_version()
    system = platform.system()
    os_name = "Windows_NT" if system == "Windows" else "Darwin" if system == "Darwin" else system
    release = platform.release()
    architecture = platform.machine()
    return {
        "User-Agent": f"kimi-code-cli/{version}",
        "X-Msh-Platform": "kimi_code_cli",
        "X-Msh-Version": version,
        "X-Msh-Device-Name": socket.gethostname(),
        "X-Msh-Device-Model": f"{os_name} {release} {architecture}".strip(),
        "X-Msh-Os-Version": release,
        "X-Msh-Device-Id": device_id(home),
    }


__all__ = [
    "code_home",
    "code_version",
    "device_id",
    "identity_headers",
    "oauth_access_token",
    "oauth_configured",
    "oauth_token_record",
    "refresh_oauth_access_token",
]
