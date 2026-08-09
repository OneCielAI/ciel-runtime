import io
import json
import unittest

from ciel_runtime_support.speech_http_controller import SpeechHttpController, SpeechHttpPorts


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(str(key).lower(), default)


class _Handler:
    def __init__(self):
        self.status = None
        self.response_headers = {}
        self.wfile = io.BytesIO()

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

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SpeechHttpControllerTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "speech": {
                "asr": {"enabled": True, "base_url": "http://ciel-asr", "endpoint": "/v1/audio/transcriptions", "model": "Qwen/Qwen3-ASR-0.6B", "language": "auto", "api_key": "asr-secret", "timeout_seconds": 30},
                "tts": {"enabled": True, "base_url": "http://ciel-tts", "endpoint": "/v1/audio/speech", "voices_endpoint": "/v1/audio/voices", "model": "OpenMOSS-Team/MOSS-TTS-Nano", "voice": "default", "language": "ko", "ref_audio": "https://example.test/reference.wav", "response_format": "wav", "speed": 1.0, "auto_speak": True, "api_key": "tts-secret", "timeout_seconds": 30},
                "colab": {"enabled": True, "distribution": "Ubuntu-26.04", "auth": "adc", "asr_session": "ciel-asr", "tts_session": "ciel-tts", "asr_accelerator": "T4", "tts_accelerator": "T4"},
                "tailscale": {"enabled": True, "asr_hostname": "ciel-asr", "tts_hostname": "ciel-tts"},
            }
        }
        self.requests = []
        self.saved = []

    def controller(self, response=None):
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

        return SpeechHttpController(SpeechHttpPorts(lambda: self.config, lambda value: self.saved.append(value), write_json, lambda *_args: None, urlopen))

    def test_public_config_masks_remote_tokens_and_lists_all_audio_endpoints(self):
        public = self.controller().public_config()

        self.assertNotIn("api_key", public["asr"])
        self.assertNotIn("api_key", public["tts"])
        self.assertNotIn("ref_audio", public["tts"])
        self.assertTrue(public["tts"]["ref_audio_set"])
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
            "asr_accelerator": "l4",
            "tts_accelerator": "a100",
        }}).encode()

        self.controller().post(handler, "/ca/speech/config", body, "application/json")

        saved = self.saved[0]["speech"]["colab"]
        self.assertEqual("Ubuntu-24.04", saved["distribution"])
        self.assertEqual("oauth2", saved["auth"])
        self.assertEqual("L4", saved["asr_accelerator"])
        self.assertEqual("A100", saved["tts_accelerator"])
        self.assertEqual(200, handler.status)

    def test_colab_connection_settings_reject_shell_metacharacters(self):
        handler = _Handler()
        body = json.dumps({"colab": {"asr_session": "ciel-asr; reboot"}}).encode()

        self.controller().post(handler, "/ca/speech/config", body, "application/json")

        self.assertEqual([], self.saved)
        self.assertEqual(400, handler.status)


if __name__ == "__main__":
    unittest.main()
