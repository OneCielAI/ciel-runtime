"""Canonical native Claude messaging socket coordinates and key digests."""

import hashlib
import os
from pathlib import Path
import re

def canonical_socket_path(path: str, platform_name: str | None = None) -> str:
    if (platform_name or os.name) == "nt":
        match = re.fullmatch(
            r"[\\/]{2}[.?][\\/]pipe[\\/](?:(LOCAL)[\\/])?([^\\/]+)",
            path,
            flags=re.IGNORECASE,
        )
        if match is None:
            return path
        local_prefix = "LOCAL\\" if match.group(1) else ""
        canonical = rf"\\.\pipe\{local_prefix}{match.group(2)}"
        return re.sub(r"[A-Z]", lambda value: value.group(0).lower(), canonical)
    return str(Path(path).expanduser().resolve(strict=False))


def session_key_hash(path: str, platform_name: str | None = None) -> str:
    canonical = canonical_socket_path(path, platform_name)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


