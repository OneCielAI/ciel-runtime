"""Metadata-only observer at the existing routed-request lifecycle boundary."""
from contextlib import contextmanager
import os
import threading
import time

from .tui_observation import ObservedResponseWriter, OBSERVED_RUNTIME_PATHS
from .work_runtime_status import RuntimeStatusTracker, RuntimeStatusScope


class WorkRuntimeObserver:
    def __init__(self):
        self.tracker = RuntimeStatusTracker()
        self.lock = threading.RLock()
        self.active = {}

    @contextmanager
    def observe(self, handler, path, provider, model, body, binding):
        runtime = "claude" if path == "/v1/messages" else "codex"
        scope = binding.request_scope(runtime, handler.headers, body) if path in OBSERVED_RUNTIME_PATHS else None
        exact = RuntimeStatusScope.from_value(scope)
        if exact is None:
            yield
            return
        self.tracker.bind(exact)
        request_id = "work-" + str(os.getpid()) + "-" + str(time.time_ns())
        self.tracker.observe_request(exact, request_id, provider, model)
        row = {"request_id": request_id, "provider": provider, "model": model,
               "source": "routed_request", "observed_at": time.time(), "scope": exact.payload()}
        with self.lock:
            self.active[request_id] = row
        original = handler.wfile
        writer = MetadataWriter(original)
        handler.wfile = writer
        failure = None
        try:
            yield
        except Exception as exc:
            failure = "timeout" if isinstance(exc, TimeoutError) else "transport"
            raise
        finally:
            try:
                writer.finish()
                status = getattr(handler, "_ciel_runtime_response_status", None)
                if failure or writer.failed or (isinstance(status, int) and status >= 400):
                    category = failure or ("timeout" if status == 504 else "authentication" if status in (401, 403) else "http_error")
                    self.tracker.observe_failure(exact, request_id, category=category, status=status)
                else:
                    self.tracker.observe_response(exact, request_id, provider=provider,
                                                  model=writer.response_model, status=status)
            finally:
                handler.wfile = original
                with self.lock:
                    self.active.pop(request_id, None)

    def snapshot(self, scope):
        exact = RuntimeStatusScope.from_value(scope)
        data = self.tracker.snapshot()
        if exact is None or data.get("scope") != exact.payload():
            return {"in_flight": [], "last_observed": None, "last_error": None,
                    "observation_status": "unavailable"}
        with self.lock:
            active = [{k: v for k, v in row.items() if k != "scope"} for row in self.active.values()
                      if row["scope"] == exact.payload()]
        return {"in_flight": active, "last_observed": data["last_response"],
                "last_error": data["last_error"], "observation_status": "observed"}


class MetadataWriter(ObservedResponseWriter):
    def __init__(self, target):
        super().__init__(target, None, request_id="", provider="", model="")
        self.response_model = None
        self.failed = False

    def _publish_payload(self, event_name, payload):
        kind = payload.get("type") or event_name
        candidate = payload.get("message") if kind == "message_start" else payload.get("response")
        if isinstance(candidate, dict) and isinstance(candidate.get("model"), str):
            self.response_model = candidate["model"][:256]
        if kind in ("error", "response.failed"):
            self.failed = True

    def _observe_json(self, payload):
        if not isinstance(payload, dict):
            return
        if isinstance(payload.get("model"), str):
            self.response_model = payload["model"][:256]
        if payload.get("error"):
            self.failed = True

    def _observe_body(self, data):
        # Bound an incomplete SSE line too; observation never affects forwarding.
        if len(self._sse_buffer) + len(data) > 2_000_000:
            self._sse_buffer.clear()
            return
        super()._observe_body(data)
