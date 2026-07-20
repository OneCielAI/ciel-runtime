"""Restart the active Ciel Runtime installation after self-update."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path


def running_from_npm_package(path: Path, environ: MutableMapping[str, str]) -> bool:
    if environ.get("CIEL_RUNTIME_NPM_MODE") is not None:
        return True
    normalized = str(path.resolve(strict=False)).replace("\\", "/")
    return "/node_modules/@oneciel-ai/ciel-runtime/" in normalized


def forced_upgrade_environment(environ: MutableMapping[str, str]) -> dict[str, str]:
    result = dict(environ)
    result.update(CI="1", NPM_CONFIG_YES="true", npm_config_yes="true")
    result.setdefault("NPM_CONFIG_UPDATE_NOTIFIER", "false")
    result.setdefault("npm_config_update_notifier", "false")
    return result


@dataclass(frozen=True, slots=True)
class RuntimeRestartSettings:
    argv: Sequence[str]
    python_executable: str
    environ: MutableMapping[str, str]


@dataclass(frozen=True, slots=True)
class RuntimeRestartPorts:
    current_package_root: Callable[[], Path | None]
    global_package_root: Callable[[str], Path | None]
    find_executable: Callable[[str], str | None]
    execv: Callable[[str, list[str]], object]
    call: Callable[..., int]


@dataclass(frozen=True, slots=True)
class RuntimeRestartService:
    settings: RuntimeRestartSettings
    ports: RuntimeRestartPorts

    def user_args(self) -> list[str]:
        arguments = list(self.settings.argv[1:])
        return arguments[1:] if arguments and arguments[0] == "cli" else arguments

    def restart(self, npm: str, package_root: Path | None = None) -> None:
        self.settings.environ["CIEL_RUNTIME_SKIP_SELF_UPDATE"] = "1"
        user_args = self.user_args()
        root = (
            package_root
            or self.ports.current_package_root()
            or self.ports.global_package_root(npm)
        )
        package_script = root / "ciel_runtime.py" if root else None
        if package_script and package_script.exists():
            self.ports.execv(
                self.settings.python_executable,
                [self.settings.python_executable, str(package_script), "cli", *user_args],
            )
            return
        launcher = self.ports.find_executable("ciel-runtime")
        if launcher:
            raise SystemExit(
                self.ports.call(
                    [launcher, *user_args], env=dict(self.settings.environ)
                )
            )
        self.ports.execv(
            self.settings.python_executable,
            [self.settings.python_executable, *self.settings.argv],
        )


__all__ = [
    "RuntimeRestartPorts",
    "RuntimeRestartService",
    "RuntimeRestartSettings",
    "forced_upgrade_environment",
    "running_from_npm_package",
]
