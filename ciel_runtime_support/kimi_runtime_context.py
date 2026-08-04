"""Kimi Code installation, OAuth identity, and launch bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class KimiIdentityPorts:
    home: Path
    code_home: Callable[[Path], Path]
    token_record: Callable[[Path], dict[str, Any] | None]
    access_token: Callable[[Path], str | None]
    configured: Callable[[Path], bool]


@dataclass(frozen=True, slots=True)
class KimiProcessPorts:
    find_executable: Callable[[str], str | None]
    run: Callable[..., Any]
    call: Callable[..., int]
    print_line: Callable[..., None]
    environment: dict[str, str]
    augment_path: Callable[[dict[str, str]], str]


@dataclass(frozen=True, slots=True)
class KimiConfigurationPorts:
    load: Callable[[], dict[str, Any]]
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    provider_has_key: Callable[[str, dict[str, Any]], bool]
    current_alias: Callable[[dict[str, Any]], str]
    positive_int: Callable[[Any], int | None]
    clear_api_key: Callable[[str], list[str]]
    router_base: str


@dataclass(frozen=True, slots=True)
class KimiLifecyclePorts:
    install: Callable[[], str]
    oauth_configured: Callable[[], bool]
    oauth_login: Callable[[], int]
    start_router: Callable[[], Any]
    run_with_router: Callable[[Callable[[], int], bool], int]


@dataclass(frozen=True, slots=True)
class KimiRuntimeContext:
    identity: KimiIdentityPorts
    process: KimiProcessPorts
    config: KimiConfigurationPorts
    lifecycle: KimiLifecyclePorts

    def code_home(self) -> Path:
        return self.identity.code_home(self.identity.home)

    def oauth_token_record(self) -> dict[str, Any] | None:
        return self.identity.token_record(self.identity.home)

    def oauth_access_token(self) -> str | None:
        return self.identity.access_token(self.identity.home)

    def oauth_configured(self) -> bool:
        return self.identity.configured(self.identity.home)

    def install_if_missing(self) -> str:
        executable = self.process.find_executable("kimi")
        if executable:
            return executable
        npm = self.process.find_executable("npm")
        if not npm:
            raise RuntimeError(
                "Kimi Code CLI is missing; install @moonshot-ai/kimi-code "
                "(Node.js 22.19+)."
            )
        self.process.print_line(
            "Installing official Kimi Code CLI (@moonshot-ai/kimi-code)...",
            flush=True,
        )
        result = self.process.run(
            [npm, "install", "-g", "@moonshot-ai/kimi-code"], check=False
        )
        if result.returncode:
            raise RuntimeError(
                f"Kimi Code CLI installation failed (exit {result.returncode})."
            )
        executable = self.process.find_executable("kimi")
        if not executable:
            raise RuntimeError(
                "Kimi Code CLI installed but 'kimi' is not available on PATH."
            )
        return executable

    def oauth_login(self) -> int:
        return self.process.call([self.lifecycle.install(), "login"])

    def oauth_action(self, action: str) -> list[str]:
        if action != "login":
            return [f"Unsupported Kimi OAuth action: {action}"]
        try:
            code = self.lifecycle.oauth_login()
        except Exception as exc:
            return [f"Kimi OAuth login failed: {type(exc).__name__}: {exc}"]
        if code:
            return [f"Kimi OAuth login exited with status {code}."]
        if not self.lifecycle.oauth_configured():
            return [
                "Kimi OAuth login exited successfully, but no usable credential "
                "was detected; the existing Kimi API key was not cleared."
            ]
        messages = [
            "Kimi OAuth login completed in the official Kimi Code credential store."
        ]
        messages.extend(self.config.clear_api_key("kimi"))
        return messages

    def launch(self, passthrough: list[str]) -> int:
        cfg = self.config.load()
        provider, pcfg = self.config.current_provider(cfg)
        if provider != "kimi":
            self.process.print_line(
                "Launch Kimi Code requires Kimi Native or Kimi Routed provider.",
                flush=True,
            )
            return 2
        executable = self.lifecycle.install()
        routed = bool(pcfg.get("route_through_router"))
        env = self.process.environment.copy()
        env["PATH"] = self.process.augment_path(env)
        if not routed:
            if not self.lifecycle.oauth_configured():
                self.process.print_line(
                    "Kimi Code OAuth login is required for first launch.", flush=True
                )
                if self.process.call([executable, "login"], env=env):
                    return 1
            return self.process.call([executable, *passthrough], env=env)
        if (
            not self.config.provider_has_key(provider, pcfg)
            and not self.lifecycle.oauth_configured()
        ):
            self.process.print_line(
                "Kimi Routed requires Kimi OAuth login or a Kimi API key.",
                flush=True,
            )
            if self.process.call([executable, "login"], env=env):
                return 1
        manage_router = bool(self.lifecycle.start_router())
        env.update(
            {
                "KIMI_MODEL_NAME": self.config.current_alias(cfg)
                or str(pcfg.get("current_model") or "kimi-for-coding"),
                "KIMI_MODEL_API_KEY": "ciel-runtime-router-local-key",
                "KIMI_MODEL_PROVIDER_TYPE": "openai",
                "KIMI_MODEL_BASE_URL": f"{self.config.router_base.rstrip('/')}/v1",
                "KIMI_MODEL_MAX_CONTEXT_SIZE": str(
                    self.config.positive_int(pcfg.get("context_window")) or 262144
                ),
                "KIMI_MODEL_THINKING_EFFORT": str(
                    pcfg.get("effort_level") or "high"
                ),
            }
        )
        return self.lifecycle.run_with_router(
            lambda: self.process.call([executable, *passthrough], env=env),
            manage_router,
        )


@dataclass(frozen=True, slots=True)
class KimiRuntimeCompatibilityApi:
    context: Callable[[], KimiRuntimeContext]

    def code_home(self) -> Path:
        return self.context().code_home()

    def oauth_token_record(self) -> dict[str, Any] | None:
        return self.context().oauth_token_record()

    def oauth_access_token(self) -> str | None:
        return self.context().oauth_access_token()

    def oauth_configured(self) -> bool:
        return self.context().oauth_configured()

    def install_if_missing(self) -> str:
        return self.context().install_if_missing()

    def oauth_login(self) -> int:
        return self.context().oauth_login()

    def oauth_action(self, action: str) -> list[str]:
        return self.context().oauth_action(action)

    def launch(self, passthrough: list[str]) -> int:
        return self.context().launch(passthrough)


__all__ = [
    "KimiConfigurationPorts",
    "KimiIdentityPorts",
    "KimiLifecyclePorts",
    "KimiProcessPorts",
    "KimiRuntimeCompatibilityApi",
    "KimiRuntimeContext",
]
