"""Secret-free router outcomes for generation-fenced side model failures."""
import json
import time
from pathlib import Path

from .channel_message_repository import exclusive_file_lock
from .durable_json_file import reader, save


def _path(state_dir, identity, agent_id, receipt_id):
    import hashlib
    key = hashlib.sha256((identity + '\0' + agent_id + '\0' + receipt_id).encode()).hexdigest()
    return Path(state_dir) / 'side-outcomes' / key[:2] / (key + '.json')


def record(state_dir, identity, agent_id, receipt_id, outcome):
    path = _path(state_dir, identity, agent_id, receipt_id)
    value = {k: outcome.get(k) for k in ('status','code','terminal_reason','pool_size',
             'attempted','requested_model','effective_model','message') if outcome.get(k) is not None}
    value.update(identity=identity, agent_id=agent_id, receipt_id=receipt_id,
                 recorded_at=time.time())
    if isinstance(value.get('message'), str):
        value['message'] = value['message'][:300]
    with exclusive_file_lock(path):
        save(path, value)


def take(state_dir, identity, agent_id, receipt_id):
    candidates = [_path(state_dir, identity, agent_id, receipt_id)] if agent_id else []
    # Agent ids seen by Claude hooks and HTTP headers are not guaranteed to use
    # one spelling across all native hosts. Always fall back to the exact parent
    # identity + receipt when the direct spelling has no file.
    if not candidates or not candidates[0].exists():
        candidates = list((Path(state_dir) / 'side-outcomes').glob('*/*.json'))
    matches = []
    for path in candidates:
        try:
            with reader(path) as stream:
                value = json.loads(stream.read(8193))
        except FileNotFoundError:
            continue
        if isinstance(value, dict) and value.get('identity') == identity and value.get('receipt_id') == receipt_id:
            matches.append((path, value))
    if len(matches) != 1:
        return None
    path, value = matches[0]
    with exclusive_file_lock(path):
        path.unlink(missing_ok=True)
    return value
