import threading
from dataclasses import replace
from unittest.mock import Mock, patch

import test_runtime_compaction_binding as binding_fixtures
import test_runtime_compaction_control as fixtures
from ciel_runtime_support.native_model_usage import NativeModelUsage
from ciel_runtime_support.runtime_compaction_control import CompactionScope


class ContentionTests(fixtures._ControllerHarness):
    def test_slow_scan_releases_lock_and_rejects_newer_revision(self):
        self.request()
        entered, resume = threading.Event(), threading.Event()
        original = self.control.ports.checkpoint
        result, errors = [], []

        def slow(scope):
            self.assertFalse(self.lock.locked())
            proof = original(scope)
            entered.set()
            self.assertTrue(resume.wait(5))
            return proof

        control = self.controller()
        control.ports = replace(control.ports, checkpoint=slow)

        def run():
            try:
                result.append(control.external_admission_paused())
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertTrue(self.lock.acquire(timeout=1))
            try:
                newer = self.snapshot()
                newer['failure'] = 'concurrent_change'
                self.state.save(newer, 'test')
            finally:
                self.lock.release()
        finally:
            resume.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual([True], result)
        self.assertEqual(newer, self.snapshot())
        print('contention scan_lock_held=0 concurrent_writer=success stale_commit=rejected')

    def test_epoch_and_session_switch_discard_observation(self):
        for scope in (CompactionScope('claude', 'session-a', 'launch-b'),
                      CompactionScope('claude', 'session-b', 'launch-a')):
            with self.subTest(scope=scope):
                self.scope = CompactionScope('claude', 'session-a', 'launch-a')
                self.request()
                before = self.snapshot()
                original = self.control.ports.checkpoint
                def switch(current):
                    proof = original(current)
                    self.scope = scope
                    return proof
                control = self.controller()
                control.ports = replace(control.ports, checkpoint=switch)
                self.assertTrue(control.external_admission_paused())
                self.assertEqual(before, self.snapshot())

    def test_two_concurrent_dispatchers_claim_only_once(self):
        self.request()
        request = self.repository.read()
        barrier = threading.Barrier(2)
        controls = [self.controller(), self.controller()]
        original = self.control.ports.checkpoint
        def scan(scope):
            proof = original(scope)
            barrier.wait(5)
            return proof
        results, errors = [], []
        def run(control):
            try:
                results.append(control.command_ready(request))
            except Exception as exc:
                errors.append(exc)
        threads = []
        for control in controls:
            control.ports = replace(control.ports, checkpoint=scan)
            thread = threading.Thread(target=run, args=(control,))
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join(8)
            self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual([False, True], sorted(results))

    def test_no_sample_no_reservation_and_seen_sample_skip_full_scan(self):
        scan = Mock(return_value=None)
        self.control.ports = replace(self.control.ports, checkpoint=scan)
        self.control.observe_proactive(self.scope, None, 1000, 'model')
        self.assertFalse(self.control.external_admission_paused())
        scan.assert_not_called()
        sample = NativeModelUsage('one', 950)
        self.control.observe_proactive(self.scope, sample, 1000, 'model')
        self.control.observe_proactive(self.scope, None, 1000, 'other')
        scan.reset_mock()
        self.control.observe_proactive(self.scope, sample, 1000, 'model')
        scan.assert_not_called()

    def test_model_capacity_validation_conflict_cannot_reserve(self):
        self.control.observe_proactive(self.scope, NativeModelUsage('one', 950), 1000, 'model',
                                       validate=lambda: False)
        self.assertEqual({}, self.snapshot())
        self.assertIsNone(self.repository.read())

    def test_factory_rejects_path_replacement_or_append_after_scan(self):
        import ciel_runtime_support.runtime_compaction_control as module
        self.request()
        before = self.snapshot()
        original = module.native_checkpoint_from_transcript
        def changed(path, scope):
            proof = original(path, scope)
            self.append(fixtures.boundary('session-a', self.clock + 1))
            return proof
        with patch.object(module, 'native_checkpoint_from_transcript', side_effect=changed):
            self.assertTrue(self.factory().external_admission_paused())
        self.assertEqual(before, self.snapshot())

    def test_shrink_deadline_stays_gated_then_accepts_late_real_proof(self):
        self.request()
        request = self.repository.read()
        self.assertTrue(self.control.command_ready(request))
        self.control.command_submitted(request)
        self.repository.clear(request['id'])
        self.clock += 1
        self.append(fixtures.boundary('session-a', self.clock))
        self.assertTrue(self.control.external_admission_paused())
        self.clock += 11
        self.assertTrue(self.control.external_admission_paused())
        state = self.snapshot()
        self.assertEqual('awaiting_shrink', state['phase'])
        self.assertEqual('shrink_unconfirmed', state['failure'])
        self.assertEqual(self.clock, state['failure_at'])
        self.assertIsNone(self.repository.read())
        self.request(120)
        self.assertFalse(self.control.external_admission_paused())
        self.assertNotIn('failure', self.snapshot())

    def test_one_shot_overflow_survives_repeated_observation_conflicts(self):
        self.request()
        original = self.control.ports.checkpoint
        calls = []
        def race(scope):
            proof = original(scope)
            current = self.snapshot()
            current.update(phase='verified', race=len(calls))
            self.state.save(current, 'test')
            calls.append(1)
            return proof
        self.control.ports = replace(self.control.ports, checkpoint=race)
        self.control.observe_request({'messages': []}, 1400, 1000, recovery_required=True)
        self.assertEqual(2, len(calls))
        self.assertEqual('queued', self.snapshot()['phase'])
        self.assertTrue(self.snapshot()['pressure'])
        self.assertEqual(1400, self.snapshot()['baseline_tokens'])

    def test_sole_small_request_is_saved_without_using_stale_checkpoint(self):
        self.request()
        self.clock += 1
        self.append(fixtures.boundary('session-a', self.clock))
        self.assertTrue(self.control.external_admission_paused())
        original = self.control.ports.checkpoint
        def race(scope):
            proof = original(scope)
            current = self.snapshot()
            current['race'] = current.get('race', 0) + 1
            self.state.save(current, 'test')
            return proof
        self.control.ports = replace(self.control.ports, checkpoint=race)
        self.request(120)
        self.assertEqual('awaiting_shrink', self.snapshot()['phase'])
        self.assertEqual(120, self.snapshot()['shrink_observed']['tokens'])
        self.control.ports = replace(self.control.ports, checkpoint=original)
        self.assertFalse(self.control.external_admission_paused())

    def test_real_lock_error_keeps_admission_closed(self):
        from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
        self.control.ports = replace(self.control.ports, lock=Mock(side_effect=ChannelFileLockBusy(13, 'busy')))
        self.assertTrue(self.control.external_admission_paused())
        self.assertIsNone(self.repository.read())


class FullBindingContentionTests(binding_fixtures.BindingHarness):
    def test_concurrent_state_reads_never_break_atomic_replacement(self):
        import time
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        scope = self.binding.scope()
        control = self.binding.control
        stop = time.monotonic() + 1.0
        failures = []
        def scope_writer():
            number = 0
            while time.monotonic() < stop:
                number += 1
                try:
                    self.binding._update(scope.epoch, failed_probe=str(number))
                except Exception as exc:
                    failures.append(('scope_save', type(exc).__name__))
        def admission_writer():
            number = 0
            while time.monotonic() < stop:
                number += 1
                try:
                    with control.ports.lock():
                        control.ports.save({'scope': scope.payload(), 'phase': 'verified', 'n': number})
                except Exception as exc:
                    failures.append(('admission_save', type(exc).__name__))
        def reader(read, name):
            while time.monotonic() < stop:
                try:
                    if read() is None:
                        failures.append((name, 'unreadable'))
                except Exception as exc:
                    failures.append((name, type(exc).__name__))
        scope_read = lambda: self.binding._update('')  # noqa: E731
        admission_read = lambda: self.binding._admission_state()  # noqa: E731
        threads = [threading.Thread(target=scope_writer), threading.Thread(target=admission_writer),
                   threading.Thread(target=reader, args=(scope_read, 'scope_read')),
                   threading.Thread(target=reader, args=(admission_read, 'admission_read'))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
            self.assertFalse(thread.is_alive())
        self.assertEqual([], sorted(set(failures)))
        self.assertEqual([], list(self.root.glob('compaction-*.json.*.tmp')))
        print('state_race failures=0 leftover_tmp=0')

    def test_disabling_safeguard_cancels_queued_reservation(self):
        self.provider = 'cielairouter'
        config = {'current_model': 'ASTRA', 'compact_safeguard': True}
        self.binding.config = lambda: (self.provider, config)
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        self.write({'type': 'assistant', 'sessionId': 'exact', 'timestamp': binding_fixtures.stamp(__import__('time').time()),
                    'message': {'id': 'disable', 'model': 'ASTRA', 'usage': {
                        'input_tokens': 19000, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}})
        self.assertFalse(self.binding.external_admission_paused())
        request = self.requests.read()
        self.assertIsNotNone(request)
        config['compact_safeguard'] = False
        self.assertFalse(self.binding.command_ready(request))
        self.assertIsNone(self.requests.read())
        self.assertEqual('verified', self.state()['phase'])

    def test_slow_checkpoint_does_not_hold_seed_or_scope_or_admission_lock(self):
        import ciel_runtime_support.runtime_compaction_control as module
        from ciel_runtime_support.channel_message_repository import exclusive_file_lock
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        self.binding.observe_request('other', {}, {'tokens': 16000, 'native': 'claude'}, 'claude')
        entered, release = threading.Event(), threading.Event()
        original = module.native_checkpoint_from_transcript
        result = []
        def slow(path, scope):
            proof = original(path, scope)
            entered.set()
            if not release.wait(5):
                raise AssertionError('barrier timed out')
            return proof
        with patch.object(module, 'native_checkpoint_from_transcript', side_effect=slow):
            thread = threading.Thread(target=lambda: result.append(self.binding.external_admission_paused()))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                # Each lock is free while the scan is parked at the barrier.
                with exclusive_file_lock(self.root / 'compaction-seed'):
                    with exclusive_file_lock(self.binding.repository.path):
                        with self.binding.control.ports.lock():
                            held = True
                self.assertTrue(held)
                self.assertEqual('exact', self.binding.scope().session_id)
            finally:
                release.set()
                thread.join(8)
        self.assertFalse(thread.is_alive())
        self.assertEqual([True], result)
        print('full_binding slow_scan=unlocked seed=available scope=available admission=available')
