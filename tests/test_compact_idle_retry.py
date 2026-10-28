"""Exact native compact failure must unlock bounded retry, not admit input."""
from dataclasses import replace
from datetime import datetime, timezone

import test_runtime_compaction_control as fixtures
from ciel_runtime_support.runtime_compaction_control import (
    CompactionScope, _compact_idle_failure, _native_transcript_evidence,
)


def failure(scope, when, identity='idle-1', automatic=False):
    row = dict(type='system', subtype='local_command', sessionId=scope.session_id,
               uuid=identity, timestamp=datetime.fromtimestamp(when, timezone.utc).isoformat(),
               isSidechain=False, commandRun=dict(command='compact', args=''),
               commandOutcome=dict(kind='failed'), content=(
                   '<local-command-stderr>Error during compaction: API Error: '
                   'Stream idle timeout - no chunks received</local-command-stderr>'))
    if automatic:
        row.update(type='assistant', isApiErrorMessage=True, message=dict(content=[dict(
            type='text', text='Prompt is too long · automatic compaction failed: API Error: '
            'Stream idle timeout - no chunks received')]))
    return row


class CompactIdleRetryTests(fixtures._ControllerHarness):
    def setUp(self):
        super().setUp()
        self.control = self.factory()
        self.control.ports = replace(self.control.ports, now=lambda: self.clock)

    def native_start(self):
        self.request(body={'messages': [{'role': 'user', 'content': '<command-name>/compact</command-name>'}]})
        self.assertEqual('compacting', self.snapshot()['phase'])

    def test_manual_timeout_retries_without_releasing_admission(self):
        self.native_start()
        self.clock += 1
        self.append(failure(self.scope, self.clock))
        self.assertTrue(self.control.external_admission_paused())
        state = self.snapshot()
        self.assertEqual('retry_wait', state['phase'])
        self.assertEqual('native_compact_idle_timeout', state['failure'])
        self.assertNotEqual('verified', state['phase'])
        self.assertIsNone(self.repository.read())
        self.clock += 4
        self.assertTrue(self.control.external_admission_paused())
        req = self.repository.read()
        self.assertTrue(req['automatic'])
        self.assertEqual(self.scope.payload(), req['scope'])
        self.assertEqual(2, self.snapshot()['attempts'])
        self.assertTrue(self.control.command_ready(req))
        self.control.command_submitted(req)
        self.repository.clear(req['id'])
        self.native_start()
        self.clock += 1
        self.append(fixtures.boundary(self.scope.session_id, self.clock))
        self.request(120)
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual('verified', self.snapshot()['phase'])
        print('idle_retry first=retry_wait admission=paused retry=queued scope=exact checkpoint+shrink=verified')

    def test_automatic_failure_and_restart_spend_same_bounded_budget(self):
        self.native_start()
        self.clock += 1
        self.append(failure(self.scope, self.clock, automatic=True))
        self.assertTrue(self.control.external_admission_paused())
        self.clock += 4
        self.assertTrue(self.control.external_admission_paused())
        req = self.repository.read()
        self.assertTrue(self.control.command_ready(req))
        self.control.command_submitted(req)
        self.repository.clear(req['id'])
        self.native_start()
        self.clock += 1
        self.append(failure(self.scope, self.clock, identity='idle-2', automatic=True))
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual('blocked', self.snapshot()['phase'])
        self.control = self.factory()
        self.control.ports = replace(self.control.ports, now=lambda: self.clock)
        self.clock += 100
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual(2, self.snapshot()['attempts'])
        self.assertIsNone(self.repository.read())

    def test_stale_failure_cannot_cancel_manual_retry(self):
        self.native_start()
        self.clock += 1
        self.append(failure(self.scope, self.clock))
        self.control.external_admission_paused()
        self.clock += 4
        self.native_start()
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual('compacting', self.snapshot()['phase'])

    def test_failure_before_current_dispatch_is_not_replayed(self):
        self.append(failure(self.scope, self.clock))
        self.clock += 2
        self.native_start()
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual('compacting', self.snapshot()['phase'])

    def test_newer_failure_invalidates_older_checkpoint(self):
        self.native_start()
        self.clock += 1
        cp = fixtures.boundary(self.scope.session_id, self.clock)
        cp['compactMetadata']['trigger'] = 'manual'
        self.append(cp)
        self.clock += 1
        self.append(failure(self.scope, self.clock))
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual('retry_wait', self.snapshot()['phase'])
        self.assertTrue(self.snapshot()['baseline_checkpoint_id'])
        self.assertNotEqual('verified', self.snapshot()['phase'])

    def test_newer_checkpoint_wins_over_failure(self):
        self.native_start()
        self.clock += 1
        self.append(failure(self.scope, self.clock))
        self.clock += 1
        cp = fixtures.boundary(self.scope.session_id, self.clock)
        cp['compactMetadata']['trigger'] = 'manual'
        self.append(cp)
        self.assertFalse(self.control.external_admission_paused())
        self.assertEqual('verified', self.snapshot()['phase'])
        self.assertIsNone(self.repository.read())

    def test_no_native_failure_never_retries_uncertain_timeout(self):
        self.native_start()
        self.clock += 20
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual('checkpoint_unconfirmed', self.snapshot()['failure'])
        self.assertIsNone(self.repository.read())

    def test_failure_scope_and_verdict_are_strict(self):
        valid = failure(self.scope, self.clock)
        self.assertEqual(self.clock, _compact_idle_failure(valid, self.scope))
        for changes in ({'sessionId': 'other'}, {'isSidechain': True}, {'uuid': ''},
                        {'type': 'user'}, {'commandOutcome': {'kind': 'unknown'}},
                        {'commandRun': {'command': 'clear'}}, {'content': 'no chunks received'},
                        {'timestamp': 'bad'}):
            row = {**valid, **changes}
            self.assertEqual(0, _compact_idle_failure(row, self.scope), changes)
        self.assertEqual(0, _compact_idle_failure(valid, CompactionScope('codex', 'session-a', 'launch-a')))
        self.append(valid)
        ev = _native_transcript_evidence(self.trace, self.scope)
        self.assertIsNotNone(ev.failure)
        self.trace.write_text('', encoding='utf-8')
        self.assertIsNone(_native_transcript_evidence(self.trace, self.scope).failure)

    def test_terminal_failure_after_confirmation_timeout_recovers_once(self):
        self.native_start()
        self.clock += 20
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual('blocked', self.snapshot()['phase'])
        self.append(failure(self.scope, self.clock))
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual('retry_wait', self.snapshot()['phase'])
        self.clock += 4
        self.control.external_admission_paused()
        self.assertEqual(2, self.snapshot()['attempts'])
        first = self.repository.read()['id']
        self.control.external_admission_paused()
        self.assertEqual(first, self.repository.read()['id'])

    def test_future_and_partial_failure_never_authorize_retry(self):
        self.native_start()
        self.append(failure(self.scope, self.clock + 100))
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual('compacting', self.snapshot()['phase'])
        self.trace.write_text('', encoding='utf-8')
        import json
        raw = json.dumps(failure(self.scope, self.clock + 1))
        self.trace.write_text(raw, encoding='utf-8')
        self.clock += 1
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual('compacting', self.snapshot()['phase'])
        with self.trace.open('a', encoding='utf-8') as stream:
            stream.write('\n')
        self.assertTrue(self.control.external_admission_paused())
        self.assertEqual('retry_wait', self.snapshot()['phase'])

    def test_failure_scan_concurrent_append_is_not_committed(self):
        from unittest.mock import patch
        import ciel_runtime_support.runtime_compaction_control as module
        self.native_start()
        self.clock += 1
        self.append(failure(self.scope, self.clock))
        original = module.native_checkpoint_from_transcript
        before = self.snapshot()
        def changed(path, scope):
            proof = original(path, scope)
            self.append(fixtures.boundary('other-session', self.clock + 1))
            return proof
        with patch.object(module, 'native_checkpoint_from_transcript', side_effect=changed):
            self.assertTrue(self.control.external_admission_paused())
        self.assertEqual(before, self.snapshot())
