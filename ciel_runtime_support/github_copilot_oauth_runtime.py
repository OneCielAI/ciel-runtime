"""Runtime application service for the GitHub Copilot OAuth provider."""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .github_copilot_oauth import (
    DEFAULT_GITHUB_COPILOT_CLIENT_ID,
    GitHubCopilotOAuthClient,
    GitHubCopilotOAuthHttp,
    GitHubCopilotOAuthRepository,
    GitHubCopilotOAuthService,
)
from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER


@dataclass(frozen=True, slots=True)
class GitHubCopilotOAuthRuntimePorts:
    clear_model_cache: Callable[[], None]
    log: Callable[[str, str], Any]
    provider_headers: Callable[[str, dict[str, Any]], dict[str, str]]
    network_open: Callable[..., Any]


class GitHubCopilotOAuthRuntime:
    provider = "github-copilot-oauth"

    def __init__(
        self,
        config_dir: Path,
        ports: GitHubCopilotOAuthRuntimePorts,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._path = config_dir / "github-copilot-oauth.json"
        self._ports = ports
        self._environ = os.environ if environ is None else environ

    def service(self) -> GitHubCopilotOAuthService:
        client_id = str(
            self._environ.get("CIEL_RUNTIME_GITHUB_COPILOT_CLIENT_ID")
            or DEFAULT_GITHUB_COPILOT_CLIENT_ID
        ).strip()
        return GitHubCopilotOAuthService(
            repository=GitHubCopilotOAuthRepository(self._path),
            client=GitHubCopilotOAuthClient(
                http=GitHubCopilotOAuthHttp(),
                client_id=client_id,
            ),
        )

    def token(self) -> str:
        try:
            return self.service().current_token()
        except Exception as exc:
            self._ports.log(
                "WARN",
                "github_copilot_oauth_token_unavailable "
                f"error={type(exc).__name__}: {exc}",
            )
            return ""

    def force_refresh(self) -> str:
        try:
            return self.service().force_refresh()
        except Exception as exc:
            self._ports.log(
                "WARN",
                "github_copilot_oauth_force_refresh_failed "
                f"error={type(exc).__name__}: {exc}",
            )
            return ""

    def open(
        self,
        request: urllib.request.Request,
        timeout: float,
        provider: str | None,
        config: dict[str, Any] | None,
    ) -> Any:
        try:
            return self._ports.network_open(
                request, timeout, provider, config, self._ports.log
            )
        except urllib.error.HTTPError as exc:
            if (
                provider != self.provider
                or exc.code not in (401, 403)
                or config is None
            ):
                raise
            if not self.force_refresh():
                raise
            if config.get(REMOTE_BRIDGE_CONFIG_MARKER) is True:
                self._ports.log(
                    "INFO",
                    "github_copilot_oauth_token_refreshed_after_auth_error "
                    f"status={exc.code} remote_replay=false",
                )
                raise
            headers = dict(request.header_items())
            headers.update(self._ports.provider_headers(provider, config))
            retry = urllib.request.Request(
                request.full_url,
                data=request.data,
                headers=headers,
                method=request.get_method(),
            )
            self._ports.log(
                "INFO",
                "github_copilot_oauth_token_refreshed_after_auth_error "
                f"status={exc.code}",
            )
            return self._ports.network_open(
                retry, timeout, provider, config, self._ports.log
            )

    def action(self, action: str) -> list[str]:
        service = self.service()
        action = str(action or "status").strip().lower()
        if action == "logout":
            removed = service.logout()
            self._ports.clear_model_cache()
            return [
                "GitHub Copilot OAuth credentials cleared."
                if removed
                else "GitHub Copilot OAuth credentials were not stored."
            ]
        if action == "status":
            connected, login, expires_at = service.status()
            if not connected:
                return [
                    "GitHub Copilot OAuth: not connected. "
                    "Run: ciel-runtimectl copilot-oauth login"
                ]
            account = f" ({login})" if login else ""
            expiry = (
                time.strftime(
                    "%Y-%m-%d %H:%M:%S %Z",
                    time.localtime(expires_at),
                )
                if expires_at
                else "unknown"
            )
            return [
                f"GitHub Copilot OAuth: connected{account}; "
                f"Copilot token expires {expiry}."
            ]
        credentials = service.login(
            self._show_device,
            on_pending=lambda attempt: (
                print("Waiting for GitHub authorization...", flush=True)
                if attempt % 6 == 0
                else None
            ),
        )
        self._ports.clear_model_cache()
        account = (
            f" as {credentials.github_login}"
            if credentials.github_login
            else ""
        )
        return [
            f"GitHub Copilot OAuth connected{account}. "
            "Select provider: ciel-runtimectl provider github-copilot-oauth"
        ]

    def panel_rows(
        self, provider: str
    ) -> tuple[list[str], list[str]] | None:
        if provider != self.provider:
            return None
        connected, login, _expires_at = self.service().status()
        status = (
            f"Connected as {login}"
            if connected and login
            else "Connected"
            if connected
            else "Not connected"
        )
        return (
            [
                f"OAuth status: {status}",
                "Login with GitHub Device Code",
                "Refresh OAuth status",
                "Logout and clear OAuth credentials",
                "Back",
            ],
            [
                "oauth-status",
                "oauth-login",
                "oauth-status",
                "oauth-logout",
                "back",
            ],
        )

    @staticmethod
    def _show_device(verification_uri: str, user_code: str) -> None:
        print(
            "GitHub Copilot OAuth device login\n"
            f" Open: {verification_uri}\n"
            f" Code: {user_code}",
            flush=True,
        )
        try:
            webbrowser.open(verification_uri)
        except Exception:
            pass


__all__ = [
    "GitHubCopilotOAuthRuntime",
    "GitHubCopilotOAuthRuntimePorts",
]
