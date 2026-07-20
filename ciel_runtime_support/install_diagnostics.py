"""Discovery and presentation of duplicate Ciel Runtime installations."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InstallDiagnosticsSettings:
    home: Path
    environ: Mapping[str, str]
    windows: bool


@dataclass(frozen=True, slots=True)
class InstallDiagnosticsPorts:
    extra_dirs: Callable[[], list[Path]]
    package_root: Callable[[Path], Path | None]
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
        names = ["ciel-runtime"]
        if self.settings.windows:
            names.extend(["ciel-runtime.cmd", "ciel-runtime.exe"])
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

    def launcher_version(self, path: Path, timeout: float = 5.0) -> str:
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
        for launcher in self.candidates():
            root = self.ports.package_root(launcher)
            rows.append(
                {
                    "launcher": str(launcher),
                    "resolved": str(launcher.resolve(strict=False)),
                    "package_root": str(root) if root else "",
                    "version": self.launcher_version(launcher),
                }
            )
        return rows

    def warn_if_multiple(self) -> None:
        if self.settings.environ.get("CIEL_RUNTIME_SKIP_INSTALL_DIAGNOSTIC") == "1":
            return
        if not (self.ports.stdin_isatty() and self.ports.stdout_isatty()):
            return
        rows = self.ports.diagnostics()
        roots = {row["package_root"] for row in rows if row.get("package_root")}
        if len(roots) <= 1:
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
