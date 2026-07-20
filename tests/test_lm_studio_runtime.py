import unittest
from unittest import mock

from ciel_runtime_support.lm_studio_runtime import (
    LmStudioLifecycleApi,
    LmStudioModelLifecycle,
    LmStudioRuntimeServices,
    discover_lm_studio_runtime,
)


class LmStudioRuntimeTests(unittest.TestCase):
    def test_explicit_lifecycle_api_preserves_public_contract(self):
        lifecycle = mock.create_autospec(LmStudioModelLifecycle, instance=True)
        lifecycle.target_context.return_value = 65536
        lifecycle.context_guard.return_value = ["ready"]
        api = LmStudioLifecycleApi(lambda: lifecycle)

        self.assertEqual(
            65536, api.target_context(pcfg={"current_model": "model"}, info=None)
        )
        self.assertEqual(
            ["ready"],
            api.apply_loaded_context_guard(
                pcfg={"current_model": "model"}, load=True
            ),
        )
        lifecycle.context_guard.assert_called_once_with(
            {"current_model": "model"}, load=True
        )

    def services(self, http_json, log=None, current="model-a"):
        return LmStudioRuntimeServices(
            api_base=lambda _config: "http://lmstudio.test/v1",
            current_model=lambda _provider, _config: current,
            http_json=http_json,
            join_url=lambda base, path: base.removesuffix("/v1") + path,
            model_list_headers=lambda _provider, _config: {"X-Test": "1"},
            model_id_matches=lambda candidate, requested: candidate == requested,
            positive_int=lambda value: int(value) if value else None,
            model_context=lambda item: item.get("fallback_context"),
            log=log or mock.Mock(),
        )

    def test_v1_fallback_projects_loaded_instance_context(self):
        http_json = mock.Mock(
            side_effect=[
                OSError("v0 unavailable"),
                {
                    "models": [
                        {
                            "key": "model-a",
                            "max_context_length": 131072,
                            "loaded_instances": [
                                {"id": "instance-1", "config": {"context_length": 8192}}
                            ],
                            "architecture": "qwen",
                        }
                    ]
                },
            ]
        )
        log = mock.Mock()

        info = discover_lm_studio_runtime({}, self.services(http_json, log))

        self.assertEqual(8192, info["loaded_context_len"])
        self.assertEqual(["instance-1"], info["instance_ids"])
        self.assertEqual("loaded", info["state"])
        self.assertIn("api=v0", log.call_args.args[1])

    def test_both_api_failures_are_observable(self):
        log = mock.Mock()
        info = discover_lm_studio_runtime(
            {},
            self.services(mock.Mock(side_effect=OSError("offline")), log),
        )

        self.assertIsNone(info)
        self.assertEqual(2, log.call_count)
        self.assertIn("api=v1", log.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
