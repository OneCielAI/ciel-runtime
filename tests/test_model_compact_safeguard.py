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
        usage(18000)
        self.assertFalse(self.binding.external_admission_paused())
        self.assertIsNone(self.requests.read())
        usage(18001)
        self.assertFalse(self.binding.external_admission_paused())
        request = self.requests.read()
        self.assertEqual(20000, self.state()['context_limit'])
        self.assertTrue(self.binding.command_ready(request))
        self.assertEqual('model-compact-safeguard', self.state()['dispatch']['source'])
        self.binding.command_failed(request, 'failed')
        self.assertTrue(self.binding.external_admission_paused())
        self.assertIsNone(self.requests.read())

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
            self.assertFalse(self.binding.external_admission_paused())
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
