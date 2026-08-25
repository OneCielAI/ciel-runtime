"""Run an isolated Ciel router against a recording Z.AI-compatible upstream."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[6]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def main() -> int:
    captured: dict[str, object] = {}

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            captured.update(
                path=self.path,
                authorization=self.headers.get("authorization"),
                x_api_key=self.headers.get("x-api-key"),
                body=json.loads(self.rfile.read(length)),
            )
            response = json.dumps(
                {
                    "id": "resp_router_evidence",
                    "object": "response",
                    "status": "completed",
                    "model": "glm-5.3",
                    "output": [],
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    upstream_port = free_port()
    router_port = free_port()
    upstream = ThreadingHTTPServer(("127.0.0.1", upstream_port), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with tempfile.TemporaryDirectory(prefix="ciel-zai-router-") as directory:
        config_dir = Path(directory)
        config = {
            "current_provider": "zai-coding-plan",
            "providers": {
                "zai-coding-plan": {
                    "base_url": f"http://127.0.0.1:{upstream_port}",
                    "openai_responses_base_url": f"http://127.0.0.1:{upstream_port}",
                    "anthropic_base_url": f"http://127.0.0.1:{upstream_port}",
                    "api_key": "router-evidence-key",
                    "current_model": "glm-5.3",
                    "native_compat": True,
                    "gateway_retries": 0,
                    "request_timeout_ms": 10000,
                }
            },
        }
        (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        env = {
            **os.environ,
            "CIEL_RUNTIME_CONFIG_DIR": str(config_dir),
            "CIEL_RUNTIME_TEST_ISOLATED": "1",
            "CIEL_RUNTIME_ROUTER_PORT": str(router_port),
            "CIEL_RUNTIME_ROUTER_HEALTH_TIMEOUT_SECONDS": "1",
        }
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "ciel_runtime.py"), "serve"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            health_url = f"http://127.0.0.1:{router_port}/health"
            for _ in range(100):
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    raise RuntimeError(
                        f"router exited {process.returncode}: {stdout}\n{stderr}"
                    )
                try:
                    with urllib.request.urlopen(health_url, timeout=0.2):
                        break
                except Exception:
                    time.sleep(0.05)
            else:
                raise RuntimeError("router health endpoint did not become ready")
            request_body = json.dumps(
                {
                    "model": "ciel-runtime-zai-coding-plan-glm-5.3",
                    "input": "reply exactly ROUTER_OK",
                    "stream": False,
                    "thinking": {"type": "disabled"},
                    "reasoning_effort": "xhigh",
                    "temperature": 0.2,
                }
            ).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{router_port}/v1/responses",
                data=request_body,
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                downstream = json.loads(response.read())
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            upstream.shutdown()
            upstream.server_close()

    body = captured.get("body")
    assert isinstance(body, dict)
    evidence = {
        "router_status": downstream["status"],
        "upstream_path": captured["path"],
        "authorization": captured["authorization"],
        "x_api_key": captured["x_api_key"],
        "model": body["model"],
        "thinking": body["thinking"],
        "reasoning_effort": body["reasoning_effort"],
        "temperature": body["temperature"],
    }
    print(json.dumps(evidence, indent=2, sort_keys=True))
    assert evidence == {
        "authorization": "Bearer router-evidence-key",
        "model": "glm-5.3",
        "reasoning_effort": "max",
        "router_status": "completed",
        "temperature": 1.0,
        "thinking": {"type": "enabled"},
        "upstream_path": "/v1/responses",
        "x_api_key": "router-evidence-key",
    }
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
