"""Atomic JSON writes remain durable across bounded Windows reader contention."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ciel_runtime_support.durable_json_file import reader, save


class DurableJsonTests(unittest.TestCase):
    def test_cooperating_reader_and_writer_preserve_complete_snapshots(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'receipt.json'
            save(path, {'version': 1})
            started = threading.Event()
            finished = threading.Event()
            errors = []
            def write():
                started.set()
                try:
                    save(path, {'version': 2})
                except Exception as error:
                    errors.append(error)
                finally:
                    finished.set()
            with reader(path) as old:
                thread = threading.Thread(target=write)
                thread.start()
                self.assertTrue(started.wait(5))
                self.assertEqual({'version': 1}, json.load(old))
            thread.join(15)
            self.assertTrue(finished.is_set())
            self.assertEqual([], errors)
            with reader(path) as new:
                self.assertEqual({'version': 2}, json.load(new))

    def test_missing_reader_remains_file_not_found(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(FileNotFoundError):
                with reader(Path(raw) / 'missing' / 'receipt.json'):
                    self.fail('missing reader must not yield')
            # Polling absent receipts must not create lock sidecars or folders.
            self.assertEqual([], list(Path(raw).iterdir()))

    @unittest.skipUnless(os.name == 'nt', 'Windows open-reader replacement behavior')
    def test_real_open_reader_releases_after_first_replace_denial(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'receipt.json'
            save(path, {'version': 1})
            opened = threading.Event()
            release = threading.Event()
            closed = threading.Event()

            def read():
                with path.open('rb'):
                    opened.set()
                    release.wait(5)
                closed.set()

            thread = threading.Thread(target=read)
            thread.start()
            self.assertTrue(opened.wait(5))
            replace = os.replace
            denials = []

            def observed(source, target):
                try:
                    return replace(source, target)
                except PermissionError as error:
                    denials.append(error.winerror)
                    release.set()
                    self.assertTrue(closed.wait(5))
                    raise

            try:
                with patch('ciel_runtime_support.durable_json_file.os.replace', observed):
                    save(path, {'version': 2})
            finally:
                release.set()
                thread.join(5)
            self.assertEqual([5], denials)
            self.assertEqual({'version': 2}, json.loads(path.read_text()))
            self.assertEqual([], list(Path(raw).glob('*.tmp')))

    def test_permanent_denial_is_bounded_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'receipt.json'
            save(path, {'version': 1})
            denied = PermissionError('denied')
            denied.winerror = 5
            with patch('ciel_runtime_support.durable_json_file.os.name', 'nt'), \
                 patch('ciel_runtime_support.durable_json_file._io_lock', side_effect=lambda _: nullcontext()), \
                 patch('ciel_runtime_support.durable_json_file.os.replace', side_effect=denied) as replace, \
                 patch('ciel_runtime_support.durable_json_file.time.sleep') as delay:
                with self.assertRaises(PermissionError):
                    save(path, {'version': 2})
            self.assertEqual(20, replace.call_count)
            self.assertEqual(19, delay.call_count)
            self.assertEqual({'version': 1}, json.loads(path.read_text()))
            self.assertEqual([], list(Path(raw).glob('*.tmp')))

    def test_non_windows_denial_is_not_retried(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'receipt.json'
            save(path, {'version': 1})
            with patch('ciel_runtime_support.durable_json_file.os.name', 'posix'), \
                 patch('ciel_runtime_support.durable_json_file.os.replace', side_effect=PermissionError('denied')) as replace, \
                 patch('ciel_runtime_support.durable_json_file.time.sleep') as delay:
                with self.assertRaises(PermissionError):
                    save(path, {'version': 2})
            self.assertEqual(1, replace.call_count)
            delay.assert_not_called()
            self.assertEqual({'version': 1}, json.loads(path.read_text()))
