import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.config_value_codec import parse_bool
from ciel_runtime_support.router_access import (
    RouterAccessConfigService,
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
        self.assertTrue(policy.request_allowed(local, config, lambda: ""))
        self.assertEqual("expected", router_request_bearer_token(remote))
        self.assertTrue(policy.request_allowed(remote, config, lambda: "expected"))
        self.assertFalse(policy.request_allowed(remote, config, lambda: "wrong"))

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


if __name__ == "__main__":
    unittest.main()
