"""Standalone protocol proof: owned CLI, empty temp config/cwd, loopback fixture.
No product imports, live changes, external injection, or production settings.
Official ping frame: https://platform.claude.com/docs/en/api/messages-streaming
The chosen 5s cadence is a harness parameter, not an API documentation guarantee.
Run python -I with this absolute path. Durable outputs stay in a fresh run dir.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
MODEL = "claude-sonnet-5-5"
MARKER = "fixture_liveness_ok"


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def event(kind, value):
    return (f"event: {kind}\ndata: " + json.dumps(value) + "\n\n").encode()


def message():
    msg = dict(id="msg_owned_liveness", type="message", role="assistant", model=MODEL,
               content=[], stop_reason=None, stop_sequence=None,
               usage=dict(input_tokens=1, output_tokens=0))
    return b"".join([
        event("message_start", dict(type="message_start", message=msg)),
        event("content_block_start", dict(type="content_block_start", index=0,
              content_block=dict(type="text", text=""))),
        event("content_block_delta", dict(type="content_block_delta", index=0,
              delta=dict(type="text_delta", text=MARKER))),
        event("content_block_stop", dict(type="content_block_stop", index=0)),
        event("message_delta", dict(type="message_delta",
              delta=dict(stop_reason="end_turn", stop_sequence=None), usage=dict(output_tokens=1))),
        event("message_stop", dict(type="message_stop")),
    ])


class Fixture:
    def __init__(self, directory, mode, delay_s, ping_s):
        self.directory, self.mode = directory, mode
        self.delay_s, self.ping_s = delay_s, ping_s
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.events = []
        self.started = time.monotonic()

    def record(self, kind, **data):
        row = dict(at=now(), elapsed_s=round(time.monotonic()-self.started, 4), kind=kind, **data)
        with self.lock:
            self.events.append(row)
            with (self.directory / "wire.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")

    def handler(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def fixed(self, value, status=200):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
                self.wfile.flush()
                self.close_connection = True

            def do_GET(self):
                path = urlsplit(self.path).path
                fixture.record("ancillary_get", path=path)
                if path.startswith("/v1/models"):
                    self.fixed(dict(data=[dict(id=MODEL, type="model", display_name="Owned fixture")], has_more=False))
                else:
                    self.fixed({})

            def do_POST(self):
                self.close_connection = True
                path = urlsplit(self.path).path
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {}
                if path.endswith("/count_tokens"):
                    fixture.record("count_tokens", path=path)
                    return self.fixed(dict(input_tokens=1))
                if path != "/v1/messages":
                    fixture.record("ancillary_post", path=path)
                    return self.fixed({})
                # Never persist header values or prompt bodies.
                received = time.monotonic()
                fixture.record("messages_request", path=path, stream=body.get("stream"),
                               tools_count=len(body.get("tools", [])), request_bytes=len(raw))
                if body.get("stream") is not True:
                    fixture.record("nonstream_rejected")
                    return self.fixed(dict(type="error", error=dict(type="invalid_request_error",
                                      message="Owned fixture requires streaming; no fallback")), 400)
                try:
                    if fixture.mode == "delayed_headers" and fixture.stop.wait(fixture.delay_s):
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.flush()
                    fixture.record("headers_flushed", request_elapsed_s=round(time.monotonic()-received, 4))
                    if fixture.mode == "early_sse":
                        deadline = received + fixture.delay_s
                        next_ping = time.monotonic()
                        while time.monotonic() < deadline:
                            if fixture.stop.wait(max(0, min(next_ping, deadline)-time.monotonic())):
                                return
                            if time.monotonic() >= deadline:
                                break
                            self.wfile.write(event("ping", dict(type="ping")))
                            self.wfile.flush()
                            fixture.record("ping_flushed", request_elapsed_s=round(time.monotonic()-received, 4))
                            next_ping += fixture.ping_s
                    self.wfile.write(message())
                    self.wfile.flush()
                    fixture.record("real_message_flushed", request_elapsed_s=round(time.monotonic()-received, 4))
                    self.close_connection = True
                except OSError as exc:
                    fixture.record("client_disconnect", error_type=type(exc).__name__)
        return Handler


def environment(root, port, timeout_ms):
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE",
            "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    for name in ("home", "config", "cwd", "appdata", "localappdata", "tmp"):
        (root/name).mkdir()
    env.update(HOME=str(root/"home"), USERPROFILE=str(root/"home"),
               APPDATA=str(root/"appdata"), LOCALAPPDATA=str(root/"localappdata"),
               TEMP=str(root/"tmp"), TMP=str(root/"tmp"), CLAUDE_CONFIG_DIR=str(root/"config"),
               ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}",
               ANTHROPIC_API_KEY="fixture-key-not-a-real-credential",
               API_TIMEOUT_MS=str(timeout_ms), CLAUDE_CODE_MAX_RETRIES="0",
               CLAUDE_CODE_RETRY_WATCHDOG="0", CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES="0",
               CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK="1",
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1",
               NO_PROXY="127.0.0.1,localhost", NO_COLOR="1")
    return env


def run_case(exe, directory, mode, delay_s, ping_s, timeout_ms):
    directory.mkdir()
    fixture = Fixture(directory, mode, delay_s, ping_s)
    server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.handler())
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="owned-claude-liveness-") as tmp:
            root = Path(tmp)
            env = environment(root, server.server_port, timeout_ms)
            argv = [str(exe), "--bare", "-p", "Reply only with the fixture response.",
                    "--tools", "", "--strict-mcp-config", "--mcp-config", "{\"mcpServers\":{}}",
                    "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence",
                    "--model", MODEL, "--max-turns", "1", "--output-format", "json"]
            fixture.record("launch", argv=argv, api_timeout_ms=timeout_ms,
                           config_empty=not any((root/"config").iterdir()),
                           cwd_empty=not any((root/"cwd").iterdir()), environment_keys=sorted(env),
                           controls={k: v for k, v in env.items() if k.startswith("CLAUDE_CODE_")})
            with (directory/"stdout.txt").open("w", encoding="utf-8") as out, (directory/"stderr.txt").open("w", encoding="utf-8") as err:
                proc = subprocess.Popen(argv, cwd=root/"cwd", env=env, stdin=subprocess.DEVNULL,
                                        stdout=out, stderr=err)
                fixture.record("owned_child_started", pid=proc.pid)
                try:
                    rc = proc.wait(timeout=delay_s + timeout_ms/1000 + 60)
                    harness_timeout = False
                except subprocess.TimeoutExpired:
                    proc.kill()  # Only this owned child, never process-name kill.
                    rc = proc.wait(timeout=15)
                    harness_timeout = True
            stdout = (directory/"stdout.txt").read_text(encoding="utf-8")
            stderr = (directory/"stderr.txt").read_text(encoding="utf-8")
            elapsed = round(time.monotonic()-started, 4)
            with fixture.lock:
                events = list(fixture.events)
            requests = [r for r in events if r["kind"] == "messages_request"]
            completed = [r for r in events if r["kind"] == "real_message_flushed"]
            headers = [r for r in events if r["kind"] == "headers_flushed"]
            try:
                cli_result = json.loads(stdout)
            except ValueError:
                cli_result = {}
            pings = [r["request_elapsed_s"] for r in events if r["kind"] == "ping_flushed"]
            ping_gaps = [b-a for a,b in zip(pings, pings[1:])]
            timeout_text = any(x in (stdout+stderr).lower() for x in ("timed out", "timeout"))
            checks = dict(one_message_request=len(requests)==1,
                          streaming=bool(requests) and all(r["stream"] is True for r in requests),
                          no_tools=bool(requests) and all(r["tools_count"]==0 for r in requests),
                          no_harness_timeout=not harness_timeout)
            if mode == "delayed_headers":
                checks.update(cli_timeout_error=rc != 0 and timeout_text,
                              no_headers_before_exit=not headers, no_real_message=not completed)
            else:
                checks.update(exit_zero=rc==0, marker_returned=cli_result.get("result")==MARKER,
                              cli_success=cli_result.get("is_error") is False,
                              pings_within_ten_seconds=bool(pings) and max(ping_gaps, default=0)<=10,
                              real_message_after_delay=bool(completed) and completed[0]["request_elapsed_s"]>=delay_s,
                              immediate_headers=bool(headers) and headers[0]["request_elapsed_s"]<1,
                              pings_observed=any(r["kind"]=="ping_flushed" for r in events))
            result = dict(mode=mode, delay_s=delay_s, ping_s=ping_s, api_timeout_ms=timeout_ms,
                          returncode=rc, elapsed_s=elapsed, checks=checks, passed=all(checks.values()),
                          ping_count=len(pings), max_ping_gap_s=max(ping_gaps, default=0),
                          first_real_message_s=completed[0]["request_elapsed_s"] if completed else None)
            (directory/"result.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
            print(json.dumps(result), flush=True)
            return result
    finally:
        fixture.stop.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--api-timeout-ms", type=int, default=10000)
    parser.add_argument("--baseline-delay-s", type=float, default=20)
    parser.add_argument("--delay-s", type=float, default=365)
    parser.add_argument("--ping-s", type=float, default=5)
    parser.add_argument("--run-name", default="run-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args()
    if args.api_timeout_ms <= 0 or args.baseline_delay_s <= args.api_timeout_ms/1000:
        parser.error("baseline must exceed positive API_TIMEOUT_MS")
    if not 1 <= args.ping_s <= 10 or args.ping_s >= args.api_timeout_ms/1000:
        parser.error("ping interval must be 1-10s and below selected timeout")
    if args.delay_s <= 0 or Path(args.run_name).name != args.run_name or args.run_name in (".", "..", ""):
        parser.error("positive delay and plain run-directory name required")
    candidates = [Path.home()/".local/share/claude/versions/2.1.308.exe",
                  Path.home()/".local/share/claude/versions/2.1.308"]
    exe = (args.executable or next((p for p in candidates if p.is_file()), candidates[0])).resolve()
    directory = ROOT/args.run_name
    directory.mkdir(exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="owned-claude-metadata-") as tmp:
        root = Path(tmp)
        env = environment(root, 1, args.api_timeout_ms)
        version = subprocess.run([str(exe), "--version"], env=env, cwd=root/"cwd",
                                 capture_output=True, text=True, timeout=30)
        help_result = subprocess.run([str(exe), "--help"], env=env, cwd=root/"cwd",
                                     capture_output=True, text=True, timeout=30)
    (directory/"native-help.txt").write_text(help_result.stdout+help_result.stderr, encoding="utf-8")
    metadata = dict(started_at=now(), executable=str(exe), sha256=digest(exe),
                    version=version.stdout.strip(), version_exit=version.returncode,
                    script_sha256=digest(Path(__file__)), parameters={k: str(v) for k, v in vars(args).items()},
                    official_ping_reference="https://platform.claude.com/docs/en/api/messages-streaming",
                    isolation="OS environment allowlist; empty HOME/config/cwd; --bare; no settings/tools/MCP/persistence",
                    limitation="Protocol proof, not runtime integration or live Celly modification")
    (directory/"metadata.json").write_text(json.dumps(metadata, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(metadata), flush=True)
    if version.returncode or version.stdout.strip() != "2.1.308 (Claude Code)":
        raise RuntimeError("Expected native Claude 2.1.308")
    results = []
    for name, mode, delay in (("baseline", "delayed_headers", args.baseline_delay_s),
                              ("stream", "early_sse", args.delay_s)):
        results.append(run_case(exe, directory/name, mode, delay, args.ping_s, args.api_timeout_ms))
        (directory/"results.json").write_text(json.dumps(results, indent=2)+"\n", encoding="utf-8")
    summary = dict(finished_at=now(), passed=all(r["passed"] for r in results),
                   six_minute_claim=all(r["passed"] for r in results) and args.delay_s > 360,
                   results=results)
    (directory/"summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary), flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
