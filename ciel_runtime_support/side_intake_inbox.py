"""Per-event durable mailbox; consumption commits a receipt before unlinking mail."""
from __future__ import annotations

import hashlib
import heapq
import json
from pathlib import Path
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
PRIORITIES = ('P1', 'P2', 'P3', 'P4', 'P5')


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


class SideIntakeInbox:
    def __init__(self, state_dir: Path, session_id: str) -> None:
        from .work_session_lineage import IDENTITY
        if not isinstance(session_id, str) or not IDENTITY.fullmatch(session_id):
            raise WorkPlanError('runtime-qualified session identity required')
        digest = hashlib.sha256(session_id.encode()).hexdigest()
        self.session_id = session_id
        self.path = Path(state_dir) / 'side-inboxes' / digest / 'inbox.json'
        self.directory = self.path.parent

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

    def pending(self, limit: int = 10, *, after: str = '') -> list[dict[str, Any]]:
        self._migrate()
        rows = []
        if limit <= 0:
            return rows
        # Keyset rotation prevents a permanently failing first batch from
        # starving independent events. Only one page is retained in memory.
        paths = (self.directory / 'pending').glob('*/*.json')
        selected = heapq.nsmallest(limit, (p for p in paths if p.stem > after), key=lambda p: p.stem)
        if not selected and after:
            selected = heapq.nsmallest(limit, (self.directory / 'pending').glob('*/*.json'), key=lambda p: p.stem)
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
        def candidates():
            for area in ('pending', 'receipts'):
                for path in (self.directory / area).glob('*/*.json'):
                    if area == 'pending' and self._event_path('receipts', path.stem).exists():
                        continue
                    try:
                        yield path.stat().st_mtime_ns, path.stem, path
                    except FileNotFoundError:
                        continue
        selected=heapq.nlargest(limit,candidates(),key=lambda entry:(entry[0],entry[1]))
        rows=[]
        for _,key,path in selected:
            row=self._read_file(path)
            if row is not None:
                self._validate(key,row)
                rows.append(self._receipt(key,row,duplicate=False))
        return rows

    @staticmethod
    def _receipt(key: str, row: dict[str, Any], *, duplicate: bool) -> dict[str, Any]:
        return {'receipt_id': key, 'accepted': True, 'duplicate': duplicate,
                'stage': row['stage'], 'item_id': row['item_id'],
                'recap': ' '.join(row['text'].split())[:500], 'original': row['text'],
                'execution_approved': False, 'external_ack': row.get('external_ack', 'not_sent'),
                'processing': row.get('processing'), 'side_failure': row.get('side_failure'),
                'main_fallback': row.get('main_fallback'), 'context': row.get('context'),
                'result': row.get('result')}
