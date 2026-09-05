"""Replay a real Claude search through the event bus and a loopback WS endpoint."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ciel_runtime_support.observability import EventBus, EventConfig
from ciel_runtime_support.router_http import EventHttpAdapter, EventHttpPorts
from ciel_runtime_support.transcript_delta_delivery import TranscriptDeltaDeliveryService, TranscriptDeliveryPorts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", type=Path)
    parser.add_argument("call_id")
    args = parser.parse_args()
    records = []
    with args.transcript.open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            for block in (record.get("message") or {}).get("content", []):
                if isinstance(block, dict) and (block.get("id") == args.call_id or block.get("tool_use_id") == args.call_id):
                    records.append({"type": record.get("type"), "message": {"content": [block]}})
    if len(records) != 2:
        raise RuntimeError("Expected one call and one result")
    bus = EventBus(EventConfig(True, "info", 100))
    with tempfile.TemporaryDirectory(prefix="ciel-search-stream-") as td:
        root = Path(td)
        path = root / "claude.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        service = TranscriptDeltaDeliveryService(root / "cursors.json", "probe", TranscriptDeliveryPorts(
            load_config=lambda: {"tool_call_events": {"start_mode": "beginning", "include_arguments": False}},
            latest_transcript=lambda: path,
            scope=lambda: {"runtime": "claude", "session_id": "probe"},
            log=lambda *_: None, event_publish=bus.publish, event_recent=bus.recent,
        ))
        assert service.poll_tool_call_events() == 2
        assert service.poll_tool_call_events() == 0

    def end_stream(*_args, **_kwargs):
        raise BrokenPipeError()

    adapter = EventHttpAdapter(EventHttpPorts(bus.recent, end_stream, lambda: "", lambda *_: None, lambda *_: None, lambda *_: None))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            adapter.handle_get(self, parsed.path, parse_qs(parsed.query))

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=5) as connection:
            connection.sendall(b"GET /ca/events/ws?category=tool.call&after=1 HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
            stream = connection.makefile("rb")
            status = stream.readline().decode().strip()
            while stream.readline() != b"\r\n":
                pass
            header = stream.read(2)
            assert header[0] == 0x81
            size = header[1] & 127
            if size in (126, 127):
                size = int.from_bytes(stream.read(2 if size == 126 else 8), "big")
            event = json.loads(stream.read(size))
            assert event["data"]["phase"] == "result"
            assert event["data"]["urls"]
            assert "arguments" not in event["data"]
            print(json.dumps({"http": status, "event": event}, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
