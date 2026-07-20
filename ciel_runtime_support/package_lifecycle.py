"""Reusable npm-backed runtime package installation and update lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Callable


@dataclass(frozen=True, slots=True)
class NpmPackageLifecyclePorts:
    find_executable: Callable[[str], str | None]
    install_prefix: Callable[[], Path | None]
    install_command: Callable[[str, str, Path | None], list[str]]
    run_upgrade: Callable[..., tuple[int, str]]
    add_prefix_bin: Callable[[Path | None], None]
    latest_version: Callable[[str, str], str]
    version_newer: Callable[[str, str], bool]
    output: Callable[..., None] = print


class NpmPackageLifecycle:
    def __init__(self, ports: NpmPackageLifecyclePorts) -> None:
        self._ports = ports

    def install_if_missing(
        self,
        *,
        executable_name: str,
        label: str,
        package_spec: str,
        skip_env: str,
    ) -> str | None:
        executable = self._ports.find_executable(executable_name)
        if executable:
            return executable
        if os.environ.get(skip_env) == "1":
            return None
        npm = self._ports.find_executable("npm")
        if not npm:
            self._print(
                f"{label} executable was not found, and npm is not available to install {package_spec}."
            )
            return None
        prefix = self._ports.install_prefix()
        command = self._ports.install_command(npm, package_spec, prefix)
        self._print(f"{label} executable was not found; installing {package_spec}...")
        if prefix is not None:
            self._print(f"Installing {label} into active npm prefix: {prefix}")
        return_code, output = self._ports.run_upgrade(command, timeout=300)
        if output:
            self._print(output)
        if return_code != 0:
            self._print(f"{label} install failed ({return_code}).")
            if prefix is not None:
                self._print(
                    f"Install targeted the active install prefix ({prefix}). "
                    "If this prefix is not writable, install with the permissions used for that prefix."
                )
            return None
        self._ports.add_prefix_bin(prefix)
        executable = self._ports.find_executable(executable_name)
        if executable:
            self._print(f"{label} installed: {executable}")
        else:
            self._print(
                f"{label} install completed, but the {executable_name} executable is still not visible in PATH."
            )
        return executable

    def update_check(
        self,
        executable: str,
        *,
        executable_name: str,
        label: str,
        package_spec: str,
        skip_env: str,
        current_version: Callable[[str], str],
        enabled: bool = True,
    ) -> str:
        if not enabled or os.environ.get(skip_env) == "1":
            return executable
        self._print(f"Checking {label} update before launch...")
        current = current_version(executable)
        if current:
            self._print(f"Current {label} version: {current}")
        npm = self._ports.find_executable("npm")
        if not npm:
            self._print(f"{label} update check skipped: npm was not found.")
            return executable
        latest = self._ports.latest_version(npm, package_spec)
        if not latest:
            self._print(f"{label} update check could not read the latest npm version; continuing.")
            return executable
        if current and not self._ports.version_newer(latest, current):
            self._print(f"{label} is up to date ({current}).")
            return executable
        self._print(f"{label} update available: {current or 'unknown'} -> {latest}; upgrading automatically.")
        prefix = self._ports.install_prefix()
        if prefix is not None:
            self._print(f"Updating {label} in active npm prefix: {prefix}")
        return_code, output = self._ports.run_upgrade(
            self._ports.install_command(npm, package_spec, prefix),
            timeout=300,
        )
        if output:
            self._print(output)
        if return_code != 0:
            self._print(f"{label} update failed ({return_code}); continuing with current version.")
            return executable
        self._ports.add_prefix_bin(prefix)
        updated = self._ports.find_executable(executable_name) or executable
        new_version = current_version(updated)
        if new_version:
            self._print(f"{label} version after update: {new_version}")
        return updated

    def _print(self, message: str) -> None:
        self._ports.output(message, flush=True)


@dataclass(frozen=True, slots=True)
class SelfUpdatePorts:
    running_from_package: Callable[[], bool]
    find_executable: Callable[[str], str | None]
    latest_version: Callable[[str, str], str]
    version_newer: Callable[[str, str], bool]
    package_root: Callable[[], Path | None]
    prefix_from_root: Callable[[Path], Path | None]
    install_command: Callable[[str, str, Path | None], list[str]]
    forced_environment: Callable[[], dict[str, str]]
    restart: Callable[..., None]
    output: Callable[..., None] = print


class SelfUpdateLifecycle:
    PACKAGE_SPEC = "@oneciel-ai/ciel-runtime@latest"

    def __init__(self, current_version: str, ports: SelfUpdatePorts) -> None:
        self._current_version = current_version
        self._ports = ports

    def run(self, enabled: bool = True) -> bool:
        if not enabled or os.environ.get("CIEL_RUNTIME_SKIP_SELF_UPDATE") == "1":
            return False
        raw_check = os.environ.get("CIEL_RUNTIME_SELF_UPDATE_CHECK")
        if raw_check is not None and raw_check.strip().lower() in {"0", "false", "no", "off"}:
            return False
        if not self._ports.running_from_package():
            return False
        npm = self._ports.find_executable("npm")
        if not npm:
            return False
        latest = self._ports.latest_version(npm, self.PACKAGE_SPEC)
        if not latest or not self._ports.version_newer(latest, self._current_version):
            return False
        self._print(
            f"Ciel Runtime update available: {self._current_version} -> {latest}; upgrading automatically."
        )
        package_root = self._ports.package_root()
        prefix = self._ports.prefix_from_root(package_root) if package_root else None
        command = self._ports.install_command(npm, self.PACKAGE_SPEC, prefix)
        if prefix is not None:
            self._print(f"Updating current Ciel Runtime install prefix: {prefix}")
        try:
            update = subprocess.run(
                command,
                text=True,
                input="y\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=self._ports.forced_environment(),
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            self._print("Ciel Runtime update timed out; continuing with current version.")
            return False
        except (OSError, subprocess.SubprocessError) as exc:
            self._print(f"Ciel Runtime update failed ({type(exc).__name__}); continuing.")
            return False
        output = (update.stdout or "").strip()
        if output:
            self._print(output)
        if update.returncode != 0:
            self._print(
                f"Ciel Runtime update exited with {update.returncode}; continuing with current version."
            )
            if prefix is not None:
                self._print(
                    f"Update targeted the active install prefix ({prefix}). "
                    "If this prefix is not writable, reinstall or update with the permissions used for that prefix."
                )
            return False
        self._print("Ciel Runtime updated. Restarting with the new version...")
        try:
            self._ports.restart(npm, package_root=package_root)
        except SystemExit:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            self._print(
                f"Restart failed ({type(exc).__name__}); continuing with the current process."
            )
        return True

    def _print(self, message: str) -> None:
        self._ports.output(message, flush=True)


__all__ = [
    "NpmPackageLifecycle",
    "NpmPackageLifecyclePorts",
    "SelfUpdateLifecycle",
    "SelfUpdatePorts",
]
