#!/usr/bin/env python3
"""Owned loopback socket acceptance; no runtime service or provider calls."""
import os
# Isolate credentials/routing before importing repository modules.
removed = 0
for key in list(os.environ):
    if key.upper().startswith("CIEL_RUNTIME_") or any(x in key.upper() for x in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH", "PROXY")):
        os.environ.pop(key, None)
        removed += 1
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
import sys
sys.dont_write_bytecode = True
import hashlib
import http.client
import http.server
import json
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from contextlib import contextmanager
ROOT = next(p for p in Path(__file__).resolve().parents if (p / "ciel_runtime_support").is_dir())
sys.path[:0] = [str(ROOT / "tests"), str(ROOT)]
from test_codex_backend_item_repair import adapter_for, FakeHandler

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *_args):
        pass
    def do_POST(self):
        self.connection.settimeout(3)
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.calls += 1
        if self.server.calls > len(self.server.plans):
            self.send_error(500)
            return
        status, payload, broken = self.server.plans[self.server.calls - 1]
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        if broken:
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()
        self.close_connection = True

cleanup = []
@contextmanager
def owned_server(plans):
    # Single server thread also handles requests; no unowned worker threads.
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.plans, server.calls = plans, 0
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), name="acceptance-owned-http", daemon=False)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive(), "owned thread failed to stop"
        assert server.socket.fileno() == -1, "listener not closed"
        cleanup.append({"thread_joined": True, "listener_closed": True})

# Only loopback connect permitted, including any attempted redirects.
connects = []
def audit(event, args):
    if event == "socket.connect":
        address = args[1]
        if address[0] != "127.0.0.1":
            raise RuntimeError("non-loopback connect blocked")
        connects.append(address[1])
sys.addaudithook(audit)
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

def error_body(tag="fixture"):
    value = {"type":"error", "error":{"type":"server_error", "message":tag + ": upstream reported a failed response without usable output (HTTP 502)"}}
    return b"event: error" + bytes([10]) + b"data: " + json.dumps(value).encode() + bytes([10,10])
def sse(kind, value):
    return ("event: " + kind + chr(10) + "data: " + json.dumps(value) + chr(10)*2).encode()
ERROR = error_body()
OUTPUT = sse("response.output_text.delta", {"type":"response.output_text.delta", "delta":"synthetic accepted output"})
SUCCESS = OUTPUT + sse("response.completed", {"type":"response.completed", "response":{"status":"completed", "output":[{"type":"message", "role":"assistant", "content":[{"type":"output_text", "text":"synthetic accepted output"}]}]}})
BODY = {"model":"synthetic-loopback-model", "input":[], "stream":True}
results = {}

def run_case(name, plans, retries, expected_calls, expected_output=None, expect_broken=False):
    logs, waits, attempts = [], [], []
    handler = FakeHandler()
    final_error = exception_type = final_preserved = None
    with owned_server(plans) as server:
        port = server.server_address[1]
        def real_urlopen(request, *, timeout, provider, pcfg):
            assert request.full_url == "http://127.0.0.1:" + str(port) + "/backend-api/codex/responses"
            assert handler.status is None and handler.wfile.written == b"", "replayed after downstream open"
            assert request.get_header("Authorization") is None
            attempts.append(1)
            return urllib.request.urlopen(request, timeout=timeout)
        adapter = adapter_for(real_urlopen, logs, transport_retries=retries, classify_transport=lambda _error: True)
        adapter._upstream_base = "http://127.0.0.1:" + str(port) + "/backend-api/codex"
        adapter._request = replace(adapter._request, upstream_headers=lambda _config,_headers: {}, request_timeout=lambda _config: 3.0, overload_budget=lambda: 0.0)
        adapter._retry = replace(adapter._retry, sleep=waits.append)
        try:
            adapter.forward_json(handler,"codex",{},BODY)
        except urllib.error.HTTPError as exc:
            exception_type, final_error = type(exc).__name__, exc.code
            final_preserved = exc.read() == plans[-1][1]
            exc.close()
        except Exception as exc:
            exception_type = type(exc).__name__
        assert server.calls == expected_calls and len(attempts) == expected_calls, name + " request count"
        assert len(waits) == expected_calls - 1
        if expected_output is not None:
            assert exception_type is None and handler.status == 200 and handler.wfile.written == expected_output
        elif expect_broken:
            assert handler.status == 200 and exception_type == "IncompleteRead", str(exception_type)
        else:
            assert final_error == 502 and final_preserved and handler.status is None and handler.wfile.written == b""
        results[name] = {"passed":True, "server_requests":server.calls, "urlopen_attempts":len(attempts), "retry_limit":retries, "retry_wait_count":len(waits), "downstream_status":handler.status, "downstream_bytes":len(handler.wfile.written), "exception_type":exception_type, "terminal_http_status":final_error, "terminal_body_preserved":final_preserved, "success_output_exact":expected_output is not None and handler.wfile.written == expected_output}
        results[name]["retry_logs"] = [m for _level,m in logs if m.startswith("codex_no_output_http_retry ")]
        assert len(results[name]["retry_logs"]) == len(waits)

def main():
    paths = [ROOT/"ciel_runtime_support/router_http.py", ROOT/"tests/test_codex_backend_item_repair.py", ROOT/"tests/test_codex_no_output_http_retry.py"]
    before = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    baseline = {t.ident for t in threading.enumerate()}
    failure = None
    try:
        run_case("explicit_error_only_sse_502_then_output_200",[(502,ERROR,False),(200,SUCCESS,False)],1,2,expected_output=SUCCESS)
        run_case("repeated_explicit_errors_bounded",[(502,error_body("attempt-"+str(i)),False) for i in range(3)],2,3)
        run_case("explicit_error_zero_retry_budget",[(502,ERROR,False)],0,1)
        ambiguous = json.dumps({"error":{"type":"server_error","message":"gateway failed"}}).encode()
        run_case("ambiguous_502",[(502,ambiguous,False)],2,1)
        value = {"type":"error","error":{"type":"server_error","message":"upstream reported a failed response without usable output"},"output":[{"type":"function_call"}]}
        run_case("output_bearing_json_error",[(502,json.dumps(value).encode(),False)],2,1)
        run_case("output_bearing_sse_error",[(502,OUTPUT+ERROR,False)],2,1)
        first = OUTPUT + b":" + b" " * (65536-len(OUTPUT)-3) + bytes([10,10])
        assert len(first)==65536
        run_case("after_open_broken_chunked_stream",[(200,b"10000"+bytes([13,10])+first+bytes([13,10]),True)],2,1,expect_broken=True)
        assert results["after_open_broken_chunked_stream"]["downstream_bytes"]==65536
    except Exception as exc:
        failure = type(exc).__name__
    after = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    thread_cleanup = not any(t.ident not in baseline for t in threading.enumerate())
    result = {"passed":failure is None and len(results)==7 and thread_cleanup and before==after,"failure_type":failure,"transport":"real urllib.request.urlopen; owned HTTPServer on 127.0.0.1 port 0","environment_cleaned_before_repository_imports":True,"removed_environment_variable_count":removed,"runtime_env_remaining":sum(k.upper().startswith("CIEL_RUNTIME_") for k in os.environ),"credential_headers_sent":False,"network_guard":"socket.connect audit denies non-127.0.0.1; proxies disabled","socket_connect_count":len(connects),"owned_listener_count":len(set(connects)),"cleanup":cleanup,"no_new_threads_remaining":thread_cleanup,"production_and_tests_unchanged_during_run":before==after,"source_sha256":before,"cases":results}
    directory = Path(__file__).resolve().parent
    (directory/"result.json").write_text(json.dumps(result,indent=2)+chr(10),encoding="utf-8")
    lines = ["Loopback HTTP acceptance: "+("PASS" if result["passed"] else "FAIL"),"Payload-free evidence. Real urllib sockets; no provider or remote-host calls."]
    for name,row in results.items():
        lines.append(name+": PASS requests="+str(row["server_requests"])+" retries="+str(row["retry_wait_count"])+" downstream_status="+str(row["downstream_status"])+" exception="+str(row["exception_type"])+" body_preserved="+str(row["terminal_body_preserved"]))
        lines.extend(row["retry_logs"])
    lines.append("Cleanup: listener_close_and_thread_join="+str(len(cleanup))+"; no_new_threads_remaining="+str(thread_cleanup))
    lines.append("Production/tests unchanged during run="+str(before==after))
    (directory/"run.log").write_text(chr(10).join(lines)+chr(10),encoding="utf-8")
    print(chr(10).join(lines))
    return 0 if result["passed"] else 1
if __name__ == "__main__":
    raise SystemExit(main())
