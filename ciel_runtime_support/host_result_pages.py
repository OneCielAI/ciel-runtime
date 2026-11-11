"""Digest-addressed pages for host-only Work results.

Claude Code replaces an MCP tool result above its token limit with the text
"Error: result (N characters ...) exceeds maximum allowed tokens" and leaves
isError false. On 2026-11-06 a 333-node walkie board made work_side_claim
182,656 characters in the owned reproduction, so the mod parsed that text,
no side event was ever processed and every claim was retried. Host callers
therefore receive a large result as pages they reassemble and verify.
"""
from __future__ import annotations

import hashlib
import json
import re
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
TOOL = 'work_host_page'
_ID = re.compile(r'[0-9a-f]{64}')
_LONE_SURROGATE = re.compile('[\ud800-\udfff]')


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
    _prune(directory)
    save(directory / (digest + '.json'), {'identity': identity, 'parts': parts})
    return json.dumps({'paged': {'id': digest, 'parts': len(parts), 'chars': len(text)}})


def page(state_dir: Path, identity: str, page_id: Any, part: Any) -> dict[str, Any]:
    if not isinstance(page_id, str) or not _ID.fullmatch(page_id) or type(part) is not int:
        raise WorkPlanError('invalid host page request')
    try:
        with reader(_directory(state_dir, identity) / (page_id + '.json')) as stream:
            row = json.loads(stream.read().decode('utf-8'))
    except FileNotFoundError:
        raise WorkPlanError('host page expired; repeat the original call') from None
    parts = row.get('parts') if isinstance(row, dict) else None
    if not isinstance(parts, list) or row.get('identity') != identity or not 0 <= part < len(parts):
        raise WorkPlanError('host page unavailable')
    return {'id': page_id, 'part': part, 'parts': len(parts), 'text': parts[part]}
