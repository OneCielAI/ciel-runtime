"""GitHub Device OAuth and short-lived Copilot token management."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_GITHUB_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
GITHUB_API_VERSION = "2022-11-28"
COPILOT_API_VERSION = "2025-04-01"
COPILOT_VSCODE_VERSION = "1.128.0"
COPILOT_CHAT_VERSION = "0.43.0"
COPILOT_USER_AGENT = f"GitHubCopilotChat/{COPILOT_CHAT_VERSION}"


@dataclass(frozen=True, slots=True)
class GitHubCopilotOAuthCredentials:
    github_access_token: str = ""
    copilot_token: str = ""
    copilot_token_expires_at: int = 0
    github_login: str = ""

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any] | None
    ) -> GitHubCopilotOAuthCredentials:
        value = value or {}
        try:
            expires_at = int(value.get("copilot_token_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        return cls(
            github_access_token=str(value.get("github_access_token") or ""),
            copilot_token=str(value.get("copilot_token") or ""),
            copilot_token_expires_at=expires_at,
            github_login=str(value.get("github_login") or ""),
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "github_access_token": self.github_access_token,
            "copilot_token": self.copilot_token,
            "copilot_token_expires_at": self.copilot_token_expires_at,
            "github_login": self.github_login,
        }

    def copilot_token_is_fresh(
        self, now: float, refresh_lead_seconds: int = 300
    ) -> bool:
        return bool(
            self.copilot_token
            and self.copilot_token_expires_at > int(now) + refresh_lead_seconds
        )


@dataclass(frozen=True, slots=True)
class GitHubCopilotOAuthRepository:
    path: Path

    def load(self) -> GitHubCopilotOAuthCredentials:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return GitHubCopilotOAuthCredentials()
        return GitHubCopilotOAuthCredentials.from_mapping(
            value if isinstance(value, dict) else {}
        )

    def save(self, credentials: GitHubCopilotOAuthCredentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(credentials.as_mapping(), ensure_ascii=False),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)

    def clear(self) -> bool:
        if not self.path.exists():
            return False
        self.path.unlink()
        return True


@dataclass(frozen=True, slots=True)
class GitHubCopilotOAuthHttp:
    urlopen: Callable[..., Any] = urllib.request.urlopen
    timeout_seconds: float = 30.0

    def post_form(self, url: str, values: Mapping[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(values).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        return self._json(request)

    def get_json(
        self, url: str, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        return self._json(
            urllib.request.Request(url, headers=dict(headers), method="GET")
        )

    def _json(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with self.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub OAuth HTTP {exc.code}: {raw[:500]}"
            ) from exc
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError("GitHub OAuth returned a non-object response.")
        return value


@dataclass(frozen=True, slots=True)
class GitHubCopilotOAuthClient:
    http: GitHubCopilotOAuthHttp
    client_id: str = DEFAULT_GITHUB_COPILOT_CLIENT_ID
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time

    def request_device_code(self) -> dict[str, Any]:
        response = self.http.post_form(
            GITHUB_DEVICE_CODE_URL,
            {"client_id": self.client_id, "scope": "read:user"},
        )
        self._raise_oauth_error(response)
        for field in ("device_code", "user_code", "verification_uri"):
            if not str(response.get(field) or "").strip():
                raise RuntimeError(
                    f"GitHub device authorization omitted {field}."
                )
        return response

    def poll_access_token(
        self,
        device: Mapping[str, Any],
        on_pending: Callable[[int], None] | None = None,
    ) -> str:
        device_code = str(device.get("device_code") or "")
        interval = max(1, int(device.get("interval") or 5))
        expires_in = max(interval, int(device.get("expires_in") or 900))
        deadline = self.now() + expires_in
        attempt = 0
        while self.now() < deadline:
            self.sleep(interval)
            attempt += 1
            response = self.http.post_form(
                GITHUB_ACCESS_TOKEN_URL,
                {
                    "client_id": self.client_id,
                    "device_code": device_code,
                    "grant_type": (
                        "urn:ietf:params:oauth:grant-type:device_code"
                    ),
                },
            )
            access_token = str(response.get("access_token") or "").strip()
            if access_token:
                return access_token
            error = str(response.get("error") or "").strip()
            if error == "authorization_pending":
                if on_pending is not None:
                    on_pending(attempt)
                continue
            if error == "slow_down":
                interval += 5
                continue
            self._raise_oauth_error(response)
        raise RuntimeError("GitHub device authorization expired.")

    def exchange_copilot_token(
        self, github_access_token: str
    ) -> tuple[str, int]:
        response = self.http.get_json(
            GITHUB_COPILOT_TOKEN_URL,
            {
                "Authorization": f"token {github_access_token}",
                "Accept": "application/json",
                "User-Agent": COPILOT_USER_AGENT,
                "Editor-Version": f"vscode/{COPILOT_VSCODE_VERSION}",
                "Editor-Plugin-Version": (
                    f"copilot-chat/{COPILOT_CHAT_VERSION}"
                ),
                "X-GitHub-Api-Version": COPILOT_API_VERSION,
            },
        )
        token = str(response.get("token") or "").strip()
        try:
            expires_at = int(response.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if not token or not expires_at:
            raise RuntimeError(
                "GitHub did not return a usable Copilot token. "
                "Confirm that this account has Copilot access."
            )
        return token, expires_at

    def github_login(self, github_access_token: str) -> str:
        response = self.http.get_json(
            GITHUB_USER_URL,
            {
                "Authorization": f"Bearer {github_access_token}",
                "Accept": "application/json",
                "User-Agent": COPILOT_USER_AGENT,
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
        )
        return str(response.get("login") or "").strip()

    @staticmethod
    def _raise_oauth_error(response: Mapping[str, Any]) -> None:
        error = str(response.get("error") or "").strip()
        if not error:
            return
        detail = str(response.get("error_description") or error)
        raise RuntimeError(f"GitHub OAuth failed: {detail}")


@dataclass(frozen=True, slots=True)
class GitHubCopilotOAuthService:
    repository: GitHubCopilotOAuthRepository
    client: GitHubCopilotOAuthClient
    now: Callable[[], float] = time.time

    def current_token(self) -> str:
        credentials = self.repository.load()
        if credentials.copilot_token_is_fresh(self.now()):
            return credentials.copilot_token
        if not credentials.github_access_token:
            return ""
        token, expires_at = self.client.exchange_copilot_token(
            credentials.github_access_token
        )
        refreshed = GitHubCopilotOAuthCredentials(
            github_access_token=credentials.github_access_token,
            copilot_token=token,
            copilot_token_expires_at=expires_at,
            github_login=credentials.github_login,
        )
        self.repository.save(refreshed)
        return token

    def force_refresh(self) -> str:
        credentials = self.repository.load()
        if not credentials.github_access_token:
            return ""
        token, expires_at = self.client.exchange_copilot_token(
            credentials.github_access_token
        )
        self.repository.save(
            GitHubCopilotOAuthCredentials(
                github_access_token=credentials.github_access_token,
                copilot_token=token,
                copilot_token_expires_at=expires_at,
                github_login=credentials.github_login,
            )
        )
        return token

    def login(
        self,
        show_device: Callable[[str, str], None],
        on_pending: Callable[[int], None] | None = None,
    ) -> GitHubCopilotOAuthCredentials:
        device = self.client.request_device_code()
        show_device(
            str(device["verification_uri"]),
            str(device["user_code"]),
        )
        access_token = self.client.poll_access_token(device, on_pending)
        copilot_token, expires_at = self.client.exchange_copilot_token(
            access_token
        )
        login = self.client.github_login(access_token)
        credentials = GitHubCopilotOAuthCredentials(
            github_access_token=access_token,
            copilot_token=copilot_token,
            copilot_token_expires_at=expires_at,
            github_login=login,
        )
        self.repository.save(credentials)
        return credentials

    def status(self) -> tuple[bool, str, int]:
        credentials = self.repository.load()
        return (
            bool(credentials.github_access_token),
            credentials.github_login,
            credentials.copilot_token_expires_at,
        )

    def logout(self) -> bool:
        return self.repository.clear()


__all__ = [
    "COPILOT_API_VERSION",
    "COPILOT_CHAT_VERSION",
    "COPILOT_USER_AGENT",
    "COPILOT_VSCODE_VERSION",
    "DEFAULT_GITHUB_COPILOT_CLIENT_ID",
    "GITHUB_ACCESS_TOKEN_URL",
    "GITHUB_COPILOT_TOKEN_URL",
    "GITHUB_DEVICE_CODE_URL",
    "GitHubCopilotOAuthClient",
    "GitHubCopilotOAuthCredentials",
    "GitHubCopilotOAuthHttp",
    "GitHubCopilotOAuthRepository",
    "GitHubCopilotOAuthService",
]
