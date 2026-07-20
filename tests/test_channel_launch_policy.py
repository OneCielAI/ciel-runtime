import subprocess
import unittest
from unittest import mock

from ciel_runtime_support.channel_launch_policy import (
    ChannelLaunchPolicy,
    ChannelLaunchPorts,
)


class ChannelLaunchPolicyTests(unittest.TestCase):
    def policy(self, *, delivery="llm", auth_process=None):
        process = auth_process or subprocess.CompletedProcess(
            [],
            0,
            stdout='{"loggedIn":true,"authMethod":"oauth"}',
            stderr="",
        )
        return ChannelLaunchPolicy(
            native_router_names=frozenset({"ai-net"}),
            ports=ChannelLaunchPorts(
                has_option=lambda args, *names: any(
                    arg in names for arg in args
                ),
                channel_specs=lambda _c, _p, _e=None: [
                    "server:ai-net",
                    "server:external",
                ],
                delivery_mode=lambda _c=None: delivery,
                run_auth_status=mock.Mock(return_value=process),
            ),
        )

    def test_native_bridge_and_llm_delivery_are_mutually_scoped(self):
        native = self.policy(delivery="native")
        llm = self.policy(delivery="llm")

        self.assertTrue(native.native_bridge(False, {}, []))
        self.assertFalse(native.native_bridge(True, {}, []))
        self.assertTrue(llm.llm_delivery(True, []))
        self.assertFalse(llm.llm_delivery(False, []))

    def test_claude_args_filter_builtin_router_channel(self):
        policy = self.policy()

        args = policy.claude_args({}, [], native_channel_bridge=True)

        self.assertEqual(
            [
                "--dangerously-load-development-channels",
                "server:external",
            ],
            args,
        )

    def test_stdin_proxy_respects_print_and_web_bridge_settings(self):
        policy = self.policy()

        self.assertTrue(policy.stdin_proxy(True, [], {}))
        self.assertFalse(policy.stdin_proxy(True, ["--print"], {}))
        self.assertFalse(
            policy.stdin_proxy(
                True,
                [],
                {"claude_code": {"web_chat_session_bridge": False}},
            )
        )

    def test_external_server_and_process_sse_decisions(self):
        policy = self.policy()

        self.assertFalse(policy.specs_include_external_server(["server:ai-net"]))
        self.assertTrue(
            policy.specs_include_external_server(["server:external"])
        )
        self.assertTrue(policy.process_starts_sse(True, False, False))
        self.assertFalse(policy.process_starts_sse(True, False, True))

    def test_claude_auth_status_is_projected(self):
        policy = self.policy()

        self.assertEqual((True, "oauth"), policy.claude_auth_available("claude"))

        logged_out = self.policy(
            auth_process=subprocess.CompletedProcess(
                [],
                0,
                stdout='{"loggedIn":false}',
                stderr="",
            )
        )
        self.assertEqual(
            (False, "not_logged_in"),
            logged_out.claude_auth_available("claude"),
        )


if __name__ == "__main__":
    unittest.main()
