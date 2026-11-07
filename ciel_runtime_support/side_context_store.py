"""Host-bound parent request snapshots for native side actors on the router path."""
import copy
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from .channel_message_repository import exclusive_file_lock
from .durable_json_file import reader, save
from .side_context_projection import SideContextUnavailable, digest

MAX_BYTES = 64 * 1024 * 1024
MAX_PARENT_BYTES = 16 * 1024 * 1024
_KEY = re.compile(r'^[0-9a-f]{32}$')
_ID = re.compile(r'^claude:[A-Za-z0-9_-]{1,200}$')
_SECRET = re.compile(r'(?i)(bearer\s+|sk-|token["\s:=]+)[A-Za-z0-9._~+/=-]{8,}')


class SideContextStore:
    def __init__(self, state_dir, identity):
        if not isinstance(identity, str) or not _ID.fullmatch(identity):
            raise SideContextUnavailable('exact parent conversation required')
        self.identity = identity
        self.directory = Path(state_dir) / 'side-contexts' / hashlib.sha256(identity.encode()).hexdigest()

    def path(self, key):
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise SideContextUnavailable('invalid side context handle')
        return self.directory / 'actors' / (key + '.json')

    @staticmethod
    def read(path):
        try:
            with reader(path) as stream:
                raw = stream.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(raw) > MAX_BYTES:
            raise SideContextUnavailable('side context snapshot exceeds capacity')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise SideContextUnavailable('side context snapshot invalid')
        return value

    def ready(self):
        """Return current capture metadata only for this launch's exact session."""
        latest = self.read(self.directory / 'latest.json')
        if not latest or latest.get('unavailable') or not isinstance(latest.get('parent'), dict):
            return None
        if latest.get('epoch'):
            from .runtime_paths import ROUTER_INSTANCE_DIR
            current = self.read(ROUTER_INSTANCE_DIR / 'compaction-scope.json')
            if (not current or current.get('epoch') != latest['epoch']
                    or 'claude:' + str(current.get('session_id')) != self.identity):
                return None
        return latest

    @staticmethod
    def preview(parent, *, limit=12, chars=6000):
        """Bounded readable transcript preview; exact history stays router-only."""
        rows = []
        for message in parent.get('messages', []):
            if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant'):
                continue
            content = message.get('content')
            blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else content
            if not isinstance(blocks, list):
                continue
            text = []
            for block in blocks:
                if not isinstance(block, dict) or block.get('type') != 'text':
                    continue
                value = block.get('text')
                if isinstance(value, str) and value.strip():
                    text.append(value.strip())
            if text:
                value = _SECRET.sub(r'\1‹hidden›', '\n'.join(text))[:1000]
                rows.append({'role': message['role'], 'text': value})
        selected = rows[-limit:]
        while selected and len(json.dumps(selected, ensure_ascii=False)) > chars:
            selected.pop(0)
        return selected

    def capture(self, body, provider, epoch=None):
        # No credential headers, host tokens or full config are stored here.
        parent = {key: copy.deepcopy(body[key]) for key in ('system', 'tools', 'messages', 'model') if key in body}
        oversized = len(json.dumps(parent).encode()) > MAX_PARENT_BYTES
        value = {'provider': provider, 'captured_at': time.time(), 'epoch': epoch}
        if oversized:
            # A rejected new capture must not make an old snapshot look current.
            # Existing actor files retain their frozen history for in-flight work.
            value['unavailable'] = 'parent snapshot exceeds capacity'
        else:
            value.update(parent=parent, digest=digest(parent))
        latest_path = self.directory / 'latest.json'
        with exclusive_file_lock(latest_path):
            previous = self.read(latest_path)
            if previous and previous.get('captured_at', 0) > value['captured_at']:
                return previous
            save(latest_path, value)
        if oversized:
            raise SideContextUnavailable(value['unavailable'])
        return value

    def prepare(self, event, settings, model, *, key=None, shadow=False, snapshot=None):
        profile = {'mode': settings.get('context_mode', 'full'),
                   'percent': settings.get('context_percent', 30), 'model': model, 'shadow': shadow}
        if profile['mode'] not in ('full', 'recent'):
            raise SideContextUnavailable('parent context profile not enabled')
        if not isinstance(model, str) or not model or len(model) > 300:
            raise SideContextUnavailable('resolved side model required')
        key = key or uuid.uuid4().hex
        path = self.path(key)
        rebase = False
        previous_info = None
        with exclusive_file_lock(path):
            value = self.read(path)
            latest = snapshot or self.ready()
            if not latest:
                raise SideContextUnavailable('current parent capture unavailable; stale context not reused')
            if value is not None and not shadow and latest and (
                latest.get('digest') != value.get('digest') or latest.get('epoch') != value.get('epoch')
                or latest.get('provider') != value.get('provider')
            ):
                # Never insert a changed parent prefix ahead of already-signed
                # side history. Rebase only at an event boundary/new parent version.
                rebase = True
                previous_info = {'previous_digest': value['digest'], 'previous_captured_at': value['captured_at']}
            if value is None:
                latest = snapshot or self.read(self.directory / 'latest.json')
                if not latest:
                    raise SideContextUnavailable('parent request not observed on this router; direct path cannot project history')
                value = {**latest, 'identity': self.identity, 'profile': profile, 'agent_id': None,
                         'projection': None, 'selection': None}
            elif value.get('profile') != profile or value.get('identity') != self.identity:
                raise SideContextUnavailable('side context profile changed; a new actor is required')
            if not rebase:
                value.update(receipt_id=event['receipt_id'], generation=event['generation'])
                save(path, value)
        if rebase:
            return {**self.prepare(event, settings, model, snapshot=latest), 'rebased': True, **previous_info}
        return {'key': key, 'captured_at': value['captured_at'], 'parent_digest': value['digest'],
                'profile': profile, 'selection': value.get('selection'),
                'parent_transcript_preview': self.preview(value['parent'])}
