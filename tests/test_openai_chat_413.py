import copy
from contextlib import ExitStack
import io
import json
import unittest
import urllib.error
from unittest import mock

import ciel_runtime


class _Handler:
    def __init__(self):
        self.path = "/v1/messages"
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


class OpenAIChatProvider413Tests(unittest.TestCase):
    def _run_messages_request(self, *, stream, retry_then_413=False):
        config = {
            "current_provider": "kimi",
            "providers": {
                "kimi": copy.deepcopy(
                    ciel_runtime.DEFAULT_CONFIG["providers"]["kimi"]
                )
            },
        }
        provider_config = config["providers"]["kimi"]
        provider_config.update(
            {
                "api_key": "sk-test",
                "current_model": "k3",
                "gateway_retries": 10,
                "official_tools_enabled": True,
                "stream_enabled": True,
            }
        )
        handler = _Handler()
        body = {
            "model": "ciel-runtime-kimi-k3",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 16,
            "stream": stream,
        }
        upstream_body = (
            b'{"error":{"type":"invalid_request_error",'
            b'"message":"provider payload limit"}}'
        )

        attempts = 0

        def reject_request(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if retry_then_413 and attempts == 1:
                raise urllib.error.HTTPError(
                    "https://api.kimi.com/coding/v1/chat/completions",
                    503,
                    "Service Unavailable",
                    {"content-type": "application/json"},
                    io.BytesIO(b'{"error":{"message":"retry"}}'),
                )
            raise urllib.error.HTTPError(
                "https://api.kimi.com/coding/v1/chat/completions",
                413,
                "Payload Too Large",
                {"content-type": "application/json"},
                io.BytesIO(upstream_body),
            )

        patch_values = {
            # Model alias resolution may refresh /v1/models when another test
            # has populated or invalidated the process-wide model cache.  Keep
            # catalog discovery out of this transport-attempt invariant: the
            # provider_urlopen calls counted below must be chat requests only.
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
        with ExitStack() as stack:
            urlopen = stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "provider_urlopen",
                    side_effect=reject_request,
                )
            )
            for name, replacement in patch_values.items():
                stack.enter_context(
                    mock.patch.object(ciel_runtime, name, side_effect=replacement)
                )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "begin_pending_channel_delivery")
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "commit_pending_channel_delivery_cursors")
            )
            delivery_failed = stack.enter_context(
                mock.patch.object(ciel_runtime, "mark_pending_channel_delivery_failed")
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "mark_pending_channel_delivery_success")
            )
            stack.enter_context(mock.patch.object(ciel_runtime, "write_context_usage"))
            stack.enter_context(mock.patch.object(ciel_runtime, "dump_request_for_trace"))
            stack.enter_context(mock.patch.object(ciel_runtime.time, "sleep"))

            handled = ciel_runtime.route_runtime_post(
                handler,
                config,
                "kimi",
                provider_config,
                "/v1/messages",
                body,
            )

        return handler, handled, urlopen, delivery_failed

    def _assert_413_response(self, handler, handled, urlopen, delivery_failed):
        self.assertTrue(handled)
        self.assertEqual([413], handler.statuses)
        self.assertEqual(1, urlopen.call_count)
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual("error", payload["type"])
        self.assertEqual("request_too_large", payload["error"]["type"])
        self.assertEqual("provider payload limit", payload["error"]["message"])
        self.assertNotIn(b"event: message_start", handler.wfile.getvalue())
        delivery_failed.assert_called_once()

    def test_v1_messages_nonstream_preserves_provider_413(self):
        self._assert_413_response(*self._run_messages_request(stream=False))

    def test_v1_messages_stream_detects_413_before_sending_sse_200(self):
        self._assert_413_response(*self._run_messages_request(stream=True))

    def test_v1_messages_stream_emits_error_event_if_retry_notice_started_sse(self):
        handler, handled, urlopen, delivery_failed = self._run_messages_request(
            stream=True,
            retry_then_413=True,
        )

        self.assertTrue(handled)
        self.assertEqual([200], handler.statuses)
        self.assertEqual(2, urlopen.call_count)
        output = handler.wfile.getvalue().decode()
        self.assertIn("event: message_start", output)
        self.assertIn("event: error", output)
        error_block = output.rsplit("event: error\n", 1)[1]
        payload = json.loads(
            next(line[6:] for line in error_block.splitlines() if line.startswith("data: "))
        )
        self.assertEqual("request_too_large", payload["error"]["type"])
        self.assertEqual("provider payload limit", payload["error"]["message"])
        delivery_failed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
