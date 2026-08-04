import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import runtime_primitives


class RuntimePrimitiveTests(unittest.TestCase):
    def test_source_fingerprint_is_stable_sha256_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.py"
            path.write_bytes(b"ciel-runtime")
            self.assertEqual(runtime_primitives.source_fingerprint(path), "1521462b344c3e2b")

    def test_positive_environment_int_rejects_non_positive_values(self) -> None:
        self.assertEqual(runtime_primitives.positive_environment_int({"VALUE": "0"}, "VALUE", 12), 12)
        self.assertEqual(runtime_primitives.positive_environment_int({"VALUE": "24"}, "VALUE", 12), 24)

    def test_model_preset_supports_exact_and_prefix_matches(self) -> None:
        presets = {"model:tag": {"exact": True}, "family": {"family": True}}
        self.assertEqual(runtime_primitives.model_preset("model:tag", presets, lambda value: [value]), {"exact": True})
        self.assertEqual(runtime_primitives.model_preset("family-v2", presets, lambda value: [value]), {"family": True})

    def test_join_url_avoids_duplicate_v1(self) -> None:
        self.assertEqual(runtime_primitives.join_url("https://example.test/v1", "/v1/models"), "https://example.test/v1/models")

    def test_url_is_up_converts_request_failure_to_false(self) -> None:
        self.assertTrue(runtime_primitives.url_is_up("https://example.test", lambda *_args, **_kwargs: {}))

        def fail(*_args: object, **_kwargs: object) -> None:
            raise OSError("offline")

        self.assertFalse(runtime_primitives.url_is_up("https://example.test", fail))

    def test_colorize_status_text_can_be_disabled(self) -> None:
        self.assertEqual(
            runtime_primitives.colorize_status_text("wait", enabled=False, palette=(1,), monotonic=lambda: 0.0),
            "wait",
        )


if __name__ == "__main__":
    unittest.main()
