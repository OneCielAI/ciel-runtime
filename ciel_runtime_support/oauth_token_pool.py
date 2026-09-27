"""Which stored OAuth token a routed request uses.

Rules (agreed 2026-09-27):

- A conversation stays on its token for as long as the token has usage left,
  across turns.
- A token whose observed usage reaches the threshold is *draining*: new
  conversations avoid it, and a conversation pinned to it moves to another
  token only when a new user turn starts, never inside a tool loop.
- A token the upstream refuses for a usage limit is out of rotation until the
  reported reset; then it is available again. Such a refusal moves the
  request to another token right away (the router retries before any output).
- Tokens are filled in store order: a new conversation takes the first token
  that is not draining, so one account is used up before the next starts.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Iterable

from ciel_runtime_support.oauth_token_store import (
    STATUS_ACTIVE,
    OAuthCredential,
    OAuthStoreSnapshot,
    OAuthTokenState,
    OAuthTokenStore,
)
from ciel_runtime_support.oauth_usage_signals import UsageObservation

DEFAULT_THRESHOLD_PERCENT = 95.0
SESSION_PIN_TTL_SECONDS = 12 * 3600.0


@dataclass(frozen=True, slots=True)
class OAuthLease:
    token_id: str
    provider: str
    credential: OAuthCredential
    # The token the conversation was on before this request, when it moved.
    moved_from: str = ""
    reason: str = ""


class OAuthTokenPool:
    def __init__(
        self,
        store: OAuthTokenStore,
        *,
        threshold_percent: float = DEFAULT_THRESHOLD_PERCENT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.threshold_percent = threshold_percent
        self.clock = clock

    def acquire(
        self,
        provider: str,
        session_key: str,
        *,
        turn_start: bool,
        exclude: Iterable[str] = (),
    ) -> OAuthLease | None:
        """Pick the token for one upstream request; None when the store has none."""

        excluded = set(exclude)
        now = self.clock()
        with self.store.transaction() as snapshot:
            self._recover(snapshot, now)
            _prune_pins(snapshot, now)
            tokens = [token for token in snapshot.for_provider(provider) if token.status == STATUS_ACTIVE]
            if not tokens:
                return None
            pin_key = f"{provider}:{session_key or 'default'}"
            pinned = snapshot.get(str(snapshot.sessions.get(pin_key, {}).get("token_id") or ""))
            usable = [token for token in tokens if token.token_id not in excluded and token.exhausted_until <= now]
            choice, reason = self._choose(pinned, usable, tokens, excluded, turn_start, now)
            choice.last_used_at = now
            snapshot.sessions[pin_key] = {"token_id": choice.token_id, "seen_at": now}
            moved_from = pinned.token_id if pinned is not None and pinned.token_id != choice.token_id else ""
            token_id = choice.token_id
        credential = self.store.credential(token_id)
        if credential is None:
            return None
        return OAuthLease(token_id, provider, credential, moved_from, reason if moved_from else "")

    def _choose(
        self,
        pinned: OAuthTokenState | None,
        usable: list[OAuthTokenState],
        tokens: list[OAuthTokenState],
        excluded: set[str],
        turn_start: bool,
        now: float,
    ) -> tuple[OAuthTokenState, str]:
        if pinned is not None and pinned in usable:
            if not pinned.draining or not turn_start:
                return pinned, ""
            fresh = [token for token in usable if not token.draining and token is not pinned]
            if fresh:
                return fresh[0], f"{pinned.token_id} reached {self.threshold_percent:g}% usage"
            return pinned, ""
        fresh = [token for token in usable if not token.draining]
        if fresh:
            reason = "limit reached" if pinned is not None and pinned.exhausted_until > now else "unpinned"
            if pinned is not None and pinned.token_id in excluded:
                reason = "upstream refused the previous token"
            return fresh[0], reason
        if usable:
            return min(usable, key=_peak), "all tokens are draining"
        # Every token is out of rotation: send the one that frees first and let
        # the upstream answer with its own limit message.
        remaining = [token for token in tokens if token.token_id not in excluded] or tokens
        return min(remaining, key=lambda token: token.exhausted_until), "all tokens are limited"

    def observe(self, token_id: str, observation: UsageObservation) -> None:
        if observation.empty:
            return
        now = self.clock()
        with self.store.transaction() as snapshot:
            token = snapshot.get(token_id)
            if token is None:
                return
            for name, (used, reset_at) in observation.windows.items():
                token.usage[name] = {"used_percent": round(used, 2), "reset_at": reset_at}
            if observation.windows:
                token.usage_observed_at = now
            if observation.limited or observation.transient:
                token.exhausted_until = max(token.exhausted_until, observation.reset_at)
                token.last_error = "usage limit reached" if observation.limited else "rate limited"
            elif token.exhausted_until and token.exhausted_until <= now:
                token.exhausted_until = 0.0
            token.draining = _peak(token) >= self.threshold_percent

    def recover(self) -> list[str]:
        """Return tokens whose limit reset has passed to service; the ids restored."""

        with self.store.transaction() as snapshot:
            return self._recover(snapshot, self.clock())

    def _recover(self, snapshot: OAuthStoreSnapshot, now: float) -> list[str]:
        restored: list[str] = []
        for token in snapshot.tokens:
            was_out = bool(token.exhausted_until) or token.draining
            changed = False
            if token.exhausted_until and token.exhausted_until <= now:
                token.exhausted_until = 0.0
                token.last_error = ""
                changed = True
            for name, window in list(token.usage.items()):
                reset_at = float(window.get("reset_at") or 0.0)
                if reset_at and reset_at <= now:
                    del token.usage[name]
                    changed = True
            draining = _peak(token) >= self.threshold_percent
            if draining != token.draining:
                token.draining = draining
                changed = True
            if was_out and changed and not token.exhausted_until and not token.draining:
                restored.append(token.token_id)
        return restored


def _peak(token: OAuthTokenState) -> float:
    return max((float(window.get("used_percent") or 0.0) for window in token.usage.values()), default=0.0)


def _prune_pins(snapshot: OAuthStoreSnapshot, now: float) -> None:
    ids = {token.token_id for token in snapshot.tokens}
    snapshot.sessions = {
        key: pin
        for key, pin in snapshot.sessions.items()
        if pin.get("token_id") in ids and now - float(pin.get("seen_at") or 0.0) < SESSION_PIN_TTL_SECONDS
    }


__all__ = ["DEFAULT_THRESHOLD_PERCENT", "OAuthLease", "OAuthTokenPool"]
