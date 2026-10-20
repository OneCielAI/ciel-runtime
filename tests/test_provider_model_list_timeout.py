"""Model-list fetch against a slow or unreachable provider (Walkie 49376 / 53394).

A cold CielAiRouter answers its first GET /v1/models after 2-6 s.  These
tests run the real fetch path (urllib, no mocked HTTP) against a local server
and check the timeout, the single retry and the "list unavailable" wording.
"""

import copy
import json
import socket
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import ciel_runtime
from ciel_runtime_support import provider_models


API_KEY = "router-test-secret-49376"
REQUESTED = "ciel-Opus 5.5"
CATALOG = {
    "object": "list",
    "data": [
        {
            "id": "ASTRA", "owned_by": "combo", "root": "ASTRA",
            "capabilities": {"tool_calling": True, "reasoning": True, "thinking": True},
        },
        {
            "id": REQUESTED, "owned_by": "claude", "root": REQUESTED,
            "capabilities": {"tool_calling": True, "reasoning": True, "thinking": True},
        },
    ],
}
NOT_IN_CATALOG = "is not in the provider catalog"


class CatalogServer:
    """Local HTTP server; plan[i] decides how request i is answered.

    A plan is ("delay", seconds), ("status", code) or ("drop", None).  The
    last plan repeats.  Sleeping handlers wake at close().
    """

    def __init__(self, *plans):
        self.plans = list(plans)
        self.requests: list[dict[str, str]] = []
        self.release = threading.Event()
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                with outer._lock:
                    index = len(outer.requests)
                    outer.requests.append({"path": self.path, "authorization": self.headers.get("authorization", "")})
                kind, value = outer.plans[min(index, len(outer.plans) - 1)]
                if kind == "drop":
                    # Close without a response: the client sees a dropped connection.
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                if kind == "delay" and outer.release.wait(value):
                    return
                status = value if kind == "status" else 200
                body = json.dumps(CATALOG if status == 200 else {"error": {"message": "unauthorized"}}).encode()
                try:
                    self.send_response(status)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass  # the client already gave up

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass

        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

    @property
    def catalog_requests(self) -> int:
        return sum(1 for item in self.requests if item["path"].startswith("/v1/models"))


class IsolatedRuntime:
    """Temporary config, cache and registry around the real ciel_runtime."""

    def __init__(self, base_url: str, current_model: str = "ASTRA"):
        self.base_url = base_url
        self.current_model = current_model

    def __enter__(self):
        self._dir = TemporaryDirectory()
        self.root = Path(self._dir.name)
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["cielairouter"])
        pcfg.update(api_key=API_KEY, base_url=self.base_url, current_model=self.current_model)
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps({"provider": "cielairouter", "providers": {"cielairouter": pcfg}}), encoding="utf-8")
        self.log_lines: list[str] = []
        self._patches = [
            mock.patch.object(ciel_runtime, "CONFIG_DIR", self.root),
            mock.patch.object(ciel_runtime, "MODEL_LIST_CACHE_PATH", self.root / "list.json"),
            mock.patch.object(ciel_runtime, "MODEL_REGISTRY_PATH", self.root / "registry.json"),
            mock.patch.object(ciel_runtime, "GATEWAY_CACHE_PATH", self.root / "gateway.json", create=True),
            mock.patch.object(ciel_runtime, "load_config", side_effect=self.load),
            mock.patch.object(ciel_runtime, "get_current_provider", side_effect=lambda cfg: ("cielairouter", cfg["providers"]["cielairouter"])),
            mock.patch.object(ciel_runtime, "save_config", side_effect=self.save),
            mock.patch.object(ciel_runtime, "clear_model_cache"),
            mock.patch.object(ciel_runtime, "router_log", side_effect=lambda level, message: self.log_lines.append(f"{level} {message}")),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()
        self._dir.cleanup()

    def load(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def save(self, value):
        self.config_path.write_text(json.dumps(value), encoding="utf-8")

    def provider_config(self):
        return self.load()["providers"]["cielairouter"]

    def select(self, value: str) -> list[str]:
        return ciel_runtime.model_selection_controller().select(value)


def short_timeouts(timeout: float = 1.0, backoff: float = 0.05):
    """Shrink the fetch timeout so timeout tests run in seconds.

    create=True keeps these patches harmless on code without the constants.
    """
    return (
        mock.patch.object(provider_models, "MODEL_LIST_TIMEOUT_SECONDS", timeout, create=True),
        mock.patch.object(provider_models, "MODEL_LIST_RETRY_BACKOFF_SECONDS", backoff, create=True),
    )


class ModelListTimeoutTests(unittest.TestCase):
    def assert_no_secret(self, runtime: IsolatedRuntime, messages: list[str]) -> None:
        text = "\n".join([*messages, *runtime.log_lines])
        self.assertNotIn(API_KEY, text)
        self.assertNotIn("authorization", text.lower())

    def test_model_list_timeout_is_one_named_20_second_constant(self):
        self.assertEqual(20.0, provider_models.MODEL_LIST_TIMEOUT_SECONDS)

    def test_a_slow_first_response_within_20_seconds_returns_the_catalog(self):
        # (a) A cold provider that answers after 7 s; the old 6 s limit failed here.
        with CatalogServer(("delay", 7.0)) as server, IsolatedRuntime(server.base_url) as runtime:
            messages = runtime.select(REQUESTED)
            self.assertEqual(f"Model for cielairouter set to {REQUESTED}.", messages[0], messages)
            self.assertEqual(REQUESTED, runtime.provider_config()["current_model"])
            self.assertEqual(1, server.catalog_requests)
            self.assertEqual(f"Bearer {API_KEY}", server.requests[0]["authorization"])

    def test_b_one_timeout_then_an_answer_succeeds_through_the_retry(self):
        timeout, backoff = short_timeouts()
        with timeout, backoff, CatalogServer(("delay", 7.0), ("delay", 0.0)) as server, IsolatedRuntime(server.base_url) as runtime:
            messages = runtime.select(REQUESTED)
            self.assertEqual(f"Model for cielairouter set to {REQUESTED}.", messages[0], messages)
            self.assertEqual(2, server.catalog_requests)

    def test_b_a_dropped_connection_is_retried_once(self):
        timeout, backoff = short_timeouts()
        with timeout, backoff, CatalogServer(("drop", None), ("delay", 0.0)) as server, IsolatedRuntime(server.base_url) as runtime:
            messages = runtime.select(REQUESTED)
            self.assertEqual(f"Model for cielairouter set to {REQUESTED}.", messages[0], messages)
            self.assertEqual(2, server.catalog_requests)

    def test_c_an_unreachable_catalog_is_reported_as_unchecked_not_missing(self):
        timeout, backoff = short_timeouts(timeout=0.5)
        with timeout, backoff, CatalogServer(("delay", 30.0)) as server, IsolatedRuntime(server.base_url) as runtime:
            messages = runtime.select(REQUESTED)
            self.assertEqual(1, len(messages), messages)
            self.assertNotIn(NOT_IN_CATALOG, messages[0])
            self.assertIn("could not fetch the model list from cielairouter (timeout after 0.5 s)", messages[0])
            self.assertIn(f"{REQUESTED!r} was not checked", messages[0])
            # The CLI and remote management treat this prefix as a refusal (exit 2).
            self.assertTrue(messages[0].startswith("Model selection rejected"))
            self.assertEqual("ASTRA", runtime.provider_config()["current_model"])
            self.assertEqual(2, server.catalog_requests)
            self.assert_no_secret(runtime, messages)
            # A failed lookup is not persisted as a catalog: asking again
            # fetches again and still reports the list as unavailable.
            self.assertFalse((runtime.root / "list.json").exists())
            self.assertFalse((runtime.root / "registry.json").exists())
            again = runtime.select(REQUESTED)
            self.assertNotIn(NOT_IN_CATALOG, again[0])
            self.assertIn("could not fetch the model list", again[0])
            self.assertEqual(4, server.catalog_requests)

    def test_c_launch_keeps_the_configured_model_when_the_list_is_unavailable(self):
        timeout, backoff = short_timeouts(timeout=0.5)
        with timeout, backoff, CatalogServer(("delay", 30.0)) as server, IsolatedRuntime(server.base_url, current_model=REQUESTED) as runtime:
            ok, messages = ciel_runtime.ensure_current_model_from_provider_list("cielairouter", runtime.provider_config())
            self.assertTrue(ok, messages)
            self.assertEqual(1, len(messages), messages)
            self.assertNotIn(NOT_IN_CATALOG, messages[0])
            self.assertIn(f"keeping configured model {REQUESTED}", messages[0])
            self.assertIn("could not fetch the model list from cielairouter (timeout after 0.5 s)", messages[0])
            self.assert_no_secret(runtime, messages)

    def test_d_an_unauthorized_list_is_not_retried(self):
        timeout, backoff = short_timeouts()
        with timeout, backoff, CatalogServer(("status", 401)) as server, IsolatedRuntime(server.base_url) as runtime:
            messages = runtime.select(REQUESTED)
            self.assertEqual(1, server.catalog_requests)
            self.assertEqual(1, len(messages), messages)
            self.assertTrue(messages[0].startswith("Model selection rejected"), messages)
            self.assertNotIn(NOT_IN_CATALOG, messages[0])
            self.assertIn("could not fetch the model list from cielairouter (HTTP 401)", messages[0])
            self.assertEqual("ASTRA", runtime.provider_config()["current_model"])
            self.assert_no_secret(runtime, messages)


class TransientFetchErrorTests(unittest.TestCase):
    def test_timeouts_and_connection_errors_are_transient(self):
        transient = provider_models.is_transient_model_list_error
        self.assertTrue(transient(TimeoutError("timed out")))
        self.assertTrue(transient(urllib.error.URLError(TimeoutError("timed out"))))
        self.assertTrue(transient(urllib.error.URLError(ConnectionRefusedError(10061, "refused"))))
        self.assertTrue(transient(ConnectionResetError()))

    def test_http_errors_and_bad_payloads_are_not_transient(self):
        transient = provider_models.is_transient_model_list_error
        for code in (400, 401, 403, 404, 429, 500):
            self.assertFalse(transient(urllib.error.HTTPError("http://x", code, "error", {}, None)), code)
        self.assertFalse(transient(ValueError("bad json")))

    def test_retry_runs_at_most_twice_and_sleeps_once(self):
        calls = []
        sleeps = []

        def fetch():
            calls.append(1)
            raise TimeoutError("timed out")

        with mock.patch.object(provider_models.time, "sleep", side_effect=sleeps.append):
            with self.assertRaises(TimeoutError):
                provider_models.fetch_model_list_with_retry(fetch)
        self.assertEqual(2, len(calls))
        self.assertEqual([provider_models.MODEL_LIST_RETRY_BACKOFF_SECONDS], sleeps)

    def test_no_retry_after_an_http_error(self):
        calls = []

        def fetch():
            calls.append(1)
            raise urllib.error.HTTPError("http://x", 401, "unauthorized", {}, None)

        with mock.patch.object(provider_models.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                provider_models.fetch_model_list_with_retry(fetch)
        self.assertEqual(1, len(calls))
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
