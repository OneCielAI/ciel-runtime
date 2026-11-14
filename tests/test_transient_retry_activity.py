"""Pre-output retries are visible: router activity, event bus, statusline, final message.

Journal 2026-11-13 fixes/router-upstream-error-retry, design points (d) and (e).
The activity port is RuntimeActivityRepository.router_activity(event, provider, model,
**fields); it publishes router.<event> on the event bus and writes router-activity.json,
which the statusline renders.
"""
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import transient_retry as retry
from ciel_runtime_support.openai_chat_passthrough import OpenAIChatPassthrough, OpenAIChatPassthroughPorts
from test_provider_responses_gateway_replay_guard import passthrough as responses_passthrough, COMPLETE, body
from test_upstream_retry import ENTRYPOINTS, Response, invoke, services

NL = bytes([10])
TRANSIENT = b'{"type":"error","error":{"type":"overloaded_error","message":"service temporarily unavailable"}}'
LATE = NL.join([b"event: error", b'data: {"type":"error","error":{"type":"server_error",'
                b'"message":"Model claude/claude-opus-5-5 timed out (HTTP 504)"},"request_id":"req_fixture"}', b"", b""])
OUTPUT = NL.join([b"event: message_start", b'data: {"type":"message_start","message":{"id":"msg_ok"}}', b"", b""])


def rejection(code=503, raw=TRANSIENT):
    return urllib.error.HTTPError("http://a/v1/messages", code, "x", {"content-type": "application/json"},
                                  io.BytesIO(raw))


class Recorder:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, event, provider, model=None, **fields):
        self.calls.append((event, provider, model, fields))
        if self.fail:
            raise OSError("activity file unavailable")

    def events(self, name):
        return [fields for event, _, _, fields in self.calls if event == name and "kind" in fields]


def opener(*steps):
    """Each step is a factory; the last one repeats. Fresh objects per open."""
    queue = list(steps)

    def open_once():
        step = queue.pop(0) if len(queue) > 1 else queue[0]
        value = step()
        if isinstance(value, BaseException):
            raise value
        return value
    return open_once


class ExecutorActivityTests(unittest.TestCase):
    def open(self, open_once, activity=None, publish=None, config=None):
        return retry.open_with_transient_retry(open_once, provider="cielairouter", config=config or {},
                                               log=lambda *_: None, publish=publish, sleep=lambda _: None,
                                               model="m", activity=activity)

    def test_each_retry_reaches_activity_and_not_publish(self):
        activity, published = Recorder(), []
        with self.open(opener(rejection, Response), activity, lambda **e: published.append(e)) as response:
            self.assertEqual(b'{"id":"ok"}', response.read())
        retries = activity.events("retry")
        self.assertEqual(1, len(retries))
        self.assertEqual(("retry", "cielairouter", "m"), activity.calls[0][:3])
        # Unset gateway_retries uses the 420 s window, so "total" is its attempt
        # ceiling (team-lead ruling 2026-11-13, evidence/claude-window-330).
        self.assertEqual({"kind": "http_rejection", "code": 503, "attempt": 1,
                          "total": retry.WINDOW_RETRY_CEILING}, {
            key: retries[0][key] for key in ("kind", "code", "attempt", "total")})
        self.assertEqual(retry.RETRY_WINDOW_SECONDS, retries[0]["window"])
        self.assertGreater(retries[0]["wait"], 0)
        self.assertEqual([], published)
        self.assertEqual([], activity.events("error"))

    def test_exhaustion_reports_a_final_error_with_attempt_count(self):
        activity = Recorder()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.open(opener(rejection), activity)
        final = activity.events("error")
        self.assertEqual(1, len(final))
        # Immediate failures under the 420 s window stop at its attempt ceiling
        # (team-lead ruling 2026-11-13, evidence/claude-window-330).
        ceiling = retry.WINDOW_RETRY_CEILING
        self.assertEqual({"reason": "pre_output_retries_exhausted", "attempts": ceiling, "stop": "attempt_ceiling",
                          "code": 503, "kind": "http_rejection"},
                         {key: final[0][key] for key in ("reason", "attempts", "stop", "code", "kind")})
        self.assertEqual(ceiling, len(activity.events("retry")))
        self.assertIn(f"{ceiling} time(s)", caught.exception._ciel_transient_retry_note)
        caught.exception.close()

    def test_first_attempt_terminal_is_not_reported_as_exhaustion(self):
        activity = Recorder()
        auth = b'{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}'
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.open(opener(lambda: rejection(401, auth)), activity)
        self.assertEqual([], activity.calls)
        caught.exception.close()

    def test_failing_activity_never_fails_the_request(self):
        activity = Recorder(fail=True)
        with self.open(opener(rejection, Response), activity) as response:
            self.assertEqual(b'{"id":"ok"}', response.read())
        self.assertEqual(1, len(activity.events("retry")))

    def test_publish_only_callers_also_get_the_final_error_event(self):
        published = []
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.open(opener(rejection), publish=lambda **event: published.append(event))
        categories = [event["category"] for event in published]
        # Window attempt ceiling (team-lead ruling 2026-11-13, evidence/claude-window-330).
        self.assertEqual(["router.retry"] * retry.WINDOW_RETRY_CEILING + ["router.error"], categories)
        self.assertEqual("pre_output_retries_exhausted", published[-1]["data"]["reason"])
        caught.exception.close()


class LateSSEActivityTests(unittest.TestCase):
    def serve(self, bodies, config=None):
        hits, logs, activity = [], [], Recorder()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                hits.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                payload = bodies[min(len(hits) - 1, len(bodies) - 1)]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                time.sleep(.25)  # past the 100 ms open-time preflight: a late (read-time) error
                self.wfile.write(payload)
                self.wfile.flush()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        http_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/messages", data=b"same-payload")
        response = retry.open_with_transient_retry(
            lambda: http_opener.open(req, timeout=3), provider="cielairouter", config=config or {},
            log=lambda level, text: logs.append(text), sleep=lambda _: None, request=req, model="m",
            activity=activity)
        with response:
            result = response.read()
        return result, hits, activity, chr(10).join(logs)

    def test_late_retry_names_the_upstream_status_from_the_message(self):
        result, hits, activity, _ = self.serve([LATE, OUTPUT])
        self.assertEqual(OUTPUT, result)
        self.assertEqual(2, len(hits))
        retries = activity.events("retry")
        self.assertEqual(("late_sse_error", 504, 1), (retries[0]["kind"], retries[0]["code"], retries[0]["attempt"]))

    def test_exhaustion_message_keeps_type_and_appends_a_bounded_summary(self):
        result, hits, activity, logs = self.serve([LATE], {"gateway_retries": 2})
        self.assertEqual(3, len(hits))
        payload = json.loads(result.split(b"data: ", 1)[1])
        self.assertEqual("server_error", payload["error"]["type"])
        message = payload["error"]["message"]
        self.assertTrue(message.startswith("Model claude/claude-opus-5-5 timed out (HTTP 504) (Ciel Runtime retried 2 time(s) over "))
        self.assertTrue(message.endswith("before any output; giving up: retry limit reached)"))
        self.assertLessEqual(len(message), retry._FINAL_MESSAGE_LIMIT)
        final = activity.events("error")
        self.assertEqual(("pre_output_retries_exhausted", 2, 504), (final[0]["reason"], final[0]["attempts"], final[0]["code"]))
        for operation_id in {fields["operation_id"] for *_, fields in activity.calls}:
            self.assertNotIn(operation_id, message)
        self.assertNotIn("Model claude", logs)

    def test_overlong_upstream_message_is_cut_to_the_bound(self):
        long_error = LATE.replace(b"timed out (HTTP 504)", b"x" * 5000)
        result, _, _, _ = self.serve([long_error], {"gateway_retries": 1})
        message = json.loads(result.split(b"data: ", 1)[1])["error"]["message"]
        self.assertEqual(retry._FINAL_MESSAGE_LIMIT, len(message))
        self.assertTrue(message.endswith("giving up: retry limit reached)"))

    def test_output_before_error_reports_nothing(self):
        result, hits, activity, _ = self.serve([OUTPUT + LATE, OUTPUT])
        self.assertEqual(OUTPUT + LATE, result)
        self.assertEqual(1, len(hits))
        self.assertEqual([], activity.calls)


class CallSiteWiringTests(unittest.TestCase):
    def test_every_upstream_retry_entrypoint_passes_the_activity_writer(self):
        for fn in ENTRYPOINTS:
            with self.subTest(fn=fn.__name__):
                activity = Recorder()
                steps = [rejection(), Response()]
                dispatch = mock.Mock(side_effect=lambda *a, **kw: (_ for _ in ()).throw(steps.pop(0))
                                     if isinstance(steps[0], BaseException) else steps.pop(0))
                with mock.patch.object(retry.time, "sleep"):
                    invoke(fn, services(dispatch, activity=activity), {"base_url": "http://a"})
                retries = activity.events("retry")
                self.assertEqual(1, len(retries))
                self.assertEqual(("http_rejection", 503), (retries[0]["kind"], retries[0]["code"]))

    def test_chat_passthrough_passes_its_activity_port(self):
        activity, calls = Recorder(), []

        def dispatch(req, **kwargs):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise rejection()
            return Response()

        route = OpenAIChatPassthrough(OpenAIChatPassthroughPorts(
            normalize_model=lambda p, c, m: m, normalize_request=lambda p, c, b: b,
            upstream_base=lambda p, c: c["base_url"], join_url=lambda b, p: b + p,
            headers=lambda p, c, h: {"authorization": "k"}, urlopen=dispatch,
            timeout_seconds=lambda c: 1, copy_response_headers=lambda *a: None, activity=activity))
        downstream = SimpleNamespace(headers={}, wfile=io.BytesIO(), send_response=mock.Mock(), end_headers=mock.Mock())
        with mock.patch.object(retry.time, "sleep"):
            route.forward(downstream, "cielairouter", {"base_url": "http://a"}, {"model": "m", "messages": []})
        self.assertEqual(2, len(calls))
        self.assertEqual(1, len(activity.events("retry")))

    def test_responses_passthrough_passes_its_activity_port(self):
        activity, calls = Recorder(), []

        def dispatch(req, **kwargs):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise rejection()
            response = io.BytesIO(COMPLETE)
            response.headers, response.status = {"content-type": "text/event-stream"}, 200
            return response

        route = responses_passthrough(dispatch, [])
        route._ports = route._ports.__class__(**{**{f: getattr(route._ports, f) for f in route._ports.__slots__},
                                                 "activity": activity})
        downstream = SimpleNamespace(headers={}, wfile=io.BytesIO(), send_response=mock.Mock(), end_headers=mock.Mock())
        with mock.patch.object(retry.time, "sleep"):
            route.forward(downstream, "cielairouter", {"base_url": "http://a"}, body())
        self.assertEqual(2, len(calls))
        self.assertEqual(1, len(activity.events("retry")))


class StatuslineRenderingTests(unittest.TestCase):
    def render(self, activity):
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        import ciel_runtime

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "statusline.py"
            script.write_text(ciel_runtime.STATUSLINE_SCRIPT, encoding="utf-8")
            (Path(tmp) / "router-activity.json").write_text(
                json.dumps({"updated_at": time.time(), "provider": "cielairouter", "model": "m", **activity}),
                encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if not key.startswith("CIEL_RUNTIME_")}
            env.update({"CIEL_RUNTIME_CONFIG_DIR": tmp, "CIEL_RUNTIME_STATUSLINE_ANSI": "0",
                        "CIEL_RUNTIME_PROVIDER": "cielairouter",
                        "CIEL_RUNTIME_MODEL_ALIAS": "ciel-runtime-cielairouter-m"})
            proc = subprocess.run([sys.executable, str(script)],
                                  input=json.dumps({"model": {"display_name": "m"}, "workspace": {"current_dir": tmp}}),
                                  text=True, capture_output=True, env=env, check=True, timeout=60)
            return proc.stdout

    def test_pre_output_retry_shows_attempt_cause_and_wait(self):
        out = self.render({"event": "retry", "kind": "late_sse_error", "attempt": 4, "total": 20,
                           "code": 504, "wait": 8.0})
        self.assertIn("retry 4/20 after upstream 504 wait 8s", out)

    def test_final_exhaustion_shows_the_attempt_count(self):
        out = self.render({"event": "error", "reason": "pre_output_retries_exhausted", "attempts": 3,
                           "code": "server_error", "kind": "late_sse_error"})
        self.assertIn("gave up after 3 retries (server_error)", out)

    def test_rate_limit_retry_rendering_is_unchanged(self):
        out = self.render({"event": "retry", "attempt": 1, "total": 3, "code": 429, "wait": 5})
        self.assertIn("retry 1/3", out)
        self.assertNotIn("after upstream", out)
        self.assertNotIn("wait 5s", out)

    def test_untrusted_code_text_is_not_rendered(self):
        out = self.render({"event": "retry", "kind": "late_sse_error", "attempt": 1, "total": 3,
                           "code": "<script> PRIVATE"})
        self.assertNotIn("PRIVATE", out)


if __name__ == "__main__":
    unittest.main()
