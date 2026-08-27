import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.config_value_codec import parse_bool
from ciel_runtime_support.router_access import (
    RouterAccessConfigService,
    RouterAccessHttpController,
    RouterAccessMutationPorts,
    RouterAccessPolicy,
    RouterExternalTokenRepository,
    is_loopback_address,
    router_request_bearer_token,
)


def parse_env_bool(value, default=None):
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


class RouterAccessTests(unittest.TestCase):
    def policy(self, environment=None, config=None):
        return RouterAccessPolicy(
            environ=environment or {},
            parse_bool=parse_bool,
            parse_env_bool=parse_env_bool,
            load_config=lambda: config or {},
        )

    def test_policy_requires_explicit_confirmation_for_external_bind(self):
        policy = self.policy(
            config={
                "router_debug_external_access": True,
                "router_debug_external_access_confirmed": True,
            }
        )
        self.assertTrue(policy.external_access_enabled())
        self.assertEqual("0.0.0.0", policy.bind_host())
        self.assertFalse(
            self.policy(config={"router_debug_external_access": True}).external_access_enabled()
        )

    def test_environment_bind_override_and_invalid_debug_fallback(self):
        policy = self.policy(
            environment={
                "CIEL_RUNTIME_ROUTER_BIND_HOST": "192.0.2.8",
                "CIEL_RUNTIME_ROUTER_DEBUG_EXTERNAL": "invalid",
            },
            config={
                "router_debug_external_access": True,
                "router_debug_external_access_confirmed": True,
            },
        )
        self.assertTrue(policy.external_access_enabled())
        self.assertEqual("192.0.2.8", policy.bind_host())

    def test_saved_web_backend_host_is_used_for_server_bind(self):
        config = {
            "web_backend": {"host": "100.64.1.2"},
            "router_debug_external_access": True,
            "router_debug_external_access_confirmed": True,
        }
        self.assertEqual("100.64.1.2", self.policy(config=config).bind_host())

    def test_remote_bridge_enables_external_auth_and_uses_bridge_host(self):
        config = {
            "remote_bridge": {"enabled": True, "host": "100.64.1.9"},
        }
        policy = self.policy(config=config)
        self.assertTrue(policy.external_access_enabled())
        self.assertEqual("100.64.1.9", policy.bind_host())

    def test_bridge_environment_off_does_not_disable_existing_web_access(self):
        config = {
            "remote_bridge": {"enabled": True, "host": "100.64.1.9"},
            "web_backend": {"enabled": True, "host": "100.64.1.10"},
        }
        policy = self.policy(
            environment={"CIEL_RUNTIME_REMOTE_BRIDGE": "0"},
            config=config,
        )

        self.assertTrue(policy.external_access_enabled())
        self.assertEqual("100.64.1.10", policy.bind_host())

    def test_invalid_bridge_environment_override_fails_closed(self):
        config = {
            "remote_bridge": {"enabled": True, "host": "100.64.1.9"},
        }
        policy = self.policy(
            environment={"CIEL_RUNTIME_REMOTE_BRIDGE": "invalid"},
            config=config,
        )

        self.assertFalse(policy.remote_bridge_enabled(config))
        self.assertFalse(policy.external_access_enabled())
        self.assertEqual("127.0.0.1", policy.bind_host())

    def test_request_auth_allows_loopback_and_compares_external_token(self):
        config = {
            "router_debug_external_access": True,
            "router_debug_external_access_confirmed": True,
        }
        policy = self.policy(config=config)
        local = SimpleNamespace(client_address=("127.0.0.2", 1), headers={})
        remote = SimpleNamespace(
            client_address=("192.0.2.9", 1),
            headers={"Authorization": "Bearer expected"},
        )
        self.assertTrue(is_loopback_address("localhost"))
        self.assertTrue(policy.request_allowed(local, config, lambda: "", lambda: ""))
        self.assertEqual("expected", router_request_bearer_token(remote))
        self.assertTrue(
            policy.request_allowed(remote, config, lambda: "expected", lambda: "bridge")
        )
        self.assertFalse(
            policy.request_allowed(remote, config, lambda: "wrong", lambda: "bridge")
        )

    def test_bridge_request_context_preserves_local_routing_without_bridge_token(self):
        config = {"remote_bridge": {"enabled": True, "host": "0.0.0.0"}}
        policy = self.policy(config=config)
        local = SimpleNamespace(client_address=("127.0.0.1", 1), headers={})
        proxied = SimpleNamespace(
            client_address=("127.0.0.1", 1),
            headers={"Authorization": "Bearer bridge-token"},
        )
        remote = SimpleNamespace(client_address=("192.0.2.9", 1), headers={})

        self.assertFalse(
            policy.remote_bridge_request(local, config, lambda: "bridge-token")
        )
        self.assertTrue(
            policy.remote_bridge_request(proxied, config, lambda: "bridge-token")
        )
        self.assertTrue(
            policy.remote_bridge_request(remote, config, lambda: "bridge-token")
        )

    def test_bridge_only_token_is_scoped_to_bridge_paths(self):
        config = {"remote_bridge": {"enabled": True, "host": "0.0.0.0"}}
        policy = self.policy(config=config)
        remote = SimpleNamespace(
            client_address=("192.0.2.9", 1),
            path="/v1/messages",
            headers={"x-api-key": "bridge-token"},
        )

        self.assertEqual("bridge-token", router_request_bearer_token(remote))
        self.assertTrue(
            policy.request_allowed(
                remote,
                config,
                lambda: "admin-token",
                lambda: "bridge-token",
            )
        )
        remote.path = "/ca/config/llm"
        self.assertFalse(
            policy.request_allowed(
                remote,
                config,
                lambda: "admin-token",
                lambda: "bridge-token",
            )
        )
        remote.path = "/health"
        self.assertFalse(
            policy.request_allowed(
                remote,
                config,
                lambda: "admin-token",
                lambda: "bridge-token",
            )
        )

    def test_explicit_debug_external_access_retains_admin_paths(self):
        config = {
            "remote_bridge": {"enabled": True, "host": "0.0.0.0"},
            "router_debug_external_access": True,
            "router_debug_external_access_confirmed": True,
        }
        remote = SimpleNamespace(
            client_address=("192.0.2.9", 1),
            path="/ca/config/llm",
            headers={"Authorization": "Bearer admin-token"},
        )

        self.assertTrue(
            self.policy(config=config).request_allowed(
                remote,
                config,
                lambda: "admin-token",
                lambda: "bridge-token",
            )
        )
        remote.headers = {"Authorization": "Bearer bridge-token"}
        self.assertFalse(
            self.policy(config=config).request_allowed(
                remote,
                config,
                lambda: "admin-token",
                lambda: "bridge-token",
            )
        )
        remote.path = "/v1/messages"
        self.assertTrue(
            self.policy(config=config).request_allowed(
                remote,
                config,
                lambda: "admin-token",
                lambda: "bridge-token",
            )
        )

    def test_equal_bridge_and_admin_tokens_fail_closed_for_admin_paths(self):
        config = {
            "remote_bridge": {"enabled": True, "host": "0.0.0.0"},
            "router_debug_external_access": True,
            "router_debug_external_access_confirmed": True,
        }
        remote = SimpleNamespace(
            client_address=("192.0.2.9", 1),
            path="/ca/config/llm",
            headers={"Authorization": "Bearer duplicated-token"},
        )

        self.assertFalse(
            self.policy(config=config).request_allowed(
                remote,
                config,
                lambda: "duplicated-token",
                lambda: "duplicated-token",
            )
        )

    def test_token_repository_prefers_environment_and_persists_generated_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = RouterExternalTokenRepository(
                path=root / "router.token",
                config_dir=root,
                environ={"CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN": "from-env"},
            )
            self.assertEqual("from-env", repository.ensure())

            repository = RouterExternalTokenRepository(
                path=root / "router.token", config_dir=root, environ={}
            )
            token = repository.ensure()
            self.assertTrue(token)
            self.assertEqual(token, repository.get())

    def test_token_repository_creates_nested_token_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = RouterExternalTokenRepository(
                path=root / "router-instances" / "instance" / "router.token",
                config_dir=root,
                environ={},
            )

            token = repository.ensure()

            self.assertTrue(token)
            self.assertEqual(token, repository.get())

    def test_token_repository_supports_distinct_bridge_environment_variable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = RouterExternalTokenRepository(
                path=root / "bridge.token",
                config_dir=root,
                environ={
                    "CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN": "admin-token",
                    "CIEL_RUNTIME_REMOTE_BRIDGE_TOKEN": "bridge-token",
                },
                env_name="CIEL_RUNTIME_REMOTE_BRIDGE_TOKEN",
            )

            self.assertEqual("bridge-token", repository.get())

    def test_config_service_persists_both_guard_flags(self):
        config = {}
        saved = []
        cache_clears = []
        service = RouterAccessConfigService(
            policy=self.policy(config=config),
            ports=RouterAccessMutationPorts(
                load_config=lambda: config,
                save_config=lambda value: saved.append(dict(value)),
                clear_model_cache=lambda: cache_clears.append(True),
                ensure_token=lambda: "token",
            ),
        )
        lines = service.set_external_access(True)
        self.assertTrue(config["router_debug_external_access"])
        self.assertTrue(config["router_debug_external_access_confirmed"])
        self.assertEqual(1, len(saved))
        self.assertEqual([True], cache_clears)
        self.assertIn("External access token: token", lines)

    def test_http_controller_writes_authenticated_external_rejection(self):
        handler = SimpleNamespace(
            client_address=("192.0.2.9", 1),
            headers={},
            wfile=BytesIO(),
            send_response=lambda status: responses.append(status),
            send_header=lambda name, value: headers.append((name, value)),
            end_headers=lambda: None,
        )
        responses: list[int] = []
        headers: list[tuple[str, str]] = []
        controller = RouterAccessHttpController(
            request_allowed=lambda _handler, _config: False,
            external_access_enabled=lambda _config: True,
        )

        self.assertTrue(controller.reject_external_request(handler, {}))

        self.assertEqual([401], responses)
        self.assertIn(
            ("www-authenticate", 'Bearer realm="ciel-runtime"'),
            headers,
        )
        self.assertIn(b'"unauthorized"', handler.wfile.getvalue())


if __name__ == "__main__":
    unittest.main()
