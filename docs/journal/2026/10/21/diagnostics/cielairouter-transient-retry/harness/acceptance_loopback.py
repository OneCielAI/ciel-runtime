"""Owned loopback acceptance for the CielAiRouter transient retry budget.

Real HTTP server on 127.0.0.1, real urllib opening; the only synthetic part is
the provider body. No live runtime, provider or remote host is touched.

usage: python -I acceptance_loopback.py [--label NAME]
"""
import argparse
import hashlib
import io
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

for _name in list(os.environ):
    if _name.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_", "CODEX_", "OPENAI_")) \
            or any(word in _name.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
        del os.environ[_name]

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[7]
sys.path.insert(0, str(REPO))
from ciel_runtime_support.transient_retry import (  # noqa: E402
    BUDGET_CONFIG_KEY, open_with_transient_retry,
)

SCREENSHOT = (
    b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",'
    b'"message":"service_unavailable: Service temporarily unavailable: all targets '
    b'were skipped by pre-dispatch filters"}}\n\n'
)
EDGE = SCREENSHOT.replace(b"all targets were skipped by pre-dispatch filters",
                          b"provider edge temporary error")
QUOTA = b'{"type":"error","error":{"type":"insufficient_quota","message":"quota exhausted"}}'
MALFORMED = b"<html>503</html>"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self.server.hits.append(self.path)
        status, body = self.server.script.pop(0) if self.server.script else (200, b"ok")
        self.send_response(status)
        self.send_header("content-type", "text/event-stream" if body.startswith(b"event:") else "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def serve(script):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.hits, server.script = [], list(script)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}/v1/responses"


def open_request(url):
    return urllib.request.Request(url, data=b"{}", headers={"content-type": "application/json"})


def case(name, script, expected_hits, *, retries=3, expect_error=None, raw_ok=None):
    server, thread, url = serve(script)
    config = {"gateway_retries": retries}
    sleeps = []
    result = {"case": name}
    try:
        try:
            response = open_with_transient_retry(
                lambda: urllib.request.urlopen(open_request(url), timeout=5),
                provider="cielairouter", config=config,
                log=lambda *_a: None, sleep=sleeps.append, request=open_request(url),
            )
            body = response.read()
            result.update(status="ok", body=body.decode("utf-8", "replace")[:120])
            assert expect_error is None, f"unexpected success: {body!r}"
            if raw_ok is not None:
                assert body == raw_ok, (body, raw_ok)
        except urllib.error.HTTPError as error:
            body = error.read()
            result.update(status=f"http_{error.code}", body=body.decode("utf-8", "replace")[:160])
            assert expect_error is not None, f"unexpected HTTP {error.code}: {body!r}"
            expect_error(error, body)
        result["hits"] = len(server.hits)
        result["sleeps"] = len(sleeps)
        result["budget_used"] = getattr(config.get(BUDGET_CONFIG_KEY), "attempts", 0)
        assert result["hits"] == expected_hits, (name, result["hits"], expected_hits)
        result["passed"] = True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        result["listener_closed"] = not thread.is_alive()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    results = []

    # The exact screenshot rejection then success: exactly two upstream hits.
    results.append(case("screenshot_then_success", [(503, SCREENSHOT), (200, b"ok")], 2))

    # A persistent rejection stops at the budget and preserves the error shape.
    def exhausted(error, body):
        assert error.code == 503
        assert body.startswith(b"event: error\ndata: ")
        assert b"Ciel Runtime retried this request 3 time" in body
        assert b"pre-dispatch filters" in body

    results.append(case("screenshot_exhaustion", [(503, SCREENSHOT)] * 10, 4,
                        expect_error=exhausted))

    # Explicit zero disables the owner.
    results.append(case("explicit_zero", [(503, SCREENSHOT)], 1,
                        retries=0, expect_error=lambda e, b: None))

    # Non-transient verdicts are not replayed.
    results.append(case("insufficient_quota", [(503, QUOTA)], 1,
                        expect_error=lambda e, b: None))
    results.append(case("unstructured_body", [(503, MALFORMED)], 1,
                        expect_error=lambda e, b: None))
    results.append(case("auth", [(401, b'{"type":"error","error":{"type":"authentication_error","message":"bad key"}}')], 1,
                        expect_error=lambda e, b: None))
    results.append(case("client_error", [(400, b'{"type":"error","error":{"type":"invalid_request_error","message":"bad"}}')], 1,
                        expect_error=lambda e, b: None))

    # Another transient verdict still retries within the same budget.
    results.append(case("edge_transient_then_success", [(502, EDGE), (200, b"ok")], 2))

    payload = {
        "label": args.label,
        "source": {
            "transient_retry.py": hashlib.sha256(
                (REPO / "ciel_runtime_support/transient_retry.py").read_bytes()
            ).hexdigest(),
        },
        "cases": results,
        "all_passed": all(item.get("passed") for item in results),
    }
    print(json.dumps(payload, indent=2))
    out = HERE.parent / ("loopback" + ("-" + args.label if args.label else "") + ".json")
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
