"""Digest-addressed pages for host-only Work results.

Claude Code replaces an MCP tool result above its token limit with the text
"Error: result (N characters ...) exceeds maximum allowed tokens" and leaves
isError false. On 2026-11-06 a 333-node walkie board made work_side_claim
182,656 characters in the owned reproduction, so the mod parsed that text,
no side event was ever processed and every claim was retried. Host callers
therefore receive a large result as pages they reassemble and verify.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .durable_json_file import reader, save
from .work_plan_tree import WorkPlanError

# Each page response stays far below the client limit even after JSON
# escaping of the page text (quotes and backslashes of the paged JSON).
PAGE_CHARS = 8_000
PAGE_BYTES = 24_000
MAX_PAGES = 512
TTL_SECONDS = 3600.0
PRUNE_INTERVAL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 64
TOOL = 'work_host_page'
_ID = re.compile(r'[0-9a-f]{64}')
_LONE_SURROGATE = re.compile('[\ud800-\udfff]')
_CACHE_LOCK = threading.Lock()
_PAGE_CACHE: OrderedDict[tuple[str, str, str], tuple[tuple[int, int, int, int], tuple[str, ...]]] = OrderedDict()
_LAST_PRUNE: dict[str, float] = {}


def schema() -> dict[str, Any]:
    return {'name': TOOL,
            'description': 'Host-only: read one page of a large Work host result; the host verifies the digest.',
            'inputSchema': {'type': 'object', 'properties': {
                'id': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                'part': {'type': 'integer', 'minimum': 0}}, 'required': ['id', 'part']}}


def _fits(text: str) -> bool:
    return len(text) <= PAGE_CHARS and len(text.encode('utf-8', 'surrogatepass')) <= PAGE_BYTES


def _split(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for char in text:
        width = len(char.encode('utf-8'))
        if current and (len(current) >= PAGE_CHARS or size + width > PAGE_BYTES):
            parts.append(''.join(current))
            current, size = [], 0
        current.append(char)
        size += width
    if current:
        parts.append(''.join(current))
    return parts


def _directory(state_dir: Path, identity: str) -> Path:
    return Path(state_dir) / 'host-pages' / hashlib.sha256(identity.encode('utf-8')).hexdigest()


def _prune(directory: Path) -> None:
    """Expire page payloads and their durable lock sidecars.

    ``durable_json_file.reader/save`` uses ``<payload>.io.lock``.  Pruning only
    ``*.json`` left thousands of lock files after page expiry, so lock sidecars
    are removed once their payload is gone or both files are older than the TTL.
    """
    cutoff = time.time() - TTL_SECONDS
    for path in directory.glob('*.json'):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
    for lock in directory.glob('*.io.lock'):
        try:
            payload = Path(str(lock)[:-len('.io.lock')])
            if not payload.exists() and lock.stat().st_mtime < cutoff:
                lock.unlink()
        except OSError:
            pass


def _stat_key(path: Path) -> tuple[int, int, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (int(st.st_dev), int(st.st_ino), int(st.st_size), int(st.st_mtime_ns))


def _cache_key(state_dir: Path, identity: str, page_id: str) -> tuple[str, str, str]:
    return (os.path.normcase(str(Path(state_dir))), identity, page_id)


def _cache_put(state_dir: Path, identity: str, page_id: str, stat_key: tuple[int, int, int, int] | None,
               parts: list[str] | tuple[str, ...]) -> None:
    if stat_key is None:
        return
    key = _cache_key(state_dir, identity, page_id)
    with _CACHE_LOCK:
        _PAGE_CACHE[key] = (stat_key, tuple(parts))
        _PAGE_CACHE.move_to_end(key)
        while len(_PAGE_CACHE) > _CACHE_MAX_ENTRIES:
            _PAGE_CACHE.popitem(last=False)


def _maybe_prune(directory: Path) -> None:
    now = time.monotonic()
    key = os.path.normcase(str(directory))
    with _CACHE_LOCK:
        last = _LAST_PRUNE.get(key)
        if last is not None and now - last < PRUNE_INTERVAL_SECONDS:
            return
        _LAST_PRUNE[key] = now
    _prune(directory)


def bound(state_dir: Path, identity: str, text: str) -> str:
    """Return text unchanged when it fits, else a page envelope for it."""

    if _fits(text):
        return text
    # UTF-8 cannot carry a lone surrogate; TextEncoder on the host writes U+FFFD.
    text = _LONE_SURROGATE.sub('�', text)
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    parts = _split(text)
    if len(parts) > MAX_PAGES:
        raise WorkPlanError('host result exceeds page capacity')
    directory = _directory(state_dir, identity)
    directory.mkdir(parents=True, exist_ok=True)
    _maybe_prune(directory)
    target = directory / (digest + '.json')
    # Content-addressed by sha256(text): an identical file already on disk does
    # not need another fsync + atomic replace, only an updated mtime/cache entry.
    stat_key = _stat_key(target)
    if stat_key is None:
        save(target, {'identity': identity, 'parts': parts})
        stat_key = _stat_key(target)
    else:
        try:
            os.utime(target, None)
            stat_key = _stat_key(target)
        except OSError:
            pass
    _cache_put(state_dir, identity, digest, stat_key, parts)
    return json.dumps({'paged': {'id': digest, 'parts': len(parts), 'chars': len(text)}})


def page(state_dir: Path, identity: str, page_id: Any, part: Any) -> dict[str, Any]:
    if not isinstance(page_id, str) or not _ID.fullmatch(page_id) or type(part) is not int:
        raise WorkPlanError('invalid host page request')
    target = _directory(state_dir, identity) / (page_id + '.json')
    stat_key = _stat_key(target)
    if stat_key is None:
        with _CACHE_LOCK:
            _PAGE_CACHE.pop(_cache_key(state_dir, identity, page_id), None)
        raise WorkPlanError('host page expired; repeat the original call')
    parts: list[str] | tuple[str, ...] | None = None
    key = _cache_key(state_dir, identity, page_id)
    with _CACHE_LOCK:
        hit = _PAGE_CACHE.get(key)
        if hit is not None and hit[0] == stat_key:
            _PAGE_CACHE.move_to_end(key)
            parts = hit[1]
    if parts is None:
        try:
            with reader(target) as stream:
                row = json.loads(stream.read().decode('utf-8'))
        except FileNotFoundError:
            raise WorkPlanError('host page expired; repeat the original call') from None
        raw_parts = row.get('parts') if isinstance(row, dict) else None
        if not isinstance(raw_parts, list) or row.get('identity') != identity:
            raise WorkPlanError('host page unavailable')
        parts = tuple(raw_parts)
        _cache_put(state_dir, identity, page_id, _stat_key(target), parts)
    if not 0 <= part < len(parts):
        raise WorkPlanError('host page unavailable')
    return {'id': page_id, 'part': part, 'parts': len(parts), 'text': parts[part]}
