from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.providers.nvidia_runtime import (
    NvidiaProxyRuntime,
    NvidiaProxyRuntimeConfig,
    NvidiaProxyRuntimePorts,
    NvidiaProxyStopper,
    NvidiaProxyStopPorts,
    NvidiaRuntimeApi,
)


class NvidiaProxyRuntimeTests(unittest.TestCase):
    def test_explicit_runtime_api_delegates_public_contract(self):
        runtime = mock.create_autospec(NvidiaProxyRuntime, instance=True)
        runtime.proxy_base_url.return_value = "http://localhost:8788"
        runtime.model_id.return_value = "claude-proxy"
        api = NvidiaRuntimeApi(lambda: runtime)

        self.assertEqual("http://localhost:8788", api.proxy_base_url())
        self.assertEqual("claude-proxy", api.model_id(model_id="vendor/model"))
        runtime.model_id.assert_called_once_with("vendor/model")

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


class NvidiaProxyStopperTests(unittest.TestCase):
    def stopper(self, root: Path, **overrides):
        values = {
            "read_env_file": lambda _path: {"PROXY_PORT": "9000"},
            "positive_int": lambda value: int(value) if value else None,
            "terminate_windows_port": mock.Mock(return_value=True),
            "find_executable": lambda _name: "ncp",
            "terminate_matching_processes": mock.Mock(return_value=False),
            "run": mock.Mock(),
            "log": mock.Mock(),
            "output": mock.Mock(),
        }
        values.update(overrides)
        ports = NvidiaProxyStopPorts(**values)
        return NvidiaProxyStopper(root / ".env", ports), ports

    def test_windows_stop_uses_configured_proxy_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            stopper, ports = self.stopper(Path(tmp))

            self.assertTrue(stopper.stop(platform_name="nt"))

            ports.terminate_windows_port.assert_called_once_with(  # type: ignore[attr-defined]
                9000,
                "Nvidia NCP proxy",
                quiet=True,
            )
            ports.output.assert_called_once()  # type: ignore[attr-defined]

    def test_posix_stop_invokes_ncp_and_sweeps_known_process_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            stopper, ports = self.stopper(Path(tmp))

            self.assertTrue(stopper.stop(platform_name="posix"))

            ports.run.assert_called_once()  # type: ignore[attr-defined]
            self.assertEqual(
                3,
                ports.terminate_matching_processes.call_count,  # type: ignore[attr-defined]
            )

    def test_missing_ncp_uses_legacy_process_fallback(self):
        terminate = mock.Mock(return_value=True)
        with tempfile.TemporaryDirectory() as tmp:
            stopper, _ports = self.stopper(
                Path(tmp),
                find_executable=lambda _name: None,
                terminate_matching_processes=terminate,
            )

            self.assertTrue(stopper.stop(quiet=True, platform_name="posix"))

        terminate.assert_called_once_with(
            ["nvd_claude_proxy"],
            "Nvidia NCP proxy",
            quiet=True,
        )


if __name__ == "__main__":
    unittest.main()
