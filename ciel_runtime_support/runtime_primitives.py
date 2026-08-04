from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping


def source_fingerprint(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except Exception:
        try:
            stat = path.stat()
            return f"{int(stat.st_mtime_ns)}-{int(stat.st_size)}"
        except Exception:
            return "unknown"


def positive_environment_int(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = str(environment.get(name) or "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return default


def model_preset(
    model_id: str,
    presets: Mapping[str, dict[str, Any]],
    lookup_ids: Callable[[str], tuple[str, ...] | list[str]],
) -> dict[str, Any]:
    for candidate in lookup_ids(model_id):
        if candidate in presets:
            return presets[candidate]
        candidate_base = candidate.split(":", 1)[0]
        for key, value in presets.items():
            if candidate.startswith(key) or (":" not in candidate and key.startswith(candidate_base)):
                return value
    return {}


def join_url(base: str, path: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/v1") and path.startswith("/v1/"):
        return base + path[3:]
    return base + path


def url_is_up(url: str, request_json: Callable[..., Any]) -> bool:
    try:
        request_json(url, timeout=1.5)
        return True
    except Exception:
        return False


def colorize_status_text(
    text: str,
    *,
    enabled: bool,
    palette: tuple[int, ...],
    monotonic: Callable[[], float],
) -> str:
    if not enabled:
        return text
    parts: list[str] = []
    phase = int(monotonic() * 8)
    for index, char in enumerate(text):
        if char.isspace():
            parts.append(char)
            continue
        color = palette[(phase + index) % len(palette)]
        parts.append(f"\033[1;38;5;{color}m{char}\033[0m")
    return "".join(parts)
