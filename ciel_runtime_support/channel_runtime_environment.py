"""Bounded environment configuration policy for Channel runtime behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ChannelRuntimeEnvironmentPolicy:
    environment: Mapping[str, str]
    launch_recent_default: float
    probe_timeout_default: float

    def launch_recent_seconds(self) -> float:
        raw = str(
            self.environment.get(
                "CIEL_RUNTIME_CHANNEL_LAUNCH_RECENT_SECONDS",
                "",
            )
            or ""
        ).strip()
        if not raw:
            return self.launch_recent_default
        return self._float(raw, self.launch_recent_default)

    def probe_timeout_seconds(self) -> float:
        raw = self.environment.get(
            "CIEL_RUNTIME_CHANNEL_PROBE_TIMEOUT_SECONDS"
        )
        if raw is None:
            return self.probe_timeout_default
        value = self._float(str(raw).strip(), self.probe_timeout_default)
        return value if value > 0 else self.probe_timeout_default

    def pending_scan_limit(self) -> int:
        return self._bounded_int(
            self.environment.get(
                "CIEL_RUNTIME_CHANNEL_PENDING_SCAN_LIMIT",
                "500",
            ),
            default=500,
            minimum=100,
            maximum=5000,
        )

    def wake_batch_limit(self) -> int:
        return self._bounded_int(
            self.environment.get(
                "CIEL_RUNTIME_CHANNEL_WAKE_BATCH_LIMIT",
                "8",
            ),
            default=8,
            minimum=1,
            maximum=50,
        )

    def wake_claim_ttl_seconds(self) -> float:
        return self._bounded_float(
            self.environment.get(
                "CIEL_RUNTIME_CHANNEL_WAKE_CLAIM_TTL_SECONDS"
            ),
            default=300.0,
            minimum=5.0,
            maximum=1800.0,
        )

    def unseen_retry_seconds(self) -> float:
        return self._bounded_float(
            self.environment.get(
                "CIEL_RUNTIME_CHANNEL_WAKE_UNSEEN_RETRY_SECONDS"
            ),
            default=20.0,
            minimum=2.0,
            maximum=300.0,
        )

    def inflight_stale_seconds(self) -> float:
        return self._bounded_float(
            self.environment.get(
                "CIEL_RUNTIME_CHANNEL_WAKE_INFLIGHT_STALE_SECONDS"
            ),
            default=180.0,
            minimum=30.0,
            maximum=1800.0,
        )

    def codex_submit_retries(self) -> int:
        return self._bounded_int(
            self.environment.get(
                "CIEL_RUNTIME_CODEX_CHANNEL_WAKE_SUBMIT_RETRIES"
            ),
            default=4,
            minimum=1,
            maximum=8,
        )

    def codex_submit_delay_seconds(self) -> float:
        return self._bounded_milliseconds(
            self.environment.get(
                "CIEL_RUNTIME_CODEX_CHANNEL_WAKE_SUBMIT_DELAY_MS"
            ),
            default=0.25,
            minimum=0.13,
            maximum=5.0,
        )

    def windows_startup_grace_seconds(self) -> float:
        return self._bounded_milliseconds(
            self.environment.get(
                "CIEL_RUNTIME_WINDOWS_CHANNEL_STARTUP_GRACE_MS"
            ),
            default=8.0,
            minimum=0.0,
            maximum=60.0,
        )

    @staticmethod
    def inflight_is_stale(
        state: str,
        started_at: float,
        current_time: float,
        stale_seconds: float,
    ) -> bool:
        return bool(
            state in {"queued", "unknown"}
            and started_at > 0
            and current_time - started_at >= stale_seconds
        )

    @staticmethod
    def _float(raw: str, default: float) -> float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _bounded_float(
        cls,
        raw: str | None,
        *,
        default: float,
        minimum: float,
        maximum: float,
    ) -> float:
        if raw is None:
            return default
        value = cls._float(raw, default)
        return max(minimum, min(maximum, value))

    @staticmethod
    def _bounded_int(
        raw: str | None,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, value))

    @classmethod
    def _bounded_milliseconds(
        cls,
        raw: str | None,
        *,
        default: float,
        minimum: float,
        maximum: float,
    ) -> float:
        if raw is None:
            return default
        value = cls._float(raw, default * 1000.0) / 1000.0
        return max(minimum, min(maximum, value))


__all__ = ["ChannelRuntimeEnvironmentPolicy"]
