"""Launch-time selection against local HTTP stubs (central discovery + gateway /healthz).

Records which requests carried an Authorization header; prints only secret-free facts.
"""
import copy
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, r"C:\Users\djlov\ciel-runtime")
SECRET = "sk-stub-router-key-0123456789abcdef"
seen = []


def serve(handler_body):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            auth = self.headers.get("Authorization") or ""
            seen.append({"server": handler_body.__name__, "path": self.path,
                         "authorization": "bearer-router-key" if auth == f"Bearer {SECRET}" else ("other" if auth else "none")})
            status, payload = handler_body(self.path, auth)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def gateway(path, _auth):
    if path == "/healthz":
        return 200, {"nodeId": "stub-node-1", "role": "gateway", "ready": True}
    return 404, {}


gw = serve(gateway)
gw_url = f"http://127.0.0.1:{gw.server_address[1]}"


def central(path, auth):
    if path != "/api/gateway/discovery" or auth != f"Bearer {SECRET}":
        return 401, {}
    now = datetime.now(timezone.utc)
    stamp = now.isoformat().replace("+00:00", "Z")
    return 200, {"object": "list", "protocol_version": 1, "generated_at": stamp, "data": [
        {"id": "stub-node-1", "endpoint": gw_url, "status": "online", "health": "healthy",
         "lastSeenAt": stamp, "availableUntil": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")},
        {"id": "remote-node", "endpoint": "http://10.255.0.9:20128", "status": "online", "health": "healthy",
         "lastSeenAt": stamp, "availableUntil": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")},
    ]}


ce = serve(central)
central_url = f"http://127.0.0.1:{ce.server_address[1]}"

import ciel_runtime  # noqa: E402
from ciel_runtime_support import launch_data_path  # noqa: E402

logs, output = [], []
with TemporaryDirectory() as temp:
    record = Path(temp) / "launch-data-path.json"
    resolver = launch_data_path.LaunchDataPathResolver(ciel_runtime.provider_configuration_policy,
                                                       lambda level, message: logs.append(f"{level} {message}"),
                                                       output=output.append, record_path=record)
    pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["cielairouter"])
    pcfg.update(base_url=central_url, api_key=SECRET, current_model="ASTRA", gateway_data_path="direct")
    route = resolver("claude", "cielairouter", pcfg)
    print("route:", repr(route))
    print("direct:", route.direct, "| endpoint == stub gateway:", route.endpoint == gw_url)
    off = resolver("codex", "cielairouter", {**pcfg, "gateway_selection": "off"})
    print("selection off:", off.summary())
    default = resolver("claude", "cielairouter", {k: v for k, v in pcfg.items() if k != "gateway_data_path"})
    print("default setting:", default.summary())
    cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
    cfg["current_provider"] = "cielairouter"
    cfg["providers"]["cielairouter"] = pcfg
    ids = ["ASTRA", "cc/claude-opus-5-5"]
    with mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=ids), \
         mock.patch.object(ciel_runtime, "cached_or_configured_model_ids", return_value=ids):
        routed_env = ciel_runtime.env_vars(cfg)
        direct_env = ciel_runtime.env_vars(cfg, data_path=route)
    keys = ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "CIEL_RUNTIME_MODEL_ALIAS")
    for key in keys:
        print(f"claude {key}: router={routed_env.get(key)} direct={direct_env.get(key).replace(gw_url, '<stub-gateway>')}")
    print("claude ANTHROPIC_AUTH_TOKEN is router key (router/direct):",
          routed_env.get("ANTHROPIC_AUTH_TOKEN") == SECRET, direct_env.get("ANTHROPIC_AUTH_TOKEN") == SECRET)
    codex_args = ciel_runtime.codex_runtime_config_args(route.endpoint, env_key=launch_data_path.GATEWAY_API_KEY_ENV)
    print("codex provider args:", [a.replace(gw_url, "<stub-gateway>") for a in codex_args if a != "-c"])
    written = " ".join([*logs, *output, record.read_text(encoding="utf-8"), repr(route), *codex_args,
                        *launch_data_path.status_lines(pcfg, record)])
    print("key present in logs/output/record/repr/codex argv/status:", SECRET in written)
    print("log lines:")
    for line in logs:
        print("  ", line.replace(gw_url, "<stub-gateway>").replace(central_url.split("//")[1], "<stub-central>"))
    print("status lines:")
    for line in launch_data_path.status_lines(pcfg, record):
        print("  ", line.replace(gw_url, "<stub-gateway>"))
print("stub requests:")
for item in seen:
    print("  ", item)
