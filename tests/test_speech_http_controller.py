import io
import json
import unittest

from ciel_runtime_support.speech_http_controller import SpeechHttpController, SpeechHttpPorts
from ciel_runtime_support.speech_models import (
    DEFAULT_COSYVOICE_REFERENCE_AUDIO,
    DEFAULT_COSYVOICE_REFERENCE_TEXT,
    DEFAULT_TTS_REFERENCE_AUDIO,
)


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(str(key).lower(), default)


class _Handler:
    def __init__(self):
        self.status = None
        self.response_headers = {}
        self.wfile = io.BytesIO()
        self.close_connection = False

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name.lower()] = value

    def end_headers(self):
        pass


class _Response:
    def __init__(self, data=b"", content_type="application/json", status=200):
        self.data = data
        self.headers = _Headers({"content-type": content_type})
        self.status = status
        self.offset = 0

    def read(self, size=-1):
        if size is None or size < 0:
            chunk = self.data[self.offset:]
            self.offset = len(self.data)
            return chunk
        chunk = self.data[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def read1(self, size=-1):
        return self.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SpeechHttpControllerTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "speech": {
                "asr": {"enabled": True, "base_url": "http://ciel-asr", "endpoint": "/v1/audio/transcriptions", "model": "Qwen/Qwen3-ASR-0.6B", "language": "auto", "silence_ms": 900, "min_speech_ms": 300, "vad_threshold": 0.018, "api_key": "asr-secret", "timeout_seconds": 30},
                "tts": {"enabled": True, "base_url": "http://ciel-tts", "endpoint": "/v1/audio/speech", "voices_endpoint": "/v1/audio/voices", "model": "OpenMOSS-Team/MOSS-TTS-Nano", "voice": "default", "language": "ko", "ref_audio": "https://example.test/reference.wav", "ref_text": "reference words", "response_format": "wav", "speed": 1.0, "auto_speak": True, "streaming": False, "sample_rate": 48000, "api_key": "tts-secret", "timeout_seconds": 30},
                "colab": {"enabled": True, "distribution": "Ubuntu-26.04", "auth": "adc", "asr_session": "ciel-asr", "tts_session": "ciel-tts", "asr_model": "Qwen/Qwen3-ASR-0.6B", "asr_accelerator": "T4", "tts_accelerator": "T4", "tts_backend": "moss"},
                "tailscale": {"enabled": True, "asr_hostname": "ciel-asr", "tts_hostname": "ciel-tts"},
            }
        }
        self.requests = []
        self.saved = []

    def controller(self, response=None, *, colab_action=None, colab_status=None):
        def write_json(handler, value, status=200):
            data = json.dumps(value).encode()
            handler.send_response(status)
            handler.send_header("content-type", "application/json")
            handler.send_header("content-length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)

        def urlopen(request, timeout):
            self.requests.append((request, timeout))
            return response or _Response(b'{"text":"hello"}')

        return SpeechHttpController(SpeechHttpPorts(lambda: self.config, lambda value: self.saved.append(value), write_json, lambda *_args: None, urlopen, colab_action, colab_status))

    def test_public_config_masks_remote_tokens_and_lists_all_audio_endpoints(self):
        public = self.controller().public_config()

        self.assertNotIn("api_key", public["asr"])
        self.assertNotIn("api_key", public["tts"])
        self.assertNotIn("ref_audio", public["tts"])
        self.assertTrue(public["tts"]["ref_audio_set"])
        self.assertEqual("custom", public["tts"]["ref_audio_source"])
        self.assertTrue(public["asr"]["api_key_set"])
        self.assertEqual("Ubuntu-26.04", public["colab"]["distribution"])
        self.assertEqual("ciel-asr", public["colab"]["asr_session"])
        self.assertEqual("POST /v1/audio/transcriptions", public["endpoints"]["asr"])
        self.assertEqual("POST /v1/audio/speech", public["endpoints"]["tts"])

    def test_json_asr_request_is_converted_to_openai_multipart(self):
        handler = _Handler()
        body = json.dumps({"audio_base64": "UklGRg==", "filename": "voice.wav", "content_type": "audio/wav"}).encode()

        handled = self.controller().post(handler, "/v1/audio/transcriptions", body, "application/json")

        self.assertTrue(handled)
        request, timeout = self.requests[0]
        self.assertEqual("http://ciel-asr/v1/audio/transcriptions", request.full_url)
        self.assertEqual("Bearer asr-secret", request.headers["Authorization"])
        self.assertIn("multipart/form-data; boundary=", request.headers["Content-type"])
        self.assertIn(b'name="model"', request.data)
        self.assertIn(b'filename="voice.wav"', request.data)
        self.assertNotIn(b'name="language"', request.data)
        self.assertEqual(30, timeout)
        self.assertEqual(200, handler.status)

    def test_colab_profile_is_validated_and_saved(self):
        handler = _Handler()
        body = json.dumps({"colab": {"profile": "second-account"}}).encode()

        self.controller().post(handler, "/ca/speech/config", body, "application/json")

        self.assertEqual("second-account", self.saved[0]["speech"]["colab"]["profile"])

    def test_colab_action_uses_saved_profile_and_ephemeral_secrets(self):
        handler = _Handler()
        calls = []
        body = json.dumps({
            "action": "deploy",
            "secrets": {"tailscale_auth_key": "tail-secret", "speech_api_key": "voice-secret"},
        }).encode()
        controller = self.controller(colab_action=lambda action, settings, secrets: calls.append((action, settings, secrets)) or {"ok": True, "job": {"id": "abc"}})

        controller.post(handler, "/ca/speech/colab/action", body, "application/json")

        self.assertEqual("deploy", calls[0][0])
        self.assertEqual("adc", calls[0][1]["auth"])
        self.assertEqual("tail-secret", calls[0][2]["tailscale_auth_key"])
        self.assertEqual(200, handler.status)

    def test_colab_job_status_endpoint_reports_latest_job(self):
        handler = _Handler()
        controller = self.controller(colab_status=lambda _job_id: {"ok": True, "job": {"id": "abc", "running": True}})

        handled = controller.get(handler, "/ca/speech/colab/job")

        self.assertTrue(handled)
        self.assertEqual("abc", json.loads(handler.wfile.getvalue())["job"]["id"])

    def test_tts_request_receives_defaults_and_returns_binary_audio(self):
        handler = _Handler()
        controller = self.controller(_Response(b"RIFFaudio", "audio/wav"))

        handled = controller.post(handler, "/v1/audio/speech", b'{"input":"hello"}', "application/json")

        self.assertTrue(handled)
        request, _timeout = self.requests[0]
        payload = json.loads(request.data)
        self.assertEqual("OpenMOSS-Team/MOSS-TTS-Nano", payload["model"])
        self.assertEqual("default", payload["voice"])
        self.assertEqual("ko", payload["language"])
        self.assertEqual("https://example.test/reference.wav", payload["ref_audio"])
        self.assertEqual("Bearer tts-secret", request.headers["Authorization"])
        self.assertEqual("audio/wav", handler.response_headers["content-type"])
        self.assertEqual(b"RIFFaudio", handler.wfile.getvalue())

    def test_cosyvoice_tts_stream_is_flushed_and_forwarded_as_pcm(self):
        handler = _Handler()
        self.config["speech"]["tts"].update({
            "model": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            "sample_rate": 24000,
            "streaming": True,
        })
        controller = self.controller(_Response(b"\x00\x00\x01\x00", "audio/pcm"))

        handled = controller.post(
            handler,
            "/v1/audio/speech",
            b'{"input":"hello","stream":true,"stream_format":"audio","response_format":"pcm"}',
            "application/json",
        )

        self.assertTrue(handled)
        payload = json.loads(self.requests[0][0].data)
        self.assertTrue(payload["stream"])
        self.assertEqual("audio", payload["stream_format"])
        self.assertEqual("pcm", payload["response_format"])
        self.assertEqual("audio/pcm", handler.response_headers["content-type"])
        self.assertEqual("close", handler.response_headers["connection"])
        self.assertTrue(handler.close_connection)
        self.assertEqual(b"\x00\x00\x01\x00", handler.wfile.getvalue())

    def test_cosyvoice_repairs_legacy_moss_reference_pair(self):
        handler = _Handler()
        self.config["speech"]["tts"].update({
            "model": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            "ref_audio": DEFAULT_TTS_REFERENCE_AUDIO,
            "ref_text": "",
        })

        self.controller().post(handler, "/v1/audio/speech", b'{"input":"hello"}', "application/json")

        self.assertEqual(200, handler.status)
        payload = json.loads(self.requests[0][0].data)
        self.assertEqual(DEFAULT_COSYVOICE_REFERENCE_AUDIO, payload["ref_audio"])
        self.assertEqual(DEFAULT_COSYVOICE_REFERENCE_TEXT, payload["ref_text"])

    def test_public_config_identifies_builtin_cosyvoice_reference(self):
        self.config["speech"]["tts"].update({
            "model": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            "ref_audio": DEFAULT_COSYVOICE_REFERENCE_AUDIO,
            "ref_text": DEFAULT_COSYVOICE_REFERENCE_TEXT,
        })

        public = self.controller().public_config()

        self.assertEqual("default", public["tts"]["ref_audio_source"])

    def test_cosyvoice_rejects_incomplete_custom_reference_pair(self):
        handler = _Handler()
        self.config["speech"]["tts"].update({
            "model": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            "ref_audio": "https://example.test/custom.wav",
            "ref_text": "",
        })

        self.controller().post(handler, "/v1/audio/speech", b'{"input":"hello"}', "application/json")

        self.assertEqual(400, handler.status)
        self.assertEqual([], self.requests)
        self.assertIn("custom ref_audio", handler.wfile.getvalue().decode())

    def test_cosyvoice_rejects_half_of_per_request_reference_pair(self):
        handler = _Handler()
        self.config["speech"]["tts"].update({"model": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"})

        self.controller().post(
            handler,
            "/v1/audio/speech",
            b'{"input":"hello","ref_audio":"https://example.test/custom.wav"}',
            "application/json",
        )

        self.assertEqual(400, handler.status)
        self.assertEqual([], self.requests)
        self.assertIn("request must provide both", handler.wfile.getvalue().decode())

    def test_saving_cosyvoice_model_repairs_legacy_reference_pair(self):
        handler = _Handler()
        self.config["speech"]["tts"].update({
            "ref_audio": DEFAULT_TTS_REFERENCE_AUDIO,
            "ref_text": "",
        })
        body = json.dumps({"tts": {"model": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"}}).encode()

        self.controller().post(handler, "/ca/speech/config", body, "application/json")

        tts = self.saved[0]["speech"]["tts"]
        self.assertEqual(DEFAULT_COSYVOICE_REFERENCE_AUDIO, tts["ref_audio"])
        self.assertEqual(DEFAULT_COSYVOICE_REFERENCE_TEXT, tts["ref_text"])

    def test_blank_token_update_preserves_existing_secret(self):
        handler = _Handler()
        body = json.dumps({"asr": {"base_url": "https://new-asr.tailnet.ts.net", "api_key": ""}}).encode()

        self.controller().post(handler, "/ca/speech/config", body, "application/json")

        self.assertEqual("asr-secret", self.saved[0]["speech"]["asr"]["api_key"])
        self.assertEqual("https://new-asr.tailnet.ts.net", self.saved[0]["speech"]["asr"]["base_url"])

    def test_colab_connection_settings_are_validated_and_saved(self):
        handler = _Handler()
        body = json.dumps({"colab": {
            "enabled": True,
            "distribution": "Ubuntu-24.04",
            "auth": "oauth2",
            "asr_session": "speech-asr",
            "tts_session": "speech-tts",
            "asr_model": "Qwen/Qwen3-ASR-1.7B",
            "asr_accelerator": "l4",
            "tts_accelerator": "a100",
            "tts_backend": "cosyvoice3",
        }}).encode()

        self.controller().post(handler, "/ca/speech/config", body, "application/json")

        saved = self.saved[0]["speech"]["colab"]
        self.assertEqual("Ubuntu-24.04", saved["distribution"])
        self.assertEqual("oauth2", saved["auth"])
        self.assertEqual("L4", saved["asr_accelerator"])
        self.assertEqual("Qwen/Qwen3-ASR-1.7B", saved["asr_model"])
        self.assertEqual("A100", saved["tts_accelerator"])
        self.assertEqual("cosyvoice3", saved["tts_backend"])
        self.assertEqual(200, handler.status)

    def test_colab_connection_settings_reject_shell_metacharacters(self):
        handler = _Handler()
        body = json.dumps({"colab": {"asr_session": "ciel-asr; reboot"}}).encode()

        self.controller().post(handler, "/ca/speech/config", body, "application/json")

        self.assertEqual([], self.saved)
        self.assertEqual(400, handler.status)

    def test_live_voice_vad_settings_are_bounded(self):
        handler = _Handler()
        body = json.dumps({"asr": {
            "silence_ms": 50,
            "min_speech_ms": 9999,
            "vad_threshold": 0.9,
        }}).encode()

        self.controller().post(handler, "/ca/speech/config", body, "application/json")

        saved = self.saved[0]["speech"]["asr"]
        self.assertEqual(250, saved["silence_ms"])
        self.assertEqual(2000, saved["min_speech_ms"])
        self.assertEqual(0.2, saved["vad_threshold"])


if __name__ == "__main__":
    unittest.main()
