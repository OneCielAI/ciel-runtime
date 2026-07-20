"""Reusable npm-backed runtime package installation and update lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
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


__all__ = ["NpmPackageLifecycle", "NpmPackageLifecyclePorts"]
