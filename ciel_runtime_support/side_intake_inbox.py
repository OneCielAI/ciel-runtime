"""Per-event durable mailbox; consumption commits a receipt before unlinking mail."""
from __future__ import annotations

import hashlib
import heapq
import json
import os
from pathlib import Path
import time
from typing import Any

from .channel_message_repository import exclusive_file_lock
from .work_plan_tree import WorkPlanError

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024  # Legacy document parser, not a queue ceiling.
MAX_EVENT_BYTES = 128 * 1024


class SideIntakeInbox:
    def __init__(self, state_dir: Path, session_id: str) -> None:
        from .work_session_lineage import IDENTITY
        if not isinstance(session_id, str) or not IDENTITY.fullmatch(session_id):
            raise WorkPlanError('runtime-qualified session identity required')
        digest = hashlib.sha256(session_id.encode()).hexdigest()
        self.path = Path(state_dir) / 'side-inboxes' / digest / 'inbox.json'
        self.directory = self.path.parent

    def _event_path(self, area: str, key: str) -> Path:
        if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise WorkPlanError('invalid side receipt identity')
        return self.directory / area / key[:2] / (key + '.json')

    @staticmethod
    def _read_file(path: Path, limit: int = MAX_EVENT_BYTES) -> dict[str, Any] | None:
        try:
            with path.open('rb') as stream:
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
                or len(row['text']) > 16000 or not isinstance(row.get('event_key'), str)
                or row.get('digest') != hashlib.sha256(row['text'].encode()).hexdigest()
                or key != hashlib.sha256(row['event_key'].encode()).hexdigest()
                or row.get('stage') not in ('received', 'board_queued')
                or not (row.get('item_id') is None or isinstance(row.get('item_id'), str))):
            raise WorkPlanError('side inbox row is invalid; original preserved')

    @staticmethod
    def _save_file(path: Path, row: dict[str, Any]) -> None:
        raw = json.dumps(row, ensure_ascii=False).encode('utf8')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f'.{path.stem}.{os.getpid()}.{time.time_ns()}.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name != 'nt':
                descriptor = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

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

    def receive(self, text: str, event_key: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise WorkPlanError('side intake text must contain 1-16000 characters')
        if not isinstance(event_key, str) or not event_key.strip() or len(event_key) > 300:
            raise WorkPlanError('stable event key required (1-300 characters)')
        self._migrate()
        key = hashlib.sha256(event_key.encode()).hexdigest()
        digest = hashlib.sha256(text.encode()).hexdigest()
        with exclusive_file_lock(self._event_path('locks', key)):
            existing = self._load(key)
            if existing is not None:
                if existing['digest'] != digest:
                    raise WorkPlanError('event key reused with different content')
                if existing['item_id']:
                    self._event_path('pending', key).unlink(missing_ok=True)
                return self._receipt(key, existing, duplicate=True)
            row = {'text': text, 'event_key': event_key, 'digest': digest,
                   'received_at': time.time(), 'stage': 'received', 'item_id': None,
                   'external_ack': 'not_sent'}
            self._save_file(self._event_path('pending', key), row)
            return self._receipt(key, row, duplicate=False)

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
        def records():
            for area in ('pending', 'receipts'):
                for path in (self.directory / area).glob('*/*.json'):
                    row = self._read_file(path)
                    if row is not None:
                        self._validate(path.stem, row)
                        if area == 'pending' and self._event_path('receipts', path.stem).exists():
                            continue
                        yield row.get('received_at', 0), path.stem, row
        rows = heapq.nlargest(limit, records(), key=lambda entry: (entry[0], entry[1]))
        return [self._receipt(key, row, duplicate=False) for _, key, row in rows]

    @staticmethod
    def _receipt(key: str, row: dict[str, Any], *, duplicate: bool) -> dict[str, Any]:
        return {'receipt_id': key, 'accepted': True, 'duplicate': duplicate,
                'stage': row['stage'], 'item_id': row['item_id'],
                'recap': ' '.join(row['text'].split())[:500],
                'execution_approved': False, 'external_ack': row.get('external_ack', 'not_sent')}
