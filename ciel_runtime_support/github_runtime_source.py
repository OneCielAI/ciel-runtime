"""Install and update Ciel Runtime straight from the GitHub repository.

npm publishing can fail independently of the code (2026-09/10: every nightly
publish got ``E404`` on PUT), and then no install ever sees a new version.
This source reads the branch head from GitHub's smart-HTTP ref listing (no
API token, no API rate limit) and installs the commit tarball with npm, so
the install layout stays the same as a registry install.

The installed commit is recorded in a marker file inside the package root,
because a repository checkout carries only the base ``package.json`` version.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Callable

REPOSITORY = "OneCielAI/ciel-runtime"
# npm dist-tag channel -> branch that feeds it.
BRANCH_FOR_CHANNEL = {"nightly": "nightly", "latest": "main"}
SOURCE_MARKER = ".ciel-runtime-source.json"
REFS_URL = f"https://github.com/{REPOSITORY}.git/info/refs?service=git-upload-pack"
_SHA = re.compile(r"[0-9a-f]{40}")
_NIGHTLY_SHA = re.compile(r"-nightly\.[0-9-]+\.([0-9a-f]{7,40})$")


def tarball_url(sha: str) -> str:
    return f"https://codeload.github.com/{REPOSITORY}/tar.gz/{sha}"


def branch_head_from_refs(body: bytes | str, branch: str) -> str:
    """Commit of ``refs/heads/<branch>`` in a git-upload-pack ref advertisement."""

    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    wanted = f"refs/heads/{branch}"
    for line in re.split(r"[\n\x00]", text):
        parts = line.strip().split(" ", 1)
        if len(parts) != 2 or parts[1].strip() != wanted:
            continue
        # The first field may carry the 4-hex pkt-line length before the commit.
        commit = parts[0].strip()[-40:]
        if _SHA.fullmatch(commit):
            return commit
    return ""


def remote_branch_head(
    branch: str,
    *,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    timeout: float = 8.0,
) -> str:
    request = urllib.request.Request(REFS_URL, headers={"User-Agent": "ciel-runtime-self-update"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return branch_head_from_refs(response.read(), branch)
    except Exception:
        return ""


def read_source_marker(package_root: Path | None) -> dict[str, Any]:
    if package_root is None:
        return {}
    try:
        data = json.loads((package_root / SOURCE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_source_marker(package_root: Path | None, marker: dict[str, Any]) -> None:
    if package_root is None:
        return
    (package_root / SOURCE_MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")


def channel_branch(current_version: str, marker: dict[str, Any], override: str = "") -> str:
    """Branch this install follows: marker, then the override, then the version's channel.

    npm keeps no record of the tarball a global install came from, so a first
    install from GitHub carries no marker; ``CIEL_RUNTIME_UPDATE_BRANCH`` names
    its branch until the first self-update writes the marker.
    """

    branch = str(marker.get("branch") or "") or str(override or "").strip()
    if branch:
        return branch
    channel = "nightly" if "-nightly." in str(current_version or "") else "latest"
    return BRANCH_FOR_CHANNEL[channel]


def installed_commit(current_version: str, marker: dict[str, Any]) -> str:
    """Full or abbreviated commit of this install; empty when unknown."""

    sha = str(marker.get("sha") or "")
    if sha:
        return sha
    match = _NIGHTLY_SHA.search(str(current_version or ""))
    return match.group(1) if match else ""


def is_current(installed: str, remote: str) -> bool:
    return bool(installed and remote and remote.startswith(installed))


__all__ = [
    "BRANCH_FOR_CHANNEL",
    "REPOSITORY",
    "SOURCE_MARKER",
    "branch_head_from_refs",
    "channel_branch",
    "installed_commit",
    "is_current",
    "read_source_marker",
    "remote_branch_head",
    "tarball_url",
    "write_source_marker",
]
