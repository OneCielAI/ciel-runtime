"""Retrying a Codex turn the upstream rejected item by item."""

import io
import json
import unittest
import urllib.error

from ciel_runtime_support.router_http import (
    CodexBackendHttpAdapter,
    CodexBackendRequestPorts,
    CodexBackendRetryPorts,
)
from ciel_runtime_support.upstream_error_policy import retryable_exception

# Verbatim 404 from api.openai.com after the session's model changed. The
# reasoning item had been minted by the router while a foreign provider served
# the Codex client, so no OpenAI backend had ever stored it.
MISSING_ITEM_404 = json.dumps(
    {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": (
                "Item with id 'rs_e50d87c9_0' not found. Items are not persisted "
                "when `store` is set to false. Try again with `store` set to true, "
                "or remove this item from your input."
            ),
        },
    }
).encode("utf-8")

MISSING_TOOL_OUTPUT_400 = json.dumps(
    {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": (
                "invalid_request_error: No tool output found for custom tool call "
                "call_aul297D4jWf5nUXpEmbine7E."
            ),
        },
    }
).encode("utf-8")


class FakeWfile:
    def __init__(self):
        self.written = b""

    def write(self, chunk):
        self.written += chunk

    def flush(self):
        pass


class FakeHandler:
    def __init__(self):
        self.path = "/backend-api/codex/responses"
        self.headers = {}
        self.wfile = FakeWfile()
        self.status = None

    def send_response(self, status):
        self.status = status

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, payload=b"ok"):
        self._chunks = [payload]
        self._buffer = io.BytesIO(payload)

    def readline(self, limit=-1):
        return self._buffer.readline(limit)

    def read(self, _size=None):
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class SseFailure:
    """A 200 response whose control preamble carries the refusal."""

    def __init__(self, payload):
        self.payload = payload


class ScriptedUpstream:
    """Raise scripted failures, then answer, recording every request body."""

    def __init__(self, failures):
        self._failures = list(failures)
        self.bodies = []

    def __call__(self, request, **_kwargs):
        self.bodies.append(json.loads(request.data.decode("utf-8")))
        if self._failures:
            scripted = self._failures.pop(0)
            if isinstance(scripted, SseFailure):
                return FakeResponse(scripted.payload)
            code, payload = scripted
            raise urllib.error.HTTPError(
                request.full_url, code, "Not Found", {}, io.BytesIO(payload)
            )
        return FakeResponse()


def adapter_for(upstream, logs, *, transport_retries=0, classify_transport=lambda _error: False):
    return CodexBackendHttpAdapter(
        "https://api.openai.com/backend-api/codex",
        CodexBackendRequestPorts(
            body_with_channel_context=lambda body: (body, None),
            begin_channel_delivery=lambda _handler, _body: None,
            upstream_headers=lambda _config, _headers: {"authorization": "Bearer t"},
            urlopen=upstream,
            request_timeout=lambda _config: 30.0,
            transport_retry_limit=lambda: transport_retries,
            retryable_exception=classify_transport,
        ),
        CodexBackendRetryPorts(
            retry_limit=lambda: 0,
            read_preamble=lambda _response: None,
            retry_wait=lambda _attempt: 0.0,
            log=lambda level, message: logs.append((level, message)),
            publish=lambda **_kwargs: None,
            sleep=lambda _seconds: None,
        ),
    )


def sealed_body():
    """A replayed turn whose reasoning item the upstream never stored."""

    return {
        "model": "gpt-5.4-codex",
        "input": [
            {"type": "message", "id": "msg_019fd64e-1892-7f33-99c0-9e1c5087a5be"},
            {
                "type": "reasoning",
                "id": "rs_e50d87c9_0",
                "summary": [{"type": "summary_text", "text": "hidden"}],
                "encrypted_content": None,
            },
        ],
    }


class MissingItemRetryTests(unittest.TestCase):
    def test_upstream_missing_tool_output_verdict_drops_exact_pair_and_retries(self):
        rejected = "call_aul297D4jWf5nUXpEmbine7E"
        body = {
            "model": "gpt-5.4-codex",
            "input": [
                {"type": "message", "role": "user"},
                {
                    "type": "custom_tool_call",
                    "call_id": rejected,
                    "name": "apply_patch",
                    "input": "patch",
                },
                {
                    "type": "custom_tool_call_output",
                    "call_id": rejected,
                    "output": "done",
                },
                {
                    "type": "function_call",
                    "call_id": "call_preserved",
                    "name": "shell",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_preserved",
                    "output": "ok",
                },
            ],
        }
        upstream = ScriptedUpstream([(400, MISSING_TOOL_OUTPUT_400)])
        logs = []

        adapter_for(upstream, logs).forward_json(FakeHandler(), "codex", {}, body)

        self.assertEqual(2, len(upstream.bodies))
        self.assertEqual(5, len(upstream.bodies[0]["input"]))
        self.assertEqual(
            ["message", "function_call", "function_call_output"],
            [item["type"] for item in upstream.bodies[1]["input"]],
        )
        self.assertTrue(
            any("codex_rejected_tool_pair_dropped" in message for _level, message in logs)
        )

    def test_missing_tool_output_for_absent_call_id_is_relayed_unchanged(self):
        upstream = ScriptedUpstream([(400, MISSING_TOOL_OUTPUT_400)])
        body = {"model": "gpt-5.4-codex", "input": [{"type": "message"}]}

        with self.assertRaises(urllib.error.HTTPError) as raised:
            adapter_for(upstream, []).forward_json(FakeHandler(), "codex", {}, body)

        self.assertEqual(400, raised.exception.code)
        self.assertEqual(MISSING_TOOL_OUTPUT_400, raised.exception.read())
        self.assertEqual(1, len(upstream.bodies))

    def test_orphan_custom_tool_call_is_removed_before_upstream_request(self):
        body = {
            "model": "gpt-5.4-codex",
            "input": [
                {
                    "type": "custom_tool_call",
                    "id": "ctc_valid_shape",
                    "call_id": "call_TqE66SGyCQxJYqXPdlJBpM98",
                    "name": "apply_patch",
                    "input": "patch",
                },
                {
                    "type": "message",
                    "id": "msg_continue",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
        }
        upstream = ScriptedUpstream([])

        adapter_for(upstream, []).forward_json(FakeHandler(), "codex", {}, body)

        self.assertEqual(1, len(upstream.bodies))
        self.assertEqual(
            ["message"],
            [item["type"] for item in upstream.bodies[0]["input"]],
        )
        self.assertEqual("call_TqE66SGyCQxJYqXPdlJBpM98", body["input"][0]["call_id"])

    def test_invalid_historical_tool_name_never_reaches_codex_upstream(self):
        body = {
            "model": "gpt-5.6-sol",
            "input": [
                {"type": "message", "role": "user", "content": "before"},
                {
                    "type": "function_call",
                    "call_id": "call_invalid_parallel",
                    "name": "multi_tool_use.parallel",
                    "arguments": '{"tool_calls":[]}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_invalid_parallel",
                    "output": "unsupported call: multi_tool_use.parallel",
                },
                {"type": "message", "role": "user", "content": "continue"},
            ],
        }
        upstream = ScriptedUpstream([])

        adapter_for(upstream, []).forward_json(FakeHandler(), "codex", {}, body)

        self.assertEqual(1, len(upstream.bodies))
        self.assertEqual(
            ["message", "message"],
            [item["type"] for item in upstream.bodies[0]["input"]],
        )
        self.assertEqual("multi_tool_use.parallel", body["input"][1]["name"])

    def test_transport_failure_before_response_is_retried_without_freezing(self):
        class TransientUpstream:
            def __init__(self):
                self.calls = 0

            def __call__(self, _request, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise urllib.error.URLError(OSError(113, "No route to host"))
                return FakeResponse()

        upstream = TransientUpstream()
        logs = []

        adapter_for(
            upstream,
            logs,
            transport_retries=2,
            classify_transport=retryable_exception,
        ).forward_json(FakeHandler(), "codex", {}, sealed_body())

        self.assertEqual(2, upstream.calls)
        self.assertTrue(any("codex_transport_retry" in message for _level, message in logs))

    def test_router_minted_item_never_reaches_the_upstream(self):
        upstream = ScriptedUpstream([])
        handler = FakeHandler()
        logs = []

        adapter_for(upstream, logs).forward_json(handler, "codex", {}, sealed_body())

        self.assertEqual(1, len(upstream.bodies))
        self.assertEqual(
            ["msg_019fd64e-1892-7f33-99c0-9e1c5087a5be"],
            [item["id"] for item in upstream.bodies[0]["input"]],
        )
        self.assertEqual(200, handler.status)

    def test_native_compaction_window_fields_reach_codex_unchanged(self):
        body = {
            "model": "gpt-5.4-codex",
            "input": [
                {
                    "type": "compaction",
                    "encrypted_content": "opaque-native-checkpoint",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
            "context_management": [
                {"type": "compaction", "compact_threshold": 900_000}
            ],
            "prompt_cache_key": "native-codex-window",
        }
        upstream = ScriptedUpstream([])

        adapter_for(upstream, []).forward_json(FakeHandler(), "codex", {}, body)

        self.assertEqual([body], upstream.bodies)

    def test_an_unknown_item_the_prefilter_kept_is_dropped_and_retried(self):
        # An upstream-shaped ID the prefilter has no reason to touch, so only
        # the 404 verdict can identify it.
        body = sealed_body()
        body["input"][1]["id"] = "rs_0b9eabb999711d12016a755d56dddc81978d609657b795ba6b"
        payload = MISSING_ITEM_404.replace(
            b"rs_e50d87c9_0",
            b"rs_0b9eabb999711d12016a755d56dddc81978d609657b795ba6b",
        )
        upstream = ScriptedUpstream([(404, payload)])
        handler = FakeHandler()
        logs = []

        adapter_for(upstream, logs).forward_json(handler, "codex", {}, body)

        self.assertEqual(2, len(upstream.bodies))
        self.assertEqual(2, len(upstream.bodies[0]["input"]))
        self.assertEqual([], [i["id"] for i in upstream.bodies[1]["input"] if i.get("id")])
        self.assertEqual(200, handler.status)
        self.assertTrue(any("codex_unstored_items_repaired" in message for _l, message in logs))

    def test_a_long_replay_recovers_in_one_retry_not_one_per_item(self):
        # The freeze this guards against: repairing one item per verdict turned
        # a resumed session into hundreds of silent round trips.
        body = {
            "model": "gpt-5.4-codex",
            "input": [
                {"type": "message", "id": f"msg_{index:04d}", "role": "assistant"}
                for index in range(500)
            ],
        }
        upstream = ScriptedUpstream(
            [(404, MISSING_ITEM_404.replace(b"rs_e50d87c9_0", b"msg_0000"))]
        )

        adapter_for(upstream, []).forward_json(FakeHandler(), "codex", {}, body)

        self.assertEqual(2, len(upstream.bodies))
        self.assertEqual(500, len(upstream.bodies[1]["input"]))
        self.assertEqual([], [i["id"] for i in upstream.bodies[1]["input"] if i.get("id")])

    def test_repeated_rejections_stop_instead_of_retrying_forever(self):
        # A verdict the repair cannot satisfy must reach the client, not spin.
        sealed = '{"detail":"invalid_request_error: The encrypted content aa...zz= could not be verified."}'
        upstream = ScriptedUpstream([(400, sealed.encode())] * 400)
        body = {
            "model": "gpt-5.4-codex",
            "input": [
                {"type": "reasoning", "encrypted_content": f"aa{index}zz="}
                for index in range(400)
            ],
        }
        logs = []

        with self.assertRaises(urllib.error.HTTPError):
            adapter_for(upstream, logs).forward_json(FakeHandler(), "codex", {}, body)

        self.assertLessEqual(len(upstream.bodies), 65)
        self.assertTrue(any("codex_replay_repair_exhausted" in m for _l, m in logs))

    def test_a_tool_call_is_retried_with_its_content_intact(self):
        body = {
            "model": "gpt-5.4-codex",
            "input": [
                {
                    "type": "function_call",
                    "id": "fc_0b9eabb999711d12016a755e3465a88197b1c4c29d90ff0789",
                    "name": "shell",
                    "arguments": '{"command":"ls"}',
                    "call_id": "call_1",
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            ],
        }
        payload = MISSING_ITEM_404.replace(
            b"rs_e50d87c9_0",
            b"fc_0b9eabb999711d12016a755e3465a88197b1c4c29d90ff0789",
        )
        upstream = ScriptedUpstream([(404, payload)])

        adapter_for(upstream, []).forward_json(FakeHandler(), "codex", {}, body)

        retried = upstream.bodies[1]["input"]
        self.assertEqual(2, len(retried))
        self.assertNotIn("id", retried[0])
        self.assertEqual("shell", retried[0]["name"])
        self.assertEqual("call_1", retried[0]["call_id"])
        self.assertEqual("call_1", retried[1]["call_id"])

    def test_an_unrelated_404_reaches_the_client_unchanged(self):
        upstream = ScriptedUpstream([(404, b'{"error":{"message":"Unknown model"}}')])

        with self.assertRaises(urllib.error.HTTPError) as raised:
            adapter_for(upstream, []).forward_json(
                FakeHandler(), "codex", {}, sealed_body()
            )

        self.assertEqual(404, raised.exception.code)
        self.assertEqual(b'{"error":{"message":"Unknown model"}}', raised.exception.read())
        self.assertEqual(1, len(upstream.bodies))

    def test_a_request_with_nothing_left_to_repair_reaches_the_client(self):
        payload = MISSING_ITEM_404.replace(b"rs_e50d87c9_0", b"rs_not_in_this_request")
        upstream = ScriptedUpstream([(404, payload)])
        body = {"model": "gpt-5.4-codex", "input": [{"type": "message", "role": "user"}]}

        with self.assertRaises(urllib.error.HTTPError) as raised:
            adapter_for(upstream, []).forward_json(FakeHandler(), "codex", {}, body)

        self.assertEqual(404, raised.exception.code)
        self.assertEqual(1, len(upstream.bodies))

    def test_a_repeated_unknown_item_rejection_stops_after_one_repair(self):
        payload = MISSING_ITEM_404.replace(b"rs_e50d87c9_0", b"msg_019fd64e-1892-7f33-99c0-9e1c5087a5be")
        upstream = ScriptedUpstream([(404, payload), (404, payload)])

        with self.assertRaises(urllib.error.HTTPError):
            adapter_for(upstream, []).forward_json(
                FakeHandler(), "codex", {}, sealed_body()
            )

        self.assertEqual(2, len(upstream.bodies))


def _sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


# Verbatim shape captured from chatgpt.com/backend-api/codex: a 200 response
# whose control preamble ends in response.failed carrying the refusal.
CONTEXT_FAILED_SSE = _sse(
    "response.created", {"type": "response.created", "response": {"id": "resp_1"}}
) + _sse(
    "response.in_progress",
    {"type": "response.in_progress", "response": {"id": "resp_1"}},
) + _sse(
    # The upstream announces the refusal here first; scanning must not stop.
    "error",
    {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "code": "context_length_exceeded",
            "message": "Your input exceeds the context window of this model.",
            "param": "input",
        },
        "sequence_number": 2,
    },
) + _sse(
    "response.failed",
    {
        "type": "response.failed",
        "response": {
            "id": "resp_1",
            "status": "failed",
            "error": {
                "code": "context_length_exceeded",
                "message": "Your input exceeds the context window of this model.",
            },
        },
    },
)


class ContextExceededCompactionTests(unittest.TestCase):
    """The upstream refuses an oversized turn inside a 200 stream."""

    def upstream(self, failures):
        return ScriptedUpstream(failures)

    def adapter(self, upstream, logs, compacted):
        from ciel_runtime_support.router_http import (
            CodexBackendHttpAdapter,
            CodexBackendRequestPorts,
            CodexBackendRetryPorts,
        )
        from ciel_runtime_support.codex_router import read_codex_response_preamble

        def compact(body, budget, **_kwargs):
            compacted.append(budget)
            projected = dict(body)
            projected["input"] = body["input"][len(body["input"]) // 2 :]
            return projected

        return CodexBackendHttpAdapter(
            "https://chatgpt.com/backend-api/codex",
            CodexBackendRequestPorts(
                body_with_channel_context=lambda body: (body, {}),
                begin_channel_delivery=lambda _handler, _body: None,
                upstream_headers=lambda _config, _headers: {"authorization": "Bearer t"},
                urlopen=upstream,
                request_timeout=lambda _config: 30.0,
            ),
            CodexBackendRetryPorts(
                retry_limit=lambda: 0,
                read_preamble=read_codex_response_preamble,
                retry_wait=lambda _attempt: 0.0,
                log=lambda level, message: logs.append((level, message)),
                publish=lambda **_kwargs: None,
                sleep=lambda _seconds: None,
                estimate_tokens=lambda body: len(json.dumps(body)) // 4,
                compact_responses=compact,
            ),
        )

    def body(self, items=64):
        return {"model": "gpt-5.6-sol",
                "input": [{"type": "message", "role": "user",
                           "content": [{"type": "input_text", "text": "x" * 400}]}
                          for _ in range(items)]}

    def test_a_refused_turn_is_compacted_and_retried(self):
        upstream = self.upstream([SseFailure(CONTEXT_FAILED_SSE)])
        logs, budgets = [], []

        self.adapter(upstream, logs, budgets).forward_json(
            FakeHandler(), "codex", {}, self.body(), mutate_responses=True
        )

        self.assertEqual(2, len(upstream.bodies))
        self.assertLess(len(upstream.bodies[1]["input"]), len(upstream.bodies[0]["input"]))
        self.assertEqual(1, len(budgets))

    def test_compaction_is_reported_at_the_default_log_level(self):
        upstream = self.upstream([SseFailure(CONTEXT_FAILED_SSE)])
        logs = []

        self.adapter(upstream, logs, []).forward_json(
            FakeHandler(), "codex", {}, self.body(), mutate_responses=True
        )

        self.assertTrue(any(level == "ERROR" and "codex_context_compacted" in message
                            for level, message in logs))

    def test_the_upstream_refusal_reaches_the_client_when_compaction_is_exhausted(self):
        upstream = self.upstream([SseFailure(CONTEXT_FAILED_SSE)] * 5)
        handler = FakeHandler()

        self.adapter(upstream, [], []).forward_json(
            handler, "codex", {}, self.body(), mutate_responses=True
        )

        self.assertLessEqual(len(upstream.bodies), 4)
        self.assertIn(b"context_length_exceeded", handler.wfile.written)
        self.assertEqual(200, handler.status)


if __name__ == "__main__":
    unittest.main()
