"""Exact-session runtime metadata for the Work status projection.

This module deliberately keeps configured routing separate from observations. A
request is retained only long enough to correlate a later response or failure;
no request is reported as an inflight completion. Scope identity is the exact
(runtime, session id, launch epoch) tuple, so sidechains and other sessions are
ignored by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Mapping
import time

_MAX_TEXT = 256
_MAX_REQUEST_ID = 256
_ERROR_CATEGORIES = frozenset({"authentication", "cancellation", "http_error", "timeout", "transport", "unavailable", "unknown"})


def _text(value: Any, limit: int = _MAX_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    value = str(value).strip()
    return value if value and len(value) <= limit and not any(ord(c) < 32 for c in value) else None


def _status(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if 100 <= value <= 599 else None


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RuntimeStatusScope:
    runtime: str
    session_id: str
    epoch: str

    def __post_init__(self) -> None:
        if not self.runtime or not self.session_id or not self.epoch:
            raise ValueError("runtime, session_id and epoch are required")

    def payload(self) -> dict[str, str]:
        return {"runtime": self.runtime, "session_id": self.session_id, "epoch": self.epoch}

    @classmethod
    def from_value(cls, value: Any) -> "RuntimeStatusScope | None":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            values = (value.get("runtime"), value.get("session_id"), value.get("epoch"))
        else:
            values = (getattr(value, "runtime", None), getattr(value, "session_id", None), getattr(value, "epoch", None))
        runtime, session_id, epoch = (_text(item) for item in values)
        return cls(runtime, session_id, epoch) if runtime and session_id and epoch else None


class RuntimeStatusTracker:
    """Track only completed observations belonging to one exact launch scope."""

    def __init__(self, *, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.time
        self._lock = RLock()
        self._scope: RuntimeStatusScope | None = None
        self._configured_provider: str | None = None
        self._configured_model: str | None = None
        self._pending: dict[str, tuple[RuntimeStatusScope, str | None, str | None]] = {}
        self._last_response: dict[str, Any] | None = None
        self._last_error: dict[str, Any] | None = None

    def bind(self, scope: RuntimeStatusScope | Mapping[str, Any] | Any | None) -> None:
        """Replace scope and clear observations from every previous epoch."""
        exact = RuntimeStatusScope.from_value(scope)
        with self._lock:
            if exact != self._scope:
                self._scope = exact
                self._pending.clear()
                self._last_response = None
                self._last_error = None

    def observe_configuration(self, provider: Any, model: Any = None) -> None:
        """Record configured routing; this is never an observed upstream result."""
        with self._lock:
            self._configured_provider = _text(provider)
            self._configured_model = _text(model)

    def _accept(self, scope: Any) -> RuntimeStatusScope | None:
        exact = RuntimeStatusScope.from_value(scope)
        return exact if exact is not None and exact == self._scope else None

    def observe_request(self, scope: Any, request_id: Any, provider: Any, model: Any = None) -> bool:
        """Remember correlation data without exposing an inflight status."""
        key = _text(request_id, _MAX_REQUEST_ID)
        with self._lock:
            exact = self._accept(scope)
            if exact is None or key is None or key in self._pending:
                return False
            if len(self._pending) >= 256:
                self._pending.pop(next(iter(self._pending)))
            self._pending[key] = (exact, _text(provider), _text(model))
        return True

    def observe_response(self, scope: Any, request_id: Any, provider: Any = None, model: Any = None,
                         status: Any = 200, observed_at: Any = None) -> bool:
        key = _text(request_id, _MAX_REQUEST_ID)
        with self._lock:
            exact = self._accept(scope)
            if exact is None or key is None:
                return False
            pending = self._pending.pop(key, None)
            if pending is None or pending[0] != exact:
                return False
            request_provider, request_model = pending[1:]
            self._last_response = {"provider": _text(provider), "model": _text(model),
                                   "request_provider": request_provider, "request_model": request_model, "source": "upstream_response",
                                   "status": _status(status), "observed_at": _timestamp(observed_at) or _timestamp(self._now()), "request_id": key}
            self._last_error = None
        return True

    def observe_failure(self, scope: Any, request_id: Any, *, category: Any, provider: Any = None,
                        model: Any = None, status: Any = None, observed_at: Any = None) -> bool:
        key = _text(request_id, _MAX_REQUEST_ID)
        category = _text(category)
        with self._lock:
            exact = self._accept(scope)
            if exact is None or key is None or category not in _ERROR_CATEGORIES:
                return False
            pending = self._pending.pop(key, None)
            if pending is None or pending[0] != exact:
                return False
            request_provider, request_model = pending[1:]
            self._last_error = {"category": category, "provider": _text(provider) or request_provider, "model": _text(model) or request_model,
                                "status": _status(status), "observed_at": _timestamp(observed_at) or _timestamp(self._now()), "request_id": key, "source": "correlated_request_failure"}
            self._last_response = None
        return True

    def snapshot(self) -> dict[str, Any]:
        """Return JSON-safe metadata; no pending request is represented."""
        with self._lock:
            scope = self._scope
            response = dict(self._last_response) if self._last_response else None
            error = dict(self._last_error) if self._last_error else None
            return {"scope": scope.payload() if scope else None, "runtime": scope.runtime if scope else None,
                    "configured_provider": self._configured_provider, "configured_model": self._configured_model,
                    "observed_provider": response.get("provider") if response else None,
                    "observed_model": response.get("model") if response else None,
                    "observed_at": response.get("observed_at") if response else None,
                    "source": response.get("source") if response else None,
                    "inflight": None, "inflight_source": None,
                    "last_response": response, "last_error": error, "build": None}


__all__ = ["RuntimeStatusScope", "RuntimeStatusTracker"]
