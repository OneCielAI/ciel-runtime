import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.chat_files import ChatFilePorts, ChatFileRepository
from ciel_runtime_support.request_body_policy import RouterRequestBodyPolicy
from ciel_runtime_support.request_limits_config import (
    MIB,
    WorkspaceRequestLimits,
    REQUEST_BODY_MEMORY_MULTIPLIER,
    TTS_BATCH_REQUEST_MAX_BYTES,
    base64_json_wire_max_bytes,
    resolve_workspace_request_limits,
    update_workspace_request_limit,
)
from ciel_runtime_support.speech_http_controller import (
    SpeechHttpController,
    SpeechHttpPorts,
)
from ciel_runtime_support.workspace_router_selection import workspace_digest


class RequestLimitResolutionIntegrationTests(unittest.TestCase):
    def test_malformed_environment_value_falls_back_to_workspace_value(self):
        workspace = "C:/work/alpha"
        config = {}
        update_workspace_request_limit(
            config,
            workspace,
            "model_request_max_bytes",
            "256 MiB",
        )

        limits = resolve_workspace_request_limits(
            config,
            workspace,
            {"CIEL_RUNTIME_ROUTER_MODEL_REQUEST_MAX_BYTES": "not-a-size"},
        )

        self.assertEqual(256 * MIB, limits.model_request_max_bytes)
        self.assertEqual("workspace", limits.sources["model_request_max_bytes"])

    def test_valid_environment_value_wins_over_workspace_value(self):
        workspace = "C:/work/alpha"
        config = {}
        update_workspace_request_limit(
            config,
            workspace,
            "speech_audio_max_bytes",
            "200 MiB",
        )

        limits = resolve_workspace_request_limits(
            config,
            workspace,
            {"CIEL_RUNTIME_SPEECH_AUDIO_MAX_BYTES": str(300 * MIB)},
        )

        self.assertEqual(300 * MIB, limits.speech_audio_max_bytes)
        self.assertEqual(
            "environment:CIEL_RUNTIME_SPEECH_AUDIO_MAX_BYTES",
            limits.sources["speech_audio_max_bytes"],
        )

    def test_workspace_guard_rejects_a_record_for_another_canonical_path(self):
        target = "C:/work/alpha"
        config = {
            "request_limits": {
                workspace_digest(target): {
                    "workspace": "C:/work/not-alpha",
                    "model_request_max_bytes": 400 * MIB,
                }
            }
        }

        limits = resolve_workspace_request_limits(config, target, {})

        self.assertEqual(512 * MIB, limits.model_request_max_bytes)
        self.assertEqual("default", limits.sources["model_request_max_bytes"])

    def test_reset_removes_only_active_workspace_record(self):
        alpha = "C:/work/alpha"
        beta = "C:/work/beta"
        config = {}
        update_workspace_request_limit(
            config,
            alpha,
            "chat_attachment_max_bytes",
            "250 MiB",
        )
        update_workspace_request_limit(
            config,
            beta,
            "chat_attachment_max_bytes",
            "300 MiB",
        )

        update_workspace_request_limit(config, alpha, "reset", "")

        self.assertNotIn(workspace_digest(alpha), config["request_limits"])
        self.assertIn(workspace_digest(beta), config["request_limits"])
        self.assertEqual(
            300 * MIB,
            resolve_workspace_request_limits(
                config,
                beta,
                {},
            ).chat_attachment_max_bytes,
        )

    def test_hard_max_decoded_attachment_derives_admissible_wire_and_inflight_caps(self):
        workspace = "C:/work/alpha"
        config = {}
        update_workspace_request_limit(
            config,
            workspace,
            "chat_attachment_max_bytes",
            "500 MiB",
        )
        update_workspace_request_limit(
            config,
            workspace,
            "speech_audio_max_bytes",
            "500 MiB",
        )
        update_workspace_request_limit(
            config,
            workspace,
            "inflight_request_max_bytes",
            "512 MiB",
        )

        limits = resolve_workspace_request_limits(config, workspace, {})
        expected_wire = base64_json_wire_max_bytes(500 * MIB)

        self.assertGreater(expected_wire, 512 * MIB)
        self.assertEqual(expected_wire, limits.chat_attachment_wire_max_bytes)
        self.assertEqual(expected_wire, limits.speech_audio_wire_max_bytes)
        self.assertEqual(TTS_BATCH_REQUEST_MAX_BYTES, limits.largest_wire_request_bytes)
        self.assertEqual(
            REQUEST_BODY_MEMORY_MULTIPLIER * TTS_BATCH_REQUEST_MAX_BYTES,
            limits.inflight_request_max_bytes,
        )


def _tiny_limits(
    *,
    chat: int = 3,
    speech: int = 3,
    reference: int = 3,
) -> WorkspaceRequestLimits:
    provisional = WorkspaceRequestLimits(
        workspace="C:/work/alpha",
        model_request_max_bytes=8 * MIB,
        chat_attachment_max_bytes=chat,
        speech_audio_max_bytes=speech,
        tts_reference_audio_max_bytes=reference,
        configured_inflight_request_max_bytes=32 * MIB,
        inflight_request_max_bytes=32 * MIB,
        sources={},
    )
    return provisional


class _Handler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()
        self.close_connection = False

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[str(name).casefold()] = value

    def end_headers(self):
        return


class RequestLimitConsumerIntegrationTests(unittest.TestCase):
    def _speech_controller(self, limits, config=None, requests=None):
        settings = config or {
            "speech": {
                "asr": {
                    "enabled": True,
                    "base_url": "http://asr.test",
                    "endpoint": "/v1/audio/transcriptions",
                },
                "tts": {
                    "enabled": True,
                    "base_url": "http://tts.test",
                    "endpoint": "/v1/audio/speech",
                    "model": "test-tts",
                },
            }
        }
        outbound = [] if requests is None else requests

        def write_json(handler, value, status=200):
            handler.status = status
            handler.wfile.write(json.dumps(value).encode())

        def urlopen(request, timeout):
            del timeout
            outbound.append(request)
            raise AssertionError("rejected media must not reach upstream")

        ports = SpeechHttpPorts(
            load_config=lambda: settings,
            save_config=lambda _value: None,
            write_json=write_json,
            log=lambda *_args: None,
            urlopen=urlopen,
            request_limits=lambda: limits,
        )
        return SpeechHttpController(ports), outbound

    def test_router_uses_derived_workspace_wire_caps(self):
        limits = _tiny_limits(chat=9, speech=12, reference=15)
        policy = RouterRequestBodyPolicy({}, limits=limits)

        self.assertEqual(
            limits.chat_attachment_wire_max_bytes,
            policy.limit_for("/ca/channel/files"),
        )
        self.assertEqual(
            limits.speech_audio_wire_max_bytes,
            policy.limit_for("/v1/audio/transcriptions"),
        )
        self.assertEqual(
            limits.speech_audio_max_bytes + MIB,
            policy.limit_for("/v1/audio/transcriptions", "audio/wav"),
        )
        self.assertEqual(
            limits.tts_reference_wire_max_bytes,
            policy.limit_for("/v1/audio/speech"),
        )

    def test_chat_repository_uses_injected_decoded_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ChatFileRepository(
                Path(directory),
                "http://router",
                ChatFilePorts(max_bytes=lambda: 3),
            )

            with self.assertRaises(OverflowError):
                repository.store_upload(
                    {
                        "name": "four.bin",
                        "encoding": "base64",
                        "content": base64.b64encode(b"four").decode(),
                    }
                )

    def test_local_path_is_stat_checked_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "four.bin"
            source.write_bytes(b"four")
            repository = ChatFileRepository(
                Path(directory) / "stored",
                "http://router",
                ChatFilePorts(max_bytes=lambda: 3),
            )

            with (
                mock.patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("oversized source must not be read"),
                ),
                self.assertRaises(OverflowError),
            ):
                repository.store_path(source)

    def test_json_asr_uses_configured_decoded_limit(self):
        controller, requests = self._speech_controller(_tiny_limits(speech=3))
        handler = _Handler()
        raw = json.dumps(
            {"audio_base64": base64.b64encode(b"four").decode()}
        ).encode()

        self.assertTrue(
            controller.post(
                handler,
                "/v1/audio/transcriptions",
                raw,
                "application/json",
            )
        )

        self.assertEqual(413, handler.status)
        self.assertEqual([], requests)
        self.assertIn("request_too_large", handler.wfile.getvalue().decode())

    def test_raw_asr_uses_configured_decoded_limit_plus_envelope_allowance(self):
        controller, requests = self._speech_controller(_tiny_limits(speech=3))
        handler = _Handler()
        raw = b"four"

        controller.post(
            handler,
            "/v1/audio/transcriptions",
            raw,
            "audio/wav",
        )

        self.assertEqual(413, handler.status)
        self.assertEqual([], requests)

    def test_tts_reference_and_public_config_use_injected_limit(self):
        limits = _tiny_limits(reference=3)
        controller, requests = self._speech_controller(limits)
        public = controller.public_config()
        handler = _Handler()
        raw = json.dumps(
            {
                "input": "hello",
                "ref_audio": (
                    "data:audio/wav;base64," + base64.b64encode(b"four").decode()
                ),
                "ref_text": "four",
            }
        ).encode()

        controller.post(handler, "/v1/audio/speech", raw, "application/json")

        self.assertEqual(3, public["limits"]["tts_reference_audio_max_bytes"])
        self.assertEqual(413, handler.status)
        self.assertEqual([], requests)

    def test_tts_batch_validates_top_level_and_per_item_reference_audio(self):
        encoded = "data:audio/wav;base64," + base64.b64encode(b"four").decode()
        payloads = (
            {"ref_audio": encoded, "ref_text": "four", "items": [{"input": "hi"}]},
            {"items": [{"input": "hi", "ref_audio": encoded, "ref_text": "four"}]},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                controller, requests = self._speech_controller(_tiny_limits(reference=3))
                handler = _Handler()

                controller.post(
                    handler,
                    "/v1/audio/speech/batch",
                    json.dumps(payload).encode(),
                    "application/json",
                )

                self.assertEqual(413, handler.status)
                self.assertEqual([], requests)
                self.assertIn("request_too_large", handler.wfile.getvalue().decode())


if __name__ == "__main__":
    unittest.main()
