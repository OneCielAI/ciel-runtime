import unittest

from ciel_runtime_support.ollama_wire_projection import (
    OllamaWireProjection,
    OllamaWireProjectionPorts,
)


class OllamaWireProjectionTests(unittest.TestCase):
    def policy(self, think=None):
        return OllamaWireProjection(
            OllamaWireProjectionPorts(
                think_value=lambda *_args: think,
                positive_int=lambda value: int(value) if value else None,
            )
        )

    def test_adapter_defaults_are_absent_from_wire(self):
        config = {
            "num_ctx": "auto",
            "keep_alive": "5m",
            "think": False,
            "ollama_options": {
                "temperature": 0.7,
                "top_p": None,
                "num_predict": 4096,
            },
        }

        projected = self.policy().apply(
            {"model": "plain", "messages": [], "stream": True},
            "ollama",
            "plain",
            config,
        )

        self.assertEqual(
            {"model": "plain", "messages": [], "stream": True}, projected
        )

    def test_explicit_non_null_overrides_are_projected(self):
        config = {
            "num_ctx": 32768,
            "keep_alive": "5m",
            "keep_alive_explicit": True,
            "max_output_tokens": 8192,
            "output_tokens_explicit": True,
            "ollama_options": {
                "temperature": 0.2,
                "top_p": None,
                "num_predict": 8192,
            },
            "ollama_explicit_options": ["temperature", "top_p", "num_predict"],
        }

        projected = self.policy(True).apply(
            {"model": "thinking", "messages": [], "stream": False},
            "ollama",
            "thinking",
            config,
            output_limit=4096,
        )

        self.assertTrue(projected["think"])
        self.assertEqual("5m", projected["keep_alive"])
        self.assertEqual(
            {"temperature": 0.2, "num_predict": 4096, "num_ctx": 32768},
            projected["options"],
        )

    def test_legacy_custom_keep_alive_is_preserved(self):
        projected = self.policy().apply(
            {}, "ollama", "model", {"keep_alive": "10m"}
        )
        self.assertEqual("10m", projected["keep_alive"])

    def test_null_values_are_not_projected(self):
        config = {
            "ollama_options": {"temperature": None},
            "ollama_explicit_options": ["temperature"],
        }
        self.assertEqual({}, self.policy().apply({}, "ollama", "model", config))


if __name__ == "__main__":
    unittest.main()
