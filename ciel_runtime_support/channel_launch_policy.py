"""Channel launch-mode decisions and Claude channel capability probe."""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from typing import Any, Protocol


RuntimeConfig = dict[str, Any]


class PassthroughOptionQuery(Protocol):
    def __call__(self, passthrough: list[str], *names: str) -> bool: ...


class ChannelSpecQuery(Protocol):
    def __call__(
        self,
        config: RuntimeConfig,
        passthrough: list[str],
        extra_specs: list[str] | None = None,
    ) -> list[str]: ...


class ChannelDeliveryModeQuery(Protocol):
    def __call__(self, config: RuntimeConfig | None = None) -> str: ...


class AuthStatusRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        text: bool,
        stdout: int,
        stderr: int,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True, slots=True)
class ChannelLaunchPorts:
    has_option: PassthroughOptionQuery
    channel_specs: ChannelSpecQuery
    delivery_mode: ChannelDeliveryModeQuery
    run_auth_status: AuthStatusRunner


@dataclass(frozen=True, slots=True)
class ChannelLaunchPolicy:
    native_router_names: frozenset[str]
    ports: ChannelLaunchPorts

    def native_passthrough_requested(self, passthrough: list[str]) -> bool:
        return self.ports.has_option(
            passthrough,
            "--channels",
            "--dangerously-load-development-channels",
        )

    def claude_args(
        self,
        config: RuntimeConfig,
        passthrough: list[str],
        extra_specs: list[str] | None = None,
        *,
        native_channel_bridge: bool = False,
    ) -> list[str]:
        if not native_channel_bridge:
            return []
        if self.native_passthrough_requested(passthrough):
            return []
        specs = [
            spec
            for spec in self.ports.channel_specs(
                config,
                passthrough,
                extra_specs,
            )
            if not self._native_router_spec(spec)
        ]
        if not specs:
            return []
        return ["--dangerously-load-development-channels", *specs]

    def native_bridge(
        self,
        use_router_mode: bool,
        config: RuntimeConfig,
        passthrough: list[str],
    ) -> bool:
        return bool(
            not use_router_mode
            and self.ports.delivery_mode(config) == "native"
            and not self.native_passthrough_requested(passthrough)
        )

    def llm_delivery(
        self,
        use_router_mode: bool,
        passthrough: list[str],
    ) -> bool:
        return bool(
            use_router_mode
            and not self.native_passthrough_requested(passthrough)
        )

    def stdin_proxy(
        self,
        use_router_mode: bool,
        passthrough: list[str],
        config: RuntimeConfig | None = None,
    ) -> bool:
        if not self.llm_delivery(use_router_mode, passthrough):
            return False
        if self.ports.has_option(passthrough, "-p", "--print"):
            return False
        claude_config = (
            config.get("claude_code")
            if isinstance(config, dict)
            else {}
        )
        if (
            isinstance(claude_config, dict)
            and claude_config.get("web_chat_session_bridge") is False
        ):
            return False
        return self.ports.delivery_mode(config) == "llm"

    @staticmethod
    def process_starts_sse(
        stdin_proxy: bool,
        native_bridge: bool,
        llm_delivery: bool,
    ) -> bool:
        return bool((stdin_proxy or native_bridge) and not llm_delivery)

    def specs_include_external_server(self, specs: list[str]) -> bool:
        for spec in specs:
            text = str(spec or "").strip()
            if not text:
                continue
            name = text.split(":", 1)[1] if ":" in text else text
            if name.strip().lower() not in self.native_router_names:
                return True
        return False

    def claude_auth_available(self, executable: str) -> tuple[bool, str]:
        try:
            process = self.ports.run_auth_status(
                [executable, "auth", "status"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return True, f"auth_status_unavailable:{type(exc).__name__}"
        if process.returncode != 0:
            return True, f"auth_status_rc_{process.returncode}"
        try:
            data = json.loads(process.stdout or "{}")
        except (json.JSONDecodeError, TypeError):
            return True, "auth_status_unparseable"
        if not bool(data.get("loggedIn")):
            return False, "not_logged_in"
        return True, str(data.get("authMethod") or "logged_in")

    def _native_router_spec(self, spec: str) -> bool:
        text = str(spec)
        return bool(
            text.startswith("server:")
            and text.split(":", 1)[1].strip().lower()
            in self.native_router_names
        )


__all__ = ["ChannelLaunchPolicy", "ChannelLaunchPorts"]
