"""Real loopback HTTP SSE reproduction for the 64KiB watcher boundary."""
import json
import subprocess
import sys
import threading
import types
import urllib.request
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[7]
sys.path.insert(0, str(ROOT))
from ciel_runtime_support import cielairouter_gateway_transport as current  # noqa: E402


def run(module):
    wire = [b""]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire[0])))
            self.end_headers()
            for start in range(0, len(wire[0]), 997):
                self.wfile.write(wire[0][start:start + 997])
                self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    rows = []
    try:
        for size in (65535, 65536, 65537, 131072, 900000):
            prefix = b'event: response.completed\ndata: {"type":"response.completed","padding":"'
            suffix = b'"}'
            wire[0] = prefix + b"x" * (size - len(prefix) - len(suffix) + 1) + suffix + b"\n\n"
            observations = []
            with module.wrap_gateway_response(
                opener.open(f"http://127.0.0.1:{server.server_port}/v1/responses"),
                "responses", on_result=observations.append,
            ) as response:
                output = bytearray()
                while part := response.read(113):
                    output.extend(part)
            rows.append({
                "event_nonnewline_bytes": size,
                "wire_bytes": len(wire[0]),
                "wire_preserved": bytes(output) == wire[0],
                "gateway_observation": [asdict(item) for item in observations],
            })
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    return rows


if __name__ == "__main__":
    source = subprocess.check_output(
        ["git", "show", "HEAD:ciel_runtime_support/cielairouter_gateway_transport.py"],
        cwd=ROOT, text=True,
    )
    old = types.ModuleType("gateway_baseline")
    sys.modules[old.__name__] = old
    exec(compile(source, "<HEAD transport>", "exec"), old.__dict__)
    print(json.dumps({"baseline_HEAD": run(old), "working_tree": run(current)}, indent=2))
