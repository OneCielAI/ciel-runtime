"""Concrete runtime adapter used while legacy launchers are incrementally decomposed."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .architecture import LaunchSpec, RuntimeAdapter, RuntimeCommand
from .registry import AdapterRegistry


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


class ClaudeRuntimeAdapter(CliRuntimeAdapter):
    def build_command(self, spec: LaunchSpec) -> RuntimeCommand:
        options = spec.runtime.options
        argv = [str(spec.runtime.executable or self.executable), "--dangerously-skip-permissions"]
        if options.get("bypass_permission_mode"):
            argv.extend(("--permission-mode", "bypassPermissions"))
        disallowed = str(options.get("disallowed_tools") or "")
        if disallowed:
            argv.extend(("--disallowedTools", disallowed))
        model = str(options.get("model") or "")
        if model:
            argv.extend(("--model", model))
        argv.extend(str(value) for value in options.get("extra_args", ()))
        if options.get("passthrough_boundary"):
            argv.append("--")
        argv.extend(spec.passthrough)
        return RuntimeCommand(argv=tuple(argv), env=dict(self.environment), cwd=spec.cwd)


class CodexRuntimeAdapter(CliRuntimeAdapter):
    def build_command(self, spec: LaunchSpec) -> RuntimeCommand:
        options = spec.runtime.options
        argv = [str(spec.runtime.executable or self.executable)]
        argv.extend(str(value) for value in options.get("yolo_args", ()))
        if spec.mode == "native":
            argv.extend(str(value) for value in options.get("model_args", ()))
        elif spec.mode == "routed":
            argv.extend(str(value) for value in options.get("routed_config_args", ()))
            argv.extend(str(value) for value in options.get("model_args", ()))
        else:
            argv.extend(str(value) for value in options.get("router_config_args", ()))
        if spec.mode != "native":
            argv.extend(str(value) for value in options.get("model_catalog_args", ()))
        for key in ("alternate_screen_args", "mcp_args", "model_alias_args"):
            argv.extend(str(value) for value in options.get(key, ()))
        argv.extend(spec.passthrough)
        return RuntimeCommand(argv=tuple(argv), env=dict(self.environment), cwd=spec.cwd)


class AgyRuntimeAdapter(CliRuntimeAdapter):
    def build_command(self, spec: LaunchSpec) -> RuntimeCommand:
        options = spec.runtime.options
        argv = [str(spec.runtime.executable or self.executable)]
        argv.extend(str(value) for value in options.get("dangerous_args", ()))
        argv.extend(spec.passthrough)
        return RuntimeCommand(argv=tuple(argv), env=dict(self.environment), cwd=spec.cwd)


RUNTIME_ADAPTERS: AdapterRegistry[RuntimeAdapter] = AdapterRegistry()
RUNTIME_ADAPTERS.register("claude", lambda **kwargs: ClaudeRuntimeAdapter(name="claude", **kwargs))
RUNTIME_ADAPTERS.register("codex", lambda **kwargs: CodexRuntimeAdapter(name="codex", **kwargs))
RUNTIME_ADAPTERS.register("agy", lambda **kwargs: AgyRuntimeAdapter(name="agy", **kwargs))


__all__ = [
    "RUNTIME_ADAPTERS",
    "AgyRuntimeAdapter",
    "ClaudeRuntimeAdapter",
    "CliRuntimeAdapter",
    "CodexRuntimeAdapter",
]
