"""Secret-free router outcomes for generation-fenced side model failures."""
import json
import time
from pathlib import Path

from .channel_message_repository import exclusive_file_lock
from .durable_json_file import reader, save


def _path(state_dir, identity, agent_id):
    import hashlib
    key = hashlib.sha256((identity + '\0' + agent_id).encode()).hexdigest()
    return Path(state_dir) / 'side-outcomes' / key[:2] / (key + '.json')


def record(state_dir, identity, agent_id, outcome):
    path = _path(state_dir, identity, agent_id)
    value = {k: outcome.get(k) for k in ('status','code','terminal_reason','pool_size',
             'attempted','requested_model','effective_model','message') if outcome.get(k) is not None}
    value['recorded_at'] = time.time()
    if isinstance(value.get('message'), str):
        value['message'] = value['message'][:300]
    with exclusive_file_lock(path):
        save(path, value)


def take(state_dir, identity, agent_id):
    path = _path(state_dir, identity, agent_id)
    with exclusive_file_lock(path):
        try:
            with reader(path) as stream:
                value = json.loads(stream.read(8193))
        except FileNotFoundError:
            return None
        path.unlink(missing_ok=True)
    return value if isinstance(value, dict) else None
