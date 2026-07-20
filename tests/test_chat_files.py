import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.chat_files import ChatFilePorts, ChatFileRepository


class ChatFileRepositoryTests(unittest.TestCase):
    def test_upload_sanitizes_name_and_projects_router_url(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ChatFileRepository(
                Path(directory),
                "http://router",
                ChatFilePorts(timestamp=lambda: 1.0, timestamp_ns=lambda: 123),
            )
            upload = repository.store_upload(
                {"name": "../ report ?.txt", "content": "hello", "content_type": "text/plain"}
            )
            self.assertEqual("123-report-.txt", upload["name"])
            self.assertEqual("http://router/ca/chat/files/123-report-.txt", upload["url"])
            self.assertEqual(b"hello", (Path(directory) / upload["name"]).read_bytes())

    def test_path_upload_preserves_source_name_and_detects_content_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "report.txt"
            source.write_text("hello", encoding="utf-8")
            repository = ChatFileRepository(
                root / "stored",
                "http://router",
                ChatFilePorts(timestamp_ns=lambda: 456),
            )
            upload = repository.store_path(source)
            self.assertEqual("report.txt", upload["original_name"])
            self.assertEqual("text/plain", upload["content_type"])

    def test_size_limit_and_invalid_base64_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ChatFileRepository(Path(directory), "http://router")
            with mock.patch.dict(os.environ, {"CIEL_RUNTIME_CHAT_FILE_MAX_BYTES": "3"}):
                with self.assertRaises(OverflowError):
                    repository.store_upload({"name": "large.txt", "content": "four"})
            with self.assertRaisesRegex(ValueError, "invalid base64"):
                repository.store_upload({"name": "bad.bin", "encoding": "base64", "content": "%%%"})

    def test_message_projection_includes_attachment_metadata(self):
        text = ChatFileRepository.message_text(
            "Review this",
            [{
                "original_name": "report.txt",
                "url": "http://router/file",
                "bytes": 5,
                "content_type": "text/plain",
            }],
        )
        self.assertIn("Review this", text)
        self.assertIn("[report.txt](http://router/file) (5 bytes, text/plain)", text)


if __name__ == "__main__":
    unittest.main()
