"""Keeps stored OAuth tokens fresh and returns limited tokens to service.

A refresh holds a per-token lock file for the whole exchange and re-reads the
credential inside it, so a second router or the ``tokens`` command that
refreshed first is seen and not repeated: Codex refresh tokens are single use,
and presenting a used one revokes the whole token family.

A token imported from a CLI credential file is kept in step with that file
(oauth_import_sync): a credential the CLI refreshed is adopted before
refreshing, and a refresh is written back so the CLI keeps a working login.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable

from ciel_runtime_support.channel_message_repository import exclusive_file_lock
from ciel_runtime_support.oauth_import_sync import newer_in_source, write_source
from ciel_runtime_support.oauth_token_endpoints import (
    ENDPOINTS,
    HttpPost,
    OAuthHttpError,
    credential_from_response,
    refresh_fields,
    token_request,
    urllib_post,
)
from ciel_runtime_support.oauth_token_pool import OAuthTokenPool
from ciel_runtime_support.oauth_token_store import (
    STATUS_ACTIVE,
    STATUS_REFRESH_FAILED,
    OAuthCredential,
    OAuthTokenStore,
)

REFRESH_LEAD_SECONDS = 600.0
RETRY_AFTER_FAILURE_SECONDS = 120.0
WATCH_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    token_id: str
    refreshed: bool
    detail: str = ""


class OAuthTokenRefresher:
    def __init__(
        self,
        store: OAuthTokenStore,
        *,
        post: HttpPost = urllib_post,
        clock: Callable[[], float] = time.time,
        lead_seconds: float = REFRESH_LEAD_SECONDS,
    ) -> None:
        self.store = store
        self.post = post
        self.clock = clock
        self.lead_seconds = lead_seconds
        self._retry_at: dict[str, float] = {}

    def due(self, credential: OAuthCredential, now: float) -> bool:
        return bool(credential.refresh_token) and bool(credential.expires_at) and credential.expires_at - now <= self.lead_seconds

    def refresh_due(self) -> list[RefreshOutcome]:
        now = self.clock()
        outcomes: list[RefreshOutcome] = []
        for token in self.store.snapshot().tokens:
            if token.status != STATUS_ACTIVE or self._retry_at.get(token.token_id, 0.0) > now:
                continue
            credential = self.store.credential(token.token_id)
            if credential is not None and self.due(credential, now):
                outcomes.append(self.refresh(token.token_id))
        return outcomes

    def refresh(self, token_id: str, *, force: bool = False, stale_access_token: str = "") -> RefreshOutcome:
        """Refresh one token. ``stale_access_token`` is the token an upstream just
        rejected: when the store already holds another one, that is the result."""

        with exclusive_file_lock(self.store.directory / f"oauth-refresh-{token_id}"):
            snapshot = self.store.snapshot()
            token = snapshot.get(token_id)
            credential = self.store.credential(token_id)
            if token is None or credential is None:
                return RefreshOutcome(token_id, False, "unknown token")
            now = self.clock()
            adopted = newer_in_source(token, credential)
            if adopted is not None:
                self._store(token_id, adopted, now)
                credential = adopted
                if (stale_access_token and adopted.access_token != stale_access_token) or (
                    not stale_access_token and not force and not self.due(adopted, now)
                ):
                    return RefreshOutcome(token_id, True, "adopted from source")
            if stale_access_token and credential.access_token != stale_access_token:
                return RefreshOutcome(token_id, True, "already refreshed")
            if not force and not stale_access_token and not self.due(credential, now):
                return RefreshOutcome(token_id, False, "not due")
            if not credential.refresh_token:
                return self._fail(token_id, "no refresh token stored", terminal=True)
            endpoints = ENDPOINTS[token.provider]
            try:
                payload = token_request(endpoints, refresh_fields(endpoints, credential), self.post)
            except OAuthHttpError as error:
                return self._fail(token_id, str(error), terminal=error.terminal)
            except (OSError, ValueError) as error:
                return self._fail(token_id, f"{type(error).__name__}: {error}", terminal=False)
            fresh, email = credential_from_response(token.provider, payload, credential, now=now)
            if not self._store(token_id, fresh, now, email=email):
                return RefreshOutcome(token_id, False, "removed during refresh")
            self._retry_at.pop(token_id, None)
            try:
                written = write_source(token, fresh, now=now)
            except OSError as error:
                return RefreshOutcome(token_id, True, f"refreshed; source not updated: {error}")
            return RefreshOutcome(token_id, True, "refreshed; source updated" if written else "refreshed")

    def _store(self, token_id: str, credential: OAuthCredential, now: float, *, email: str = "") -> bool:
        with self.store.transaction() as current:
            state = current.get(token_id)
            if state is None:
                return False
            self.store.replace_credential(token_id, credential)
            state.refreshed_at = now
            state.expires_at = credential.expires_at
            state.email = state.email or email
            state.status = STATUS_ACTIVE
            state.last_error = ""
        return True

    def _fail(self, token_id: str, detail: str, *, terminal: bool) -> RefreshOutcome:
        self._retry_at[token_id] = self.clock() + RETRY_AFTER_FAILURE_SECONDS
        with self.store.transaction() as snapshot:
            state = snapshot.get(token_id)
            if state is not None:
                state.last_error = f"refresh failed: {detail}"[:500]
                if terminal:
                    state.status = STATUS_REFRESH_FAILED
        return RefreshOutcome(token_id, False, detail)


class OAuthTokenWatcher:
    """Router background thread: restore limited tokens and refresh expiring ones."""

    def __init__(
        self,
        pool: OAuthTokenPool,
        refresher: OAuthTokenRefresher,
        log: Callable[[str, str], None],
        *,
        interval_seconds: float = WATCH_INTERVAL_SECONDS,
    ) -> None:
        self.pool = pool
        self.refresher = refresher
        self.log = log
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        for token_id in self.pool.recover():
            self.log("INFO", f"oauth_token_restored token={token_id}")
        for outcome in self.refresher.refresh_due():
            level = "INFO" if outcome.refreshed else "WARN"
            self.log(level, f"oauth_token_refresh token={outcome.token_id} refreshed={outcome.refreshed} detail={outcome.detail}")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ciel-oauth-token-watcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as error:  # the watcher must outlive one bad tick
                self.log("WARN", f"oauth_token_watch_failed error={type(error).__name__}: {error}")
            self._stop.wait(self.interval_seconds)


__all__ = [
    "OAuthTokenRefresher",
    "OAuthTokenWatcher",
    "REFRESH_LEAD_SECONDS",
    "RefreshOutcome",
]
