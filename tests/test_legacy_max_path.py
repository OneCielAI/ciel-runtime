"""Side intake persists under the legacy 260-character Windows path limit.

Observed 2026-11-07 on two AAP hosts (LongPathsEnabled=0): every SSE event
failed to index because the priority-marker temp path was 262 characters,
`C:\\Users\\<11>\\AppData\\Roaming\\ciel-runtime\\workspaces\\<12>\\side-inboxes\\
<64>\\priority\\P3\\<2>\\.<64>.<pid>.<time_ns>.tmp`, and the events stayed
deferred with no side fork or ACK. The marker itself (236) fits.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ciel_runtime_support.durable_json_file import save
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, finish

LEGACY_LIMIT = 259  # MAX_PATH 260 counts the terminating NUL.
SESSION = 'claude:max-path'
# Fixed width of the remote failure: pid 18912, a 19-digit time_ns.
OLD_SUFFIX = '.18912.1794116450222143000.tmp'


@contextmanager
def legacy_max_path():
    """Fail opens of longer paths the way CreateFileW does without long paths."""
    original = Path.open

    def bounded(self, *args, **kwargs):
        if len(str(self)) > LEGACY_LIMIT:
            raise FileNotFoundError(2, 'No such file or directory', str(self))
        return original(self, *args, **kwargs)

    with patch.object(Path, 'open', bounded):
        yield


def padded(base: Path, length: int) -> Path:
    """A directory under base whose own path is exactly length characters."""
    need = length - len(str(base)) - 1
    if need < 1:
        raise unittest.SkipTest('temporary directory too long for the AAP shape')
    path = base
    while need > 0:
        part = min(need, 100)
        if need - part == 1:  # Never leave a single separator with no name.
            part -= 1
        path = path / ('x' * part)
        need -= part + 1
    return path


class LegacyMaxPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_marker_shape_saves_with_temp_under_the_limit(self):
        stem = hashlib.sha256(b'event').hexdigest()
        directory = padded(self.base, 166)  # The AAP marker directory length.
        target = directory / (stem + '.json')
        old = directory / ('.' + stem + OLD_SUFFIX)
        self.assertEqual(262, len(str(old)))
        self.assertEqual(236, len(str(target)))
        replaced = []
        real_replace = os.replace

        def observed(source, destination):
            replaced.append(Path(source))
            return real_replace(source, destination)

        with legacy_max_path():
            with self.assertRaises(FileNotFoundError):
                old.parent.mkdir(parents=True, exist_ok=True)
                old.open('xb')
            with patch('ciel_runtime_support.durable_json_file.os.replace', observed):
                save(target, {'receipt_id': stem})
        self.assertEqual({'receipt_id': stem}, json.loads(target.read_text()))
        self.assertEqual(1, len(replaced))
        self.assertEqual(target.parent, replaced[0].parent)
        self.assertLess(len(str(replaced[0])), len(str(target)))
        self.assertEqual([], list(directory.glob('*.tmp')))

    def side_state(self) -> Path:
        # state_dir + side-inboxes\<64>\priority\P3\<2> is 166, as on AAP hosts.
        tail = len('/side-inboxes/') + 64 + len('/priority/P3/') + 2
        return padded(self.base, 166 - tail)

    def test_p3_event_is_received_claimed_and_finished_at_aap_depth(self):
        state = self.side_state()
        text = json.dumps({'data': {'priority': 3, 'text': 'how many items are running?'}})
        with legacy_max_path():
            inbox = SideIntakeInbox(state, SESSION)
            first = inbox.receive(text, 'walkie:1')
            key = first['receipt_id']
            marker = inbox.directory / 'priority' / 'P3' / key[:2] / (key + '.json')
            self.assertEqual(166, len(str(marker.parent)))
            self.assertTrue(marker.exists())
            self.assertEqual(text, inbox._load(key)['text'])
            # A redelivery of the still-received event re-indexes and stays one.
            marker.unlink()
            again = inbox.receive(text, 'walkie:1')
            self.assertTrue(again['duplicate'])
            self.assertTrue(marker.exists())
            lease = claim(state, SESSION, 'side-actor')
            self.assertEqual((key, 'P3'), (lease['receipt_id'], lease['source_priority']))
            receipt = finish(state, SESSION, key, lease['generation'],
                             {'action': 'answer', 'summary': 'answered', 'reply': 'none'})
        self.assertEqual('processed', receipt['stage'])
        self.assertFalse(marker.exists())
        self.assertFalse(inbox._event_path('pending', key).exists())
        self.assertEqual([], [p for p in state.rglob('*.tmp')])

    def test_p1_reserved_claim_works_at_aap_depth(self):
        state = self.side_state()
        with legacy_max_path():
            inbox = SideIntakeInbox(state, SESSION)
            inbox.receive('ordinary request', 'walkie:ordinary')
            urgent = inbox.receive(json.dumps({'data': {'priority': 'P1', 'text': 'now'}}), 'walkie:urgent')
            picked = claim(state, SESSION, 'reserved', urgent_only=True)
        self.assertEqual((urgent['receipt_id'], 'P1'), (picked['receipt_id'], picked['source_priority']))


if __name__ == '__main__':
    unittest.main()
