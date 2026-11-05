import time
from test_runtime_compaction_binding import BindingHarness, stamp

class SafeguardTests(BindingHarness):
    def test_high_fresh_metadata(self):
        self.provider = 'cielairouter'
        self.binding.config = lambda: (self.provider, {'current_model': 'ASTRA'})
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        def usage(tokens):
            self.write({'type': 'assistant', 'sessionId': 'exact', 'timestamp': stamp(time.time()),
                'message': {'id': str(time.time_ns()), 'model': 'ASTRA', 'usage': {
                    'input_tokens': tokens, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}})
            self.binding.safeguard_checked = 0
        usage(17999)
        self.assertFalse(self.binding.before_external_injection())
        self.assertIsNone(self.requests.read())
        usage(18000)
        # Polling never queues compact; the actual pending-input boundary does.
        self.assertFalse(self.binding.external_admission_paused())
        self.assertIsNone(self.requests.read())
        self.assertTrue(self.binding.before_external_injection())
        request = self.requests.read()
        self.assertEqual(20000, self.state()['context_limit'])
        self.assertTrue(self.binding.command_ready(request))
        self.assertEqual('model-compact-safeguard', self.state()['dispatch']['source'])
        self.binding.command_failed(request, 'failed')
        self.assertFalse(self.binding.external_admission_paused())
        self.assertFalse(self.binding.before_external_injection())
        self.assertIsNone(self.requests.read())
        usage(18001)
        self.assertTrue(self.binding.before_external_injection())
        self.assertIsNotNone(self.requests.read())

    def test_invalid_latest_checkpoint_alias_and_partial(self):
        from ciel_runtime_support.native_model_usage import latest_native_model_usage
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        scope = self.binding.scope()
        since = self.binding.repository.load('test')['started_at']
        def read():
            return latest_native_model_usage(self.path, scope, since, 'ASTRA')
        def record(**changes):
            value = {'type': 'assistant', 'sessionId': 'exact', 'timestamp': stamp(time.time()),
                'message': {'id': str(time.time_ns()), 'model': 'CIEL-RUNTIME-CIELAIROUTER-ASTRA',
                    'usage': {'input_tokens': 1, 'cache_read_input_tokens': 19000, 'cache_creation_input_tokens': 2}}}
            value.update(changes)
            self.write(value)
        record()
        self.assertEqual(19003, read().tokens)
        for changes in ({'timestamp': stamp(1)}, {'message': {'id': 'x', 'usage': {'input_tokens': 19000}}},
                        {'message': {'id': 'x', 'model': 'ASTRA', 'usage': {'input_tokens': 19000}}}):
            record(**changes)
            self.assertIsNone(read())
        record()
        record(isSidechain=True)
        record(sessionId='wrong')
        self.assertEqual(19003, read().tokens)
        self.checkpoint()
        self.assertIsNone(read())
        with self.path.open('a') as stream:
            stream.write('{')
        self.assertIsNone(read())

    def test_manual_wins_and_drop_cancels(self):
        self.provider = 'cielairouter'
        self.binding.config = lambda: (self.provider, {'current_model': 'ASTRA'})
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        from ciel_runtime_support.native_model_usage import NativeModelUsage
        scope = self.binding.scope()
        control = self.binding.control
        high = NativeModelUsage('sample', 19000)
        manual = self.requests.queue('mcp', '', 'new_session')
        control.observe_proactive(scope, high, 20000, 'astra')
        self.assertEqual(manual['id'], self.requests.read()['id'])
        self.requests.clear(manual['id'])
        control.observe_proactive(scope, high, 20000, 'astra')
        self.assertIsNone(self.requests.read())
        control.observe_proactive(scope, NativeModelUsage('new', 19000), 20000, 'astra')
        self.assertIsNotNone(self.requests.read())
        control.observe_proactive(scope, NativeModelUsage('low', 10), 20000, 'astra')
        self.assertIsNone(self.requests.read())
        self.assertFalse(control.external_admission_paused())

    def test_model_change_checkpoint_and_restart_dedupe(self):
        from ciel_runtime_support.native_model_usage import NativeModelUsage
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        scope = self.binding.scope()
        control = self.binding.control
        sample = NativeModelUsage('once', 19000)
        control.observe_proactive(scope, sample, 20000, 'astra')
        control.observe_proactive(scope, None, 20000, 'other')
        self.assertIsNone(self.requests.read())
        self.make().control.observe_proactive(scope, sample, 20000, 'astra')
        self.assertIsNone(self.requests.read())
        control.observe_proactive(scope, NativeModelUsage('next', 19000), 20000, 'astra')
        self.checkpoint()
        self.assertFalse(control.external_admission_paused())
        self.assertIsNone(self.requests.read())

    def test_provider_setting_and_missing_capacity(self):
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        self.write({'type': 'assistant', 'sessionId': 'exact', 'timestamp': stamp(time.time()),
            'message': {'id': 'high', 'model': 'ASTRA', 'usage': {'input_tokens': 19000,
                'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}})
        for provider, enabled, limit in [('other', True, 20000), ('cielairouter', False, 20000),
                                         ('cielairouter', True, 0)]:
            self.binding.config = lambda: (provider, {'current_model': 'ASTRA', 'compact_safeguard': enabled})
            self.context_limit = limit
            self.binding.safeguard_checked = 0
            self.assertFalse(self.binding.before_external_injection())
            self.assertIsNone(self.requests.read())

    def test_safeguard_dispatch_waits_for_real_tool_and_turn_boundary(self):
        import os
        from ciel_runtime_support.native_model_usage import NativeModelUsage
        from ciel_runtime_support.channel_compact_injection import (
            ChannelCompactInjectionService, ChannelCompactRequestPorts, ChannelCompactRuntimePorts)
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        scope = self.binding.scope()
        self.binding.control.observe_proactive(scope, NativeModelUsage('safe-boundary', 19000), 20000, 'astra')
        busy = {'tool': True, 'turn': True}
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        writes = []
        def write_prompt(_writer, command, enter, **_):
            os.write(write_fd, command.encode() + enter)
            writes.append(command)
            return True
        service = ChannelCompactInjectionService(
            ChannelCompactRequestPorts(self.requests.read, self.requests.clear),
            ChannelCompactRuntimePorts(lambda: busy['tool'], lambda: busy['turn'],
                lambda value: value or bytes([13]), write_prompt, repr), self.log)
        self.assertEqual('deferred', service.inject(None, runtime='claude', control=self.binding.control))
        self.assertFalse(self.binding.control.external_admission_paused())
        busy['tool'] = False
        self.assertEqual('deferred', service.inject(None, runtime='claude', control=self.binding.control))
        self.assertEqual([], writes)
        busy['turn'] = False
        self.assertEqual('injected', service.inject(None, runtime='claude', control=self.binding.control))
        self.assertEqual(b'/compact' + bytes([13]), os.read(read_fd, 100))
        self.assertIsNone(self.requests.read())
        self.assertTrue(self.binding.control.external_admission_paused())
        self.assertEqual('submitted', self.state()['dispatch']['submission'])
        self.assertEqual('none', service.inject(None, runtime='claude', control=self.binding.control))
        print('measured_safeguard usage=19000 capacity=20000 threshold=90 tool_deferred=1 turn_deferred=1 compact_writes=1')

    def test_alias_answered_by_other_native_model_uses_router_observed_names(self):
        # Emily 2026-10-26: alias _walkie_opus55-sol61-fast was answered as
        # claude-opus-5-5 / gpt-6.1-sol, so no record matched the alias.
        self.provider = 'cielairouter'
        self.binding.config = lambda: (self.provider, {'current_model': '_Walkie_Opus55-Sol61-Fast'})
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        scope = self.binding.scope()
        def usage(native, tokens):
            self.write({'type': 'assistant', 'sessionId': 'exact', 'timestamp': stamp(time.time()),
                'message': {'id': str(time.time_ns()), 'model': native, 'usage': {
                    'input_tokens': tokens, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}})
            self.binding.safeguard_checked = 0
        usage('claude-opus-5-5', 18500)
        self.assertFalse(self.binding.external_admission_paused())
        self.assertIsNone(self.requests.read())
        # Observations that do not belong to this launch's alias, session or epoch are ignored.
        from ciel_runtime_support.runtime_compaction_control import CompactionScope
        self.binding.observe_native_model(scope, 'ciel-runtime-cielairouter-other-alias', 'claude-opus-5-5')
        self.binding.observe_native_model(CompactionScope('claude', 'other', scope.epoch),
                                          'ciel-runtime-cielairouter-_walkie_opus55-sol61-fast', 'claude-opus-5-5')
        self.binding.observe_native_model(CompactionScope('claude', 'exact', 'stale-epoch'),
                                          'ciel-runtime-cielairouter-_walkie_opus55-sol61-fast', 'claude-opus-5-5')
        self.binding.observe_native_model(None, 'ciel-runtime-cielairouter-_walkie_opus55-sol61-fast', 'claude-opus-5-5')
        self.binding.safeguard_checked = 0
        self.assertFalse(self.binding.external_admission_paused())
        self.assertIsNone(self.requests.read())
        self.binding.observe_native_model(scope, 'ciel-runtime-cielairouter-_walkie_opus55-sol61-fast[1m]', 'claude-opus-5-5')
        self.binding.safeguard_checked = 0
        self.assertFalse(self.binding.external_admission_paused())
        self.assertTrue(self.binding.before_external_injection())
        request = self.requests.read()
        self.assertIsNotNone(request)
        self.assertEqual(18500, self.state()['baseline_tokens'])
        # A second upstream model of the same alias is accepted as well.
        self.binding.observe_native_model(scope, 'ciel-runtime-cielairouter-_walkie_opus55-sol61-fast', 'gpt-6.1-sol')
        self.assertEqual({'claude-opus-5-5', 'gpt-6.1-sol'},
                         set(self.binding.repository.load('test')['native_models']
                             ['ciel-runtime-cielairouter-_walkie_opus55-sol61-fast']))

    def test_native_model_observation_is_bounded_and_sanitized(self):
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        scope = self.binding.scope()
        for index in range(40):
            self.binding.observe_native_model(scope, 'alias', f'native-{index}')
        self.binding.observe_native_model(scope, 'alias', 'x' * 500)
        self.binding.observe_native_model(scope, 'alias', '')
        self.binding.observe_native_model(scope, '', 'native')
        names = self.binding.repository.load('test')['native_models']['alias']
        self.assertLessEqual(len(names), 16)
        self.assertTrue(all(0 < len(name) <= 128 for name in names))
        self.assertEqual(['alias'], list(self.binding.repository.load('test')['native_models']))

    def test_usage_appended_after_boundary_with_older_timestamp_is_ignored(self):
        from ciel_runtime_support.native_model_usage import latest_native_model_usage
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        scope = self.binding.scope()
        since = self.binding.repository.load('test')['started_at']
        self.checkpoint()  # Fixture boundary timestamp is now+1.
        self.write({'type': 'assistant', 'sessionId': 'exact', 'timestamp': stamp(time.time()),
            'message': {'id': 'late-old', 'model': 'ASTRA', 'usage': {'input_tokens': 19000,
                'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}})
        self.assertIsNone(latest_native_model_usage(self.path, scope, since, 'ASTRA'))

    def test_boundary_without_shrink_releases_pending_input(self):
        from ciel_runtime_support.native_model_usage import NativeModelUsage
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        control = self.binding.control
        sample = NativeModelUsage('boundary-once', 18000)
        control.observe_proactive(self.binding.scope(), sample, 20000, 'astra')
        self.assertTrue(control.injection_compact_pending())
        request = self.requests.read()
        self.assertTrue(control.command_ready(request))
        control.command_submitted(request)
        self.write({'type': 'system', 'subtype': 'compact_boundary', 'sessionId': 'exact',
                    'timestamp': stamp(time.time() + 1), 'uuid': 'no-shrink-count',
                    'compactMetadata': {'trigger': 'manual'}})
        self.assertFalse(control.injection_compact_pending(),
                         {'state': self.state(), 'logs': self.logs})
        self.assertEqual('injection_compact_boundary_observed', self.state()['failure'])
        self.assertNotEqual('verified', self.state()['phase'])
        control.observe_proactive(self.binding.scope(), sample, 20000, 'astra')
        self.assertFalse(control.injection_compact_pending())
        self.assertIsNone(self.requests.read())

    def test_timeout_without_dispatch_releases_and_does_not_retry_same_sample(self):
        from unittest.mock import patch
        from ciel_runtime_support.native_model_usage import NativeModelUsage
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        control = self.binding.control
        sample = NativeModelUsage('timeout-once', 18000)
        control.observe_proactive(self.binding.scope(), sample, 20000, 'astra')
        self.assertTrue(control.injection_compact_pending())
        deadline = self.state()['injection_deadline']
        with patch('ciel_runtime_support.runtime_compaction_control.time.time', return_value=deadline + 1):
            # ports.now is captured at construction, so replace it explicitly.
            import dataclasses
            control.ports = dataclasses.replace(control.ports, now=lambda: deadline + 1)
            self.assertFalse(control.injection_compact_pending())
            control.observe_proactive(self.binding.scope(), sample, 20000, 'astra')
            self.assertFalse(control.injection_compact_pending())
        self.assertEqual('injection_compact_unconfirmed', self.state()['failure'])
        self.assertIsNone(self.requests.read())

    def test_pending_sse_compact_pipe_boundary_then_deliver_once(self):
        import dataclasses
        import os
        from test_channel_pending_injection import _InjectionHarness, _message
        from ciel_runtime_support.channel_pending_injection import inject_pending_channel_messages
        from ciel_runtime_support.channel_compact_injection import (
            ChannelCompactInjectionService, ChannelCompactRequestPorts, ChannelCompactRuntimePorts)
        self.provider = 'cielairouter'
        self.binding.config = lambda: (self.provider, {'current_model': 'ASTRA'})
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        self.write({'type': 'assistant', 'sessionId': 'exact', 'timestamp': stamp(time.time()),
            'message': {'id': 'integration-high', 'model': 'ASTRA', 'usage': {
                'input_tokens': 18000, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}})
        harness = _InjectionHarness([_message(1)])
        services = harness.services()
        services = dataclasses.replace(services, io=dataclasses.replace(services.io,
            before_external_injection=self.binding.before_external_injection))
        self.assertEqual(0, inject_pending_channel_messages(None, 0, services=services))
        self.assertEqual([], harness.written)
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        def write_prompt(_writer, command, enter, **_):
            os.write(write_fd, command.encode() + enter)
            return True
        compact = ChannelCompactInjectionService(
            ChannelCompactRequestPorts(self.requests.read, self.requests.clear),
            ChannelCompactRuntimePorts(lambda: False, lambda: False,
                lambda value: value or bytes([13]), write_prompt, repr), self.log)
        self.assertEqual('injected', compact.inject(None, runtime='claude', control=self.binding))
        self.assertEqual(b'/compact' + bytes([13]), os.read(read_fd, 100))
        self.assertEqual(0, inject_pending_channel_messages(None, 0, services=services))
        self.assertEqual([], harness.written)
        self.write({'type': 'system', 'subtype': 'compact_boundary', 'sessionId': 'exact',
            'timestamp': stamp(time.time() + 1), 'uuid': 'integration-boundary',
            'compactMetadata': {'trigger': 'manual'}})
        cursor = inject_pending_channel_messages(None, 0, services=services)
        self.assertEqual(1, cursor)
        self.assertEqual(['body-1'], harness.written)
        inject_pending_channel_messages(None, cursor, services=services)
        self.assertEqual(['body-1'], harness.written)
        self.assertEqual('none', compact.inject(None, runtime='claude', control=self.binding))
        print('n89 pipe_command=/compact+CR boundary_without_postTokens=observed sse_body=body-1 deliveries=1 cursor=1')

    def test_provider_overflow_converts_injection_wait_to_ungated_recovery(self):
        from ciel_runtime_support.native_model_usage import NativeModelUsage
        self.binding.bind('claude', session_id='exact', cwd=self.root)
        self.binding.observe_request('other', {}, {'tokens': 100}, 'claude')
        control = self.binding.control
        control.observe_proactive(self.binding.scope(), NativeModelUsage('before-overflow', 18000), 20000, 'astra')
        self.assertTrue(control.injection_compact_pending())
        self.binding.observe_failure('other', {}, {'tokens': 15000},
                                     'prompt is too long: 21000 tokens > 20000 maximum', 'claude')
        self.assertTrue(self.state()['pressure'])
        self.assertNotIn('injection_compact', self.state())
        self.assertFalse(control.injection_compact_pending())
        self.assertFalse(self.binding.external_admission_paused())
        request = self.requests.read()
        self.assertTrue(control.command_ready(request))
        control.command_failed(request, 'command_not_submitted')
        self.assertEqual('retry_wait', self.state()['phase'])
