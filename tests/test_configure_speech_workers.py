import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "configure_speech_workers.py"
SPEC = importlib.util.spec_from_file_location("configure_speech_workers", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ConfigureSpeechWorkersTests(unittest.TestCase):
    def configure(self, config, backend, asr_model=None):
        saved = []
        runtime = types.SimpleNamespace(load_config=lambda: config, save_config=lambda value: saved.append(value))
        with mock.patch.dict(sys.modules, {"ciel_runtime": runtime}):
            result = MODULE.configure(
                "http://ciel-asr/",
                "http://ciel-tts/",
                tts_backend=backend,
                asr_model=asr_model,
            )
        return result, saved[0]

    def test_cosyvoice_selection_sets_streaming_model_and_official_reference(self):
        result, config = self.configure({"speech": {"tts": {}}}, "cosyvoice3")

        tts = config["speech"]["tts"]
        self.assertEqual("FunAudioLLM/Fun-CosyVoice3-0.5B-2512", tts["model"])
        self.assertEqual(24000, tts["sample_rate"])
        self.assertTrue(tts["streaming"])
        self.assertEqual(MODULE.DEFAULT_COSYVOICE_REFERENCE_AUDIO, tts["ref_audio"])
        self.assertEqual(MODULE.DEFAULT_COSYVOICE_REFERENCE_TEXT, tts["ref_text"])
        self.assertEqual("cosyvoice3", result["colab"]["tts_backend"])

    def test_switching_backend_preserves_custom_reference_voice(self):
        config = {"speech": {"tts": {"ref_audio": "data:audio/wav;base64,custom", "ref_text": "custom words"}}}

        _result, saved = self.configure(config, "cosyvoice3")

        self.assertEqual("data:audio/wav;base64,custom", saved["speech"]["tts"]["ref_audio"])
        self.assertEqual("custom words", saved["speech"]["tts"]["ref_text"])

    def test_moss_selection_keeps_non_streaming_48khz_defaults(self):
        _result, config = self.configure({"speech": {"tts": {}}}, "moss")

        tts = config["speech"]["tts"]
        self.assertEqual("OpenMOSS-Team/MOSS-TTS-Nano", tts["model"])
        self.assertEqual(48000, tts["sample_rate"])
        self.assertFalse(tts["streaming"])

    def test_qwen_asr_model_selection_is_persisted_for_deployment(self):
        result, config = self.configure({"speech": {"tts": {}}}, "moss", "Qwen/Qwen3-ASR-1.7B")

        self.assertEqual("Qwen/Qwen3-ASR-1.7B", config["speech"]["asr"]["model"])
        self.assertEqual("Qwen/Qwen3-ASR-1.7B", result["colab"]["asr_model"])


if __name__ == "__main__":
    unittest.main()
