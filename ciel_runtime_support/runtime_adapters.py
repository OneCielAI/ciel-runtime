"""Concrete runtime adapter used while legacy launchers are incrementally decomposed."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .architecture import LaunchSpec, RuntimeAdapter, RuntimeCommand


@dataclass(frozen=True)
class CliRuntimeAdapter(RuntimeAdapter):
    """Materialize an already-normalized CLI launch through the runtime contract."""

    name: str
    executable: str
    environment: Mapping[str, str] = field(default_factory=dict)
    channel_injection: bool = False

    def find_executable(self) -> Path | None:
        return Path(self.executable) if self.executable else None

    def build_command(self, spec: LaunchSpec) -> RuntimeCommand:
        executable = spec.runtime.executable or self.executable
        if not executable:
            raise RuntimeError(f"{self.name} runtime executable is unavailable")
        prefix = spec.runtime.options.get("prefix_args", ())
        prefix_args = tuple(str(value) for value in prefix) if isinstance(prefix, (list, tuple)) else ()
        return RuntimeCommand(
            argv=(str(executable), *prefix_args, *spec.passthrough),
            env=dict(self.environment),
            cwd=spec.cwd,
        )

    def mcp_config_paths(self, spec: LaunchSpec) -> tuple[Path, ...]:
        return spec.runtime.mcp_config_paths

    def supports_channel_injection(self, spec: LaunchSpec) -> bool:
        return bool(self.channel_injection and spec.runtime.enable_channels)


__all__ = ["CliRuntimeAdapter"]
