"""npm process, version, and installed-package path utilities."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path


def parse_version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in re.split(r"[^0-9]+", value.strip()) if item)


def version_newer(latest: str, current: str) -> bool:
    left = list(parse_version_tuple(latest))
    right = list(parse_version_tuple(current))
    size = max(len(left), len(right), 1)
    left.extend([0] * (size - len(left)))
    right.extend([0] * (size - len(right)))
    return tuple(left) > tuple(right)


def npm_latest_package_version(
    npm: str, package_spec: str, timeout: float = 8.0
) -> str:
    try:
        process = subprocess.run(
            [npm, "view", package_spec, "version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception:
        return ""
    if process.returncode != 0:
        return ""
    output = (process.stdout or "").strip()
    return output.splitlines()[-1].strip() if output else ""


def npm_global_package_root(
    npm: str,
    package_name: str = "@oneciel-ai/ciel-runtime",
    timeout: float = 8.0,
) -> Path | None:
    try:
        process = subprocess.run(
            [npm, "root", "-g"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception:
        return None
    if process.returncode != 0:
        return None
    root = (process.stdout or "").strip()
    if not root:
        return None
    package_path = Path(root)
    for part in package_name.split("/"):
        if part:
            package_path /= part
    return package_path


def npm_prefix_from_package_root(package_root: Path) -> Path | None:
    """Infer the npm install prefix from a global installed package root."""

    parts = package_root.parts
    for index, part in enumerate(parts):
        if part != "node_modules":
            continue
        try:
            node_modules = Path(*parts[: index + 1])
        except Exception:
            return None
        parent = node_modules.parent
        return parent.parent if parent.name == "lib" else parent
    return None


def npm_global_install_command(
    npm: str, package_spec: str, prefix: Path | None = None
) -> list[str]:
    command = [npm, "install", "-g"]
    if prefix is not None:
        command.extend(["--prefix", str(prefix)])
    command.append(package_spec)
    return command


def npm_install_runtime_command(
    npm: str, package_spec: str, prefix: Path | None = None
) -> list[str]:
    command = npm_global_install_command(npm, package_spec, prefix)
    command.insert(3, "--prefer-online")
    return command


def npm_global_bin_dir_from_prefix(prefix: Path) -> Path:
    return prefix if os.name == "nt" else prefix / "bin"


def executable_version(executable: str, timeout: float = 8.0) -> str:
    try:
        process = subprocess.run(
            [executable, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except Exception:
        return ""
    if process.returncode != 0:
        return ""
    match = re.search(r"\d+(?:\.\d+)+", process.stdout or "")
    return match.group(0) if match else ""


def run_upgrade_command(
    command: list[str],
    environ: Mapping[str, str],
    timeout: float = 300.0,
) -> tuple[int, str]:
    try:
        process = subprocess.run(
            command,
            text=True,
            input="y\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dict(environ),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return process.returncode, (process.stdout or "").strip()


def claude_code_current_version(claude: str) -> str:
    return executable_version(claude)


def codex_current_version(codex: str) -> str:
    return executable_version(codex)


def package_root_from_installed_path(path: Path) -> Path | None:
    """Return the npm package root when a path lives inside this package."""

    try:
        resolved = path.resolve(strict=False)
    except Exception:
        resolved = path
    parts = resolved.parts
    for index in range(0, max(0, len(parts) - 2)):
        if (
            parts[index] == "node_modules"
            and parts[index + 1] == "@oneciel-ai"
            and parts[index + 2] == "ciel-runtime"
        ):
            try:
                return Path(*parts[: index + 3])
            except Exception:
                return None
    return None


__all__ = [
    "claude_code_current_version",
    "codex_current_version",
    "executable_version",
    "npm_global_bin_dir_from_prefix",
    "npm_global_install_command",
    "npm_global_package_root",
    "npm_install_runtime_command",
    "npm_latest_package_version",
    "npm_prefix_from_package_root",
    "package_root_from_installed_path",
    "parse_version_tuple",
    "run_upgrade_command",
    "version_newer",
]
