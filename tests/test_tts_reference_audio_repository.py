import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.tts_reference_audio_repository import (
    REFERENCE_MARKER_PREFIX,
    TtsReferenceAudioRepository,
)


TOKEN = "a" * 48


class TtsReferenceAudioRepositoryTests(unittest.TestCase):
    def repository(self, root: Path) -> TtsReferenceAudioRepository:
        return TtsReferenceAudioRepository(
            root,
            token=lambda: TOKEN,
            process_id=lambda: 123,
            clock_ns=lambda: 456,
        )

    def test_stores_private_binary_and_round_trips_data_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tts-reference-audio"
            repository = self.repository(root)
            source = "data:audio/wav;base64," + base64.b64encode(b"RIFFaudio").decode()

            marker = repository.store_data_url(source, 1024)

            self.assertEqual(REFERENCE_MARKER_PREFIX + TOKEN, marker)
            sidecar = root / f"{TOKEN}.bin"
            self.assertTrue(sidecar.is_file())
            self.assertNotIn(b"UklGRmF1ZGlv", sidecar.read_bytes())
            self.assertEqual(source, repository.expand_data_url(marker, 1024))
            if os.name != "nt":
                self.assertEqual(0o600, sidecar.stat().st_mode & 0o777)

    def test_atomic_replace_and_private_permissions_are_attempted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "refs"
            repository = self.repository(root)
            source = "data:audio/pcm;base64," + base64.b64encode(b"audio").decode()

            with (
                mock.patch(
                    "ciel_runtime_support.tts_reference_audio_repository.os.replace",
                    wraps=os.replace,
                ) as replace,
                mock.patch(
                    "ciel_runtime_support.tts_reference_audio_repository.os.chmod",
                    wraps=os.chmod,
                ) as chmod,
            ):
                repository.store_data_url(source, 100)

            self.assertEqual(1, replace.call_count)
            self.assertTrue(any(call.args[1] == 0o600 for call in chmod.call_args_list))
            self.assertEqual([], list(root.glob("*.tmp")))

    def test_rejects_oversized_input_before_base64_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(Path(directory))
            source = "data:audio/wav;base64," + "A" * 9

            with (
                mock.patch(
                    "ciel_runtime_support.tts_reference_audio_repository.base64.b64decode",
                    side_effect=AssertionError("must reject before decoding"),
                ),
                self.assertRaises(OverflowError),
            ):
                repository.store_data_url(source, 3)

    def test_resolve_checks_sidecar_size_before_reading_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self.repository(root)
            marker = repository.store_data_url(
                "data:audio/wav;base64," + base64.b64encode(b"four").decode(),
                4,
            )

            with self.assertRaises(OverflowError):
                repository.expand_data_url(marker, 3)

    def test_non_markers_pass_through_and_discard_is_marker_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(Path(directory))
            url = "https://example.test/reference.wav"

            self.assertEqual(url, repository.expand_data_url(url, 1))
            self.assertFalse(repository.discard("../../config.json"))

    def test_discard_removes_only_the_referenced_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self.repository(root)
            marker = repository.store_data_url(
                "data:audio/wav;base64," + base64.b64encode(b"audio").decode(),
                100,
            )
            unrelated = root / "unrelated.bin"
            unrelated.write_bytes(b"keep")

            self.assertTrue(repository.discard(marker))
            self.assertFalse((root / f"{TOKEN}.bin").exists())
            self.assertEqual(b"keep", unrelated.read_bytes())

    def test_missing_or_malformed_sidecar_has_safe_error(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(Path(directory))

            with self.assertRaisesRegex(ValueError, "upload it again"):
                repository.expand_data_url(REFERENCE_MARKER_PREFIX + TOKEN, 100)


if __name__ == "__main__":
    unittest.main()
