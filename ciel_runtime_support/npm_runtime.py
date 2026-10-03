"""npm process, version, and installed-package path utilities."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping
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


RUNTIME_PACKAGE_NAME = "@one-ciel-ai/ciel-runtime"
# Installs made before 2026-10-03 live under the first npm scope; the account
# that owned it became unreachable, so new builds publish under the @one-ciel-ai org.
RUNTIME_PACKAGE_SCOPES = ("@one-ciel-ai", "@oneciel-ai")


def runtime_package_spec(current_version: str) -> str:
    """npm spec for updating this install, keeping it on its release channel.

    Nightly builds are published as ``X.Y.Z-nightly.<stamp>.<sha>`` under the
    ``nightly`` dist-tag; following ``latest`` from one would swap it for the
    stable release.
    """

    tag = "nightly" if "-nightly." in str(current_version or "") else "latest"
    return f"{RUNTIME_PACKAGE_NAME}@{tag}"


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
    package_name: str = RUNTIME_PACKAGE_NAME,
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


# npm 12 skips a package's install scripts unless the package is listed in
# allow-scripts (docs.npmjs.com/cli/v12/using-npm/config#allow-scripts). Claude
# Code's postinstall puts its native binary over bin/claude.exe; skipped, it
# leaves a shebang-less placeholder that fails with "Exec format error"
# (sarah-ai 2026-09-28, npm 12.1.0, Claude Code 2.1.284). npm 11 does not know
# the flag and warns "Unknown cli config", so it is only passed to npm 12+.
ALLOW_SCRIPTS_MIN_NPM_MAJOR = 12
_REGISTRY_PACKAGE_SPEC = re.compile(
    r"^((?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*)(?:@[^/\\\s]+)?$"
)
_NPM_MAJOR_VERSIONS: dict[str, int | None] = {}


def npm_major_version(npm: str) -> int | None:
    if npm not in _NPM_MAJOR_VERSIONS:
        version = parse_version_tuple(executable_version(npm, timeout=15.0))
        _NPM_MAJOR_VERSIONS[npm] = version[0] if version else None
    return _NPM_MAJOR_VERSIONS[npm]


def registry_package_name(package_spec: str) -> str | None:
    """Package name of a registry spec (``@scope/name@tag`` -> ``@scope/name``); None for paths and URLs."""

    match = _REGISTRY_PACKAGE_SPEC.match(str(package_spec or "").strip())
    return match.group(1) if match else None


def npm_global_install_command(
    npm: str,
    package_spec: str,
    prefix: Path | None = None,
    *,
    npm_major: Callable[[str], int | None] = npm_major_version,
) -> list[str]:
    command = [npm, "install", "-g"]
    if prefix is not None:
        command.extend(["--prefix", str(prefix)])
    package_name = registry_package_name(package_spec)
    if package_name and (npm_major(npm) or 0) >= ALLOW_SCRIPTS_MIN_NPM_MAJOR:
        command.append(f"--allow-scripts={package_name}")
    command.append(package_spec)
    return command


def npm_install_runtime_command(
    npm: str,
    package_spec: str,
    prefix: Path | None = None,
    *,
    npm_major: Callable[[str], int | None] = npm_major_version,
) -> list[str]:
    command = npm_global_install_command(npm, package_spec, prefix, npm_major=npm_major)
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
            and parts[index + 1] in RUNTIME_PACKAGE_SCOPES
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
    "npm_major_version",
    "npm_prefix_from_package_root",
    "package_root_from_installed_path",
    "parse_version_tuple",
    "registry_package_name",
    "run_upgrade_command",
    "runtime_package_spec",
    "version_newer",
]
