import threading
import unittest

from ciel_runtime_support.terminal_input_frames import TerminalInputFrames


class TerminalInputFramesTests(unittest.TestCase):
    def test_every_paste_split_preserves_payload_and_complete_markers(self):
        payload = b'\x1b[200~' + '1660377 한글 [20~ literal'.encode() + b'\x1b[201~'
        for split in range(len(payload) + 1):
            writes = []
            frames = TerminalInputFrames(writes.append, idle_seconds=10)
            try:
                frames.feed(payload[:split])
                frames.feed(payload[split:])
            finally:
                frames.close()
            self.assertEqual(payload, b''.join(writes))
            for marker in (b'\x1b[200~', b'\x1b[201~'):
                self.assertTrue(any(marker in write for write in writes), (split, writes))

    def test_standalone_escape_is_delivered_without_next_key(self):
        delivered = threading.Event()
        writes = []
        def write(data):
            writes.append(data)
            delivered.set()
        frames = TerminalInputFrames(write, idle_seconds=0.01)
        try:
            frames.feed(b'\x1b')
            self.assertTrue(delivered.wait(1))
            self.assertEqual([b'\x1b'], writes)
        finally:
            frames.close()

    def test_literal_markers_alt_arrows_f9_and_newlines_preserved(self):
        payload = b'[200~literal[201~\r\n\x1bx\x1b[A\x1bOP\x1b[20~'
        writes = []
        frames = TerminalInputFrames(writes.append, idle_seconds=10)
        for byte in payload:
            frames.feed(bytes([byte]))
        frames.close()
        self.assertEqual(payload, b''.join(writes))
