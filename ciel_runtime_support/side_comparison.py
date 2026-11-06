"""Read-only comparison receipts; never apply Work changes or send replies."""
import hashlib
import json
import time

from .channel_message_repository import exclusive_file_lock
from .side_context_store import SideContextStore
from .side_intake_inbox import SideIntakeInbox
from .work_plan_tree import WorkPlanError


def freeze(state_dir, identity, receipt_id, workload):
    from .durable_json_file import save
    store = SideContextStore(state_dir, identity)
    path = store.directory / 'comparisons' / (receipt_id + '.json')
    with exclusive_file_lock(path):
        value = store.read(path)
        if value is None:
            snapshot = store.read(store.directory / 'latest.json')
            if not snapshot or snapshot.get('unavailable') or not isinstance(snapshot.get('parent'), dict):
                raise WorkPlanError('comparison parent snapshot unavailable')
            value = {'snapshot': snapshot, 'workload': workload}
            save(path, value)
        return value


def plan(state_dir, identity, receipt_id, parent_model, settings):
    inbox = SideIntakeInbox(state_dir, identity)
    row = inbox._load(receipt_id)
    if not row or row.get('stage') != 'processed':
        raise WorkPlanError('comparison requires a committed primary decision')
    if settings.get('comparison_enabled') is not True:
        raise WorkPlanError('side comparison disabled')
    model = settings.get('comparison_model', 'inherit')
    models = list(dict.fromkeys([parent_model, parent_model if model == 'inherit' else model]))
    profiles = [{'context_mode': mode, 'context_percent': settings.get('context_percent', 30), 'model': selected}
                for selected in models for mode in ('full', 'recent')]
    return [{'profile_id': hashlib.sha256(str(profile).encode()).hexdigest()[:16], **profile} for profile in profiles]


def context(state_dir, identity, receipt_id, profile, parent_model, settings):
    profiles = plan(state_dir, identity, receipt_id, parent_model, settings)
    selected = next((row for row in profiles if row['profile_id'] == profile), None)
    if selected is None:
        raise WorkPlanError('unknown comparison profile')
    inbox = SideIntakeInbox(state_dir, identity)
    row = inbox._load(receipt_id)
    generation = row['processing']['generation']
    contexts = SideContextStore(state_dir, identity)
    frozen = contexts.read(contexts.directory / 'comparisons' / (receipt_id + '.json'))
    if not frozen:
        raise WorkPlanError('comparison plan must freeze its Work snapshot first')
    prepared = contexts.prepare({'receipt_id': receipt_id, 'generation': generation},
                                selected, selected['model'], shadow=True, snapshot=frozen['snapshot'])
    return {**prepared, 'profile_id': profile, 'original': row['text'], 'primary': row['result'], 'workload': frozen['workload']}


def record(state_dir, identity, receipt_id, profile_id, result):
    if not isinstance(result, dict) or len(json.dumps(result, ensure_ascii=False).encode('utf8')) > 128 * 1024:
        raise WorkPlanError('bounded comparison result required (128 KiB)')
    if result.get('context_key'):
        contexts = SideContextStore(state_dir, identity)
        context = contexts.read(contexts.path(result['context_key']))
        if not context or context.get('receipt_id') != receipt_id or not context['profile'].get('shadow'):
            raise WorkPlanError('comparison context does not match receipt')
        result = {**result, 'profile': context['profile'], 'context': context.get('selection'),
                  'parent_digest': context['digest']}
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if not row or row.get('stage') != 'processed':
            raise WorkPlanError('primary side decision missing')
        rows = row['result'].setdefault('comparisons', {})
        if profile_id in rows:
            return inbox._receipt(receipt_id, row, duplicate=True)
        if len(rows) >= 4:
            raise WorkPlanError('comparison profile limit reached')
        # Store decision proposals as data only. None of the Work apply/reply
        # paths are called here, even when a shadow proposes a mutation.
        rows[profile_id] = {**result, 'recorded_at': time.time(), 'shadow': True,
                            'work_applied': False, 'reply_sent': False}
        inbox._save_file(inbox._event_path('receipts', receipt_id), row)
        return inbox._receipt(receipt_id, row, duplicate=False)
