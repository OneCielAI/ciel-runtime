"""Deterministically collide an owned delivery poller with BindingHarness."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / 'scripts' / 'run_test_group.py').is_file())
sys.path.insert(0, str(ROOT))
for name in tuple(os.environ):
    if name.startswith(('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC')):
        os.environ.pop(name)
from scripts.run_test_group import isolate_runtime_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with isolate_runtime_state() as isolated:
        os.environ['CLAUDE_CONFIG_DIR'] = str(Path(isolated) / 'claude')
        sys.path.insert(0, str(ROOT / 'tests'))
        import ciel_runtime
        import ciel_runtime_support.channel_transcript_repository as transcripts
        import ciel_runtime_support.runtime_compaction_binding as binding_module
        from ciel_runtime_support.transcript_delta_delivery import (
            TranscriptDeliveryPorts, TranscriptDeltaDeliveryService)
        from test_model_compact_safeguard import SafeguardTests
        original_scan = transcripts.ChannelTranscriptRepository._scan_latest
        original_usage = binding_module.latest_native_model_usage
        records = []
        for iteration in range(args.repeat):
            enter = threading.Event()
            entered = threading.Event()
            release = threading.Event()
            events = []
            case = SafeguardTests('test_high_fresh_metadata')
            original_setup = case.setUp
            result = unittest.TestResult()
            with tempfile.TemporaryDirectory(prefix='owned-poller-') as raw:
                root = Path(raw)
                foreign = transcripts.ChannelTranscriptRepository(
                    root, {}, {'runtime': 'claude', 'claude_config_dir': str(root / 'claude')}, time.time)
                def latest():
                    while not enter.is_set():
                        if service._stop.wait(0.01):
                            return None
                    if service._stop.is_set():
                        return None
                    return foreign.latest(ttl_seconds=0)
                service = TranscriptDeltaDeliveryService(root / 'cursors.json', 'owned-safeguard-repro',
                    TranscriptDeliveryPorts(lambda: {}, latest, lambda: {'runtime': 'claude'},
                                            lambda *values: events.append({'event': 'poller_log', 'values': values})))
                def scan(repository, now):
                    if repository is foreign:
                        events.append({'event': 'foreign_scan_entered',
                                       'worker': id(transcripts._TRANSCRIPT_SCAN_WORKER),
                                       'thread': threading.current_thread().name})
                        entered.set()
                        if not release.wait(10):
                            raise TimeoutError('owned poller release missing')
                        return None
                    return original_scan(repository, now)
                def usage(*values, **kwargs):
                    sample = original_usage(*values, **kwargs)
                    events.append({'event': 'usage', 'path': str(values[0]),
                                   'sample': dataclasses.asdict(sample) if sample else None})
                    return sample
                def setup():
                    original_setup()
                    before = case.binding.before_external_injection
                    def boundary():
                        response = before()
                        events.append({'event': 'before', 'response': response,
                            'scope': case.binding.repository.load('diagnostic'),
                            'cache': dict(case.binding.cache), 'state': case.state(),
                            'logs': list(case.logs), 'transcript': case.path.read_text(encoding='utf-8')})
                        return response
                    case.binding.before_external_injection = boundary
                    enter.set()
                    if service._thread.is_alive():
                        if not entered.wait(5):
                            raise AssertionError('owned poller never acquired the fixture worker')
                    events.append({'event': 'fixture_setup', 'poller_alive': service._thread.is_alive(),
                                   'fixture_worker': id(transcripts._TRANSCRIPT_SCAN_WORKER)})
                    case.addCleanup(release.set)
                case.setUp = setup
                with patch.object(ciel_runtime, '_TRANSCRIPT_DELIVERY_SERVICE', service), \
                     patch.object(transcripts.ChannelTranscriptRepository, '_scan_latest', scan), \
                     patch.object(binding_module, 'latest_native_model_usage', usage):
                    service.start()
                    started = time.perf_counter()
                    try:
                        case.run(result)
                    finally:
                        release.set()
                        service.stop(timeout=5)
                    records.append({'iteration': iteration, 'success': result.wasSuccessful(),
                                    'elapsed': time.perf_counter() - started, 'events': events,
                                    'failures': [text for _, text in result.failures],
                                    'errors': [text for _, text in result.errors],
                                    'owned_poller_stopped': not service._thread.is_alive()})
        args.output.write_text(json.dumps(records, indent=2, default=str) + '\n', encoding='utf-8')
        print(json.dumps({'cases': len(records), 'passed': sum(r['success'] for r in records),
                          'failed': sum(not r['success'] for r in records),
                          'all_owned_pollers_stopped': all(r['owned_poller_stopped'] for r in records),
                          'evidence': str(args.output)}, indent=2))
        if any(not r['success'] for r in records):
            print('\n'.join(records[0]['failures'] + records[0]['errors']))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
