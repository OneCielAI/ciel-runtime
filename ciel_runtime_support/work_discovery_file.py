"""File-backed Work discovery instruction: the stop-time default message.

The message body lives in a local file so a person (or an external writer)
can change it without touching config. The loader keeps one in-memory copy
per resolved path and reloads only when the file changed on disk
(mtime/size/content signature), so every Stop sees the current text while
unchanged files cost one stat call.

Fail closed: a missing, unreadable, empty or oversized file yields no
instruction, so discovery stays off instead of falling back to stale text.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

MAX_FILE_INSTRUCTION_CHARS = 8192

# Interim default body for a configured-but-not-yet-written instruction
# file: until the Walkie integration lands, the stop-time message tells the
# session to check Walkie for open items. When that integration finishes,
# set this to None (one place) and a missing file fails closed again —
# no file is ever auto-created by the read path.
DEFAULT_FILE_INSTRUCTION: str | None = (
    "작업큐에 여유가 생기면 와키에서 최근 미결 아이템이 있는지 확인하고 스스로 할 일을 찾아라. "
    "우선순위 P1→P5, 같은 우선순위에서는 최신순으로 검색해서 픽업하라."
)

_cache_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int, str] | None, str | None]] = {}


def resolve_instruction_path(value: Any, workspace_dir: Path | None) -> Path | None:
    """Resolve a configured instruction path; relative paths are workspace-bound."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = Path(value.strip())
    if raw.is_absolute():
        return raw
    if workspace_dir is None:
        return None
    return Path(workspace_dir) / raw


def _signature(path: Path) -> tuple[int, int, str] | None:
    try:
        stat = path.stat()
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_FILE_INSTRUCTION_CHARS * 4:
        return None
    return (stat.st_mtime_ns, stat.st_size, hashlib.sha256(data).hexdigest())


def load_file_instruction(value: Any, workspace_dir: Path | None) -> str | None:
    """Current file text, reloaded in memory whenever the file changed.

    A configured path whose file does not exist yet yields
    DEFAULT_FILE_INSTRUCTION (the interim Walkie wording) without creating
    anything on disk; the moment a real file appears it takes over.
    """
    path = resolve_instruction_path(value, workspace_dir)
    if path is None:
        return None
    key = str(path)
    signature = _signature(path)
    if signature is None:
        with _cache_lock:
            _cache[key] = (None, None)
        # Distinguish "not written yet" (interim default) from unreadable:
        # only a genuinely absent file gets the default body.
        try:
            exists = path.exists()
        except OSError:
            exists = True
        return None if exists else DEFAULT_FILE_INSTRUCTION
    with _cache_lock:
        cached_signature, cached_text = _cache.get(key, (None, None))
        if cached_signature == signature:
            return cached_text
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        with _cache_lock:
            _cache[key] = (signature, None)
        return None
    clean = text.strip()
    if not clean or len(clean) > MAX_FILE_INSTRUCTION_CHARS:
        with _cache_lock:
            _cache[key] = (signature, None)
        return None
    with _cache_lock:
        _cache[key] = (signature, clean)
    return clean


__all__ = [
    "DEFAULT_FILE_INSTRUCTION",
    "MAX_FILE_INSTRUCTION_CHARS",
    "load_file_instruction",
    "resolve_instruction_path",
]
