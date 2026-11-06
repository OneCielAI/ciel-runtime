"""Discovery and presentation of duplicate Ciel Runtime installations."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


def windows_user_runtime_home(expand: bool) -> str:
    """Read the user-level Windows runtime home with wrapper-compatible types."""

    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, value_type = winreg.QueryValueEx(key, "CIEL_RUNTIME_HOME")
        allowed = {winreg.REG_SZ}
        if expand:
            allowed.add(winreg.REG_EXPAND_SZ)
        if value_type not in allowed or not isinstance(value, str):
            return ""
        return os.path.expandvars(value) if value_type == winreg.REG_EXPAND_SZ else value
    except (OSError, ImportError):
        return ""


@dataclass(frozen=True, slots=True)
class InstallDiagnosticsSettings:
    home: Path
    environ: Mapping[str, str]
    windows: bool


@dataclass(frozen=True, slots=True)
class InstallDiagnosticsPorts:
    extra_dirs: Callable[[], list[Path]]
    package_root: Callable[[Path], Path | None]
    windows_user_runtime_home: Callable[[bool], str]
    current_root: Callable[[], Path | None]
    parse_version: Callable[[str], tuple[int, ...]]
    diagnostics: Callable[[], list[dict[str, str]]]
    stdin_isatty: Callable[[], bool]
    stdout_isatty: Callable[[], bool]
    write_error: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class InstallDiagnosticsService:
    settings: InstallDiagnosticsSettings
    ports: InstallDiagnosticsPorts

    def candidate_dirs(self) -> list[Path]:
        raw_dirs = [
            Path(entry)
            for entry in self.settings.environ.get("PATH", "").split(";" if self.settings.windows else ":")
            if entry
        ]
        raw_dirs.extend(self.ports.extra_dirs())
        raw_dirs.extend(
            [self.settings.home / ".npm-global" / "bin", self.settings.home / "bin"]
        )
        if not self.settings.windows:
            raw_dirs.extend([Path("/usr/local/bin"), Path("/usr/bin")])
        return list(dict.fromkeys(raw_dirs))

    def candidates(self) -> list[Path]:
        names = (
            ["ciel-runtime.cmd", "ciel-runtime.ps1", "ciel-runtime.exe"]
            if self.settings.windows
            else ["ciel-runtime"]
        )
        candidates: list[Path] = []
        seen: set[str] = set()
        for directory in self.candidate_dirs():
            for name in names:
                candidate = directory / name
                if not candidate.exists():
                    continue
                try:
                    key = str(candidate.resolve(strict=False))
                except Exception:
                    key = str(candidate)
                if key not in seen:
                    seen.add(key)
                    candidates.append(candidate)
        return candidates

    @staticmethod
    def _version_from_root(root: Path) -> str:
        try:
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
            version = package.get("version") if isinstance(package, dict) else ""
            if isinstance(version, str):
                return version.strip()
        except (OSError, ValueError, TypeError):
            pass
        try:
            constants = (root / "ciel_runtime_support" / "runtime_constants.py").read_text(
                encoding="utf-8"
            )
        except OSError:
            return ""
        match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', constants, re.MULTILINE)
        return match.group(1).strip() if match else ""

    def _npm_install_root(self, path: Path) -> Path | None:
        roots: list[Path] = []
        for candidate in (path, path.resolve(strict=False)):
            root = self.ports.package_root(candidate)
            if root is not None:
                roots.append(root)
        for scope in ("@one-ciel-ai", "@oneciel-ai"):
            roots.append(path.parent / "node_modules" / scope / "ciel-runtime")
        for root in roots:
            if root.is_dir() and ((root / "package.json").is_file() or (root / "ciel_runtime.py").is_file()):
                return root.resolve(strict=False)
        return None

    def _known_install(self, path: Path) -> tuple[Path, str] | None:
        # install.sh/install.ps1 place wrappers in ~/.local/bin. Resolve the
        # exact one home that each wrapper selects; never fall through to a
        # different install when that selected home is missing.
        if path.parent.name == "bin" and path.parent.parent.name == ".local":
            default_home = path.parent.parent / "share" / "ciel-runtime"
            if path.suffix.lower() in (".cmd", ".ps1"):
                selected_home = self.settings.environ.get("CIEL_RUNTIME_HOME_OVERRIDE")
                if not selected_home:
                    registered = self.ports.windows_user_runtime_home(
                        path.suffix.lower() == ".ps1"
                    )
                    if registered and (Path(registered) / "ciel_runtime.py").is_file():
                        selected_home = registered
                selected_home = (
                    selected_home
                    or self.settings.environ.get("CIEL_RUNTIME_HOME")
                    or str(default_home)
                )
            else:
                selected_home = self.settings.environ.get("CIEL_RUNTIME_HOME") or str(default_home)
            selected_root = Path(selected_home)
            if not selected_root.is_dir():
                return None
            version = self._version_from_root(selected_root)
            if version or (selected_root / "ciel_runtime.py").is_file():
                return selected_root.resolve(strict=False), version
            return None
        npm_root = self._npm_install_root(path)
        if npm_root is not None:
            return npm_root, self._version_from_root(npm_root)
        return None

    def launcher_version(self, path: Path, timeout: float = 5.0) -> str:
        known = self._known_install(path)
        if known is not None:
            return known[1]
        environ = dict(self.settings.environ)
        environ.update(
            CIEL_RUNTIME_SKIP_INSTALL_DIAGNOSTIC="1",
            CIEL_RUNTIME_SKIP_SELF_UPDATE="1",
            CIEL_RUNTIME_SELF_UPDATE_CHECK="off",
        )
        try:
            process = subprocess.run(
                [str(path), "--version"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=environ,
                timeout=timeout,
            )
        except Exception:
            return ""
        if process.returncode != 0:
            return ""
        output = process.stdout or ""
        match = re.search(r"ciel-runtime\s+(.+)", output, re.IGNORECASE)
        return match.group(1).strip() if match else output.strip().splitlines()[-1].strip()

    def diagnostics(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen_launches: set[str] = set()
        for launcher in self.candidates():
            known = self._known_install(launcher)
            root = known[0] if known is not None else self.ports.package_root(launcher)
            version = known[1] if known is not None else self.launcher_version(launcher)
            launch_key = f"{os.path.normcase(str(root.resolve(strict=False)))}\0{version}" if root else ""
            if launch_key and launch_key in seen_launches:
                continue
            if launch_key:
                seen_launches.add(launch_key)
            rows.append(
                {
                    "launcher": str(launcher),
                    "resolved": str(launcher.resolve(strict=False)),
                    "package_root": str(root) if root else "",
                    "version": version,
                }
            )
        return rows

    def warn_if_multiple(self) -> None:
        if self.settings.environ.get("CIEL_RUNTIME_SKIP_INSTALL_DIAGNOSTIC") == "1":
            return
        if not (self.ports.stdin_isatty() and self.ports.stdout_isatty()):
            return
        rows = self.ports.diagnostics()
        versions = {row["version"] for row in rows if row.get("version")}
        if len(versions) <= 1:
            return
        current_root = str(self.ports.current_root() or "")
        first = rows[0] if rows else {}
        newest = max(
            (row for row in rows if row.get("version")),
            key=lambda row: self.ports.parse_version(row["version"]),
            default=None,
        )
        self.ports.write_error(
            "Ciel Runtime warning: multiple ciel-runtime npm installs are visible."
        )
        if first:
            self.ports.write_error(
                f"  shell resolves ciel-runtime to: {first.get('launcher')} "
                f"({first.get('version') or 'unknown version'})"
            )
        if current_root:
            self.ports.write_error(f"  current package root: {current_root}")
        if newest and newest is not first:
            self.ports.write_error(
                f"  newer visible install: {newest.get('launcher')} "
                f"({newest.get('version')})"
            )
        self.ports.write_error(
            "  Fix by keeping one install prefix: update or uninstall the stale "
            "higher-priority install."
        )


__all__ = [
    "InstallDiagnosticsPorts",
    "InstallDiagnosticsService",
    "InstallDiagnosticsSettings",
]
