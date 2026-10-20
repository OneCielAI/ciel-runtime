"""Per-request CielAiRouter gateway base URL overlay.

The configured CielAiRouter ``base_url`` is the central discovery base.  When
``cielairouter_gateways.CielAiRouterGatewayService`` can be imported, the router
asks it for a gateway each time a CielAiRouter request is dispatched, after
provider failover has chosen the serving provider, and sends that request to
the selected gateway.  The selection is applied to a deep copy of the provider
config: config.json and the router's loaded config are never written.  The copy
keeps every credential and records the central base under
``DISCOVERY_BASE_URL_KEY``; model catalog fetches and model cache identities
use ``discovery_provider_config`` so they keep reading the central base.

The selector service is created lazily on the first CielAiRouter request and
does its own single-flight discovery; when it is missing, fails, or returns no
gateway candidate, the request uses the central base as before.

Each client session (Claude Code ``x-claude-code-session-id``, Codex
``prompt_cache_key``/session headers) keeps the gateway it started on: a
session's first request ranks the gateways again (the fastest healthy one at
that moment) and later requests reuse that pick until it fails.  Requests
without a session identity share the scope's current pick.  Selection outcomes are logged when they change and exposed through
``status()`` (router health); nothing here retries a failed request.

A failure the gateway is responsible for (connection, timeout, a stream that
ends before its completion event, HTTP 5xx) is reported back to the selector
through ``observed_open``, which moves later requests to another gateway.  The
failed request itself is never replayed: it may already have reached the model.
A selected discovery node whose endpoint is the central base is a central
route (reason ``CENTRAL_GATEWAY_REASON``): the request goes to the central base
unchanged, without a route token, so nothing observes it.  Like a route without
a candidate it is never pinned to a session; the session's next request asks
the selector again (which shares an exploration within its refresh interval).
Set ``gateway_selection: "off"`` in the CielAiRouter provider config to always
use the configured base URL.

``select_launch_gateway`` serves ``gateway_data_path=direct`` (``launch_data_path``):
the launcher asks the same selector once, before a CLI starts, for the gateway
the CLI then calls itself.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import itertools
import re
import threading
import time
import urllib.parse
from collections import OrderedDict
from typing import Any, Callable, Mapping

from .credentials import provider_config_api_keys
from .providers.constants import PROVIDER_DEFAULT_BASE_URLS

PROVIDER = "cielairouter"
DISCOVERY_BASE_URL_KEY = "ciel_gateway_discovery_base_url"
# A JSON-safe token on the overlay copy; the transport looks the selection up by it.
ROUTE_TOKEN_KEY = "ciel_gateway_route"
SELECTION_SETTING = "gateway_selection"
_OFF_VALUES = frozenset({"off", "false", "0", "no", "disabled", "pinned"})
_MAX_ROUTE_TOKENS = 4096
_MAX_SESSIONS = 512
# Transport observation reason -> selector failure class.
_FAILURE_CLASSES = {
    "timeout": "timeout",
    "connection_reset": "connection",
    "network_error": "connection",
    "eof": "connection",
    "eof_before_terminal": "stream_truncated",
    "http_5xx": "5xx",
}
# Route reason when the selected discovery node's endpoint is the central base.
CENTRAL_GATEWAY_REASON = "gateway endpoint is the central base"
SELECTOR_MODULE = "ciel_runtime_support.cielairouter_gateways"
SELECTOR_CLASS = "CielAiRouterGatewayService"
# Exceptions this module defines carry no remote response content or keys
# (cielairouter_gateways.DiscoveryError); any other text is not logged.
_SAFE_ERROR_NAMES = frozenset({"DiscoveryError", "GatewayAuthenticationError"})

Log = Callable[[str, str], Any]


def discovery_base_url(pcfg: Mapping[str, Any]) -> str:
    """The central discovery base for a CielAiRouter provider config (or overlay copy)."""

    value = pcfg.get(DISCOVERY_BASE_URL_KEY) or pcfg.get("base_url") or PROVIDER_DEFAULT_BASE_URLS[PROVIDER]
    return str(value).strip().rstrip("/")


def discovery_provider_config(pcfg: Any) -> Any:
    """``pcfg`` with the central base restored when it is a gateway overlay copy.

    Model catalog fetches and cache identities use this so a per-request
    gateway never changes which catalog is read or which cache entry is used.
    """

    if not isinstance(pcfg, Mapping) or DISCOVERY_BASE_URL_KEY not in pcfg:
        return pcfg
    restored = dict(pcfg)
    restored["base_url"] = restored.pop(DISCOVERY_BASE_URL_KEY)
    restored.pop(ROUTE_TOKEN_KEY, None)
    return restored


def selection_enabled(pcfg: Mapping[str, Any]) -> bool:
    return str(pcfg.get(SELECTION_SETTING, "auto") or "auto").strip().lower() not in _OFF_VALUES


def _protocol(url: str) -> str | None:
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    if path.endswith("/messages"):
        return "anthropic"
    if path.endswith("/responses") or path.endswith("/responses/compact"):
        return "responses"
    if path.endswith("/chat/completions"):
        return "chat"
    return None


def _origin(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc or "-"
    except ValueError:
        return "-"


def _error_text(error: BaseException) -> str:
    name = type(error).__name__
    if name in _SAFE_ERROR_NAMES and str(error):
        return f"{name}: {' '.join(str(error).split())[:160]}"
    return name


def _gateway_endpoint(selection: Any, central: str) -> str:
    """The gateway base a selection routes to; empty when the request uses ``central``."""

    endpoint = str(getattr(selection, "endpoint", "") or "").rstrip("/")
    if getattr(selection, "candidate", None) is None or not endpoint or endpoint == central:
        return ""
    return endpoint


def _load_selector() -> Any:
    module = importlib.import_module(SELECTOR_MODULE)
    return getattr(module, SELECTOR_CLASS)()


class CielAiRouterGatewayOverlay:
    """Applies the selected CielAiRouter gateway to one request's provider config copy."""

    def __init__(
        self,
        selector_factory: Callable[[], Any] = _load_selector,
        *,
        log: Log = lambda _level, _message: None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.selector_factory = selector_factory
        self.log = log
        self.clock = clock
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._selector: Any = None
        self._selector_state = "not_loaded"   # not_loaded | available | unavailable
        self._selector_error = ""
        self._routes: dict[str, dict[str, Any]] = {}
        self._tokens: OrderedDict[str, Any] = OrderedDict()
        self._token_counter = itertools.count(1)
        self._sessions: OrderedDict[tuple[str, str, str, str], Any] = OrderedDict()
        self._session_condition = threading.Condition(self._lock)
        self._session_flights: set[tuple[str, str, str, str]] = set()

    # ---- selector -------------------------------------------------------------------------

    def _selector_service(self) -> Any:
        with self._lock:
            if self._selector_state == "not_loaded":
                try:
                    self._selector = self.selector_factory()
                    self._selector_state = "available"
                except Exception as error:  # noqa: BLE001 - a missing selector keeps the central base
                    self._selector = None
                    self._selector_state = "unavailable"
                    self._selector_error = type(error).__name__
                    self.log(
                        "WARN",
                        f"cielairouter_gateway_selector_unavailable module={SELECTOR_MODULE} "
                        f"error={self._selector_error}; requests use the configured base URL",
                    )
            return self._selector

    # ---- per request ----------------------------------------------------------------------

    def apply(self, provider: str, pcfg: Any, *, path: str = "", session: str = "") -> Any:
        """The provider config this request is sent with.

        Other providers, and CielAiRouter requests without a usable gateway,
        get their config back unchanged (an overlay copy is restored to the
        central base).  A selected gateway yields a new deep copy whose
        ``base_url`` is the gateway and whose credentials are untouched.
        """

        if provider != PROVIDER or not isinstance(pcfg, Mapping):
            return pcfg
        if not selection_enabled(pcfg):
            return discovery_provider_config(pcfg)
        central = discovery_base_url(pcfg)
        keys = provider_config_api_keys(pcfg)
        if not keys:
            # Discovery is authorized by the router key; without one the
            # request fails the same way at the central base.
            return discovery_provider_config(pcfg)
        selector = self._selector_service()
        if selector is None:
            return discovery_provider_config(pcfg)
        model = str(pcfg.get("current_model") or "")
        started = self.monotonic()
        session_key = (central, hashlib.sha256(keys[0].encode()).hexdigest(), model, session) if session else None
        try:
            selection = self._select_session(session_key, selector, central, keys[0], model)
        except Exception as error:  # noqa: BLE001 - selection never fails the request
            self._record(central, {"state": "failed", "error": _error_text(error)}, started)
            return discovery_provider_config(pcfg)
        endpoint = str(getattr(selection, "endpoint", "") or "").rstrip("/")
        candidate = getattr(selection, "candidate", None)
        gateway = _gateway_endpoint(selection, central)
        route = {
            "state": "gateway" if gateway else "central",
            "endpoint": endpoint or None,
            "node_id": getattr(candidate, "node_id", None),
            "reason": str(getattr(selection, "reason", "") or ""),
            "degraded": bool(getattr(selection, "degraded", False)),
            "generation": getattr(selection, "generation", None),
        }
        if candidate is not None and endpoint == central:
            # The selected discovery node is the central base itself: the
            # request goes to central unchanged, so report central.
            route["selection_reason"] = route["reason"]
            route["reason"] = CENTRAL_GATEWAY_REASON
        self._record(central, route, started)
        if not gateway:
            return discovery_provider_config(pcfg)
        overlay = copy.deepcopy(dict(pcfg))
        overlay["base_url"] = endpoint
        overlay[DISCOVERY_BASE_URL_KEY] = central
        overlay[ROUTE_TOKEN_KEY] = self._remember(selection)
        self.log(
            "DEBUG",
            f"cielairouter_gateway_route path={path or '-'} central={_origin(central)} endpoint={endpoint} "
            f"node={route['node_id'] or '-'} generation={route['generation']}",
        )
        return overlay

    @staticmethod
    def _select(selector: Any, central: str, key: str, model: str, *, fresh: bool) -> Any:
        try:
            return selector.select(central, key, model, fresh=fresh)
        except TypeError:
            # A selector without per-session ranking keeps its own sticky pick.
            return selector.select(central, key, model)

    def _select_session(self, session_key: Any, selector: Any, central: str, key: str, model: str) -> Any:
        if session_key is None:
            return self._select(selector, central, key, model, fresh=False)
        # One owner per auth-scoped session. Other sessions/keys may explore
        # concurrently; no network work holds the overlay lock.
        with self._session_condition:
            while session_key in self._session_flights:
                self._session_condition.wait()
            self._session_flights.add(session_key)
        try:
            selection = self._session_selection(session_key, selector)
            if selection is None:
                selection = self._select(selector, central, key, model, fresh=True)
                # Only a gateway route is pinned: its failures are observed and
                # release the pin.  A central route (no candidate, or a node at
                # the central base) is never observed, so it is asked again.
                if _gateway_endpoint(selection, central):
                    with self._lock:
                        self._sessions[session_key] = selection
                        self._sessions.move_to_end(session_key)
                        while len(self._sessions) > _MAX_SESSIONS:
                            self._sessions.popitem(last=False)
            return selection
        finally:
            with self._session_condition:
                self._session_flights.discard(session_key)
                self._session_condition.notify_all()

    def _session_selection(self, session_key: Any, selector: Any) -> Any:
        """Reuse a session pin only until its candidate expires or cools down."""
        with self._lock:
            selection = self._sessions.get(session_key)
            if selection is not None:
                self._sessions.move_to_end(session_key)
        if selection is None:
            return None
        cooling = getattr(selector, "is_cooling", None)
        expires = getattr(getattr(selection, "candidate", None), "available_until", float("inf"))
        if expires <= self.clock() or (callable(cooling) and cooling(selection)):
            with self._lock:
                if self._sessions.get(session_key) is selection:
                    del self._sessions[session_key]
            return None
        return selection

    def _remember(self, selection: Any) -> str:
        with self._lock:
            token = f"r{next(self._token_counter)}"
            self._tokens[token] = selection
            while len(self._tokens) > _MAX_ROUTE_TOKENS:
                self._tokens.popitem(last=False)
            return token

    def _selection_for(self, pcfg: Any) -> Any:
        if not isinstance(pcfg, Mapping):
            return None
        token = pcfg.get(ROUTE_TOKEN_KEY)
        if not isinstance(token, str):
            return None
        with self._lock:
            return self._tokens.get(token)

    def report(self, selection: Any, reason: str) -> None:
        """Tell the selector this gateway failed; later requests use another one."""

        classification = _FAILURE_CLASSES.get(reason)
        selector = self._selector
        if classification is None or selector is None or selection is None:
            return
        try:
            rotated = bool(selector.record_failure(selection, classification))
        except Exception as error:  # noqa: BLE001 - a report never affects the response
            self.log("WARN", f"cielairouter_gateway_report_failed error={type(error).__name__}")
            return
        endpoint = str(getattr(selection, "endpoint", "") or "")
        node = getattr(getattr(selection, "candidate", None), "node_id", None) or "-"
        self.log(
            "WARN",
            f"cielairouter_gateway_failure endpoint={endpoint or '-'} node={node} "
            f"reason={reason} class={classification} rotated={rotated}"
            + ("; later requests reselect a gateway" if rotated else ""),
        )

    def observed_open(self, request: Any, pcfg: Any, open_request: Callable[[], Any]) -> Any:
        """Open ``request``; when it goes to this request's gateway, watch the outcome.

        Only requests whose URL is under the selected gateway are observed (the
        central catalog never counts against a gateway).  The response bytes,
        exceptions and timing are exactly what ``open_request`` produces.
        """

        selection = self._selection_for(pcfg)
        url = str(getattr(request, "full_url", "") or "")
        endpoint = str(getattr(selection, "endpoint", "") or "").rstrip("/")
        if selection is None or not endpoint or not (url == endpoint or url.startswith(endpoint + "/")):
            return open_request()
        from .cielairouter_gateway_transport import observe_gateway_error, wrap_gateway_response

        def on_result(observation: Any) -> None:
            if not observation.success:
                self.report(selection, observation.reason)

        try:
            response = open_request()
        except BaseException as error:
            observe_gateway_error(error, on_result=on_result)
            raise
        protocol = _protocol(url)
        if protocol is None:
            return response
        try:
            content_type = str(response.headers.get("content-type") or "").lower()
        except Exception:  # noqa: BLE001 - an odd response object is passed through unobserved
            return response
        return wrap_gateway_response(
            response,
            protocol,
            on_result=on_result,
            stream="text/event-stream" in content_type,
        )

    def _record(self, central: str, route: dict[str, Any], started: float) -> None:
        elapsed_ms = int(max(0.0, self.monotonic() - started) * 1000)
        signature = (route.get("state"), route.get("endpoint"), route.get("reason"), route.get("error"),
                     route.get("selection_reason"))
        with self._lock:
            previous = self._routes.get(central)
            changed = previous is None or previous.get("signature") != signature
            self._routes[central] = {**route, "signature": signature, "at": self.clock(),
                                     "since": self.clock() if changed else previous.get("since")}
        if not changed:
            return
        origin = _origin(central)
        if route["state"] == "gateway":
            self.log(
                "INFO",
                f"cielairouter_gateway_selected central={origin} endpoint={route['endpoint']} "
                f"node={route['node_id'] or '-'} reason={route['reason'] or '-'} degraded={route['degraded']} "
                f"generation={route['generation']} elapsed_ms={elapsed_ms}",
            )
        elif route["state"] == "central":
            selected_node = (
                f"node={route['node_id'] or '-'} selection_reason={route['selection_reason'] or '-'} "
                if "selection_reason" in route else ""
            )
            self.log(
                "WARN" if route.get("degraded") else "INFO",
                f"cielairouter_gateway_central central={origin} reason={route['reason'] or '-'} {selected_node}"
                f"degraded={route['degraded']} generation={route['generation']} elapsed_ms={elapsed_ms}; "
                "requests use the configured base URL",
            )
        else:
            self.log(
                "WARN",
                f"cielairouter_gateway_select_failed central={origin} error={route['error']} "
                f"elapsed_ms={elapsed_ms}; requests use the configured base URL",
            )

    # ---- diagnostics ----------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Secret-free diagnostics: selector availability, last route per central base."""

        with self._lock:
            selector = self._selector
            payload: dict[str, Any] = {
                "provider": PROVIDER,
                "selector_module": SELECTOR_MODULE,
                "selector": self._selector_state,
                "selector_error": self._selector_error,
                "sessions": len(self._sessions),
                "routes": [
                    {"central": _origin(central),
                     **{key: value for key, value in route.items() if key != "signature"}}
                    for central, route in self._routes.items()
                ],
            }
        gateways: Any = []
        if selector is not None and callable(getattr(selector, "status", None)):
            try:
                gateways = selector.status()
            except Exception as error:  # noqa: BLE001 - status never raises
                gateways = {"error": type(error).__name__}
        payload["gateways"] = gateways
        return payload

    def status_line(self) -> str:
        with self._lock:
            routes = list(self._routes.values())
        gateways = [route for route in routes if route.get("state") == "gateway"]
        if gateways:
            route = gateways[-1]
            return f"CielAiRouter gateway {route.get('endpoint')} ({route.get('reason') or 'selected'})"
        return ""


_OVERLAY: CielAiRouterGatewayOverlay | None = None
_OVERLAY_LOCK = threading.Lock()


def gateway_overlay(log: Log | None = None) -> CielAiRouterGatewayOverlay:
    """The router process's overlay (the selector itself loads on first use)."""

    global _OVERLAY
    with _OVERLAY_LOCK:
        if _OVERLAY is None:
            _OVERLAY = CielAiRouterGatewayOverlay()
        if log is not None:
            _OVERLAY.log = log
        return _OVERLAY


def use_gateway_overlay(overlay: CielAiRouterGatewayOverlay | None) -> None:
    """Replace the process overlay (tests)."""

    global _OVERLAY
    with _OVERLAY_LOCK:
        _OVERLAY = overlay


def observed_gateway_open(request: Any, pcfg: Any, open_request: Callable[[], Any]) -> Any:
    """Transport hook: open through the process overlay's observation (no-op without a route)."""

    with _OVERLAY_LOCK:
        overlay = _OVERLAY
    if overlay is None:
        return open_request()
    return overlay.observed_open(request, pcfg, open_request)


def select_launch_gateway(
    pcfg: Mapping[str, Any],
    *,
    log: Log | None = None,
    selector_factory: Callable[[], Any] = _load_selector,
) -> Any:
    """The gateway a ``gateway_data_path=direct`` launch sends its model requests to.

    Runs once in the launcher, right before the CLI starts, with the same
    selector the router uses (a fresh ranking, as for a new session).  Only a
    healthy, non-degraded gateway other than the central base is returned; any
    other outcome carries the reason the launch keeps the router data path.
    The reason and error text never contain the key or remote response content.
    """

    from .credentials import transportable_api_key
    from .launch_data_path import LaunchGateway
    from .model_override import forced_model

    central = discovery_base_url(pcfg)
    origin = _origin(central)
    if not selection_enabled(pcfg):
        return LaunchGateway(reason="gateway_selection is off", central=origin)
    keys = [key for key in provider_config_api_keys(pcfg) if transportable_api_key(key)]
    if not keys:
        return LaunchGateway(reason="no API key", central=origin)
    model = str(pcfg.get("current_model") or "").strip()
    try:
        selection = CielAiRouterGatewayOverlay._select(selector_factory(), central, keys[0], model, fresh=True)
    except Exception as error:  # noqa: BLE001 - selection never blocks a launch
        if log is not None:
            log("WARN", f"cielairouter_launch_gateway_failed central={origin} error={type(error).__name__}")
        return LaunchGateway(reason=f"gateway selection failed ({type(error).__name__})", central=origin)
    endpoint = str(getattr(selection, "endpoint", "") or "").rstrip("/")
    candidate = getattr(selection, "candidate", None)
    node = str(getattr(candidate, "node_id", "") or "")
    reason = " ".join(str(getattr(selection, "reason", "") or "").split())[:160]
    if candidate is None or not endpoint or endpoint == central:
        return LaunchGateway(reason=f"central base ({reason or 'no gateway selected'})", central=origin)
    if getattr(selection, "degraded", False):
        return LaunchGateway(reason=f"degraded gateway ({reason or 'degraded'})", node_id=node, central=origin)
    upstream = re.sub(r"\[(?:1m)\]\s*$", "", forced_model(dict(pcfg)) or model, flags=re.IGNORECASE).strip()
    return LaunchGateway(endpoint=endpoint, node_id=node, reason=reason or "selected", central=origin,
                         model=upstream, api_key=keys[0])


def gateway_overlay_status() -> dict[str, Any]:
    """Status without creating the overlay or loading the selector."""

    with _OVERLAY_LOCK:
        overlay = _OVERLAY
    if overlay is None:
        return {"provider": PROVIDER, "selector_module": SELECTOR_MODULE, "selector": "not_loaded",
                "selector_error": "", "routes": [], "gateways": []}
    return overlay.status()


__all__ = [
    "CENTRAL_GATEWAY_REASON",
    "DISCOVERY_BASE_URL_KEY",
    "PROVIDER",
    "ROUTE_TOKEN_KEY",
    "SELECTION_SETTING",
    "SELECTOR_CLASS",
    "SELECTOR_MODULE",
    "CielAiRouterGatewayOverlay",
    "discovery_base_url",
    "discovery_provider_config",
    "gateway_overlay",
    "gateway_overlay_status",
    "observed_gateway_open",
    "select_launch_gateway",
    "selection_enabled",
    "use_gateway_overlay",
]
