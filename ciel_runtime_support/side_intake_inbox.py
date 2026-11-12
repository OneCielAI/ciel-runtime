"""Per-event durable mailbox; consumption commits a receipt before unlinking mail."""
from __future__ import annotations

import hashlib
import heapq
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

from .channel_message_repository import exclusive_file_lock
from .side_intake_limits import SIDE_INTAKE_PENDING_RECORD_MAX_BYTES, SIDE_INTAKE_TEXT_MAX_CHARS
from .work_plan_tree import WorkPlanError

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024  # Legacy document parser, not a queue ceiling.
# A pending record holds one original (up to 32,000 four-byte characters) plus
# its key, digest, route and timestamps.
MAX_PENDING_RECORD_BYTES = SIDE_INTAKE_PENDING_RECORD_MAX_BYTES
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
# 64-hex keys: about 60,000 quarantined events before the index stops growing.
MAX_QUARANTINE_INDEX_BYTES = 4 * 1024 * 1024
PRIORITIES = ('P1', 'P2', 'P3', 'P4', 'P5')
RECEIPT_ID = re.compile(r'[0-9a-f]{64}\Z')
RECEIPT_STAGES = ('received', 'processing', 'processed', 'failed', 'quarantined', 'duplicate')
# Web/operator projection boundary. The key rule is the terminal pane's
# side-intake.js OPAQUE_KEY plus common API/private-key spellings; matching
# keys are omitted from web projections, and string values pass redact_text.
OPAQUE_KEY = re.compile(r'token|secret|passw(?:or)?d|authori[sz]ation|signature|cookie|credential'
                        r'|activity_context|api[_-]?key|private[_-]?key|bearer', re.I)
PUBLIC_TEXT_LIMIT = 16_000
PUBLIC_RECAP_LIMIT = 500
PUBLIC_FIELD_LIMIT = 4_000
RECEIPT_PAGE_DEFAULT = 50
RECEIPT_PAGE_MAX = 100
# One request projects at most this many uncached records. The process cache
# keeps later refetches (side.changed) to changed records only.
RECEIPT_READ_BUDGET = 2_000
RECEIPT_COUNT_WINDOW = 2_000
_SUMMARY_CACHE_MAX = 20_000
_SUMMARY_CACHE: dict[str, dict[str, tuple[int, int, dict[str, Any]]]] = {}
_SUMMARY_CACHE_LOCK = threading.Lock()


def _bounded_text(value: Any, limit: int = PUBLIC_TEXT_LIMIT) -> tuple[str, bool]:
    """Credential-shaped text hidden, then bounded; returns (text, truncated)."""
    from .side_redaction import redact_text
    text = redact_text(value) if isinstance(value, str) else ''
    return (text, False) if len(text) <= limit else (text[:limit - 1] + '…', True)


def redact_public(value: Any, *, string_limit: int = PUBLIC_FIELD_LIMIT, items: int | None = 20,
                  depth_limit: int = 6, _depth: int = 0, _lost: list[bool] | None = None) -> Any:
    """Operator-facing copy: opaque keys hidden, strings redacted and bounded."""
    lost = _lost if _lost is not None else []
    if isinstance(value, str):
        text, truncated = _bounded_text(value, string_limit)
        if truncated:
            lost.append(True)
        return text
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if _depth >= depth_limit:
        lost.append(True)
        return '‹nested›'
    child = dict(string_limit=string_limit, items=items, depth_limit=depth_limit, _depth=_depth + 1, _lost=lost)
    if isinstance(value, (list, tuple)):
        if items is not None and len(value) > items:
            lost.append(True)
        return [redact_public(item, **child) for item in list(value)[:items]]
    if isinstance(value, dict):
        entries = list(value.items())
        if items is not None and len(entries) > items * 5:
            lost.append(True)
        return {_bounded_text(str(key), 200)[0]: redact_public(item, **child)
                for key, item in entries[:None if items is None else items * 5]
                if not OPAQUE_KEY.search(str(key))}
    return redact_public(str(value), **child)


def _public_original(value: Any, limit: int = PUBLIC_TEXT_LIMIT) -> tuple[str, bool]:
    """A redacted, bounded original: JSON events keep their structure."""
    raw = value if isinstance(value, str) else ''
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return _bounded_text(raw, limit)
    if not isinstance(parsed, (dict, list)):
        return _bounded_text(raw, limit)
    lost: list[bool] = []
    rendered = json.dumps(redact_public(parsed, string_limit=limit, items=None, depth_limit=40, _lost=lost),
                          ensure_ascii=False, indent=2)
    if len(rendered) > limit:
        return rendered[:limit - 1] + '…', True
    return rendered, bool(lost)


def _event_recap(value: Any) -> str:
    """One-line event summary, following side-intake.js eventView()."""
    raw = value if isinstance(value, str) else ''
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, dict):
        return _bounded_text(' '.join(raw.split()), PUBLIC_RECAP_LIMIT)[0]
    body = parsed.get('data') if isinstance(parsed.get('data'), dict) else parsed
    payload = body.get('payload') if isinstance(body.get('payload'), dict) else {}
    text = next((item for item in (body.get('text'), body.get('message'), payload.get('text'),
                                   parsed.get('text'), parsed.get('message'))
                 if isinstance(item, str) and item.strip()), '')
    kind = ' · '.join(item for item in (parsed.get('type'), body.get('event_type'))
                      if isinstance(item, str) and item)
    if not text:
        text = json.dumps(redact_public(parsed, string_limit=200), ensure_ascii=False)
    line = ('[' + kind + '] ' if kind else '') + ' '.join(text.split())
    return _bounded_text(line, PUBLIC_RECAP_LIMIT)[0]


def _priority(value: Any) -> str | None:
    if value in PRIORITIES:
        return value
    if type(value) is int and 1 <= value <= 5:
        return 'P' + str(value)
    return None


def source_priority(text: Any) -> str | None:
    """The source system's own urgency for one event as P1..P5, else None.

    Walkie carries a thread task's priority (1 = P1, highest .. 5; P3 default)
    at data.message_context.thread_task.priority, never at data.priority
    (observed 2026-11-07: urgent #116793 indexed nowhere and waited behind
    the aged backlog). An explicit data.priority still wins.
    """
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    data = body.get('data', body) if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return None
    explicit = _priority(data.get('priority'))
    if explicit:
        return explicit
    context = data.get('message_context')
    task = context.get('thread_task') if isinstance(context, dict) else None
    return _priority(task.get('priority')) if isinstance(task, dict) else None


def source_identity(text: Any) -> str | None:
    """The source message one event is about, shared by every event for it.

    Walkie sends message.created, task.auto_assigned and task updates for one
    message (Prody 2026-11-09: 119649 got two side replies). Events without a
    concrete message id, such as reminders, have no shared source.
    """
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    data = body.get('data') if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return None
    payload = data.get('payload') if isinstance(data.get('payload'), dict) else {}
    context = data.get('message_context') if isinstance(data.get('message_context'), dict) else {}
    conversation = data.get('conversation') or data.get('conversation_id') or payload.get('conversation_id')
    message = data.get('message_id') or payload.get('message_id') or context.get('id')
    if not isinstance(conversation, str) or not conversation or len(conversation) > 200:
        return None
    message = str(message) if isinstance(message, (str, int)) and not isinstance(message, bool) else ''
    if not message or len(message) > 100:
        return None
    return 'message:' + conversation + ':' + message


class SideIntakeInbox:
    def __init__(self, state_dir: Path, session_id: str) -> None:
        from .work_session_lineage import IDENTITY
        if not isinstance(session_id, str) or not IDENTITY.fullmatch(session_id):
            raise WorkPlanError('runtime-qualified session identity required')
        digest = hashlib.sha256(session_id.encode()).hexdigest()
        self.session_id = session_id
        self.path = Path(state_dir) / 'side-inboxes' / digest / 'inbox.json'
        self.directory = self.path.parent

    WATERMARK_FILE = 'web-watermark'

    def watermark(self) -> str | None:
        """Opaque value that changes after every receipt mutation.

        '' means no mutation was recorded yet; None means the marker could not
        be read at this instant (a concurrent replace), so callers keep their
        previous value instead of guessing.
        """
        try:
            with (self.directory / self.WATERMARK_FILE).open('rb') as stream:
                value = stream.read(101).decode('ascii')
        except FileNotFoundError:
            return ''
        except (OSError, UnicodeDecodeError):
            return None
        return value if len(value) <= 100 else None

    @staticmethod
    def _touch_watermark(directory: Path) -> None:
        """Invalidate web readers after a committed receipt write.

        This is a hint, not receipt authority: the receipt is already durable.
        It deliberately avoids durable_json_file's cross-process byte lock,
        whose contended retry sleeps a full second, because every receive,
        claim and outbox write would otherwise share this one file with each
        polling SSE stream. Values carry randomness, so concurrent writers
        never restore an older value; a lost race still changes the marker.
        """
        target = directory / SideIntakeInbox.WATERMARK_FILE
        temporary = directory / f'.{SideIntakeInbox.WATERMARK_FILE}.{os.getpid()}.{os.urandom(6).hex()}.tmp'
        try:
            temporary.write_bytes(f'{time.time_ns()}-{os.urandom(8).hex()}'.encode('ascii'))
            for _ in range(20):
                try:
                    os.replace(temporary, target)
                    return
                except PermissionError:
                    # A reader has the marker open for microseconds (Windows
                    # refuses replace over an open file, winerror 5).
                    time.sleep(0.002)
        except OSError:
            pass
        finally:
            temporary.unlink(missing_ok=True)

    def _event_path(self, area: str, key: str) -> Path:
        if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise WorkPlanError('invalid side receipt identity')
        return self.directory / area / key[:2] / (key + '.json')

    @staticmethod
    def _read_file(path: Path, limit: int | None = None) -> dict[str, Any] | None:
        if limit is None:
            limit = MAX_RECEIPT_BYTES if path.parent.parent.name == 'receipts' else MAX_PENDING_RECORD_BYTES
        from .durable_json_file import reader
        try:
            with reader(path) as stream:
                raw = stream.read(limit + 1)
        except FileNotFoundError:
            return None
        if len(raw) > limit:
            raise WorkPlanError('side inbox record exceeds parser limit; original preserved')
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise WorkPlanError('side inbox is unreadable; original preserved') from error
        if not isinstance(row, dict):
            raise WorkPlanError('side inbox schema is invalid; original preserved')
        return row

    @staticmethod
    def _validate(key: str, row: dict[str, Any]) -> None:
        if (not isinstance(row.get('text'), str) or not row['text'].strip()
                or (len(row['text']) > SIDE_INTAKE_TEXT_MAX_CHARS
                    and (row.get('route') or {}).get('side_intake_fallback') not in ('raw_text', 'notice'))
                or not isinstance(row.get('event_key'), str)
                or row.get('digest') != hashlib.sha256(row['text'].encode()).hexdigest()
                or key != hashlib.sha256(row['event_key'].encode()).hexdigest()
                or row.get('stage') not in ('received', 'board_queued', 'processed')
                or not isinstance(row.get('route', {}), dict)
                or (row.get('stage') == 'processed' and not isinstance(row.get('result'), dict))
                or not (row.get('item_id') is None or isinstance(row.get('item_id'), str))):
            raise WorkPlanError('side inbox row is invalid; original preserved')

    @staticmethod
    def _save_file(path: Path, row: dict[str, Any]) -> None:
        from .durable_json_file import save
        limit = MAX_RECEIPT_BYTES if path.parent.parent.name == 'receipts' else MAX_PENDING_RECORD_BYTES
        if len(json.dumps(row, ensure_ascii=False).encode('utf8')) > limit:
            raise WorkPlanError('side inbox write exceeds parser limit; previous record preserved')
        save(path, row)
        # Every authoritative receipt mutation is immediately invalidated for
        # web SSE. This derives the inbox root from the fixed area/shard/file path.
        if path.parent.parent.name in ('pending', 'receipts'):
            SideIntakeInbox._touch_watermark(path.parent.parent.parent)

    def _migrate(self) -> None:
        marker = self.directory / 'mailbox-v2.json'
        if marker.exists():
            return
        with exclusive_file_lock(self.path):
            if marker.exists():
                return
            legacy = self._read_file(self.path, MAX_DOCUMENT_BYTES)
            if legacy is not None:
                if type(legacy.get('version')) is not int or not isinstance(legacy.get('requests'), dict):
                    raise WorkPlanError('side inbox schema is invalid; original preserved')
                for key, row in legacy['requests'].items():
                    if not isinstance(row, dict):
                        raise WorkPlanError('side inbox row is invalid; original preserved')
                    self._validate(key, row)
                for key, row in legacy['requests'].items():
                    target = self._event_path('receipts' if row['item_id'] else 'pending', key)
                    with exclusive_file_lock(self._event_path('locks', key)):
                        if self._load(key) is None:
                            self._save_file(target, row)
            # Keep the original document unchanged as migration/recovery evidence.
            self._save_file(marker, {'schema': 2, 'migrated_at': time.time()})

    def _migrate_existing(self) -> bool:
        """Web reads never create an absent inbox; an existing one is migrated
        exactly as every other reader does. Returns whether it exists."""
        if not self.directory.is_dir():
            return False
        self._migrate()
        return True

    def _load(self, key: str) -> dict[str, Any] | None:
        receipt = self._read_file(self._event_path('receipts', key))
        row = receipt if receipt is not None else self._read_file(self._event_path('pending', key))
        if row is not None:
            self._validate(key, row)
        return row

    def _index_priority(self, key: str, row: dict[str, Any]) -> None:
        priority=source_priority(row['text'])
        if priority:
            marker=self.directory/'priority'/priority/key[:2]/(key+'.json')
            if not marker.exists():
                self._save_file(marker,{'receipt_id':key})

    def receive(self, text: str, event_key: str, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
        fallback = route.get('side_intake_fallback') if isinstance(route, dict) else ''
        if (not isinstance(text, str) or not text.strip()
                or len(text) > SIDE_INTAKE_TEXT_MAX_CHARS and fallback not in ('raw_text', 'notice')):
            raise WorkPlanError(f'side intake text must contain 1-{SIDE_INTAKE_TEXT_MAX_CHARS} characters')
        if not isinstance(event_key, str) or not event_key.strip() or len(event_key) > 300:
            raise WorkPlanError('stable event key required (1-300 characters)')
        if route is not None and (not isinstance(route, dict) or len(json.dumps(route).encode()) > 8192):
            raise WorkPlanError('invalid side reply route')
        self._migrate()
        key = hashlib.sha256(event_key.encode()).hexdigest()
        digest = hashlib.sha256(text.encode()).hexdigest()
        with exclusive_file_lock(self._event_path('locks', key)):
            existing = self._load(key)
            if existing is not None:
                if existing['digest'] != digest or (route is not None and existing.get('route', {}) != route):
                    raise WorkPlanError('event key reused with different content or reply route')
                if existing['stage'] in ('board_queued', 'processed'):
                    self._event_path('pending', key).unlink(missing_ok=True)
                if existing['stage']=='received':
                    self._index_priority(key,existing)
                return self._receipt(key, existing, duplicate=True)
            row = {'text': text, 'event_key': event_key, 'digest': digest,
                   'received_at': time.time(), 'stage': 'received', 'item_id': None,
                   'external_ack': 'not_sent', 'route': dict(route or {}), 'session_id': self.session_id}
            self._save_file(self._event_path('pending', key), row)
            self._index_priority(key,row)
            return self._receipt(key, row, duplicate=False)

    def _clear_priority(self, key: str) -> None:
        for priority in ('P1','P2','P3','P4','P5'):
            (self.directory/'priority'/priority/key[:2]/(key+'.json')).unlink(missing_ok=True)

    # Permanently quarantined events (a completed turn whose decision was
    # invalid; retrying may duplicate an external reply) stay in pending/ for
    # the pane and the operator, but are never claimable. Prody 2026-11-10: 230
    # of them filled claim's 100-row keyset windows, so a fresh event waited up
    # to three windows. This index lets claim skip them without opening them.
    def _quarantine_index_path(self) -> Path:
        return self.directory / 'quarantine-index.json'

    def quarantined_keys(self) -> set[str]:
        # Only an optimization: an unreadable index means "skip nothing", and
        # claim still refuses every quarantined row it opens.
        try:
            row = self._read_file(self._quarantine_index_path(), MAX_QUARANTINE_INDEX_BYTES) or {}
        except WorkPlanError:
            return set()
        keys = row.get('keys')
        return {key for key in keys if isinstance(key, str) and len(key) == 64} if isinstance(keys, list) else set()

    def mark_quarantined(self, key: str) -> None:
        self._event_path('locks', key)  # validates the receipt identity
        path = self._quarantine_index_path()
        from .durable_json_file import save
        with exclusive_file_lock(path):
            keys = self.quarantined_keys()
            if key not in keys:
                keys.add(key)
                body = {'keys': sorted(keys)}
                if len(json.dumps(body).encode('utf8')) <= MAX_QUARANTINE_INDEX_BYTES:
                    save(path, body)
                    self._touch_watermark(self.directory)
        self._clear_priority(key)

    def link(self, receipt_id: str, item_id: str) -> None:
        if not isinstance(item_id, str) or not item_id.strip():
            raise WorkPlanError('side receipt requires a committed item identity')
        self._migrate()
        with exclusive_file_lock(self._event_path('locks', receipt_id)):
            row = self._load(receipt_id)
            if row is None:
                raise WorkPlanError('side receipt missing')
            if row.get('item_id') not in (None, item_id):
                raise WorkPlanError('side receipt already linked to another item')
            row.update(item_id=item_id, stage='board_queued', consumed_at=time.time())
            self._save_file(self._event_path('receipts', receipt_id), row)
            self._event_path('pending', receipt_id).unlink(missing_ok=True)
            self._clear_priority(receipt_id)

    def pending(self, limit: int = 10, *, after: str = '', skip: set[str] | frozenset[str] = frozenset()) -> list[dict[str, Any]]:
        self._migrate()
        rows = []
        if limit <= 0:
            return rows
        # Keyset rotation prevents a permanently failing first batch from
        # starving independent events. Only one page is retained in memory.
        paths = (self.directory / 'pending').glob('*/*.json')
        selected = heapq.nsmallest(limit, (p for p in paths if p.stem > after and p.stem not in skip), key=lambda p: p.stem)
        if not selected and after:
            selected = heapq.nsmallest(limit, (p for p in (self.directory / 'pending').glob('*/*.json') if p.stem not in skip),
                                       key=lambda p: p.stem)
        for path in selected:
            key = path.stem
            with exclusive_file_lock(self._event_path('locks', key)):
                row = self._load(key)
                if row is not None and row['stage'] == 'received':
                    rows.append(dict(row, receipt_id=key))
                elif row is not None:
                    path.unlink(missing_ok=True)  # Recover interrupted post-receipt unlink.
            if len(rows) >= limit:
                break
        return rows

    def summaries(self, limit: int = 10) -> list[dict[str, Any]]:
        self._migrate()
        if limit <= 0:
            return []
        selected = heapq.nlargest(limit, self._receipt_files(), key=lambda entry: (entry[0], entry[1]))
        rows = []
        for _, key, path, _ in selected:
            row = self._read_file(path)
            if row is not None:
                self._validate(key, row)
                rows.append(self._receipt(key, row, duplicate=False))
        return rows

    def _receipt_files(self):
        """Yield one metadata row per receipt, preferring committed receipts."""
        receipts = {}
        for path in (self.directory / 'receipts').glob('*/*.json'):
            try:
                stat = path.stat()
                receipts[path.stem] = (stat.st_mtime_ns, stat.st_size, path)
            except FileNotFoundError:
                continue
        for key, (mtime, size, path) in receipts.items():
            yield mtime, key, path, size
        for path in (self.directory / 'pending').glob('*/*.json'):
            if path.stem in receipts:
                continue
            try:
                stat = path.stat()
                yield stat.st_mtime_ns, path.stem, path, stat.st_size
            except FileNotFoundError:
                continue

    def _summary_cache(self) -> dict[str, tuple[int, int, dict[str, Any], dict[str, Any]]]:
        key = os.path.normcase(str(self.directory.resolve()))
        with _SUMMARY_CACHE_LOCK:
            return _SUMMARY_CACHE.setdefault(key, {})

    def _cached_public_summary(self, key: str, path: Path, mtime: int, size: int,
                               *, now: float) -> dict[str, Any]:
        """Project one record; unchanged files (same mtime and size) reuse the
        previous projection, with time-dependent fields recomputed for `now`."""
        cache = self._summary_cache()
        with _SUMMARY_CACHE_LOCK:
            cached = cache.get(key)
        if cached is not None and cached[:2] == (mtime, size):
            return self._timed_summary(cached[2], cached[3], now)
        row = self._read_file(path)
        if row is None:
            raise WorkPlanError('side inbox record disappeared during projection')
        self._validate(key, row)
        summary, inputs = self._public_summary_parts(key, row)
        with _SUMMARY_CACHE_LOCK:
            if len(cache) >= _SUMMARY_CACHE_MAX:
                cache.clear()
            cache[key] = (mtime, size, summary, inputs)
        return self._timed_summary(summary, inputs, now)

    @staticmethod
    def _stage_inputs(row: dict[str, Any]) -> dict[str, Any]:
        result = row.get('result') if isinstance(row.get('result'), dict) else {}
        failure = row.get('side_failure') if isinstance(row.get('side_failure'), dict) else {}
        processing = row.get('processing') if isinstance(row.get('processing'), dict) else {}
        fallback = row.get('main_fallback') if isinstance(row.get('main_fallback'), dict) else {}
        lease = processing.get('lease_until')
        return {'duplicate': result.get('delivery') == 'duplicate_source' or bool(result.get('duplicate_of')),
                # finish() keeps the claim's processing record on a processed
                # receipt (its generation fences duplicate finishes), so the
                # committed stage and board linkage outrank it.
                'committed': row.get('stage') in ('processed', 'board_queued'),
                'quarantined': bool(failure.get('quarantined')),
                # claim() treats an expired lease as claimable again, and a
                # legacy pending main fallback as reclaimable for the side.
                'lease_until': (lease if isinstance(lease, (int, float))
                                and fallback.get('state') != 'pending' else None),
                'failed': bool(failure)}

    @staticmethod
    def _stage(inputs: dict[str, Any], now: float) -> str:
        if inputs['duplicate']:
            return 'duplicate'
        if inputs['committed']:
            return 'processed'
        if inputs['quarantined']:
            return 'quarantined'
        if inputs['lease_until'] is not None and inputs['lease_until'] > now:
            return 'processing'
        if inputs['failed']:
            return 'failed'
        return 'received'

    @classmethod
    def _timed_summary(cls, summary: dict[str, Any], inputs: dict[str, Any], now: float) -> dict[str, Any]:
        timed = dict(summary)
        timed['stage'] = cls._stage(inputs, now)
        received, consumed = timed.get('received_at'), timed.get('consumed_at')
        if isinstance(received, (int, float)):
            end = consumed if isinstance(consumed, (int, float)) else now
            timed['latency_seconds'] = max(0, end - received)
        return timed

    @classmethod
    def _public_summary_parts(cls, key: str, row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Time-independent bounded summary fields plus the stage inputs."""
        result = row.get('result') if isinstance(row.get('result'), dict) else {}
        failure = row.get('side_failure') if isinstance(row.get('side_failure'), dict) else {}
        processing = row.get('processing') if isinstance(row.get('processing'), dict) else {}
        payload = {
            'receipt_id': key,
            'priority': source_priority(row.get('text')), 'received_at': row.get('received_at'),
            'claimed_at': processing.get('started_at'), 'consumed_at': row.get('consumed_at'),
            'item_id': result.get('item_id') or row.get('item_id'),
            'action': result.get('action'), 'kind': result.get('kind'),
            'delivery': result.get('delivery') or (row.get('outbox') or {}).get('state'),
            'failure_category': failure.get('category'),
            'source_context': row.get('source_context'),
            'source_truncated': True if row.get('source_truncated') else None,
            'attempt_count': failure.get('attempt_count'),
            'quarantined': bool(failure.get('quarantined')),
            'handoff': result.get('handoff'),
            'released_waits': redact_public(result.get('released_waits') or [], string_limit=500),
            'recap': _event_recap(row.get('text')),
        }
        payload = {name: redact_public(value, string_limit=500) if isinstance(value, str) and name != 'recap'
                   else value for name, value in payload.items() if value is not None}
        return payload, cls._stage_inputs(row)

    @classmethod
    def _public_summary(cls, key: str, row: dict[str, Any], *, now: float) -> dict[str, Any]:
        summary, inputs = cls._public_summary_parts(key, row)
        return cls._timed_summary(summary, inputs, now)

    def receipt_page(self, *, limit: int = RECEIPT_PAGE_DEFAULT, cursor: str = '', stage: str = '',
                     priority: str = '', search: str = '') -> dict[str, Any]:
        """Newest-first, keyset-paged current-session receipt summaries."""
        if type(limit) is not int or not 1 <= limit <= RECEIPT_PAGE_MAX:
            raise WorkPlanError(f'side receipt limit must be an integer from 1 to {RECEIPT_PAGE_MAX}')
        if stage and stage not in RECEIPT_STAGES:
            raise WorkPlanError('invalid side receipt stage filter')
        if priority and priority not in PRIORITIES:
            raise WorkPlanError('invalid side receipt priority filter')
        if not isinstance(search, str) or len(search) > 200:
            raise WorkPlanError('side receipt search q must be at most 200 characters')
        before = None
        if cursor:
            parts = cursor.split('-', 1)
            if (len(parts) != 2 or not parts[0].isdigit() or len(parts[0]) > 30
                    or RECEIPT_ID.fullmatch(parts[1]) is None):
                raise WorkPlanError('invalid side receipt cursor')
            before = (int(parts[0]), parts[1])
        self._migrate_existing()
        all_entries = sorted(self._receipt_files(), key=lambda entry: (entry[0], entry[1]), reverse=True)
        now = time.time()
        projected: dict[str, dict[str, Any]] = {}

        def project(entry: tuple[int, str, Path, int]) -> dict[str, Any]:
            mtime, key, path, size = entry
            if key not in projected:
                projected[key] = self._cached_public_summary(key, path, mtime, size, now=now)
            return projected[key]

        # Stage counts describe the newest receipts, independent of filters and
        # paging. Unchanged records come from the projection cache.
        stage_counts = {name: 0 for name in RECEIPT_STAGES}
        for entry in all_entries[:RECEIPT_COUNT_WINDOW]:
            stage_counts[project(entry)['stage']] += 1
        entries = [entry for entry in all_entries
                   if before is None or (entry[0], entry[1]) < before]
        wanted = search.casefold().strip()
        filtered = bool(stage or priority or wanted)
        budget = min(RECEIPT_READ_BUDGET if filtered else limit + 1, len(entries))
        items: list[dict[str, Any]] = []
        boundary = last_item = None
        scanned = 0
        for entry in entries[:budget]:
            scanned += 1
            public = project(entry)
            boundary = (entry[0], entry[1])
            if stage and public['stage'] != stage:
                continue
            if priority and public.get('priority') != priority:
                continue
            if wanted and wanted not in ' '.join(str(public.get(field) or '') for field in (
                    'receipt_id', 'recap', 'item_id', 'action', 'kind', 'failure_category')).casefold():
                continue
            if len(items) == limit:
                # One more match exists; the cursor stays after the last item.
                scanned -= 1
                break
            items.append(public)
            last_item = (entry[0], entry[1])
        has_more = scanned < len(entries)
        next_cursor = None
        if has_more:
            # Resume after the last returned item, or after the last scanned
            # record when a filtered scan hit its read budget first.
            resume = last_item if len(items) == limit else boundary
            if resume is not None:
                next_cursor = f'{resume[0]}-{resume[1]}'
        quarantined = len(self.quarantined_keys())
        return {'items': items, 'next_cursor': next_cursor, 'has_more': has_more,
                'total': len(all_entries), 'counts': stage_counts,
                'counts_complete': len(all_entries) <= RECEIPT_COUNT_WINDOW,
                'quarantined_count': quarantined, 'watermark': self.watermark() or ''}

    def receipt_detail(self, receipt_id: str) -> dict[str, Any] | None:
        if not isinstance(receipt_id, str) or RECEIPT_ID.fullmatch(receipt_id) is None:
            raise WorkPlanError('invalid side receipt identity')
        if not self._migrate_existing():
            return None
        row = self._load(receipt_id)
        if row is None:
            return None
        summary = self._public_summary(receipt_id, row, now=time.time())
        original, truncated = _public_original(row.get('text'), PUBLIC_TEXT_LIMIT)
        result = row.get('result') if isinstance(row.get('result'), dict) else {}
        failure = row.get('side_failure') if isinstance(row.get('side_failure'), dict) else {}
        fallback = row.get('main_fallback') if isinstance(row.get('main_fallback'), dict) else {}
        decision_fields = ('action', 'kind', 'item_id', 'summary', 'reply', 'final_text', 'delivery',
                           'handoff', 'based_on_version', 'title', 'priority', 'queue',
                           'released_waits', 'unreleased_waits', 'main_handoff')
        failure_fields = ('category', 'model', 'effective_model', 'attempt_count', 'failed_at',
                          'retry_not_before', 'quarantined', 'retryable', 'reason', 'status',
                          'code', 'terminal_reason', 'message', 'use_parent_model')
        fallback_fields = ('state', 'reason', 'prepared_at', 'updated_at')
        try:
            parsed = json.loads(row.get('text') or '')
        except (TypeError, ValueError):
            parsed = None
        source = {}
        if isinstance(parsed, dict):
            data = parsed.get('data') if isinstance(parsed.get('data'), dict) else parsed
            payload = data.get('payload') if isinstance(data.get('payload'), dict) else {}
            source = {'type': parsed.get('type') or data.get('event_type'),
                      'id': data.get('message_id') or payload.get('message_id'),
                      'conversation_id': data.get('conversation_id') or data.get('conversation')
                                         or payload.get('conversation_id')}
            source = {key: redact_public(value, string_limit=500) for key, value in source.items()
                      if value is not None}
        detail = {**summary, 'original': original, 'original_truncated': truncated,
                  'source': source,
                  'decision': redact_public({key: result[key] for key in decision_fields if key in result}),
                  'failure': redact_public({key: failure[key] for key in failure_fields if key in failure}),
                  'fallback': redact_public({key: fallback[key] for key in fallback_fields if key in fallback})}
        return detail

    @staticmethod
    def _receipt(key: str, row: dict[str, Any], *, duplicate: bool) -> dict[str, Any]:
        return {'receipt_id': key, 'accepted': True, 'duplicate': duplicate,
                'stage': row['stage'], 'item_id': row['item_id'],
                'recap': ' '.join(row['text'].split())[:500], 'original': row['text'],
                'execution_approved': False, 'external_ack': row.get('external_ack', 'not_sent'),
                'processing': row.get('processing'), 'side_failure': row.get('side_failure'),
                'source_context': row.get('source_context'),
                'source_truncated': row.get('source_truncated') is True,
                'main_fallback': row.get('main_fallback'), 'context': row.get('context'),
                'result': row.get('result')}
