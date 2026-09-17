import io
import json
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.codex_turn_recovery import (
    CODEX_COMPLETION_TOOL_NAME,
    CODEX_STRICT_CONTINUATION_NUDGE,
)
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough,
    ProviderResponsesPassthroughPorts,
)


# Shape Codex sends when it resumes a turn after mid-turn compaction.
SUMMARY = (
    "Another language model started to solve this problem and produced a summary "
    "of its thinking process.\n### progress"
)
TOOLS = [{"type": "function", "name": "exec_command"}]


def sse(output, response_id="resp_1"):
    chunks = [
        "event: response.output_item.done\ndata: "
        + json.dumps(
            {"type": "response.output_item.done", "output_index": index, "item": item}
        )
        + "\n\n"
        for index, item in enumerate(output)
    ]
    chunks.append(
        "event: response.completed\ndata: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {"id": response_id, "status": "completed", "output": output},
            }
        )
        + "\n\n"
    )
    return "".join(chunks).encode("utf-8")


def text_final(text="I will continue the verification now.", item_id="YK8+0AH/kN7="):
    return {
        "type": "message",
        "id": item_id,
        "role": "assistant",
        "phase": "final_answer",
        "content": [{"type": "output_text", "text": text}],
    }


def tool_call(name="exec_command"):
    return {"type": "function_call", "call_id": "call_1", "name": name, "arguments": "{}"}


def summary_message(content=None):
    return {
        "type": "message",
        "role": "user",
        "content": content or [{"type": "input_text", "text": SUMMARY}],
    }


class Response:
    status = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, payload):
        self._stream = io.BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size):
        return self._stream.read(size)


def auto_only_tool_choice(_provider, _config, body):
    """A provider rule such as Meta's: only the automatic tool choice exists."""

    if body.get("tool_choice") in (None, "auto"):
        return body
    return {**body, "tool_choice": "auto"}


def passthrough(urlopen, logs, normalize_request=None):
    return ProviderResponsesPassthrough(
        ProviderResponsesPassthroughPorts(
            project_channel_context=lambda body: (body, {"delivery": True}),
            begin_channel_delivery=mock.Mock(),
            normalize_model=lambda _provider, _config, model: model,
            normalize_request=normalize_request or (lambda _provider, _config, body: body),
            upstream_base=lambda _provider, _config: "https://example.test",
            join_url=lambda base, path: base + path,
            headers=lambda _provider, _config, _inbound: {"Authorization": "Bearer k"},
            urlopen=urlopen,
            timeout_seconds=lambda _config: 30.0,
            copy_response_headers=lambda _handler, _headers: None,
            log=lambda level, message: logs.append((level, message)),
        )
    )


def handler():
    return SimpleNamespace(
        headers={},
        wfile=io.BytesIO(),
        send_response=mock.Mock(),
        send_header=mock.Mock(),
        end_headers=mock.Mock(),
    )


def compacted_body():
    return {
        "model": "gpt-5.6-sol",
        "stream": True,
        "store": False,
        "tools": list(TOOLS),
        "input": [
            {"type": "message", "role": "user", "content": "run the verification"},
            summary_message(),
        ],
    }


class PassthroughCompletionGateTests(unittest.TestCase):
    def forward(self, body, payloads, normalize_request=None):
        logs = []
        requests = []

        def urlopen(request, **_kwargs):
            # urllib stamps the body length onto the request object while sending.
            request.add_unredirected_header("Content-length", str(len(request.data)))
            requests.append(request)
            payload = payloads[len(requests) - 1]
            if isinstance(payload, BaseException):
                raise payload
            return Response(payload)

        client = handler()
        passthrough(urlopen, logs, normalize_request).forward(
            client, "github-copilot-oauth", {}, body
        )
        return client.wfile.getvalue(), requests, logs

    def test_follow_up_obeys_the_providers_request_rules(self):
        candidate = sse([text_final()])
        continuation = sse([tool_call()], response_id="resp_2")

        written, requests, _logs = self.forward(
            compacted_body(), [candidate, continuation], auto_only_tool_choice
        )

        self.assertEqual(continuation, written)
        self.assertEqual("auto", json.loads(requests[1].data)["tool_choice"])

    def test_unconfirmed_final_is_replaced_by_the_models_next_action(self):
        candidate = sse([text_final()])
        continuation = sse([tool_call()], response_id="resp_2")

        written, requests, logs = self.forward(compacted_body(), [candidate, continuation])

        self.assertEqual(continuation, written)
        self.assertEqual(2, len(requests))
        follow_up = json.loads(requests[1].data)
        self.assertEqual("required", follow_up["tool_choice"])
        self.assertEqual(CODEX_COMPLETION_TOOL_NAME, follow_up["tools"][-1]["name"])
        self.assertEqual(
            CODEX_STRICT_CONTINUATION_NUDGE, follow_up["input"][-1]["content"][0]["text"]
        )
        replayed = follow_up["input"][-2]
        self.assertEqual("final_answer", replayed["phase"])
        self.assertNotIn("id", replayed)
        self.assertEqual(requests[0].full_url, requests[1].full_url)
        # The first request's stamped length must not leak into the follow-up.
        self.assertEqual({"Authorization": "Bearer k"}, requests[1].headers)
        self.assertTrue(
            any("provider_responses_completion_gate_continued" in m for _, m in logs)
        )

    def test_confirmed_final_is_delivered_unchanged(self):
        candidate = sse([text_final()])
        confirmation = sse([tool_call(CODEX_COMPLETION_TOOL_NAME)], response_id="resp_2")

        written, requests, logs = self.forward(compacted_body(), [candidate, confirmation])

        self.assertEqual(candidate, written)
        self.assertEqual(2, len(requests))
        self.assertTrue(
            any(
                "provider_responses_completion_gate_kept" in m and "confirmed=True" in m
                for _, m in logs
            )
        )

    def test_private_tool_never_reaches_the_client(self):
        candidate = sse([text_final()])
        mixed = sse(
            [tool_call(CODEX_COMPLETION_TOOL_NAME), tool_call()], response_id="resp_2"
        )

        written, _requests, _logs = self.forward(compacted_body(), [candidate, mixed])

        self.assertEqual(candidate, written)

    def test_failed_follow_up_keeps_the_candidate(self):
        candidate = sse([text_final()])
        rejected = urllib.error.HTTPError(
            "https://example.test/v1/responses", 400, "Bad Request", {}, io.BytesIO(b"{}")
        )

        written, requests, logs = self.forward(compacted_body(), [candidate, rejected])

        self.assertEqual(candidate, written)
        self.assertEqual(2, len(requests))
        self.assertTrue(
            any("provider_responses_completion_gate_failed" in m for _, m in logs)
        )

    def test_action_after_compaction_needs_no_follow_up(self):
        candidate = sse([tool_call()])

        written, requests, _logs = self.forward(compacted_body(), [candidate])

        self.assertEqual(candidate, written)
        self.assertEqual(1, len(requests))

    def test_final_straight_after_a_user_message_is_checked_too(self):
        body = compacted_body()
        body["input"] = [{"type": "message", "role": "user", "content": "do the work"}]
        candidate = sse([text_final()])
        continuation = sse([tool_call()], response_id="resp_2")

        written, requests, _logs = self.forward(body, [candidate, continuation])

        self.assertEqual(continuation, written)
        self.assertEqual(2, len(requests))

    def test_request_that_cannot_act_streams_without_a_check(self):
        candidate = sse([text_final()])
        for change in ({"tools": []}, {"tool_choice": "none"}):
            with self.subTest(change=change):
                body = {**compacted_body(), **change}

                written, requests, logs = self.forward(body, [candidate])

                self.assertEqual(candidate, written)
                self.assertEqual(1, len(requests))
                self.assertFalse(any("completion_gate" in m for _, m in logs))


if __name__ == "__main__":
    unittest.main()
