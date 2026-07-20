from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.providers.nvidia_runtime import (
    NvidiaProxyRuntime,
    NvidiaProxyRuntimeConfig,
    NvidiaProxyRuntimePorts,
)


class NvidiaProxyRuntimeTests(unittest.TestCase):
    def runtime(self, root: Path, **overrides):
        values = {
            "load_config": lambda: {"providers": {"nvidia-hosted": {}}},
            "read_env_file": lambda _path: {"PROXY_HOST": "localhost", "PROXY_PORT": "9000"},
            "is_url_up": lambda _url: True,
            "find_executable": lambda _name: None,
            "positive_int": lambda value: int(value) if value else None,
            "http_json": lambda *_args, **_kwargs: {},
            "join_url": lambda base, path: base.rstrip("/") + "/" + path.lstrip("/"),
        }
        values.update(overrides)
        return NvidiaProxyRuntime(
            NvidiaProxyRuntimeConfig(root / ".env", root / "ncp.log", "nvd-claude-proxy"),
            NvidiaProxyRuntimePorts(**values),
        )

    def test_proxy_environment_and_api_key_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self.runtime(Path(tmp))
            with mock.patch.dict("os.environ", {"NVIDIA_API_KEY": "secret"}, clear=True):
                self.assertEqual("http://localhost:9000", runtime.proxy_base_url())
                self.assertEqual("secret", runtime.api_key())

    def test_model_id_maps_upstream_catalog_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self.runtime(
                Path(tmp),
                http_json=lambda *_args, **_kwargs: {
                    "data": [{"id": "claude-proxy", "nvidia_id": "vendor/model"}]
                },
            )
            self.assertEqual("claude-proxy", runtime.model_id("vendor/model"))
            self.assertEqual("claude-native", runtime.model_id("claude-native"))

    def test_ensure_reuses_ready_proxy_without_spawning(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self.runtime(Path(tmp), is_url_up=mock.Mock(return_value=True))
            with mock.patch("ciel_runtime_support.providers.nvidia_runtime.subprocess.Popen") as popen:
                runtime.ensure()
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
