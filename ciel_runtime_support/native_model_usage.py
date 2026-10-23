# Fresh exact-session native usage for the model safeguard.
import hashlib
import json
import os
import math
from dataclasses import dataclass
from pathlib import Path
from .provider_model_identity import ProviderModelIdentityService as Models
from .providers.cielairouter import CielAiRouterProviderAdapter
from .runtime_compaction_control import _timestamp

@dataclass(frozen=True)
class NativeModelUsage:
    identity: str
    tokens: int

def latest_native_model_usage(path, scope, since, model):
    if path is None or scope.runtime != 'claude' or not since or not model:
        return None
    adapter = CielAiRouterProviderAdapter()
    names = {Models.strip_claude_context_suffix(model).strip()}
    names.add(adapter.upstream_api_model_id(adapter.normalize_model_id(model)))
    names.update('ciel-runtime-cielairouter-' + Models.slug(name) for name in tuple(names) if name)
    names = {name.casefold() for name in names if name}
    latest = None
    newest_boundary_at = 0.0
    try:
        with Path(path).open('rb') as stream:
            before = os.fstat(stream.fileno())
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 2 * 1024 * 1024))
            data = stream.read(2 * 1024 * 1024)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                return None
    except OSError:
        return None
    if size > 2 * 1024 * 1024:
        data = data.partition(bytes([10]))[2]
    for line in data.splitlines(keepends=True):
        if not line.endswith(bytes([10])):
            continue
        try:
            r = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if not isinstance(r, dict) or r.get('sessionId') != scope.session_id or r.get('isSidechain') is True:
            continue
        if r.get('type') == 'system' and r.get('subtype') == 'compact_boundary':
            latest = None
            newest_boundary_at = max(newest_boundary_at, _timestamp(r.get('timestamp')))
        if r.get('type') != 'assistant':
            continue
        latest = None
        m = r.get('message')
        if not isinstance(m, dict):
            continue
        usage = m.get('usage')
        native_model = m.get('model')
        identity = m.get('id') or r.get('uuid')
        observed_at = _timestamp(r.get('timestamp'))
        if (not math.isfinite(observed_at) or observed_at < max(since, newest_boundary_at)
                or not isinstance(native_model, str)
                or Models.strip_claude_context_suffix(native_model).strip().casefold() not in names
                or not isinstance(identity, str) or not identity or not isinstance(usage, dict)):
            continue
        values = [usage.get(k) for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')]
        if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values):
            key = hashlib.sha256((scope.epoch + ':' + model.casefold() + ':' + identity).encode()).hexdigest()
            latest = NativeModelUsage(key, sum(values))
    return latest
