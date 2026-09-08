"""Opt-in real Codex TUI regression; never submits input to a model."""
import json
import os
import subprocess
import time
import unittest

from ciel_runtime_support.windows_conpty import WindowsConPtySession, _visible_terminal_text


@unittest.skipUnless(os.name == 'nt' and os.environ.get('CIEL_TEST_CODEX_EXE'), 'requires opted-in Windows Codex')
class CodexPasteFramesTests(unittest.TestCase):
    def _render_chunks(self, input_chunks):
        exe = os.environ['CIEL_TEST_CODEX_EXE']
        servers = json.loads(subprocess.check_output([exe, 'mcp', 'list', '--json']))
        args = [value for server in servers for value in ('-c', f'mcp_servers.{server["name"]}.enabled=false')]
        session = WindowsConPtySession([exe, '--no-alt-screen', *args], dict(os.environ),
                                      log=lambda *args: None, mirror_output=False, forward_stdin=False)
        try:
            time.sleep(1.5)
            checkpoint = session._output_total_bytes
            chunks = iter([*input_chunks, b''])
            def read():
                time.sleep(0.02)
                return next(chunks)
            session._read_input_bytes = read
            session._pump_input()
            time.sleep(0.4)
            visible = _visible_terminal_text(session._output_since(checkpoint)[0])
            return visible
        finally:
            session.close()

    def test_split_mouse_paste_delimiters_do_not_enter_codex_draft(self):
        visible = self._render_chunks([b'\x1b', b'[200~1660377\x1b', b'[201~'])
        self.assertIn('1660377', visible)
        self.assertNotIn('[200~', visible)
        self.assertNotIn('[201~', visible)

    def test_split_palette_response_does_not_enter_codex_draft(self):
        visible = self._render_chunks([b'\x1b]', b'4;0;rgb:0c0c/0c0c/0c0c', b'\x1b', b'\\',
                                       b'\x1b[200~1660377\x1b[201~'])
        self.assertNotIn('rgb:', visible, visible)
        self.assertNotIn(']4;', visible, visible)
        self.assertIn('1660377', visible)
