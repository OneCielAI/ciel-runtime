"""Durable side-request receipt storage independent of Work board capacity."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from .channel_message_repository import exclusive_file_lock
from .work_plan_tree import WorkPlanError

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024


class SideIntakeInbox:
    def __init__(self, state_dir: Path, session_id: str) -> None:
        from .work_session_lineage import IDENTITY
        if not isinstance(session_id, str) or not IDENTITY.fullmatch(session_id):
            raise WorkPlanError('runtime-qualified session identity required')
        digest = hashlib.sha256(session_id.encode()).hexdigest()
        self.path = Path(state_dir) / 'side-inboxes' / digest / 'inbox.json'

    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_DOCUMENT_BYTES + 1)
        except FileNotFoundError:
            return {'version': 0, 'requests': {}}
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise WorkPlanError('side inbox exceeds size limit')
        try:
            doc = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise WorkPlanError('side inbox is unreadable; original preserved') from error
        if (not isinstance(doc, dict) or type(doc.get('version')) is not int
                or not isinstance(doc.get('requests'), dict)):
            raise WorkPlanError('side inbox schema is invalid; original preserved')
        for key, row in doc['requests'].items():
            if (not isinstance(key, str) or len(key) != 64 or not isinstance(row, dict)
                    or not isinstance(row.get('text'), str) or len(row['text']) > 16000
                    or not isinstance(row.get('event_key'), str)
                    or row.get('digest') != hashlib.sha256(row['text'].encode()).hexdigest()
                    or key != hashlib.sha256(row['event_key'].encode()).hexdigest()
                    or row.get('stage') not in ('received', 'board_queued')
                    or not (row.get('item_id') is None or isinstance(row.get('item_id'), str))):
                raise WorkPlanError('side inbox row is invalid; original preserved')
        return doc

    def receive(self, text: str, event_key: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise WorkPlanError('side intake text must contain 1-16000 characters')
        if not isinstance(event_key, str) or not event_key.strip() or len(event_key) > 300:
            raise WorkPlanError('stable event key required (1-300 characters)')
        key = hashlib.sha256(event_key.encode()).hexdigest()
        digest = hashlib.sha256(text.encode()).hexdigest()
        with exclusive_file_lock(self.path):
            doc = self._read()
            existing = doc['requests'].get(key)
            if existing is not None:
                if not isinstance(existing, dict) or existing.get('digest') != digest:
                    raise WorkPlanError('event key reused with different content')
                return self._receipt(key, existing, duplicate=True)
            row = {'text': text, 'event_key': event_key, 'digest': digest,
                   'received_at': time.time(), 'stage': 'received', 'item_id': None,
                   'external_ack': 'not_sent'}
            doc['requests'][key] = row
            doc['version'] += 1
            self._save(doc)
            return self._receipt(key, row, duplicate=False)

    def link(self, receipt_id: str, item_id: str) -> None:
        with exclusive_file_lock(self.path):
            doc = self._read()
            row = doc['requests'].get(receipt_id)
            if not isinstance(row, dict):
                raise WorkPlanError('side receipt missing')
            if row.get('item_id') not in (None, item_id):
                raise WorkPlanError('side receipt already linked to another item')
            row.update(item_id=item_id, stage='board_queued')
            doc['version'] += 1
            self._save(doc)

    def pending(self, limit: int = 10) -> list[dict[str, Any]]:
        with exclusive_file_lock(self.path):
            doc = self._read()
            return [dict(row, receipt_id=key) for key, row in doc['requests'].items()
                    if row.get('stage') == 'received'][:max(0, min(limit, 100))]

    def summaries(self, limit: int = 10) -> list[dict[str, Any]]:
        with exclusive_file_lock(self.path):
            rows = list(self._read()['requests'].items())[-max(1, min(limit, 100)):]
            return [self._receipt(key, row, duplicate=False) for key, row in rows]

    def _save(self, doc: dict[str, Any]) -> None:
        raw = json.dumps(doc, ensure_ascii=False).encode('utf8')
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise WorkPlanError('side inbox capacity reached; not acknowledged')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'.inbox.{os.getpid()}.{time.time_ns()}.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _receipt(key: str, row: dict[str, Any], *, duplicate: bool) -> dict[str, Any]:
        return {'receipt_id': key, 'accepted': True, 'duplicate': duplicate,
                'stage': row['stage'], 'item_id': row['item_id'],
                'recap': ' '.join(row['text'].split())[:500],
                'execution_approved': False, 'external_ack': row['external_ack']}
