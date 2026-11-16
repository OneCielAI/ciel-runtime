"""Side decisions that used to be refused at commit and quarantined (Walkie 2026-11-16).

- 23 events in 12 h: an update of an item whose note had filled with earlier
  side requests ("side update exceeds note capacity").
- 4 events: the model swapped the classification and the action
  (action "status"/"info", kind "answer").
Both lost the person's message. They now commit.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.side_work_decision import SIDE_NOTE_MARKER, apply, side_note, validate
from ciel_runtime_support.work_plan_store import WorkPlanStore

LIMIT = tree.MAX_TEXT_CHARS


class SideNoteTests(unittest.TestCase):
    def test_room_left_appends_as_before(self):
        note, compacted = side_note('base', 'more')
        self.assertEqual('base' + SIDE_NOTE_MARKER + 'more', note)
        self.assertIsNone(compacted)

    def test_oldest_side_requests_make_room_and_mains_text_stays(self):
        base = 'M' * 1400
        previous = base + SIDE_NOTE_MARKER + 'first ' + 'a' * 200 + SIDE_NOTE_MARKER + 'second ' + 'b' * 200
        note, compacted = side_note(previous, 'third ' + 'c' * 300)
        self.assertLessEqual(len(note), LIMIT)
        self.assertTrue(note.startswith(base + '\n\n['))
        self.assertIn('third ' + 'c' * 300, note)
        self.assertNotIn('first ', note)
        self.assertEqual({'trimmed': 1}, compacted) if 'second ' in note else self.assertEqual(2, compacted['trimmed'])
        # The count accumulates across compactions.
        again, compacted2 = side_note(note, 'fourth ' + 'd' * 250)
        self.assertLessEqual(len(again), LIMIT)
        self.assertIn('fourth ' + 'd' * 250, again)
        self.assertTrue(again.startswith(base + '\n\n['))
        dropped = compacted['trimmed'] + compacted2['trimmed']
        self.assertIn('[%d earlier side requests trimmed' % dropped, again)

    def test_new_request_is_cut_when_mains_note_leaves_little_room(self):
        note, compacted = side_note('M' * 1900, 'x' * 500)
        self.assertEqual(LIMIT, len(note))
        self.assertTrue(note.endswith('…'))
        self.assertTrue(compacted['truncated'])

    def test_full_main_note_is_kept_and_nothing_is_lost_but_the_copy(self):
        note, compacted = side_note('M' * LIMIT, 'x')
        self.assertEqual('M' * LIMIT, note)
        self.assertTrue(compacted['skipped'])


class CommitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkPlanStore(Path(self.tmp.name) / 'board.json')

    def key(self, label):
        return hashlib.sha256(label.encode()).hexdigest()

    def test_update_of_an_item_with_a_full_note_commits(self):
        doc, item = self.store.transaction(lambda d: tree.add_node(d, None, 'P1 contract'))
        filled = 'plan text ' * 100 + SIDE_NOTE_MARKER + 's' * 400 + SIDE_NOTE_MARKER + 't' * 450
        self.store.transaction(lambda d: tree.update_node(d, item, note=filled[:LIMIT]))
        result = apply(self.store, self.key('full'), {
            'action': 'update', 'kind': 'request', 'item_id': item, 'summary': 'follow-up',
            'note': 'new constraint ' + 'n' * 300, 'reply': 'noted', 'relay': True},
            self.store.read()['version'])
        node = next(n for n in self.store.read()['nodes'] if n['id'] == item)
        self.assertLessEqual(len(node['note']), LIMIT)
        self.assertTrue(node['note'].startswith('plan text ' * 100))
        self.assertIn('new constraint ' + 'n' * 300, node['note'])
        self.assertEqual({'item_id': item}, result['relay'])
        self.assertGreaterEqual(result['note_compacted']['trimmed'], 1)

    def test_release_on_a_full_note_still_clears_the_wait(self):
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'E2E report'))
        doc, gate = self.store.transaction(lambda d: tree.add_node(d, goal, 'review', waiting_on='Daniel review'))
        self.store.transaction(lambda d: tree.update_node(d, gate, note='M' * LIMIT))
        result = apply(self.store, self.key('release'), {
            'action': 'update', 'kind': 'request', 'item_id': gate, 'summary': 'Daniel approved',
            'reply': 'released', 'release_waits': [{'id': gate, 'waiting_on': 'Daniel review'}]},
            self.store.read()['version'])
        node = next(n for n in self.store.read()['nodes'] if n['id'] == gate)
        self.assertEqual('', node.get('waiting_on', ''))
        self.assertEqual('M' * LIMIT, node['note'])
        self.assertTrue(result['note_compacted']['skipped'])
        self.assertEqual(gate, result['released_waits'][0]['id'])

    def test_swapped_classification_and_action_commit_as_an_answer(self):
        for action in ('status', 'info'):
            with self.subTest(action=action):
                decision = validate({'action': action, 'kind': 'answer', 'summary': 's', 'reply': 'r'})
                self.assertEqual(('answer', action), (decision['action'], decision['kind']))
        result = apply(self.store, self.key('swap'), {'action': 'status', 'kind': 'answer',
                                                      'summary': 'state', 'reply': 'n1 is running'}, 0)
        self.assertEqual(('answer', 'status'), (result['action'], result['kind']))
        self.assertEqual([], self.store.read()['nodes'])
        decision = validate({'action': 'request', 'kind': 'update', 'item_id': 'n1', 'summary': 's',
                             'reply': 'r', 'note': 'x'})
        self.assertEqual(('update', 'request'), (decision['action'], decision['kind']))

    def test_unknown_values_are_still_refused(self):
        for action, kind in (('status', 'status_x'), ('banana', 'answer'), ('status', 'create'), ('info', 'request')):
            with self.subTest(action=action, kind=kind):
                with self.assertRaises(tree.WorkPlanError):
                    validate({'action': action, 'kind': kind, 'summary': 's', 'reply': 'r', 'item_id': 'n1'})


if __name__ == '__main__':
    unittest.main()
