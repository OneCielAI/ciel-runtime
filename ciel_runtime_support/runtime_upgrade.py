"""Coordinated quiet upgrades for the runtime CLI toolchain."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeUpgradeSettings:
    runtime_version: str
    environ: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RuntimeUpgradeNpmPorts:
    find_executable: Callable[[str], str | None]
    latest_version: Callable[[str, str], str]
    version_newer: Callable[[str, str], bool]
    current_package_root: Callable[[], Path | None]
    package_prefix: Callable[[Path], Path | None]
    current_prefix: Callable[[], Path | None]
    global_install_command: Callable[[str, str, Path | None], list[str]]
    runtime_install_command: Callable[[str | None, str, Path | None], list[str]]
    run_command: Callable[[list[str], float], tuple[int, str]]


@dataclass(frozen=True, slots=True)
class RuntimeUpgradeToolPorts:
    claude_version: Callable[[str], str]
    codex_version: Callable[[str], str]
    install_claude: Callable[[], str | None]
    install_codex: Callable[[], str | None]
    install_agy: Callable[[], str | None]
    update_agy: Callable[[str, bool], str]


@dataclass(frozen=True, slots=True)
class RuntimeUpgradeService:
    settings: RuntimeUpgradeSettings
    npm: RuntimeUpgradeNpmPorts
    tools: RuntimeUpgradeToolPorts
    output: Callable[[str], None]

    def ciel_runtime(self) -> int:
        npm = self.npm.find_executable("npm")
        if not npm:
            self.output("Ciel Runtime update skipped: npm was not found.")
            return 1
        package_spec = "@oneciel-ai/ciel-runtime@latest"
        latest = self.npm.latest_version(npm, package_spec)
        if latest and not self.npm.version_newer(
            latest, self.settings.runtime_version
        ):
            self.output(
                f"Ciel Runtime is up to date ({self.settings.runtime_version})."
            )
            return 0
        self.output(f"Updating Ciel Runtime to {latest or 'latest'}...")
        package_root = self.npm.current_package_root()
        install_prefix = self.npm.package_prefix(package_root) if package_root else None
        if install_prefix is not None:
            self.output(f"Updating current Ciel Runtime install prefix: {install_prefix}")
        return self._run_install(
            self.npm.global_install_command(npm, package_spec, install_prefix),
            "Ciel Runtime",
            install_prefix,
        )

    def claude(self) -> int:
        claude = self.npm.find_executable("claude")
        if not claude:
            return 0 if self.tools.install_claude() else 1
        current = self.tools.claude_version(claude)
        npm = self.npm.find_executable("npm")
        package_spec = self.settings.environ.get(
            "CIEL_RUNTIME_CLAUDE_CODE_PACKAGE", "@anthropic-ai/claude-code@latest"
        )
        latest = self.npm.latest_version(npm, package_spec) if npm else ""
        if current and latest and not self.npm.version_newer(latest, current):
            self.output(f"Claude Code is up to date ({current}).")
            return 0
        self.output(f"Updating Claude Code ({current or 'unknown'} -> {latest or 'latest'})...")
        install_prefix = self.npm.current_prefix()
        if install_prefix is not None:
            self.output(f"Updating Claude Code in active npm prefix: {install_prefix}")
        return self._run_install(
            self.npm.runtime_install_command(npm, package_spec, install_prefix),
            "Claude Code",
        )

    def codex(self) -> int:
        codex = self.npm.find_executable("codex")
        if not codex:
            return 0 if self.tools.install_codex() else 1
        current = self.tools.codex_version(codex)
        npm = self.npm.find_executable("npm")
        if not npm:
            self.output("Codex update skipped: npm was not found.")
            return 1
        package_spec = self.settings.environ.get(
            "CIEL_RUNTIME_CODEX_PACKAGE", "@openai/codex@latest"
        )
        latest = self.npm.latest_version(npm, package_spec)
        if current and latest and not self.npm.version_newer(latest, current):
            self.output(f"Codex is up to date ({current}).")
            return 0
        self.output(f"Updating Codex ({current or 'unknown'} -> {latest or 'latest'})...")
        install_prefix = self.npm.current_prefix()
        if install_prefix is not None:
            self.output(f"Updating Codex in active npm prefix: {install_prefix}")
        return self._run_install(
            self.npm.runtime_install_command(npm, package_spec, install_prefix),
            "Codex",
        )

    def agy(self) -> int:
        agy = self.npm.find_executable("agy")
        if not agy:
            return 0 if self.tools.install_agy() else 1
        return 0 if self.tools.update_agy(agy, True) else 1

    def _run_install(
        self,
        command: list[str],
        label: str,
        install_prefix: Path | None = None,
    ) -> int:
        return_code, output = self.npm.run_command(command, 300.0)
        if output:
            self.output(output)
        if return_code != 0:
            self.output(f"{label} update failed ({return_code}).")
            if install_prefix is not None:
                self.output(
                    f"Update targeted the active install prefix ({install_prefix}). "
                    "If this prefix is not writable, reinstall or update with the "
                    "permissions used for that prefix."
                )
        return return_code


__all__ = [
    "RuntimeUpgradeNpmPorts",
    "RuntimeUpgradeService",
    "RuntimeUpgradeSettings",
    "RuntimeUpgradeToolPorts",
]
