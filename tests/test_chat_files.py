from pathlib import Path
import tempfile
from threading import Condition
import unittest
from unittest import mock

from ciel_runtime_support.chat_files import ChatFilePorts, ChatFileRepository
from ciel_runtime_support.chat_http_controller import (
    CHAT_FILE_STREAM_CHUNK_BYTES,
    ChatHttpController,
    ChatHttpReadServices,
    ChatHttpWriteServices,
)


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

    def test_path_upload_streams_in_chunks_without_read_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large.bin"
            payload = b"a" * (2 * ChatFileRepository.COPY_CHUNK_BYTES + 7)
            source.write_bytes(payload)
            stored = root / "stored"
            repository = ChatFileRepository(
                stored,
                "http://router",
                ChatFilePorts(timestamp_ns=lambda: 789),
            )
            original_open = Path.open
            original_named_temporary_file = tempfile.NamedTemporaryFile
            read_sizes: list[int] = []
            write_sizes: list[int] = []

            class TrackingReader:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    self.stream.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.stream.__exit__(*args)

                def read(self, size=-1):
                    read_sizes.append(size)
                    return self.stream.read(size)

            def tracked_open(path, *args, **kwargs):
                stream = original_open(path, *args, **kwargs)
                return TrackingReader(stream) if path == source else stream

            class TrackingWriter:
                def __init__(self, stream):
                    self.stream = stream

                @property
                def name(self):
                    return self.stream.name

                def __enter__(self):
                    self.stream.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.stream.__exit__(*args)

                def write(self, data):
                    write_sizes.append(len(data))
                    return self.stream.write(data)

            def tracked_named_temporary_file(*args, **kwargs):
                return TrackingWriter(original_named_temporary_file(*args, **kwargs))

            with (
                mock.patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("store_path must stream the source"),
                ),
                mock.patch.object(Path, "open", autospec=True, side_effect=tracked_open),
                mock.patch(
                    "ciel_runtime_support.chat_files.tempfile.NamedTemporaryFile",
                    side_effect=tracked_named_temporary_file,
                ),
            ):
                upload = repository.store_path(source)

            with original_open(stored / upload["name"], "rb") as stored_stream:
                self.assertEqual(payload, stored_stream.read())
            self.assertEqual(len(payload), upload["bytes"])
            self.assertGreaterEqual(len(read_sizes), 4)
            self.assertTrue(
                all(size == ChatFileRepository.COPY_CHUNK_BYTES for size in read_sizes)
            )
            self.assertEqual(
                [
                    ChatFileRepository.COPY_CHUNK_BYTES,
                    ChatFileRepository.COPY_CHUNK_BYTES,
                    7,
                ],
                write_sizes,
            )
            self.assertFalse(any(path.suffix == ".tmp" for path in stored.iterdir()))

    def test_path_upload_removes_partial_file_when_streaming_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            source.write_bytes(b"a" * (ChatFileRepository.COPY_CHUNK_BYTES + 1))
            stored = root / "stored"
            repository = ChatFileRepository(stored, "http://router")
            original_open = Path.open

            class FailingReader:
                def __init__(self, stream):
                    self.stream = stream
                    self.calls = 0

                def __enter__(self):
                    self.stream.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.stream.__exit__(*args)

                def read(self, size=-1):
                    self.calls += 1
                    if self.calls > 1:
                        raise OSError("copy interrupted")
                    return self.stream.read(size)

            def failing_open(path, *args, **kwargs):
                stream = original_open(path, *args, **kwargs)
                return FailingReader(stream) if path == source else stream

            with (
                mock.patch.object(Path, "open", autospec=True, side_effect=failing_open),
                self.assertRaisesRegex(OSError, "copy interrupted"),
            ):
                repository.store_path(source)

            self.assertEqual([], list(stored.iterdir()))

    def test_size_limit_and_invalid_base64_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ChatFileRepository(
                Path(directory),
                "http://router",
                ChatFilePorts(max_bytes=lambda: 3),
            )
            with self.assertRaises(OverflowError):
                repository.store_upload({"name": "large.txt", "content": "four"})
            with self.assertRaisesRegex(ValueError, "invalid base64"):
                repository.store_upload({"name": "bad.bin", "encoding": "base64", "content": "%%%"})

    def test_oversized_base64_is_rejected_before_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ChatFileRepository(
                Path(directory),
                "http://router",
                ChatFilePorts(max_bytes=lambda: 3),
            )
            with (
                mock.patch(
                    "ciel_runtime_support.chat_files.base64.b64decode",
                    side_effect=AssertionError("oversized content must not be decoded"),
                ),
                self.assertRaisesRegex(OverflowError, "base64 content"),
            ):
                repository.store_upload(
                    {"name": "large.bin", "encoding": "base64", "content": "A" * 9}
                )

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


class ChatHttpFileStreamingTests(unittest.TestCase):
    def test_file_download_sets_length_and_writes_bounded_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "large.bin"
            payload = b"z" * (2 * CHAT_FILE_STREAM_CHUNK_BYTES + 11)
            target.write_bytes(payload)

            class RecordingWriter:
                def __init__(self):
                    self.chunks: list[bytes] = []

                def write(self, data):
                    chunk = bytes(data)
                    self.chunks.append(chunk)
                    return len(chunk)

            class Handler:
                def __init__(self):
                    self.status = None
                    self.headers = {}
                    self.wfile = RecordingWriter()

                def send_response(self, status):
                    self.status = status

                def send_header(self, name, value):
                    self.headers[str(name).casefold()] = str(value)

                def end_headers(self):
                    return None

            controller = ChatHttpController(
                router_base="http://router",
                reads=ChatHttpReadServices(
                    read_after=lambda *_args: [],
                    read_before=lambda *_args: [],
                    condition=Condition(),
                    safe_segment=ChatFileRepository.safe_segment,
                    files_dir=root,
                ),
                writes=ChatHttpWriteServices(
                    write_json=lambda *_args, **_kwargs: None,
                    append_message=lambda body: body,
                    store_upload=lambda body: body,
                ),
            )
            handler = Handler()

            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("downloads must stream from disk"),
            ):
                self.assertTrue(controller.get(handler, "/ca/chat/files/large.bin"))

            self.assertEqual(200, handler.status)
            self.assertEqual(str(len(payload)), handler.headers["content-length"])
            self.assertEqual("application/octet-stream", handler.headers["content-type"])
            self.assertGreater(len(handler.wfile.chunks), 1)
            self.assertTrue(
                all(len(chunk) <= CHAT_FILE_STREAM_CHUNK_BYTES for chunk in handler.wfile.chunks)
            )
            self.assertEqual(payload, b"".join(handler.wfile.chunks))


if __name__ == "__main__":
    unittest.main()
