import unittest
from unittest import mock

from ciel_runtime_support.architecture import ProviderRequestPolicy
from ciel_runtime_support.managed_service_cleanup import (
    ManagedServiceCleanupPolicy,
    ManagedServiceCleanupPorts,
)


class ManagedServiceCleanupPolicyTests(unittest.TestCase):
    def policy(
        self,
        *,
        anthropic=False,
        codex=False,
        agy=False,
        managed_service="none",
        native_compat=False,
    ):
        self.stop_router = mock.Mock(return_value=True)
        self.stop_proxy = mock.Mock(return_value=True)
        request_policy = ProviderRequestPolicy(
            chat_path="/chat",
            models_path="/models",
            managed_service=managed_service,
        )
        return ManagedServiceCleanupPolicy(
            ManagedServiceCleanupPorts(
                direct_native_anthropic=mock.Mock(return_value=anthropic),
                direct_native_codex=mock.Mock(return_value=codex),
                direct_native_agy=mock.Mock(return_value=agy),
                request_policy=mock.Mock(return_value=request_policy),
                native_compat_enabled=mock.Mock(return_value=native_compat),
                stop_idle_router=self.stop_router,
                stop_nvidia_proxy=self.stop_proxy,
            )
        )

    def test_keeps_required_provider_managed_service(self):
        policy = self.policy(managed_service="nvidia_proxy")

        policy.cleanup("provider", {}, {}, quiet=True)

        self.stop_proxy.assert_not_called()

    def test_native_compat_releases_provider_managed_service(self):
        policy = self.policy(
            managed_service="nvidia_proxy",
            native_compat=True,
        )

        policy.cleanup("provider", {}, {}, quiet=True)

        self.stop_proxy.assert_called_once_with(quiet=True)

    def test_native_runtime_stops_idle_router_and_proxy(self):
        for mode, reason in (
            ("anthropic", "native_anthropic_launch"),
            ("codex", "native_codex_launch"),
            ("agy", "native_agy_launch"),
        ):
            with self.subTest(mode=mode):
                policy = self.policy(**{mode: True})
                policy.cleanup("provider", {}, {}, quiet=True)
                self.stop_router.assert_called_once_with(reason, quiet=True)
                self.stop_proxy.assert_called_once_with(quiet=True)

    def test_disabled_launch_cleanup_preserves_services(self):
        policy = self.policy()

        policy.cleanup(
            "provider",
            {},
            {"cleanup": {"managed_services_on_launch": False}},
        )

        self.stop_router.assert_not_called()
        self.stop_proxy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
