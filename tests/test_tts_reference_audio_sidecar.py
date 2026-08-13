import base64
from contextlib import contextmanager
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.request_limits_config import MIB, WorkspaceRequestLimits
from ciel_runtime_support.request_body_policy import (
    RequestBodyCapacityExceeded,
    RequestBodyTooLarge,
    RouterRequestBodyPolicy,
)
from ciel_runtime_support.speech_http_controller import (
    SpeechHttpController,
    SpeechHttpPorts,
)
from ciel_runtime_support.tts_reference_audio_repository import (
    REFERENCE_MARKER_PREFIX,
    TtsReferenceAudioRepository,
)


class _Handler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[str(name).casefold()] = value

    def end_headers(self):
        return


class _Response:
    status = 200
    headers = {"content-type": "audio/wav"}

    def __init__(self, data=b"RIFFresult"):
        self.data = data

    def read(self, _size=-1):
        data, self.data = self.data, b""
        return data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _limits(reference=1024):
    return WorkspaceRequestLimits(
        workspace="C:/work/alpha",
        model_request_max_bytes=8 * MIB,
        chat_attachment_max_bytes=1024,
        speech_audio_max_bytes=1024,
        tts_reference_audio_max_bytes=reference,
        configured_inflight_request_max_bytes=32 * MIB,
        inflight_request_max_bytes=32 * MIB,
        sources={},
    )


class _Fixture:
    def __init__(
        self,
        root: Path,
        *,
        repository=True,
        save_failure=None,
        admission=None,
        reference_limit=1024,
    ):
        self.state = {
            "speech": {
                "tts": {
                    "enabled": True,
                    "base_url": "http://tts.test",
                    "endpoint": "/v1/audio/speech",
                    "model": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
                    "voice": "default",
                    "language": "ko",
                    "ref_audio": "",
                    "ref_text": "",
                },
                "asr": {"enabled": False},
            }
        }
        self.logs = []
        self.requests = []
        self.save_failure = save_failure
        self.reference_limit = reference_limit
        self.repository = (
            TtsReferenceAudioRepository(
                root,
                transformed_admission=admission,
            )
            if repository
            else None
        )

    def load(self):
        return copy.deepcopy(self.state)

    def save(self, value):
        if self.save_failure is not None:
            raise self.save_failure
        self.state = copy.deepcopy(value)

    @staticmethod
    def write_json(handler, value, status=200):
        raw = json.dumps(value).encode()
        handler.send_response(status)
        handler.send_header("content-type", "application/json")
        handler.send_header("content-length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    def urlopen(self, request, timeout):
        self.requests.append((request, timeout))
        return _Response()

    def controller(self):
        return SpeechHttpController(
            SpeechHttpPorts(
                self.load,
                self.save,
                self.write_json,
                lambda level, message: self.logs.append((level, message)),
                self.urlopen,
                request_limits=lambda: _limits(self.reference_limit),
                reference_audio_repository=self.repository,
            )
        )

    def post_config(self, update):
        handler = _Handler()
        self.controller().post(
            handler,
            "/ca/speech/config",
            json.dumps(update).encode(),
            "application/json",
        )
        return handler


class TtsReferenceAudioSidecarTests(unittest.TestCase):
    def test_config_save_decodes_once_and_persists_only_marker_and_text(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            source = "data:audio/wav;base64," + base64.b64encode(b"RIFFvoice").decode()
            original_decode = base64.b64decode

            with mock.patch(
                "ciel_runtime_support.tts_reference_audio_repository.base64.b64decode",
                wraps=original_decode,
            ) as decode:
                handler = fixture.post_config(
                    {"tts": {"ref_audio": source, "ref_text": "exact words"}}
                )

            saved = fixture.state["speech"]["tts"]
            self.assertEqual(200, handler.status)
            self.assertEqual(1, decode.call_count)
            self.assertTrue(saved["ref_audio"].startswith(REFERENCE_MARKER_PREFIX))
            self.assertEqual("exact words", saved["ref_text"])
            self.assertNotIn("base64", json.dumps(fixture.state))
            public = handler.wfile.getvalue().decode()
            self.assertNotIn(REFERENCE_MARKER_PREFIX, public)
            self.assertNotIn(source, public)
            self.assertEqual(1, len(list(Path(directory).glob("*.bin"))))

    def test_decoded_boundary_overflow_is_a_413_and_leaves_no_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _Fixture(root, reference_limit=4)
            # Five decoded bytes have the same padded base64 character count
            # as the four-byte limit, so the repository must enforce the
            # post-decode boundary without converting it into a router 500.
            source = "data:audio/wav;base64," + base64.b64encode(b"12345").decode()

            handler = fixture.post_config(
                {"tts": {"ref_audio": source, "ref_text": "words"}}
            )

            self.assertEqual(413, handler.status)
            self.assertIn("request_too_large", handler.wfile.getvalue().decode())
            self.assertEqual([], list(root.glob("*.bin")))
            self.assertEqual("", fixture.state["speech"]["tts"]["ref_audio"])

    def test_clear_removes_sidecar_and_exact_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _Fixture(root)
            source = "data:audio/wav;base64," + base64.b64encode(b"voice").decode()
            fixture.post_config({"tts": {"ref_audio": source, "ref_text": "words"}})
            self.assertEqual(1, len(list(root.glob("*.bin"))))

            handler = fixture.post_config({"tts": {"clear_ref_audio": True}})

            self.assertEqual(200, handler.status)
            self.assertEqual("", fixture.state["speech"]["tts"]["ref_audio"])
            self.assertEqual("", fixture.state["speech"]["tts"]["ref_text"])
            self.assertEqual([], list(root.glob("*.bin")))

    def test_replace_commits_new_marker_then_removes_old_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _Fixture(root)
            first = "data:audio/wav;base64," + base64.b64encode(b"first").decode()
            second = "data:audio/wav;base64," + base64.b64encode(b"second").decode()
            fixture.post_config({"tts": {"ref_audio": first, "ref_text": "first"}})
            old_marker = fixture.state["speech"]["tts"]["ref_audio"]

            fixture.post_config({"tts": {"ref_audio": second, "ref_text": "second"}})

            new_marker = fixture.state["speech"]["tts"]["ref_audio"]
            self.assertNotEqual(old_marker, new_marker)
            self.assertEqual(1, len(list(root.glob("*.bin"))))
            with self.assertRaisesRegex(ValueError, "upload it again"):
                fixture.repository.expand_data_url(old_marker, 1024)

    def test_failed_config_save_rolls_back_new_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _Fixture(root, save_failure=OSError("disk full"))
            source = "data:audio/wav;base64," + base64.b64encode(b"voice").decode()

            handler = fixture.post_config(
                {"tts": {"ref_audio": source, "ref_text": "words"}}
            )

            self.assertEqual(500, handler.status)
            self.assertEqual([], list(root.glob("*.bin")))
            self.assertEqual("", fixture.state["speech"]["tts"]["ref_audio"])

    def test_cleanup_failure_after_save_is_success_with_orphan_and_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _Fixture(root)
            first = "data:audio/wav;base64," + base64.b64encode(b"first").decode()
            second = "data:audio/wav;base64," + base64.b64encode(b"second").decode()
            fixture.post_config({"tts": {"ref_audio": first, "ref_text": "first"}})

            with mock.patch.object(
                fixture.repository,
                "discard",
                side_effect=PermissionError("file in use"),
            ):
                handler = fixture.post_config(
                    {"tts": {"ref_audio": second, "ref_text": "second"}}
                )

            self.assertEqual(200, handler.status)
            self.assertTrue(
                fixture.state["speech"]["tts"]["ref_audio"].startswith(
                    REFERENCE_MARKER_PREFIX
                )
            )
            self.assertEqual(2, len(list(root.glob("*.bin"))))
            self.assertTrue(any("cleanup_deferred" in item[1] for item in fixture.logs))

    def test_url_reference_is_preserved_without_creating_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _Fixture(root)
            url = "https://example.test/reference.wav"

            handler = fixture.post_config(
                {"tts": {"ref_audio": url, "ref_text": "exact words"}}
            )

            self.assertEqual(200, handler.status)
            self.assertEqual(url, fixture.state["speech"]["tts"]["ref_audio"])
            self.assertEqual([], list(root.glob("*.bin")))

    def test_legacy_embedded_reference_migrates_on_next_unrelated_save(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            source = "data:audio/wav;base64," + base64.b64encode(b"legacy").decode()
            fixture.state["speech"]["tts"].update(
                {"ref_audio": source, "ref_text": "legacy words"}
            )

            handler = fixture.post_config({"tts": {"speed": 1.1}})

            self.assertEqual(200, handler.status)
            self.assertTrue(
                fixture.state["speech"]["tts"]["ref_audio"].startswith(
                    REFERENCE_MARKER_PREFIX
                )
            )
            self.assertEqual("legacy words", fixture.state["speech"]["tts"]["ref_text"])

    def test_no_repository_preserves_legacy_embedded_config_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), repository=False)
            source = "data:audio/wav;base64," + base64.b64encode(b"legacy").decode()

            handler = fixture.post_config(
                {"tts": {"ref_audio": source, "ref_text": "legacy words"}}
            )

            self.assertEqual(200, handler.status)
            self.assertEqual(source, fixture.state["speech"]["tts"]["ref_audio"])

    def test_forward_expands_marker_only_inside_transformed_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            active = {"value": False}
            admissions = []

            @contextmanager
            def admission(path, original, transformed, content_type):
                admissions.append((path, original, transformed, content_type))
                active["value"] = True
                try:
                    yield
                finally:
                    active["value"] = False

            fixture = _Fixture(Path(directory), admission=admission)
            source = "data:audio/wav;base64," + base64.b64encode(b"voice").decode()
            marker = fixture.repository.store_data_url(source, 1024)
            fixture.state["speech"]["tts"].update(
                {"ref_audio": marker, "ref_text": "exact words"}
            )
            raw = b'{"input":"hello"}'
            handler = _Handler()
            controller = fixture.controller()
            original_urlopen = fixture.urlopen

            def checked_urlopen(request, timeout):
                self.assertTrue(active["value"])
                return original_urlopen(request, timeout)

            fixture.urlopen = checked_urlopen
            controller = fixture.controller()
            controller.post(handler, "/v1/audio/speech", raw, "application/json")

            self.assertEqual(200, handler.status)
            request = fixture.requests[0][0]
            self.assertNotIn(marker.encode(), request.data)
            self.assertIn(base64.b64encode(b"voice"), request.data)
            self.assertEqual("/v1/audio/speech", admissions[0][0])
            self.assertEqual(len(raw), admissions[0][1])
            self.assertEqual(len(request.data), admissions[0][2])
            self.assertFalse(active["value"])

    def test_client_cannot_submit_opaque_local_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            source = "data:audio/wav;base64," + base64.b64encode(b"voice").decode()
            marker = fixture.repository.store_data_url(source, 1024)
            fixture.state["speech"]["tts"].update(
                {
                    "ref_audio": "https://example.test/default.wav",
                    "ref_text": "default words",
                }
            )
            handler = _Handler()
            body = json.dumps(
                {"input": "hello", "ref_audio": marker, "ref_text": "words"}
            ).encode()

            fixture.controller().post(
                handler, "/v1/audio/speech", body, "application/json"
            )

            self.assertEqual(400, handler.status)
            self.assertEqual([], fixture.requests)
            self.assertIn("opaque", handler.wfile.getvalue().decode())

    def test_missing_configured_sidecar_returns_safe_error(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            fixture.state["speech"]["tts"].update(
                {
                    "ref_audio": REFERENCE_MARKER_PREFIX + "a" * 48,
                    "ref_text": "words",
                }
            )
            handler = _Handler()

            fixture.controller().post(
                handler,
                "/v1/audio/speech",
                b'{"input":"hello"}',
                "application/json",
            )

            self.assertEqual(400, handler.status)
            self.assertIn("upload it again", handler.wfile.getvalue().decode())
            self.assertNotIn(str(Path(directory)), handler.wfile.getvalue().decode())

    def test_transformed_route_limit_exception_is_not_downgraded_to_400(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = RouterRequestBodyPolicy(
                environment={},
                limits=_limits(reference=1),
            )
            fixture = _Fixture(
                Path(directory),
                admission=policy.admit_transformed,
                reference_limit=MIB,
            )
            source = (
                "data:audio/wav;base64,"
                + base64.b64encode(b"a" * (800 * 1024)).decode()
            )
            marker = fixture.repository.store_data_url(source, MIB)
            fixture.state["speech"]["tts"].update(
                {"ref_audio": marker, "ref_text": "words"}
            )

            with self.assertRaises(RequestBodyTooLarge):
                fixture.controller().post(
                    _Handler(),
                    "/v1/audio/speech",
                    b'{"input":"hello"}',
                    "application/json",
                )

            self.assertEqual([], fixture.requests)

    def test_transformed_capacity_exception_is_not_downgraded_to_400(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = RouterRequestBodyPolicy(
                environment={},
                limits=replace(
                    _limits(reference=1024),
                    configured_inflight_request_max_bytes=100,
                    inflight_request_max_bytes=100,
                ),
            )
            fixture = _Fixture(
                Path(directory),
                admission=policy.admit_transformed,
            )
            source = "data:audio/wav;base64," + base64.b64encode(b"a" * 100).decode()
            marker = fixture.repository.store_data_url(source, 1024)
            fixture.state["speech"]["tts"].update(
                {"ref_audio": marker, "ref_text": "words"}
            )

            with self.assertRaises(RequestBodyCapacityExceeded):
                fixture.controller().post(
                    _Handler(),
                    "/v1/audio/speech",
                    b'{"input":"hello"}',
                    "application/json",
                )

            self.assertEqual([], fixture.requests)


if __name__ == "__main__":
    unittest.main()
