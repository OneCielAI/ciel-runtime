"""Observe real loopback catalog delays using the product's unpatched policy."""
import os  # noqa: E402
import sys  # noqa: E402
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del os.environ[key]
from pathlib import Path  # noqa: E402
import argparse  # noqa: E402
import urllib.error  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.request  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
parser = argparse.ArgumentParser()
parser.add_argument("root")
parser.add_argument("output")
parser.add_argument("--delay", type=float, default=11)
args = parser.parse_args()
root, output = Path(args.root), Path(args.output)
owned = tempfile.TemporaryDirectory(prefix="ciel-slow-catalog-")
os.environ.update(CIEL_RUNTIME_CONFIG_DIR=owned.name, CIEL_RUNTIME_TEST_ISOLATED="1", HOME=owned.name, USERPROFILE=owned.name)
sys.path.insert(0, str(root))
from ciel_runtime_support.architecture import ProviderConfig  # noqa: E402
from ciel_runtime_support.provider_status import ProviderStatusServices, ProviderStatusRouting, ProviderStatusCatalog, ProviderStatusGeneric, base_url_status_line  # noqa: E402
from ciel_runtime_support.providers.cielairouter import CielAiRouterProviderAdapter  # noqa: E402
requests = []
class Server(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        row = {"started": time.time(), "path": self.path}
        requests.append(row)
        time.sleep(args.delay)
        payload = json.dumps({"data": [{"id": "owned"}]}).encode()
        try:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            row["sent"] = True
        except OSError:
            row["client_closed_after_timeout"] = True
server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
threading.Thread(target=server.serve_forever, daemon=True).start()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
policy = CielAiRouterProviderAdapter().status_policy(ProviderConfig(name="cielairouter", model="owned", base_url="http://127.0.0.1:" + str(server.server_port)))
services = ProviderStatusServices(
    ProviderStatusRouting(lambda *_: False, lambda *_: False, lambda *_: False, lambda *_: "", lambda: False, ""),
    ProviderStatusCatalog(lambda *_: {}, lambda *_args, **_kwargs: {}, lambda base, path: base.rstrip("/") + path, lambda _: "", lambda _: []),
    ProviderStatusGeneric(lambda *_: "", lambda _: False, lambda value: value,
                          lambda req, **kwargs: opener.open(req, timeout=kwargs["timeout"]), lambda *_args, **_kwargs: None))
start = time.monotonic()
status = base_url_status_line("cielairouter", {"base_url": "http://127.0.0.1:" + str(server.server_port)}, policy, services=services)
result = {"root": str(root), "elapsed": time.monotonic() - start, "status": status,
          "initial_timeout": policy.probe_timeout_seconds, "retry_timeout": getattr(policy, "probe_retry_timeout_seconds", 0), "requests": requests}
output.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
server.shutdown()
server.server_close()
