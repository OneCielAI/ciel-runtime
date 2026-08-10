"""Retry an upstream response stream only before its first delivered byte."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any


class InitialStreamRetry:
    """Iterable response wrapper with a replay-safe initial reconnect window."""

    def __init__(
        self,
        response: Any,
        reopen: Callable[[int], Any],
        retries: int,
        retryable: Callable[[BaseException], bool],
        wait_seconds: Callable[[int], float],
        sleep: Callable[[float], None],
        on_retry: Callable[[int, BaseException], None],
    ) -> None:
        self._response = response
        self._reopen = reopen
        self._retries = max(0, int(retries))
        self._retryable = retryable
        self._wait_seconds = wait_seconds
        self._sleep = sleep
        self._on_retry = on_retry

    @staticmethod
    def _close(response: Any) -> None:
        try:
            response.close()
        except Exception:
            pass

    def __iter__(self) -> Iterator[Any]:
        attempt = 0
        response = self._response
        while True:
            delivered = False
            failure: BaseException | None = None
            try:
                for chunk in response:
                    delivered = True
                    yield chunk
                if delivered:
                    self._response = response
                    return
                failure = EOFError("upstream stream ended before its first byte")
            except BaseException as error:
                failure = error
            if delivered or attempt >= self._retries or not self._retryable(failure):
                raise failure
            while True:
                attempt += 1
                self._close(response)
                self._on_retry(attempt, failure)
                self._sleep(max(0.0, float(self._wait_seconds(attempt))))
                try:
                    response = self._reopen(attempt)
                    self._response = response
                    break
                except BaseException as error:
                    failure = error
                    if attempt >= self._retries or not self._retryable(error):
                        raise

    def close(self) -> None:
        self._close(self._response)
