"""Factory crossing normalized runtime/provider contracts into executable commands."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ciel_runtime_support.architecture import LaunchSpec, ProviderConfig, RuntimeConfig


@dataclass(frozen=True, slots=True)
class RuntimeCommandFactoryPorts:
    parse_api_keys: Callable[[Any], list[str]]
    create_adapter: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class RuntimeCommandFactory:
    ports: RuntimeCommandFactoryPorts

    def materialize(
        self,
        runtime_name: str,
        executable: str,
        environment: dict[str, str],
        provider: str,
        provider_config: dict[str, Any],
        *,
        mode: str,
        protocol: str,
        cwd: Path | None = None,
        enable_channels: bool = False,
        passthrough: Iterable[str] = (),
        options: dict[str, Any] | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        if not executable:
            raise RuntimeError(f"{runtime_name} runtime command is empty")
        normalized_provider = ProviderConfig(
            name=provider,
            base_url=str(provider_config.get("base_url") or ""),
            model=str(provider_config.get("current_model") or provider_config.get("model") or ""),
            api_keys=tuple(
                self.ports.parse_api_keys(
                    provider_config.get("api_keys") or provider_config.get("api_key") or ""
                )
            ),
            options=provider_config,
        )
        runtime = RuntimeConfig(
            name=runtime_name,
            executable=executable,
            enable_channels=enable_channels,
            options=options or {},
        )
        spec = LaunchSpec(
            runtime=runtime,
            provider=normalized_provider,
            mode=mode,  # type: ignore[arg-type]
            protocol=protocol,  # type: ignore[arg-type]
            passthrough=tuple(str(value) for value in passthrough),
            cwd=cwd,
        )
        adapter = self.ports.create_adapter(
            runtime_name,
            executable=executable,
            environment=environment,
            channel_injection=enable_channels,
        )
        command = adapter.build_command(spec)
        return list(command.argv), dict(command.env)


__all__ = ["RuntimeCommandFactory", "RuntimeCommandFactoryPorts"]
