"""Regressions for the shared upstream failure model.

Each test names one path where the original upstream status, error type or
message used to be lost before it reached the CLI.
"""

import io
import json
import unittest
import urllib.error

from ciel_runtime_support.response_collection import upstream_failure_in_payload
from ciel_runtime_support.sse_stream_collection import (
    UpstreamSseError,
    collect_anthropic_message_stream,
    collect_openai_chat_stream,
)
from ciel_runtime_support.response_collection_context import (
    ResponseCollectionContext,
)
from ciel_runtime_support.upstream_error_policy import (
    UpstreamFailure,
    anthropic_error_type_for_status,
    classify_upstream_failure,
)


def http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://upstream.example/v1/chat/completions",
        code,
        "Error",
        {"content-type": "application/json"},
        io.BytesIO(body.encode("utf-8")),
    )


def sse(*payloads: dict) -> list[str]:
    return [f"data: {json.dumps(payload)}" for payload in payloads]


class UpstreamFailureClassificationTests(unittest.TestCase):
    def test_observed_terminal_status_outranks_the_body_label(self):
        # Providers answer 413 while calling the failure an invalid request.
        # The size limit is the part the CLI has to act on.
        self.assertEqual(
            "request_too_large",
            classify_upstream_failure(413, "invalid_request_error", "too big"),
        )
        self.assertEqual(
            "authentication",
            classify_upstream_failure(401, "invalid_request_error", "bad key"),
        )

    def test_declared_request_defect_survives_a_server_status(self):
        # A 500 that names an invalid request cannot be fixed by retrying.
        failure = UpstreamFailure.from_http_error(
            "kimi",
            "k3",
            http_error(
                500,
                json.dumps(
                    {
                        "error": {
                            "type": "invalid_request_error",
                            "message": "tool schema is invalid",
                        }
                    }
                ),
            ),
        )

        self.assertEqual("invalid_request", failure.category)
        self.assertFalse(failure.retryable)
        self.assertEqual(400, failure.status_code)
        self.assertEqual("tool schema is invalid", failure.message)

    def test_capacity_pressure_stays_retryable(self):
        failure = UpstreamFailure.from_payload(
            "kimi",
            "k3",
            {"code": "internal_server_error", "message": "the server is under high demand"},
            source="sse_event",
        )

        self.assertEqual("overloaded", failure.category)
        self.assertTrue(failure.retryable)

    def test_ollama_template_rejection_stays_a_request_error(self):
        failure = UpstreamFailure.from_http_error(
            "ollama",
            "qwen3.8:27b",
            http_error(
                500, json.dumps({"error": "system message must be at the beginning"})
            ),
        )

        self.assertEqual("invalid_request", failure.category)
        self.assertEqual(400, failure.status_code)
        self.assertFalse(failure.retryable)

    def test_observed_status_is_answered_verbatim_when_it_agrees(self):
        for status, expected_type in (
            (400, "invalid_request_error"),
            (404, "not_found_error"),
            (409, "invalid_request_error"),
            (422, "invalid_request_error"),
            (429, "rate_limit_error"),
        ):
            with self.subTest(status=status):
                failure = UpstreamFailure.from_http_error(
                    "provider", "model", http_error(status, "{}")
                )
                self.assertEqual(status, failure.status_code)
                self.assertEqual(expected_type, failure.anthropic_error_type)

    def test_unlabelled_server_error_keeps_its_own_status(self):
        failure = UpstreamFailure.from_http_error(
            "provider", "model", http_error(500, "internal failure")
        )

        self.assertEqual("upstream_error", failure.category)
        self.assertEqual(500, failure.status_code)
        self.assertEqual("internal failure", failure.message)

    def test_terminal_usage_limit_message_survives_a_429(self):
        failure = UpstreamFailure.from_http_error(
            "provider",
            "model",
            http_error(
                429,
                json.dumps(
                    {
                        "error": {
                            "type": "rate_limit_error",
                            "message": "you reached your session usage limit; add extra usage",
                        }
                    }
                ),
            ),
        )

        self.assertEqual(429, failure.status_code)
        self.assertEqual(
            "you reached your session usage limit; add extra usage",
            failure.anthropic_payload()["error"]["message"],
        )

    def test_output_already_sent_blocks_a_retry(self):
        failure = UpstreamFailure.from_payload(
            "provider",
            "model",
            {"error": {"type": "overloaded_error", "message": "overloaded"}},
            source="sse_event",
            output_started=True,
        )

        self.assertEqual("overloaded", failure.category)
        self.assertFalse(failure.retryable)


class ErrorInsideSuccessfulResponseTests(unittest.TestCase):
    def test_error_object_returned_with_http_200_is_not_a_finished_turn(self):
        failure = upstream_failure_in_payload(
            "provider",
            "model",
            {"error": {"type": "rate_limit_error", "message": "quota exhausted"}},
        )

        self.assertIsNotNone(failure)
        self.assertEqual("rate_limit", failure.category)
        self.assertEqual("quota exhausted", failure.message)
        self.assertEqual(429, failure.status_code)

    def test_a_normal_completion_is_left_alone(self):
        self.assertIsNone(
            upstream_failure_in_payload(
                "provider",
                "model",
                {"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
            )
        )

    def test_an_empty_error_field_is_not_a_failure(self):
        self.assertIsNone(
            upstream_failure_in_payload("provider", "model", {"error": None})
        )


class SseErrorEventTests(unittest.TestCase):
    def test_anthropic_error_event_is_not_collected_as_an_empty_message(self):
        events = sse(
            {"type": "message_start", "message": {"id": "msg_1", "role": "assistant"}},
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded"},
            },
        )

        with self.assertRaises(UpstreamSseError) as raised:
            collect_anthropic_message_stream(events)

        self.assertEqual("overloaded_error", raised.exception.code)
        self.assertEqual("Overloaded", raised.exception.message)
        self.assertFalse(raised.exception.output_started)

    def test_anthropic_error_after_output_reports_started_output(self):
        events = sse(
            {"type": "message_start", "message": {"id": "msg_1", "role": "assistant"}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "partial"},
            },
            {"type": "error", "error": {"type": "api_error", "message": "boom"}},
        )

        with self.assertRaises(UpstreamSseError) as raised:
            collect_anthropic_message_stream(events)

        self.assertTrue(raised.exception.output_started)

    def test_responses_failed_event_is_not_collected_as_a_stop(self):
        events = sse(
            {"type": "response.created", "response": {"id": "resp_1"}},
            {
                "type": "response.failed",
                "response": {
                    "id": "resp_1",
                    "error": {
                        "code": "server_is_overloaded",
                        "message": "please retry",
                    },
                },
            },
        )

        with self.assertRaises(UpstreamSseError) as raised:
            collect_openai_chat_stream(events)

        self.assertEqual("server_is_overloaded", raised.exception.code)

    def test_a_normal_chat_stream_still_collects(self):
        events = sse(
            {"choices": [{"index": 0, "delta": {"content": "hello"}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        )

        collected = collect_openai_chat_stream(events)

        self.assertEqual(
            "hello", collected.response["choices"][0]["message"]["content"]
        )


class KimiCapacityRetryTests(unittest.TestCase):
    def retryable(self, code: str, message: str, *, output_started: bool = False) -> bool:
        return ResponseCollectionContext.retryable_kimi_capacity_error(
            "kimi", UpstreamSseError(code, message, output_started=output_started)
        )

    def test_declared_request_defect_is_not_retried_as_capacity(self):
        self.assertFalse(
            self.retryable("invalid_request_error", "tool schema is invalid")
        )

    def test_unlabelled_server_error_is_not_retried_on_its_code_alone(self):
        self.assertFalse(self.retryable("internal_server_error", "tool schema is invalid"))

    def test_real_capacity_pressure_is_still_retried(self):
        self.assertTrue(self.retryable("server_is_overloaded", "please retry"))
        self.assertTrue(self.retryable("slow_down", "too fast"))
        self.assertTrue(
            self.retryable("internal_server_error", "the server is under high demand")
        )

    def test_started_output_is_never_retried(self):
        self.assertFalse(
            self.retryable("server_is_overloaded", "please retry", output_started=True)
        )

    def test_other_providers_are_untouched(self):
        self.assertFalse(
            ResponseCollectionContext.retryable_kimi_capacity_error(
                "deepseek", UpstreamSseError("server_is_overloaded", "please retry")
            )
        )


class ResponsesErrorTypeTests(unittest.TestCase):
    def test_request_failures_are_distinguishable_from_provider_faults(self):
        self.assertEqual("invalid_request_error", anthropic_error_type_for_status(400))
        self.assertEqual("not_found_error", anthropic_error_type_for_status(404))
        self.assertEqual("invalid_request_error", anthropic_error_type_for_status(422))
        self.assertEqual("rate_limit_error", anthropic_error_type_for_status(429))
        self.assertEqual("api_error", anthropic_error_type_for_status(500))

    def test_security_and_size_statuses_keep_their_established_names(self):
        self.assertEqual("authentication_error", anthropic_error_type_for_status(401))
        self.assertEqual("permission_error", anthropic_error_type_for_status(403))
        self.assertEqual("request_too_large", anthropic_error_type_for_status(413))


if __name__ == "__main__":
    unittest.main()
