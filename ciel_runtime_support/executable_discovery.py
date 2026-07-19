"""Cross-platform executable and runtime helper discovery."""

from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import site
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class ExecutableDiscovery:
    home: Path
    source_file: Path
    platform_path: Callable[[str], Path]
    ciel_user_bin: Callable[[], Path]
    agy_user_bin: Callable[[], Path]

    @staticmethod
    def candidates(name: str) -> list[str]:
        if os.name == "nt" and not Path(name).suffix:
            return [f"{name}.exe", f"{name}.cmd", f"{name}.bat", name]
        return [name]

    def extra_dirs(self) -> list[Path]:
        directories = [self.ciel_user_bin(), self.agy_user_bin()]
        for variable in ("UV_INSTALL_DIR", "CARGO_HOME"):
            if root := os.environ.get(variable):
                path = Path(root)
                directories.append(path if path.name == "bin" else path / "bin")
        if os.name == "nt":
            if appdata := os.environ.get("APPDATA"):
                directories.append(self.platform_path(appdata) / "npm")
            if local_appdata := os.environ.get("LOCALAPPDATA"):
                directories.append(self.platform_path(local_appdata) / "Programs" / "nodejs")
            python_version = f"Python{sys.version_info.major}{sys.version_info.minor}"
            for variable in ("APPDATA", "LOCALAPPDATA"):
                if root := os.environ.get(variable):
                    directories.append(self.platform_path(root) / "Python" / python_version / "Scripts")
            try:
                directories.append(self.platform_path(site.getuserbase()) / "Scripts")
            except Exception:
                pass
            directories.append(self.platform_path(sys.executable).parent / "Scripts")
        else:
            directories.extend(
                (
                    self.home / ".local" / "bin",
                    self.home / ".cargo" / "bin",
                    self.home / ".npm-global" / "bin",
                    self.home / ".bun" / "bin",
                    Path(sys.executable).resolve().parent,
                    Path("/usr/local/bin"),
                    Path("/usr/bin"),
                    Path("/bin"),
                    Path("/opt/homebrew/bin"),
                )
            )
        result: list[Path] = []
        seen: set[str] = set()
        for directory in directories:
            key = str(directory)
            if key and key not in seen:
                seen.add(key)
                result.append(directory)
        return result

    def find(self, name: str) -> str | None:
        for candidate in self.candidates(name):
            if found := shutil.which(candidate):
                return found
        for directory in self.extra_dirs():
            for candidate in self.candidates(name):
                path = directory / candidate
                if path.exists():
                    return str(path)
        return None

    def resolve(self, command: str) -> str:
        command = str(command or "").strip()
        if not command:
            return command
        pathish = Path(command).is_absolute() or os.sep in command or bool(
            os.altsep and os.altsep in command
        )
        return command if pathish else self.find(command) or command

    def resolve_mcp_process(
        self,
        command: str,
        arguments: list[str],
        finder: Callable[[str], str | None] | None = None,
    ) -> tuple[str, list[str]]:
        command = str(command or "").strip()
        find = finder or self.find
        pathish = Path(command).is_absolute() or os.sep in command or bool(
            os.altsep and os.altsep in command
        )
        resolved = command if pathish else find(command) or command
        if resolved == command and Path(command).name.lower() in ("uvx", "uvx.exe", "uvx.cmd", "uvx.bat"):
            if uv := find("uv"):
                return uv, ["tool", "run", *arguments]
            if importlib.util.find_spec("uv") is not None:
                return sys.executable, ["-m", "uv", "tool", "run", *arguments]
        return resolved, arguments

    @staticmethod
    def shell_command(arguments: list[str]) -> str:
        if os.name == "nt":
            normalized: list[str] = []
            for argument in arguments:
                path_like = "\\" in argument and (
                    (len(argument) >= 2 and argument[1] == ":")
                    or argument.startswith("\\\\")
                    or argument.endswith((".py", ".exe", ".cmd", ".bat", ".ps1"))
                )
                normalized.append(shlex.quote(argument.replace("\\", "/") if path_like else argument))
            return " ".join(normalized)
        return " ".join(shlex.quote(argument) for argument in arguments)

    def find_tool_guard(
        self,
        finder: Callable[[str], str | None] | None = None,
    ) -> Path | None:
        candidates = [
            self.source_file.resolve().with_name("ciel-runtime-tool-guard.py"),
            self.ciel_user_bin() / "ciel-runtime-tool-guard.py",
            self.ciel_user_bin() / "ciel-runtime-tool-guard",
            self.home / ".local" / "bin" / "ciel-runtime-tool-guard.py",
            self.home / ".local" / "bin" / "ciel-runtime-tool-guard",
        ]
        find = finder or self.find
        for name in ("ciel-runtime-tool-guard", "ciel-runtime-tool-guard.py"):
            if found := find(name):
                candidates.append(Path(found))
        return next((path for path in candidates if path.exists()), None)
