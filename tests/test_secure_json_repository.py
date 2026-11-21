"""Corrupt JSON classification stays opt-in for secure state repositories."""

import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.secure_json_repository import (
    SecureJsonCorruptError,
    SecureJsonEffects,
    SecureJsonRepository,
)


class SecureJsonRepositoryTests(unittest.TestCase):
    def test_default_load_preserves_legacy_none_and_strict_load_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_bytes(b"not-json\x00")
            repository = SecureJsonRepository(path, SecureJsonEffects(log=lambda *_args: None))

            self.assertIsNone(repository.load("read"))
            with self.assertRaises(SecureJsonCorruptError):
                repository.load("read", raise_on_corrupt=True)


if __name__ == "__main__":
    unittest.main()
