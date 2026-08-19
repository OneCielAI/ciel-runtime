"""Router-level regressions for upstream failures that used to look like success.

These drive `route_runtime_post` the way the CLI does, so they cover the whole
path from the upstream status to the bytes Claude and Codex actually read.
"""

import copy
from contextlib import ExitStack
import io
import json
import unittest
import urllib.error
from unittest import mock

import ciel_runtime


class _Handler:
    def __init__(self, path: str = "/v1/messages"):
        self.path = path
        self.headers = {}
        self.wfile = io.BytesIO()
        self.statuses = []
        self.response_headers = []

    def send_response(self, status, _message=None):
        self.statuses.append(status)

    def send_header(self, name, value):
        self.response_headers.append((str(name).casefold(), str(value)))

    def end_headers(self):
        return


_ROUTE_PATCHES = {
    "upstream_model_ids": lambda *_args, **_kwargs: ["k3"],
    "body_with_pending_channel_messages": lambda value: value,
    "body_with_channel_tool_result_context": lambda value: value,
    "filter_blocked_tools": lambda _provider, _config, value: value,
    "maybe_handle_plan_mode_tool_choice": lambda *_args, **_kwargs: False,
    "maybe_handle_router_debug_request": lambda *_args, **_kwargs: False,
    "maybe_handle_version_request": lambda *_args, **_kwargs: False,
    "maybe_handle_channel_clear_request": lambda *_args, **_kwargs: False,
    "maybe_handle_import_session_request": lambda *_args, **_kwargs: False,
    "maybe_handle_live_llm_options_request": lambda *_args, **_kwargs: False,
    "maybe_handle_live_api_keys_request": lambda *_args, **_kwargs: False,
    "maybe_handle_advisor_request": lambda *_args, **_kwargs: False,
}


def _kimi_config():
    config = {
        "current_provider": "kimi",
        "providers": {
            "kimi": copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["kimi"])
        },
    }
    config["providers"]["kimi"].update(
        {
            "api_key": "sk-test",
            "current_model": "k3",
            "gateway_retries": 0,
            "official_tools_enabled": True,
            "stream_enabled": True,
        }
    )
    return config, config["providers"]["kimi"]


def _run_messages_request(reject, *, stream):
    config, provider_config = _kimi_config()
    handler = _Handler()
    body = {
        "model": "ciel-runtime-kimi-k3",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 16,
        "stream": stream,
    }
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(ciel_runtime, "provider_urlopen", side_effect=reject)
        )
        for name, replacement in _ROUTE_PATCHES.items():
            stack.enter_context(
                mock.patch.object(ciel_runtime, name, side_effect=replacement)
            )
        for name in (
            "begin_pending_channel_delivery",
            "commit_pending_channel_delivery_cursors",
            "mark_pending_channel_delivery_failed",
            "mark_pending_channel_delivery_success",
            "write_context_usage",
            "dump_request_for_trace",
        ):
            stack.enter_context(mock.patch.object(ciel_runtime, name))
        stack.enter_context(mock.patch.object(ciel_runtime.time, "sleep"))
        handled = ciel_runtime.route_runtime_post(
            handler, config, "kimi", provider_config, "/v1/messages", body
        )
    return handler, handled


def _http_error(code, body):
    return urllib.error.HTTPError(
        "https://api.kimi.com/coding/v1/chat/completions",
        code,
        "Error",
        {"content-type": "application/json"},
        io.BytesIO(body.encode("utf-8")),
    )


class SessionUsageLimitReachesTheClientTests(unittest.TestCase):
    """A 429 used to be delivered as HTTP 200 with the text `HTTP Error 429`."""

    limit_body = json.dumps(
        {
            "error": {
                "type": "rate_limit_error",
                "message": "you reached your session usage limit; add extra usage",
            }
        }
    )

    def test_streaming_429_is_answered_as_a_rate_limit_error(self):
        def reject(*_args, **_kwargs):
            raise _http_error(429, self.limit_body)

        handler, handled = _run_messages_request(reject, stream=True)

        self.assertTrue(handled)
        self.assertEqual([429], handler.statuses)
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual("error", payload["type"])
        self.assertEqual("rate_limit_error", payload["error"]["type"])
        self.assertEqual(
            "you reached your session usage limit; add extra usage",
            payload["error"]["message"],
        )
        self.assertNotIn(b"event: message_start", handler.wfile.getvalue())

    def test_nonstreaming_429_is_answered_as_a_rate_limit_error(self):
        def reject(*_args, **_kwargs):
            raise _http_error(429, self.limit_body)

        handler, handled = _run_messages_request(reject, stream=False)

        self.assertTrue(handled)
        self.assertEqual([429], handler.statuses)
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual("rate_limit_error", payload["error"]["type"])
        self.assertIn("session usage limit", payload["error"]["message"])


class RequestDefectKeepsItsStatusTests(unittest.TestCase):
    """400, 404, 409 and 422 used to arrive as a local 500."""

    def _reject_with(self, status, body):
        def reject(*_args, **_kwargs):
            raise _http_error(status, body)

        return reject

    def test_unprocessable_request_keeps_status_and_message(self):
        body = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": "tool schema is invalid",
                }
            }
        )

        handler, handled = _run_messages_request(
            self._reject_with(422, body), stream=False
        )

        self.assertTrue(handled)
        self.assertEqual([422], handler.statuses)
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual("invalid_request_error", payload["error"]["type"])
        self.assertEqual("tool schema is invalid", payload["error"]["message"])

    def test_missing_model_keeps_its_not_found_status(self):
        body = json.dumps({"error": {"message": "model k3 does not exist"}})

        handler, handled = _run_messages_request(
            self._reject_with(404, body), stream=False
        )

        self.assertTrue(handled)
        self.assertEqual([404], handler.statuses)
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual("not_found_error", payload["error"]["type"])
        self.assertEqual("model k3 does not exist", payload["error"]["message"])

    def test_server_status_that_declares_a_request_defect_is_not_retried(self):
        # Kimi answers HTTP 500 while naming an invalid request.  Replaying it
        # cannot succeed, and reporting it as a server fault hides the schema.
        body = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": "tool schema is invalid",
                }
            }
        )

        handler, handled = _run_messages_request(
            self._reject_with(500, body), stream=False
        )

        self.assertTrue(handled)
        self.assertEqual([400], handler.statuses)
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual("invalid_request_error", payload["error"]["type"])
        self.assertEqual("tool schema is invalid", payload["error"]["message"])

    def test_genuine_server_error_stays_a_server_error(self):
        handler, handled = _run_messages_request(
            self._reject_with(500, "upstream exploded"), stream=False
        )

        self.assertTrue(handled)
        self.assertEqual([500], handler.statuses)
        payload = json.loads(handler.wfile.getvalue())
        self.assertIn("upstream exploded", payload["error"]["message"])


class ErrorObjectWithHttp200Tests(unittest.TestCase):
    """`{"error": ...}` returned with HTTP 200 used to become an empty turn."""

    def test_quota_error_in_a_200_body_is_not_an_empty_end_turn(self):
        payload = json.dumps(
            {"error": {"type": "rate_limit_error", "message": "quota exhausted"}}
        ).encode("utf-8")

        class _Response:
            status = 200
            headers = {"content-type": "application/json"}

            def read(self_inner):
                return payload

            def close(self_inner):
                return

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_exc):
                return False

        handler, handled = _run_messages_request(
            lambda *_args, **_kwargs: _Response(), stream=False
        )

        self.assertTrue(handled)
        self.assertEqual([429], handler.statuses)
        body = json.loads(handler.wfile.getvalue())
        self.assertEqual("rate_limit_error", body["error"]["type"])
        self.assertEqual("quota exhausted", body["error"]["message"])
        self.assertNotIn("end_turn", handler.wfile.getvalue().decode())


if __name__ == "__main__":
    unittest.main()
