"""side-intake.js against the real side backend.

tests/side_real_backend_harness.mjs runs the packaged side-intake.js; every
router answer comes from tests/side_real_backend_server.py, which runs the real
MCP dispatch, session Work store and side inbox in a temporary directory. The
engine side is a model built from owned probes (delivery-4f4c90b8,
prompt-gate-0dd78bf3); no claude.exe is started.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

NODE = shutil.which('node')
HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent


@unittest.skipUnless(NODE, 'node is required')
class SideRealBackendTests(unittest.TestCase):
    def run_mode(self, mode):
        with tempfile.TemporaryDirectory(prefix='side-real-') as scratch:
            root = Path(scratch)
            (root / 'package.json').write_text('{"type":"module"}', encoding='utf-8')
            shutil.copyfile(SOURCE / 'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js', root / 'side-intake.js')
            for name in ('side_real_backend_harness.mjs', 'side_real_backend_server.py'):
                shutil.copyfile(HERE / name, root / name)
            (root / 'config').mkdir()
            env = {key: value for key, value in os.environ.items() if not key.startswith('CIEL_RUNTIME_')}
            env.update(CIEL_RUNTIME_CONFIG_DIR=str(root / 'config'), CIEL_RUNTIME_TEST_ISOLATED='1',
                       SIDE_REAL_ROOT=str(root / 'work'), SIDE_SOURCE=str(SOURCE), PYTHON=sys.executable,
                       PYTHONIOENCODING='utf-8')
            done = subprocess.run([NODE, 'side_real_backend_harness.mjs', mode], cwd=root, env=env,
                                  capture_output=True, text=True, encoding='utf-8', timeout=180, check=False)
        self.assertEqual(0, done.returncode, done.stderr)
        return json.loads(done.stdout)

    def test_idle_and_busy_wake_are_elected_appended_and_read_on_the_real_backend(self):
        idle = self.run_mode('wake:idle')
        self.assertEqual(['prepare', 'start', 'appended', 'prompt_prepare', 'prompt_queued', 'read'], idle['wakeCalls'])
        delivered = idle['delivered']
        # One append holds the payload; the one prompt holds none.
        self.assertEqual((1, 0, 1), (delivered['payloadAppends'], delivered['payloadPrompts'], delivered['wakePrompts']))
        self.assertEqual(('appended', 'started', 'append', True),
                         tuple(delivered['backend']['wake'][key] for key in ('state', 'phase', 'channel', 'has_receipt')))
        self.assertEqual([1], idle['after']['requests'])
        self.assertTrue(idle['after']['backend']['wake']['read'])
        # Item 7: a busy main gets the append while its turn runs; the next
        # request of that turn carries it and no prompt is submitted.
        busy = self.run_mode('wake:busy')
        delivered = busy['delivered']
        self.assertEqual((1, 0, 0, [0]), (delivered['payloadAppends'], delivered['payloadPrompts'],
                                          delivered['wakePrompts'], delivered['requests']))
        self.assertEqual(('appended', False), (delivered['backend']['wake']['state'], delivered['backend']['wake']['read']))
        # The first step after the append carried it but only fences it (its
        # build may predate the append); the next request is causal proof.
        self.assertEqual(([0, 1, 0], 0, True), (busy['after']['requests'], busy['after']['wakePrompts'],
                                                busy['after']['backend']['wake']['read']))
        # Wake writes never change the Work version (wake-only transactions).
        self.assertEqual(busy['delivered']['backend']['version'], busy['after']['backend']['version'])

    def test_queued_wake_prompt_is_not_a_read_and_a_new_conversation_gets_one_reappend(self):
        # A queued wake prompt proves nothing about a request. Main's conversation
        # continues as a new identity on the same board before that prompt's turn
        # stepped; the new conversation gets the payload once, then is read.
        row = self.run_mode('continue-before-turn')
        self.assertTrue(row['lineage']['same_board'])
        in_a = row['inA']
        self.assertEqual((1, 0, 1), (in_a['payloadAppends'], in_a['payloadPrompts'], in_a['wakePrompts']))
        self.assertEqual(('appended', False, 'claude:parent', 'queued'),
                         tuple(in_a['backend']['wake'][key] for key in ('state', 'read', 'conversation_id', 'prompt_state')))
        in_b = row['inB']
        self.assertEqual((1, 0, 1), (in_b['payloadAppends'], in_b['payloadPrompts'], in_b['wakePrompts']))
        self.assertEqual(('appended', 'claude:parent2', False),
                         tuple(in_b['backend']['wake'][key] for key in ('state', 'conversation_id', 'read')))
        after = row['after']
        self.assertEqual((1, 0, 1, [1]), (after['payloadAppends'], after['payloadPrompts'], after['wakePrompts'],
                                          after['requests']))
        self.assertTrue(after['backend']['wake']['read'])
        self.assertEqual(1, row['calls'].count('B:reappend_appended'))
        self.assertEqual(0, sum(call.startswith('B:') and call.endswith((':prepare', ':start')) for call in row['calls']))

    def test_a_dead_prompt_lease_holder_yields_exactly_one_later_wake_for_a_time_limit_event(self):
        row = self.run_mode('prompt-holder-dies')
        self.assertIn('A:prompt_prepare:died', row['promptCalls'])
        window = row['inWindow']
        # The one payload append happened; the lease holder died before its prompt.
        self.assertEqual((1, 0, 0), (window['A']['payloadAppends'], window['A']['wakePrompts'], window['B']['wakePrompts']))
        self.assertEqual(('appended', 'submitting'), (window['backend']['wake']['state'],
                                                     window['backend']['wake']['prompt_state']))
        # After the window: exactly one wake prompt, still exactly one payload row.
        self.assertEqual(1, row['A']['wakePrompts'] + row['B']['wakePrompts'])
        self.assertEqual(1, row['A']['payloadAppends'] + row['B']['payloadAppends'])
        self.assertEqual(0, row['A']['payloadPrompts'] + row['B']['payloadPrompts'])
        self.assertEqual('queued', row['backend']['wake']['prompt_state'])

    def test_a_lost_prompt_lease_call_never_lets_two_processes_submit(self):
        # A lost prompt_prepare: nothing is submitted for it; a later ask holds
        # the one lease. A lost prompt_queued record: the lease stays submitting,
        # so neither process adds a second prompt for this event/conversation.
        prepare = self.run_mode('prompt-failure:prepare')
        self.assertIn('A:prompt_prepare:lost', prepare['promptCalls'])
        self.assertEqual((1, 0), (prepare['afterFirst']['payloadAppends'], prepare['afterFirst']['wakePrompts']))
        self.assertEqual(1, prepare['A']['wakePrompts'] + prepare['B']['wakePrompts'])
        self.assertEqual(0, prepare['A']['payloadPrompts'] + prepare['B']['payloadPrompts'] + prepare['B']['payloadAppends'])
        self.assertEqual('queued', prepare['backend']['wake']['prompt_state'])
        record = self.run_mode('prompt-failure:record')
        self.assertIn('A:prompt_queued:lost', record['promptCalls'])
        self.assertEqual((1, 1), (record['afterFirst']['payloadAppends'], record['afterFirst']['wakePrompts']))
        self.assertEqual(1, record['A']['wakePrompts'] + record['B']['wakePrompts'])
        self.assertEqual('submitting', record['backend']['wake']['prompt_state'])
        self.assertIn('B:prompt_prepare', record['promptCalls'])

    def test_current_backend_never_takes_the_old_router_path(self):
        # Item 5. The 32-miss fence exhaustion and the module-only fence live in
        # the host's old-router branch, entered only when a wake answer lacks
        # protocol 2. Every answer of the current backend carries it, and the
        # legacy $.state key is never written.
        for mode in ('wake:idle', 'wake:busy', 'two-process'):
            with self.subTest(mode=mode):
                row = self.run_mode(mode)
                self.assertEqual([2], row['wakeProtocols'])
                self.assertNotIn('legacyWakes', row['stateKeys'])

    def test_two_processes_on_one_session_deliver_the_payload_once_on_the_current_backend(self):
        # Item 5. Two claude.exe (own $.state, own conversation) race the real
        # election with both prepares in flight. Exactly one starts and appends.
        row = self.run_mode('two-process')
        self.assertEqual('pending', row['before'])
        # Payload deliveries, whichever channel carried them: exactly one.
        delivered = {name: row[name]['payloadAppends'] + row[name]['payloadPrompts'] for name in ('A', 'B')}
        self.assertEqual(1, delivered['A'] + delivered['B'])
        starts = [call for call in row['calls'] if call.endswith(':start')]
        self.assertEqual(1, len(starts))
        self.assertIn(row['backend']['wake']['state'], ('appended', 'queued'))

    def test_the_process_that_lost_the_election_holds_no_payload(self):
        # The losing process receives no payload and the backend grants only one
        # outstanding wake-only prompt for the shared event/conversation.
        row = self.run_mode('two-process')
        self.assertEqual(('appended', True), (row['backend']['wake']['state'], row['backend']['wake']['has_receipt']))
        self.assertEqual(0, row['A']['payloadPrompts'] + row['B']['payloadPrompts'])
        loser = row['B'] if row['A']['payloadAppends'] else row['A']
        self.assertEqual(0, loser['payloadAppends'])
        # The backend prompt lease bounds both processes to one outstanding wake
        # prompt for this event and conversation.
        self.assertEqual(1, row['A']['wakePrompts'] + row['B']['wakePrompts'])
        self.assertGreaterEqual(row['promptAttempts'], 2)  # both asked; backend granted one

    def test_intent_lease_starts_on_product_paths_and_stalls_only_under_an_injected_interleaving(self):
        # Item 6. One owner whose start is 60 s late still starts: only another
        # owner's prepare can supersede an expired intent.
        slow = self.run_mode('lease:single-slow')
        self.assertEqual(1, slow['appended'])
        self.assertEqual(['A:prepare', 'A:start'], slow['calls'][:2])
        # Two owners behind one router that takes 7 s (more than the lease) for
        # every call, in arrival order: one start is refused, the next lands.
        serial = self.run_mode('lease:serial-slow')
        self.assertEqual((1, 1), (serial['rounds'], serial['appended']))
        self.assertEqual(1, sum(call.endswith(':start:refused') for call in serial['calls']))
        # The stall needs two live owners AND a schedule this test injects: each
        # start is held past the lease exactly while the other owner prepares.
        # Nothing is delivered meanwhile (safety holds), and the first start
        # that is not overtaken delivers once.
        adversarial = self.run_mode('lease:adversarial')
        stalled = adversarial['whileAdversarial']
        self.assertEqual((0, 'submitting', 'intent', 1 + adversarial['rounds']),
                         (stalled['appended'], stalled['backend']['wake']['state'], stalled['backend']['wake']['phase'],
                          stalled['backend']['wake']['revision']))
        self.assertEqual(adversarial['rounds'], adversarial['refusedStarts'])
        self.assertEqual(1, adversarial['appended'])


if __name__ == '__main__':
    unittest.main()
