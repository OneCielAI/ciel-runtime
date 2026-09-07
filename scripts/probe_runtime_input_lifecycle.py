#!/usr/bin/env python3
"""Run an isolated HTTP proof for Runtime Input lifecycle transitions."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ciel_runtime_support.chat_http_controller import (
    ChatHttpController,
    ChatHttpReadServices,
    ChatHttpWriteServices,
)
from ciel_runtime_support.observability import EventBus, EventConfig
from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ciel-input-lifecycle-") as raw_dir:
        root = Path(raw_dir)
        bus = EventBus(EventConfig(enabled=True, level="trace", buffer_size=100))
        status = RuntimeInputStatusRepository(
            root / "runtime-input-status.jsonl",
            bus.publish,
            lambda _level, _message: None,
            threading.RLock(),
        )
        public_id = 0
        runtime_id = 72

        def append_public(payload):
            nonlocal public_id
            public_id += 1
            return {"id": public_id, **payload}

        def append_runtime(payload):
            nonlocal runtime_id
            runtime_id += 1
            return {"id": runtime_id, **payload}

        gateway = RuntimeInputGateway(
            append_runtime,
            default_input_transport=lambda: "tty",
            status=status,
        )

        def write_json(handler, payload, code=200):
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            handler.send_response(code)
            handler.send_header("content-type", "application/json; charset=utf-8")
            handler.send_header("content-length", str(len(encoded)))
            handler.end_headers()
            handler.wfile.write(encoded)

        controller = ChatHttpController(
            router_base="http://127.0.0.1",
            reads=ChatHttpReadServices(
                read_after=lambda *_args: [],
                read_before=lambda *_args: [],
                condition=threading.Condition(),
                safe_segment=lambda value, _label: value,
                files_dir=root,
                request_status=status.get,
                request_statuses=status.list_latest,
            ),
            writes=ChatHttpWriteServices(
                write_json=write_json,
                append_message=append_public,
                store_upload=lambda payload: payload,
                submit_message=gateway.submit_web_chat,
                submit_tty=gateway.submit_tty,
                default_input_transport=lambda: "tty",
            ),
        )

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                controller.post(self, urllib.parse.urlparse(self.path).path, body)

            def do_GET(self):
                controller.get(self, urllib.parse.urlparse(self.path).path)

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            request = urllib.request.Request(
                base + "/ca/channel/messages",
                data=json.dumps({"message": "한 번만 전송", "input_transport": "tty"}).encode("utf-8"),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                posted = json.loads(response.read())
            request_id = int(posted["request_id"])
            status.transition(request_id, "submitted")
            status.transition(request_id, "replied")
            with urllib.request.urlopen(
                base + f"/ca/channel/requests/{request_id}", timeout=5
            ) as response:
                queried = json.loads(response.read())
            states = [
                event["data"]["status"]
                for event in bus.recent(category="runtime_input.status")
            ]
            print(json.dumps({
                "post_http": 200,
                "request_id": request_id,
                "post_state": posted["request"]["status"],
                "queried_state": queried["request"]["status"],
                "event_states": states,
            }, ensure_ascii=False, separators=(",", ":")))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
