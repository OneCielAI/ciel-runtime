"""A `/api-key KEY` turn never reaches traces, previews, observers or upstream.

The slash command puts the raw key in the user turn. The router used to write
that turn to the TRACE request log before the shortcut handled it, and later
requests replayed it in history. Every check uses a random synthetic canary and
never prints it.
"""

import io
import json
import secrets
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import claude_router as claude
from ciel_runtime_support.request_trace import (
    RequestTracePolicy,
    RequestTraceProjection,
    RequestTraceServices,
    RouterMessagePreviewPolicy,
    dump_request_for_trace,
)
from ciel_runtime_support.slash_command_assets import API_KEYS_SLASH_COMMAND
from ciel_runtime_support.tui_observation import TuiObservationBus, publish_latest_input


def _canary() -> str:
    return "sk-ciel-canary-" + secrets.token_hex(20)


def _command_turn(key: str) -> str:
    """The text Claude Code sends for `/api-key KEY` (the installed template)."""

    body = API_KEYS_SLASH_COMMAND.split("---", 2)[2]
    return body.replace("$0", key).replace("$ARGUMENTS", key)


def _latest_user_text(body):
    messages = body.get("messages") or []
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return "\n".join(
            str(block.get("text") or "") for block in content or [] if isinstance(block, dict)
        )
    return ""


def _ports(kind, **overrides):
    return kind(**{field.name: overrides.get(field.name, mock.Mock()) for field in fields(kind)})


class TraceAndPreviewTests(unittest.TestCase):
    def services(self, root: Path) -> RequestTraceServices:
        return RequestTraceServices(
            policy=RequestTracePolicy(
                enabled=lambda: True,
                request_path=root / "requests.jsonl",
                response_path=root / "responses.jsonl",
                request_max_bytes=1_000_000,
                response_max_bytes=1_000_000,
                response_text_limit=100,
            ),
            projection=RequestTraceProjection(
                content_to_text=lambda value: str(value),
                thinking_block_count=lambda _body: 0,
                tool_continuation_block_count=lambda _body: 0,
            ),
            log=mock.Mock(),
        )

    def assert_absent(self, key: str, data: bytes, where: str) -> None:
        # The key is never part of the failure message.
        self.assertFalse(key.encode() in data, f"synthetic key present in {where}")

    def test_trace_of_the_command_turn_omits_the_key(self):
        key = _canary()
        with tempfile.TemporaryDirectory() as tmp:
            services = self.services(Path(tmp))
            body = {"model": "m", "messages": [{"role": "user", "content": _command_turn(key)}]}
            dump_request_for_trace("provider", "/v1/messages", body, services)
            data = services.policy.request_path.read_bytes()

        self.assert_absent(key, data, "the request trace")
        record = json.loads(data)
        self.assertEqual(1, record["messages_count"])  # the turn is still accounted for

    def test_trace_of_a_later_turn_omits_the_key_in_history(self):
        key = _canary()
        with tempfile.TemporaryDirectory() as tmp:
            services = self.services(Path(tmp))
            body = {"model": "m", "messages": [
                {"role": "user", "content": [{"type": "text", "text": _command_turn(key)}]},
                {"role": "assistant", "content": "Updated live API key settings."},
                {"role": "user", "content": "hello"},
            ]}
            dump_request_for_trace("provider", "/v1/messages", body, services)
            data = services.policy.request_path.read_bytes()

        self.assert_absent(key, data, "the request trace history")
        self.assertIn(b"hello", data)

    def test_event_preview_omits_the_key(self):
        key = _canary()
        policy = RouterMessagePreviewPolicy(
            environ={"CIEL_RUNTIME_ROUTER_MESSAGE_PREVIEW_CHARS": "4000"},
            load_config=lambda: {},
            positive_int=lambda value: int(value) if str(value).isdigit() else None,
            latest_user_text=_latest_user_text,
            redact_text=lambda text: text,
        )
        preview = policy.project({"messages": [{"role": "user", "content": _command_turn(key)}]})

        self.assert_absent(key, json.dumps(preview).encode(), "the event preview")

    def test_tui_latest_input_omits_the_key(self):
        key = _canary()
        bus = TuiObservationBus(enabled=True)
        publish_latest_input(
            bus,
            {"messages": [{"role": "user", "content": [{"type": "text", "text": _command_turn(key)}]}]},
            request_id="r1",
            provider="p",
            model="m",
        )

        self.assert_absent(key, json.dumps(list(bus._events)).encode(), "the TUI observation bus")


class OutboundHistoryTests(unittest.TestCase):
    def services(self, captured):
        def identity(_provider, _config, body):
            return body

        def strip(body, **_kw):
            captured["upstream"] = json.dumps(body)
            raise RuntimeError("stop before the network")

        return claude.ClaudeRouterServices(
            core=_ports(claude.ClaudeRouterCore),
            count_tokens=_ports(claude.ClaudeRouterCountTokens),
            pipeline=_ports(
                claude.ClaudeRouterPipeline,
                router_event_message_preview=lambda *_args: {},
                dump_request_for_trace=lambda *_args: None,
                filter_blocked_tools=lambda _p, _c, body: body,
                normalize_tool_choice=lambda _p, _c, body: body,
                strip_advisor_tools=lambda _p, body: body,
                inject_channel_context=lambda body: body,
                inject_tool_result_context=lambda body: body,
            ),
            shortcuts=_ports(
                claude.ClaudeRouterShortcuts,
                **{f.name: (lambda *_a, **_k: False) for f in fields(claude.ClaudeRouterShortcuts)},
            ),
            delivery=_ports(claude.ClaudeRouterDelivery, is_client_disconnect=lambda _e: False),
            routing=_ports(
                claude.ClaudeRouterRouting,
                resolve_model=lambda *_a: "model",
                select_protocol=lambda *_a: "anthropic_messages",
                provider_labels={},
                request_policy=lambda *_a: SimpleNamespace(model_alias_strategy="", stream_required=False),
            ),
            normalization=_ports(
                claude.ClaudeRouterNativeNormalization,
                normalize_provider_wire=identity,
                normalize_thinking=identity,
                normalize_system_roles=identity,
                cap_body=identity,
                apply_request_options=identity,
                rehydrate_thinking=identity,
                resolve_tool_models=identity,
                normalize_model_options=lambda _p, _c, body, _m: body,
                strip_internal_metadata=strip,
            ),
            transport=_ports(claude.ClaudeRouterTransport),
            response=_ports(claude.ClaudeRouterResponse),
            context_recovery=_ports(
                claude.ClaudeRouterContextRecovery,
                observe_request=mock.Mock(),
                reject_request=lambda *_a: None,
                stream_scope=lambda *_a: None,
            ),
        )

    def test_history_turn_with_the_key_is_not_forwarded_or_observed(self):
        key = _canary()
        captured: dict = {}
        services = self.services(captured)
        services = replace(services)
        handler = SimpleNamespace(path="/v1/messages", headers={}, wfile=io.BytesIO(),
                                  send_response=mock.Mock(), send_header=mock.Mock(), end_headers=mock.Mock())
        body = {"model": "model", "stream": False, "messages": [
            {"role": "user", "content": _command_turn(key)},
            {"role": "assistant", "content": "Updated live API key settings."},
            {"role": "user", "content": "hello"},
        ]}
        try:
            claude.handle_claude_messages_post(services, handler, {}, "test", {"stream_enabled": False},
                                               "/v1/messages", body)
        except RuntimeError:
            pass

        self.assertIn("upstream", captured)
        self.assertFalse(key in captured["upstream"], "synthetic key forwarded upstream")
        self.assertIn("hello", captured["upstream"])
        observed = json.dumps([call.args for call in services.context_recovery.observe_request.call_args_list],
                              default=str)
        self.assertFalse(key in observed, "synthetic key handed to the context observers")


if __name__ == "__main__":
    unittest.main()
