"""Send CloudEvents of increasing size over real HTTP SSE to ExternalEventReceiverService."""
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[7]))
from ciel_runtime_support.external_event_receiver import EventReceiverSecretVault, ExternalEventReceiverService  # noqa: E402

SIZES = [60_000, 65_535, 65_536, 65_537, 131_072, 900_000]


def event(size, index):
    head = {"specversion": "1.0", "id": f"big-{index}", "source": "/repro", "type": "demo", "data": {"blob": ""}}
    pad = size - len(json.dumps(head, separators=(",", ":")))
    head["data"]["blob"] = "x" * max(0, pad)
    return json.dumps(head, separators=(",", ":"))


def run(mode):
    texts = [event(size, i) for i, size in enumerate(SIZES)]
    delivered, logs = [], []
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1" if mode == "chunked" else "HTTP/1.0"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            if mode == "chunked":
                self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            for i, text in enumerate(texts):
                frame = f"id: big-{i}\ndata: {text}\n\n".encode()
                if mode == "chunked":
                    for start in range(0, len(frame), 16_384):
                        part = frame[start:start + 16_384]
                        self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                else:
                    self.wfile.write(frame)
                self.wfile.flush()
            done.wait(5)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = {"external_event_receivers": {"workspace": {}}}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        service = ExternalEventReceiverService(
            load_config=lambda: config,
            save_config=lambda value: config.update(value),
            write_json=lambda *_args: None,
            submit_event=lambda raw, **_meta: delivered.append(len(raw)) or {"id": len(delivered)},
            vault=EventReceiverSecretVault(root / "events.vault.json"),
            workspace_key="workspace",
            log=lambda level, text: logs.append(f"{level} {text}"),
            cursor_path=root / "cursors.json",
        )
        service.save_receiver("default", {"enabled": True, "transport": "sse",
                                          "url": f"http://127.0.0.1:{server.server_port}/events"})
        service.start()
        deadline = time.time() + 15
        while time.time() < deadline and len(delivered) < len(SIZES):
            time.sleep(0.05)
        service.stop()
        done.set()
        server.shutdown()
        server.server_close()
    print(f"mode={mode} sent={SIZES}")
    print(f"mode={mode} delivered_lengths={delivered}")
    print(f"mode={mode} logs={logs}")


if __name__ == "__main__":
    for mode in ("close", "chunked"):
        run(mode)
