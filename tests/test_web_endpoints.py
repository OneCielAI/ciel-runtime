import json
import unittest
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.web_endpoints import (
    TailscaleNode,
    apply_startup_web_options,
    build_web_endpoint_report,
    configure_tailscale_https,
    discover_tailscale_node,
    tailscale_https_url_for_target,
    update_web_backend_config,
    web_backend_panel_rows,
    web_backend_settings,
)


class WebEndpointTests(unittest.TestCase):
    def test_startup_options_set_host_port_and_strip_ciel_flags(self):
        environment = {}
        argv = apply_startup_web_options(
            [
                "runtime.py",
                "cli",
                "--ca-web-address=http://0.0.0.0:9123",
                "--ca-web-port",
                "9234",
                "--ca-tailscale-https=9443",
                "--",
                "--ca-web-port",
                "9999",
            ],
            environment,
        )

        self.assertEqual(
            ["runtime.py", "cli", "--", "--ca-web-port", "9999"], argv
        )
        self.assertEqual("0.0.0.0", environment["CIEL_RUNTIME_ROUTER_BIND_HOST"])
        self.assertEqual("127.0.0.1", environment["CIEL_RUNTIME_ROUTER_CLIENT_HOST"])
        self.assertEqual("9234", environment["CIEL_RUNTIME_ROUTER_PORT"])
        self.assertEqual("1", environment["CIEL_RUNTIME_ROUTER_DEBUG_EXTERNAL"])
        self.assertEqual("1", environment["CIEL_RUNTIME_TAILSCALE_HTTPS"])
        self.assertEqual("9443", environment["CIEL_RUNTIME_TAILSCALE_HTTPS_PORT"])

    def test_invalid_port_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "between 1 and 65535"):
            apply_startup_web_options(
                ["runtime.py", "--ca-web-port", "70000"], {}
            )

    def test_persisted_menu_settings_validate_and_enable_external_guard(self):
        config = {}

        lines = update_web_backend_config(
            config, "host", "http://0.0.0.0:9234", 9464
        )
        update_web_backend_config(config, "tailscale", True, 9464)

        settings = web_backend_settings(config)
        self.assertEqual("0.0.0.0", settings.host)
        self.assertEqual(9234, settings.port)
        self.assertTrue(settings.tailscale_https)
        self.assertTrue(config["router_debug_external_access"])
        self.assertTrue(config["router_debug_external_access_confirmed"])
        self.assertIn("Web backend: 0.0.0.0:9234.", lines)

    @mock.patch("ciel_runtime_support.web_endpoints.discover_tailscale_node")
    def test_menu_panel_shows_detected_tailscale_https_address(self, discover):
        discover.return_value = TailscaleNode(
            "100.64.1.2", "host.example.ts.net"
        )

        rows, values = web_backend_panel_rows(
            {
                "web_backend": {
                    "host": "127.0.0.1",
                    "port": 9234,
                    "tailscale_https": True,
                }
            },
            9464,
        )

        self.assertEqual(["host", "port", "tailscale", "back"], values)
        self.assertIn("host.example.ts.net:9234", rows[2])

    @mock.patch("ciel_runtime_support.web_endpoints.shutil.which", return_value="tailscale")
    @mock.patch("ciel_runtime_support.web_endpoints.subprocess.run")
    def test_discovers_current_tailscale_node(self, run, _which):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "BackendState": "Running",
                    "Self": {
                        "Online": True,
                        "DNSName": "host.example.ts.net.",
                        "TailscaleIPs": ["100.64.1.2", "fd7a:115c:a1e0::1"],
                    },
                }
            ),
        )

        self.assertEqual(
            TailscaleNode("100.64.1.2", "host.example.ts.net"),
            discover_tailscale_node(),
        )

    @mock.patch("ciel_runtime_support.web_endpoints.shutil.which", return_value="tailscale")
    @mock.patch("ciel_runtime_support.web_endpoints.subprocess.run")
    def test_finds_existing_https_serve_target(self, run, _which):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "TCP": {"9123": {"HTTPS": True}},
                    "Web": {
                        "host.example.ts.net:9123": {
                            "Handlers": {
                                "/": {"Proxy": "http://127.0.0.1:9234"}
                            }
                        }
                    },
                }
            ),
        )

        self.assertEqual(
            "https://host.example.ts.net:9123",
            tailscale_https_url_for_target(
                TailscaleNode("100.64.1.2", "host.example.ts.net"), 9234
            ),
        )

    @mock.patch("ciel_runtime_support.web_endpoints.discover_tailscale_node")
    @mock.patch("ciel_runtime_support.web_endpoints.tailscale_https_url_for_target")
    def test_report_recommends_https_and_exposes_bound_tailscale_ip(
        self, https_url, discover
    ):
        discover.return_value = TailscaleNode(
            "100.64.1.2", "host.example.ts.net"
        )
        https_url.return_value = ""

        report = build_web_endpoint_report("127.0.0.1", "0.0.0.0", 9234)

        self.assertEqual("http://127.0.0.1:9234/", report.local_url)
        self.assertEqual("http://100.64.1.2:9234/", report.tailscale_ip_url)
        self.assertIn("--ca-tailscale-https=9234", report.tailscale_https_command)

    @mock.patch("ciel_runtime_support.web_endpoints.shutil.which", return_value="tailscale")
    @mock.patch("ciel_runtime_support.web_endpoints.discover_tailscale_node")
    @mock.patch("ciel_runtime_support.web_endpoints.subprocess.run")
    def test_explicit_https_option_uses_separate_port(self, run, discover, _which):
        discover.return_value = TailscaleNode(
            "100.64.1.2", "host.example.ts.net"
        )
        run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

        lines = configure_tailscale_https(9234, 9443)

        self.assertIn("https://host.example.ts.net:9443/", lines[0])
        self.assertEqual(
            [
                "tailscale",
                "serve",
                "--https=9443",
                "--bg",
                "--yes",
                "http://127.0.0.1:9234",
            ],
            run.call_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
