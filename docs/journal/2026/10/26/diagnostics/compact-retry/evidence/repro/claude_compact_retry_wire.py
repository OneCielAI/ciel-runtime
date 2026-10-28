"""Owned-CLI wire probe: what does Claude Code send when /compact is retried
after a failed /compact?

Real native Claude Code, empty temp HOME/config/cwd, loopback Anthropic
fixture. No product imports, no live router, no external network. Prompt
bodies are synthetic and are stored in the run directory for comparison.
Run with python -I and an absolute path.
"""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, tempfile, threading, time, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
MODEL = "claude-sonnet-5-5"
NO_MCP = json.dumps({"mcpServers": {}})


def now():
    return datetime.now(timezone.utc).isoformat()


def sse(kind, value):
    return ("event: " + kind + "\ndata: " + json.dumps(value) + "\n\n").encode()


def reply(text):
    msg = dict(id="msg_" + uuid.uuid4().hex[:20], type="message", role="assistant", model=MODEL,
               content=[], stop_reason=None, stop_sequence=None, usage=dict(input_tokens=10, output_tokens=0))
    return b"".join([
        sse("message_start", dict(type="message_start", message=msg)),
        sse("content_block_start", dict(type="content_block_start", index=0, content_block=dict(type="text", text=""))),
        sse("content_block_delta", dict(type="content_block_delta", index=0, delta=dict(type="text_delta", text=text))),
        sse("content_block_stop", dict(type="content_block_stop", index=0)),
        sse("message_delta", dict(type="message_delta", delta=dict(stop_reason="end_turn", stop_sequence=None), usage=dict(output_tokens=5))),
        sse("message_stop", dict(type="message_stop")),
    ])


def is_compact(body):
    # Claude Code 2.1.310 puts its compact instruction in the last user turn,
    # followed by a role=system token-budget note.
    text = json.dumps(body.get("messages", [])[-3:], ensure_ascii=False)
    return "CRITICAL: Respond with TEXT ONLY" in text and "summary" in text.lower()


class Fixture:
    def __init__(self, directory, fail_mode):
        self.directory, self.fail_mode = directory, fail_mode
        self.lock = threading.Lock()
        self.count = 0
        self.compact_count = 0
        self.stop = threading.Event()

    def record(self, **row):
        row = dict(at=now(), **row)
        with self.lock, (self.directory / "wire.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def handler(self):
        fx = self

        class H(BaseHTTPRequestHandler):
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
                self.close_connection = True

            def do_GET(self):
                self.fixed(dict(data=[dict(id=MODEL, type="model", display_name="fixture")], has_more=False)
                           if urlsplit(self.path).path.startswith("/v1/models") else {})

            def do_POST(self):
                path = urlsplit(self.path).path
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {}
                if path.endswith("/count_tokens"):
                    return self.fixed(dict(input_tokens=10))
                if path != "/v1/messages":
                    return self.fixed({})
                with fx.lock:
                    fx.count += 1
                    n = fx.count
                compact = is_compact(body)
                (fx.directory / ("request-%02d.json" % n)).write_bytes(raw)
                msgs = body.get("messages", [])
                fx.record(kind="messages_request", n=n, compact=compact, stream=body.get("stream"),
                          message_count=len(msgs), bytes=len(raw),
                          messages_sha256=hashlib.sha256(json.dumps(msgs, sort_keys=True).encode()).hexdigest(),
                          system_sha256=hashlib.sha256(json.dumps(body.get("system"), sort_keys=True).encode()).hexdigest())
                if compact:
                    with fx.lock:
                        fx.compact_count += 1
                        c = fx.compact_count
                    if c == 1 and fx.fail_mode == "http500":
                        fx.record(kind="compact_fail", n=n, mode="http500")
                        return self.fixed(dict(type="error", error=dict(type="api_error", message="fixture compact failure")), 500)
                    if c == 1 and fx.fail_mode == "idle":
                        # Headers, then only SSE comments: the client event watchdog aborts.
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.flush()
                        fx.record(kind="compact_idle_headers", n=n)
                        started = time.monotonic()
                        try:
                            while not fx.stop.wait(5):
                                self.wfile.write(b": keep\n\n")
                                self.wfile.flush()
                        except OSError as exc:
                            fx.record(kind="compact_client_disconnect", n=n, after_s=round(time.monotonic() - started, 1), error=type(exc).__name__)
                        return
                    text = "<summary>fixture compact summary</summary>"
                else:
                    text = "fixture reply %d" % n
                if body.get("stream") is not True:
                    return self.fixed(dict(id="msg_ns", type="message", role="assistant", model=MODEL,
                                           content=[dict(type="text", text=text)], stop_reason="end_turn",
                                           usage=dict(input_tokens=10, output_tokens=5)))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(reply(text))
                self.wfile.flush()
                self.close_connection = True
        return H


def env_for(root, port):
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    for name in ("home", "config", "cwd", "appdata", "localappdata", "tmp"):
        (root / name).mkdir(exist_ok=True)
    env.update(HOME=str(root / "home"), USERPROFILE=str(root / "home"), APPDATA=str(root / "appdata"),
               LOCALAPPDATA=str(root / "localappdata"), TEMP=str(root / "tmp"), TMP=str(root / "tmp"),
               CLAUDE_CONFIG_DIR=str(root / "config"), ANTHROPIC_BASE_URL="http://127.0.0.1:%d" % port,
               ANTHROPIC_API_KEY="fixture-key-not-a-real-credential", CLAUDE_CODE_MAX_RETRIES="0",
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1",
               CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK="1", NO_PROXY="127.0.0.1,localhost", NO_COLOR="1")
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--executable", type=Path, required=True)
    ap.add_argument("--fail-mode", choices=("http500", "idle"), default="http500")
    ap.add_argument("--run-name", default="run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    a = ap.parse_args()
    run = ROOT / a.run_name
    run.mkdir()
    fx = Fixture(run, a.fail_mode)
    server = ThreadingHTTPServer(("127.0.0.1", 0), fx.handler())
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    session = str(uuid.uuid4())
    steps = [("seed-1", ["-p", "First synthetic message: remember the word alpha.", "--session-id", session]),
             ("seed-2", ["-p", "Second synthetic message: remember the word beta.", "--resume", session]),
             ("compact-1", ["-p", "/compact", "--resume", session]),
             ("compact-2", ["-p", "/compact", "--resume", session])]
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="owned-compact-retry-") as tmp:
            root = Path(tmp)
            env = env_for(root, server.server_port)
            for name, extra in steps:
                argv = [str(a.executable), *extra, "--model", MODEL, "--tools", "", "--strict-mcp-config",
                        "--mcp-config", NO_MCP, "--setting-sources", "", "--output-format", "json"]
                fx.record(kind="launch", step=name, argv=argv)
                t = time.monotonic()
                p = subprocess.run(argv, cwd=root / "cwd", env=env, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
                row = dict(step=name, rc=p.returncode, elapsed_s=round(time.monotonic() - t, 1),
                           stdout=p.stdout[-1500:], stderr=p.stderr[-1500:])
                fx.record(kind="step_done", **row)
                results.append(row)
            for f in (root / "config" / "projects").rglob(session + ".jsonl"):
                (run / "transcript.jsonl").write_bytes(f.read_bytes())
    finally:
        fx.stop.set()
        server.shutdown()
    (run / "steps.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps([dict(step=r["step"], rc=r["rc"], elapsed_s=r["elapsed_s"]) for r in results]))


if __name__ == "__main__":
    main()
