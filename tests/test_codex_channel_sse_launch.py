import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.codex_channel_sse_launch import (
    CodexChannelSseEffects,
    CodexChannelSseLaunchService,
    CodexChannelSseQueryPorts,
)


class CodexChannelSseLaunchServiceTests(unittest.TestCase):
    def _service(
        self,
        *,
        delivery_mode="llm",
        specs=None,
        capable=None,
        started=None,
        logs=None,
    ):
        specs = specs if specs is not None else []
        capable = capable if capable is not None else []
        started = started if started is not None else []
        logs = logs if logs is not None else []

        def auto_start(*args, **kwargs):
            started.append((args, kwargs))
            return [{"name": "started"}]

        return CodexChannelSseLaunchService(
            query=CodexChannelSseQueryPorts(
                delivery_mode=lambda _config: delivery_mode,
                channel_specs=lambda _config, _passthrough: specs,
                server_names=lambda values: [
                    value.split(":", 1)[-1] for value in values
                ],
                capable_names=lambda _config, _path: capable,
                dedupe=lambda values: list(dict.fromkeys(values)),
            ),
            effects=CodexChannelSseEffects(
                auto_start=auto_start,
                log=lambda level, message: logs.append((level, message)),
            ),
            native_channel_names=frozenset({"ciel-runtime-router"}),
        )

    def test_non_llm_delivery_does_not_start(self):
        started = []
        service = self._service(delivery_mode="native", started=started)

        self.assertEqual([], service.start({}, Path("missing.json")))
        self.assertEqual([], started)

    def test_capable_server_owned_by_explicit_channel_is_excluded(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            config = Path(raw_dir) / "codex.json"
            config.write_text("{}", encoding="utf-8")
            started = []
            service = self._service(
                specs=["server:owned"],
                capable=["owned", "available"],
                started=started,
            )

            result = service.start({}, config)

        self.assertEqual([{"name": "started"}], result)
        self.assertEqual(["available"], started[0][1]["allowed_server_names"])
        self.assertEqual([config], started[0][1]["extra_config_paths"])
        self.assertFalse(started[0][1]["include_default_paths"])

    def test_allowed_names_are_deduped_and_native_bridge_is_removed(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            config = Path(raw_dir) / "codex.json"
            config.write_text("{}", encoding="utf-8")
            started = []
            service = self._service(started=started)

            service.start(
                {},
                config,
                ["available", "ciel-runtime-router", "available"],
            )

        self.assertEqual(["available"], started[0][1]["allowed_server_names"])

    def test_empty_unowned_set_logs_skip_reason(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            config = Path(raw_dir) / "codex.json"
            config.write_text("{}", encoding="utf-8")
            logs = []
            service = self._service(
                specs=["server:owned"],
                capable=["owned"],
                logs=logs,
            )

            self.assertEqual([], service.start({}, config))

        self.assertIn("codex_channel_sse_skipped", logs[-1][1])
        self.assertIn("explicit=owned", logs[-1][1])


if __name__ == "__main__":
    unittest.main()
