import unittest
from dataclasses import dataclass
from unittest.mock import Mock

from ciel_runtime_support.compatibility_runtime import (
    CompatibilityCachePorts,
    CompatibilityCacheRepository,
    CompatibilityRuntimePorts,
    CompatibilityRuntimeProjection,
)


def positive_int(value):
    try:
        fixed = int(value)
    except (TypeError, ValueError):
        return None
    return fixed if fixed > 0 else None


@dataclass
class Policy:
    exposes_runtime_info: bool = True

    @staticmethod
    def runtime_metadata(info):
        return [f"Runtime root: {info.get('root')}"] if info.get("root") else []


class CompatibilityRuntimeProjectionTests(unittest.TestCase):
    def projection(self, *, exposed=True, info=None):
        return CompatibilityRuntimeProjection(
            CompatibilityRuntimePorts(
                provider_policy=lambda _provider: Policy(exposed),
                runtime_info=lambda _provider, _config, timeout: info,
                positive_int=positive_int,
            )
        )

    def test_vllm_hints_select_model_family_parser(self):
        self.assertIn(
            "qwen3_xml",
            CompatibilityRuntimeProjection.vllm_tool_parser_hint("Qwen3-Coder"),
        )
        self.assertIn(
            "glm47",
            CompatibilityRuntimeProjection.vllm_tool_parser_hint("GLM-4.7"),
        )
        self.assertIsNone(
            CompatibilityRuntimeProjection.vllm_tool_parser_hint("unknown")
        )

    def test_provider_policy_can_hide_runtime_diagnostics(self):
        self.assertEqual([], self.projection(exposed=False).lines("provider", {}, False))

    def test_runtime_projection_reports_metadata_and_context_mismatch(self):
        lines = self.projection(
            info={
                "models_url": "http://runtime/models",
                "runtime_model": "model-a",
                "max_model_len": 65536,
                "root": "qwen",
            }
        ).lines(
            "provider",
            {"context_window": 131072, "max_output_tokens": 65536},
            False,
        )

        self.assertIn("Runtime root: qwen", lines)
        self.assertTrue(any("differs from runtime" in line for line in lines))
        self.assertTrue(any("greater than or equal" in line for line in lines))
        self.assertTrue(lines[-1].startswith("Runtime mode note: router"))

    def test_native_projection_explains_direct_request_limit(self):
        lines = self.projection(info=None).lines("provider", {}, True)
        self.assertTrue(any("native mode sends" in line for line in lines))


class CompatibilityCacheRepositoryTests(unittest.TestCase):
    def test_record_repairs_invalid_cache_and_truncates_diagnostics(self):
        save = Mock()
        repository = CompatibilityCacheRepository(
            CompatibilityCachePorts(save_config=save, timestamp=lambda: 123)
        )
        config = {"compatibility_cache": "invalid"}

        repository.record(
            config, "provider", "model", False, 500, "m" * 600, "d" * 600
        )

        record = config["compatibility_cache"]["provider"]["model"]
        self.assertEqual(500, len(record["message"]))
        self.assertEqual(500, len(record["diagnosis"]))
        self.assertEqual(123, record["tested_at"])
        save.assert_called_once_with(config)


if __name__ == "__main__":
    unittest.main()
