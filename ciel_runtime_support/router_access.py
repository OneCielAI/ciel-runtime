"""Router bind, authentication, token persistence, and configuration policy."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class RouterRequest(Protocol):
    client_address: tuple[Any, ...]
    headers: Any


class RouterResponse(RouterRequest, Protocol):
    wfile: Any

    def send_response(self, status: int) -> Any: ...

    def send_header(self, name: str, value: str) -> Any: ...

    def end_headers(self) -> Any: ...


def is_loopback_address(host: str | None) -> bool:
    normalized = (host or "").strip().lower()
    return normalized in {"127.0.0.1", "::1", "localhost"} or normalized.startswith(
        "127."
    )


def router_request_bearer_token(handler: RouterRequest) -> str:
    try:
        authorization = str(
            handler.headers.get("authorization")
            or handler.headers.get("Authorization")
            or ""
        )
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return str(handler.headers.get("x-ciel-runtime-token") or "").strip()
    except Exception:
        return ""


@dataclass(frozen=True, slots=True)
class RouterAccessPolicy:
    environ: Mapping[str, str]
    parse_bool: Callable[[Any, bool], bool]
    parse_env_bool: Callable[[str | None, bool | None], bool | None]
    load_config: Callable[[], dict[str, Any]]

    def external_access_enabled(self, config: dict[str, Any] | None = None) -> bool:
        configured = self.parse_env_bool(
            self.environ.get("CIEL_RUNTIME_ROUTER_DEBUG_EXTERNAL"), None
        )
        if configured is not None:
            return configured
        current = self.load_config() if config is None else config
        return self.parse_bool(
            current.get("router_debug_external_access"), False
        ) and self.parse_bool(
            current.get("router_debug_external_access_confirmed"), False
        )

    def bind_host(self, config: dict[str, Any] | None = None) -> str:
        override = (
            self.environ.get("CIEL_RUNTIME_ROUTER_BIND_HOST")
            or self.environ.get("CIEL_RUNTIME_ROUTER_HOST")
            or ""
        ).strip()
        if override:
            return override
        return "0.0.0.0" if self.external_access_enabled(config) else "127.0.0.1"

    def request_allowed(
        self,
        handler: RouterRequest,
        config: dict[str, Any] | None,
        token_provider: Callable[[], str],
    ) -> bool:
        try:
            if is_loopback_address(str(handler.client_address[0])):
                return True
        except Exception:
            return False
        if not self.external_access_enabled(config):
            return False
        expected = token_provider()
        supplied = router_request_bearer_token(handler)
        return bool(expected and supplied and hmac.compare_digest(expected, supplied))


@dataclass(frozen=True, slots=True)
class RouterExternalTokenRepository:
    path: Path
    config_dir: Path
    environ: MutableMapping[str, str]

    def get(self) -> str:
        configured = str(self.environ.get("CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN") or "").strip()
        if configured:
            return configured
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def ensure(self) -> str:
        existing = self.get()
        if existing:
            return existing
        self.config_dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        temporary = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        temporary.write_text(token + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        return token


@dataclass(frozen=True, slots=True)
class RouterAccessMutationPorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], Any]
    clear_model_cache: Callable[[], Any]
    ensure_token: Callable[[], str]


@dataclass(frozen=True, slots=True)
class RouterAccessConfigService:
    policy: RouterAccessPolicy
    ports: RouterAccessMutationPorts

    def set_external_access(self, value: Any) -> list[str]:
        config = self.ports.load_config()
        enabled = self.policy.parse_bool(value, False)
        config["router_debug_external_access"] = enabled
        config["router_debug_external_access_confirmed"] = enabled
        self.ports.save_config(config)
        self.ports.clear_model_cache()
        bind = self.policy.bind_host(config)
        if enabled:
            token = self.ports.ensure_token()
            return [
                "Router debug external access: on.",
                f"Router bind host for next launch: {bind}.",
                "External clients must authenticate with Authorization: Bearer <token>.",
                f"External access token: {token}",
            ]
        return [
            "Router debug external access: off.",
            "External clients are denied immediately; next launch binds to 127.0.0.1 unless overridden by environment.",
        ]


@dataclass(frozen=True, slots=True)
class RouterAccessHttpController:
    request_allowed: Callable[
        [RouterRequest, dict[str, Any] | None],
        bool,
    ]
    external_access_enabled: Callable[
        [dict[str, Any] | None],
        bool,
    ]

    def reject_external_request(
        self,
        handler: RouterResponse,
        config: dict[str, Any] | None = None,
    ) -> bool:
        if self.request_allowed(handler, config):
            return False
        external_enabled = self.external_access_enabled(config)
        status = 401 if external_enabled else 403
        message = (
            "ciel-runtime router external authentication is required."
            if external_enabled
            else "ciel-runtime router external debug access is off."
        )
        payload = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": (
                        "unauthorized"
                        if status == 401
                        else "forbidden"
                    ),
                    "message": message,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        handler.send_response(status)
        handler.send_header(
            "content-type",
            "application/json; charset=utf-8",
        )
        handler.send_header("content-length", str(len(payload)))
        if status == 401:
            handler.send_header(
                "www-authenticate",
                'Bearer realm="ciel-runtime"',
            )
        handler.end_headers()
        handler.wfile.write(payload)
        return True


__all__ = [
    "RouterAccessConfigService",
    "RouterAccessHttpController",
    "RouterAccessMutationPorts",
    "RouterAccessPolicy",
    "RouterExternalTokenRepository",
    "is_loopback_address",
    "router_request_bearer_token",
]
