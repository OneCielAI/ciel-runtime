"""Stored OAuth tokens on the Codex routed and Anthropic routed request paths.

The router rebuilds its service graph for every request, so the workspace
token store, pool and watcher are cached here for the life of the process and
the watcher starts with the first routed request that finds stored tokens.

A request run picks a token (:class:`OAuthTokenPool` rules), sends the
request with that token's credentials, records the usage the response
reports, and before any output reaches the CLI:

- on a usage-limit 429 takes that token out until its reset and retries with
  the next token;
- on a 401 refreshes the token once and retries; if the token still fails it
  moves on, and a final 401 is relayed as 424 so the CLI does not start its
  own sign-in recovery for a token it never had.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping
import urllib.error

from ciel_runtime_support.oauth_token_pool import DEFAULT_THRESHOLD_PERCENT, OAuthLease, OAuthTokenPool
from ciel_runtime_support.oauth_token_refresh import OAuthTokenRefresher, OAuthTokenWatcher
from ciel_runtime_support.oauth_token_store import OAuthTokenStore
from ciel_runtime_support.oauth_usage_signals import UsageObservation, anthropic_observation, codex_observation

CODEX_UPSTREAM_PREFIX = "https://chatgpt.com/backend-api/codex"
ANTHROPIC_OAUTH_BETA = "oauth-2025-04-20"
Log = Callable[[str, str], None]


class OAuthRouting:
    def __init__(self, directory: Path, *, threshold_percent: float, clock: Callable[[], float] = time.time) -> None:
        self.store = OAuthTokenStore(directory, clock=clock)
        self.pool = OAuthTokenPool(self.store, threshold_percent=threshold_percent, clock=clock)
        self.refresher = OAuthTokenRefresher(self.store, clock=clock)
        self.clock = clock
        self._watcher: OAuthTokenWatcher | None = None
        self._lock = threading.Lock()

    def ensure_watcher(self, log: Log) -> None:
        with self._lock:
            if self._watcher is None:
                self._watcher = OAuthTokenWatcher(self.pool, self.refresher, log)
                self._watcher.start()

    def stop(self) -> None:
        with self._lock:
            if self._watcher is not None:
                self._watcher.stop()
                self._watcher = None


_ROUTING: OAuthRouting | None = None
_ROUTING_LOCK = threading.Lock()


def threshold_percent() -> float:
    try:
        value = float(os.environ.get("CIEL_RUNTIME_OAUTH_ROTATE_PERCENT") or DEFAULT_THRESHOLD_PERCENT)
    except ValueError:
        return DEFAULT_THRESHOLD_PERCENT
    return min(100.0, max(1.0, value))


def oauth_routing() -> OAuthRouting:
    global _ROUTING
    with _ROUTING_LOCK:
        if _ROUTING is None:
            from ciel_runtime_support.runtime_paths import WORKSPACE_STATE_DIR

            _ROUTING = OAuthRouting(Path(WORKSPACE_STATE_DIR), threshold_percent=threshold_percent())
        return _ROUTING


def use_oauth_routing(routing: OAuthRouting | None) -> None:
    """Replace the process routing (tests, or a router serving another workspace)."""

    global _ROUTING
    with _ROUTING_LOCK:
        if _ROUTING is not None and _ROUTING is not routing:
            _ROUTING.stop()
        _ROUTING = routing


# ---- request facts ----------------------------------------------------------------------------------


def _header(headers: Any, name: str) -> str:
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is None:
            value = next((v for k, v in dict(headers).items() if str(k).lower() == name.lower()), None)
        return str(value or "")
    return ""


def codex_session_key(headers: Any, body: Mapping[str, Any]) -> str:
    return str(
        body.get("prompt_cache_key")
        or _header(headers, "session_id")
        or _header(headers, "x-codex-session-id")
        or _header(headers, "conversation_id")
        or "default"
    )


def codex_turn_start(body: Mapping[str, Any]) -> bool:
    """A Responses request that ends with a user message starts a turn; one that
    ends with tool output continues it."""

    items = body.get("input")
    if not isinstance(items, list) or not items:
        return True
    last = items[-1] if isinstance(items[-1], dict) else {}
    kind = str(last.get("type") or "message")
    return kind == "message" and str(last.get("role") or "user") == "user"


def anthropic_session_key(headers: Any, body: Mapping[str, Any]) -> str:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    return str(_header(headers, "x-claude-code-session-id") or metadata.get("user_id") or "default")


def anthropic_turn_start(body: Mapping[str, Any]) -> bool:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return True
    last = messages[-1] if isinstance(messages[-1], dict) else {}
    if str(last.get("role") or "") != "user":
        return False
    content = last.get("content")
    if isinstance(content, list):
        return not any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)
    return True


def _without(headers: Mapping[str, str], *names: str) -> dict[str, str]:
    lowered = {name.lower() for name in names}
    return {key: value for key, value in headers.items() if str(key).lower() not in lowered}


def codex_headers(headers: Mapping[str, str], lease: OAuthLease) -> dict[str, str]:
    result = _without(headers, "authorization", "chatgpt-account-id")
    result["Authorization"] = f"Bearer {lease.credential.access_token}"
    if lease.credential.account_id:
        result["ChatGPT-Account-ID"] = lease.credential.account_id
    return result


def anthropic_headers(headers: Mapping[str, str], lease: OAuthLease) -> dict[str, str]:
    result = _without(headers, "authorization", "x-api-key")
    result["Authorization"] = f"Bearer {lease.credential.access_token}"
    betas = [part.strip() for part in _header(headers, "anthropic-beta").split(",") if part.strip()]
    if ANTHROPIC_OAUTH_BETA not in betas:
        betas.append(ANTHROPIC_OAUTH_BETA)
    result = _without(result, "anthropic-beta")
    result["anthropic-beta"] = ",".join(betas)
    return result


# ---- one routed request -----------------------------------------------------------------------------


class OAuthRequestRun:
    """Token choice, usage recording and token-level retries for one request."""

    def __init__(
        self,
        routing: OAuthRouting,
        provider: str,
        session_key: str,
        turn_start: bool,
        apply: Callable[[Mapping[str, str], OAuthLease], dict[str, str]],
        observe: Callable[..., UsageObservation],
        log: Log,
    ) -> None:
        self.routing = routing
        self.provider = provider
        self.session_key = session_key
        self.turn_start = turn_start
        self.apply = apply
        self.observation = observe
        self.log = log
        self.lease: OAuthLease | None = None
        self._excluded: set[str] = set()
        self._refreshed: set[str] = set()

    def headers(self, base: Mapping[str, str]) -> dict[str, str] | None:
        """Headers for the next attempt; None when no stored token can serve."""

        lease = self.routing.pool.acquire(
            self.provider, self.session_key, turn_start=self.turn_start, exclude=self._excluded
        )
        if lease is None or lease.token_id in self._excluded:
            return None
        if lease.moved_from:
            self.log("INFO", f"oauth_token_rotated provider={self.provider} from={lease.moved_from} to={lease.token_id} reason={lease.reason}")
        elif self.lease is None:
            self.log("INFO", f"oauth_token_selected provider={self.provider} token={lease.token_id}")
        self.lease = lease
        return self.apply(base, lease)

    def observe_response(self, response_headers: Any, status: int = 200) -> None:
        if self.lease is None:
            return
        observation = self.observation(response_headers, status=status, now=self.routing.clock())
        self.routing.pool.observe(self.lease.token_id, observation)

    def retry_headers(self, error: urllib.error.HTTPError, base: Mapping[str, str]) -> tuple[dict[str, str] | None, urllib.error.HTTPError]:
        """After an upstream error: headers to retry with, or None and the error to relay."""

        raw = error.read()
        relay = urllib.error.HTTPError(error.url, error.code, error.msg, error.hdrs, io.BytesIO(raw))
        lease = self.lease
        if lease is None:
            return None, relay
        if error.code == 429:
            observation = self.observation(error.headers, status=429, body=raw, now=self.routing.clock())
            self.routing.pool.observe(lease.token_id, observation)
            self._excluded.add(lease.token_id)
            kind = "limit" if observation.limited else "throttle"
            self.log("WARN", f"oauth_token_refused provider={self.provider} token={lease.token_id} kind={kind} until={int(observation.reset_at)}")
            return self.headers(base), relay
        if error.code == 401:
            if lease.token_id not in self._refreshed:
                self._refreshed.add(lease.token_id)
                outcome = self.routing.refresher.refresh(lease.token_id, stale_access_token=lease.credential.access_token)
                self.log("WARN", f"oauth_token_unauthorized provider={self.provider} token={lease.token_id} refresh={outcome.detail}")
                if outcome.refreshed:
                    self._excluded.discard(lease.token_id)
                    return self.headers(base), relay
            self._excluded.add(lease.token_id)
            retry = self.headers(base)
            if retry is not None:
                return retry, relay
            message = (
                f'{{"error":{{"type":"authentication_error","message":"stored {self.provider} OAuth token {lease.token_id} '
                f'was rejected; run ciel-runtime tokens list"}}}}'
            ).encode("utf-8")
            return None, urllib.error.HTTPError(error.url, 424, "Stored OAuth token rejected", error.hdrs, io.BytesIO(message))
        return None, relay


def codex_request_run(url: str, inbound_headers: Any, body: Mapping[str, Any], log: Log) -> OAuthRequestRun | None:
    # The routed upstream; CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM moves it (E2E).
    prefix = os.environ.get("CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM") or CODEX_UPSTREAM_PREFIX
    if not str(url).startswith(prefix.rstrip("/")):
        return None
    routing = oauth_routing()
    if not routing.store.has_tokens("codex"):
        return None
    routing.ensure_watcher(log)
    return OAuthRequestRun(
        routing, "codex", codex_session_key(inbound_headers, body), codex_turn_start(body), codex_headers, codex_observation, log
    )


def anthropic_request_run(provider: str, headers: Mapping[str, str], inbound_headers: Any, body: Mapping[str, Any], log: Log) -> OAuthRequestRun | None:
    # A configured API key is used as configured; stored tokens replace only
    # the subscription (OAuth bearer) sign-in the CLI itself sends.
    if provider != "anthropic" or any(str(name).lower() == "x-api-key" for name in headers):
        return None
    routing = oauth_routing()
    if not routing.store.has_tokens("claude"):
        return None
    routing.ensure_watcher(log)
    return OAuthRequestRun(
        routing,
        "claude",
        anthropic_session_key(inbound_headers, body),
        anthropic_turn_start(body),
        anthropic_headers,
        anthropic_observation,
        log,
    )


def open_with_oauth(run: OAuthRequestRun | None, headers: dict[str, str], open_request: Callable[[dict[str, str]], Any]) -> Any:
    """Send through ``open_request`` with stored tokens, retrying across tokens."""

    if run is None:
        return open_request(headers)
    attempt = run.headers(headers)
    if attempt is None:
        return open_request(headers)
    while True:
        try:
            response = open_request(attempt)
        except urllib.error.HTTPError as error:
            retry, relay = run.retry_headers(error, headers)
            if retry is None:
                raise relay from None
            attempt = retry
            continue
        run.observe_response(getattr(response, "headers", {}), getattr(response, "status", 200))
        return response


__all__ = [
    "OAuthRequestRun",
    "OAuthRouting",
    "anthropic_request_run",
    "anthropic_turn_start",
    "codex_request_run",
    "codex_turn_start",
    "oauth_routing",
    "open_with_oauth",
    "use_oauth_routing",
]
