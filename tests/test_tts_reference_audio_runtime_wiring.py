from contextlib import nullcontext
from dataclasses import fields
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support.speech_http_controller import SpeechHttpPorts


class TtsReferenceAudioRuntimeWiringTests(unittest.TestCase):
    def test_production_repository_uses_config_dir_and_shared_router_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = mock.Mock()
            policy.admit_transformed.return_value = nullcontext()
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(directory)),
                mock.patch.object(ciel_runtime, "_ROUTER_REQUEST_BODY_POLICY", policy),
            ):
                repository = ciel_runtime.tts_reference_audio_repository()
                controller = ciel_runtime.speech_http_controller()
                with repository.admit_transformed(
                    "/v1/audio/speech",
                    10,
                    100,
                    "application/json",
                ):
                    pass

            self.assertEqual(
                Path(directory) / "tts-reference-audio",
                repository.root,
            )
            self.assertEqual(repository.root, controller.ports.reference_audio_repository.root)
            policy.admit_transformed.assert_called_once_with(
                "/v1/audio/speech",
                10,
                100,
                "application/json",
            )

    def test_speech_ports_remain_within_architecture_field_budget(self):
        self.assertEqual(10, len(fields(SpeechHttpPorts)))


if __name__ == "__main__":
    unittest.main()
