"""Cursor commit policy for Ciel-owned message delivery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True, slots=True)
class ChannelDeliveryCursorPorts:
    response_status: Callable[[Any], int | None]
    metadata_enabled: Callable[[dict[str, Any] | None], bool]
    delivery_confirmed: Callable[[Any | None], bool]
    commit_if_newer: Callable[[int | None], None]
    log: Callable[[str, str], None]


class ChannelDeliveryCursorCommitter:
    """Commit a pending Channel cursor only after a confirmed HTTP response."""

    def __init__(self, ports: ChannelDeliveryCursorPorts) -> None:
        self.ports = ports

    @staticmethod
    def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
        try:
            value = metadata.get(key)
            if value is None or value == "":
                return None
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    def commit(
        self,
        body: dict[str, Any],
        handler: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(metadata, dict):
            body_metadata = body.get("metadata")
            metadata = body_metadata if isinstance(body_metadata, dict) else {}
        if not metadata:
            return
        if handler is not None:
            status = self.ports.response_status(handler)
            if status is None or status < 200 or status >= 400:
                self.ports.log(
                    "INFO",
                    "channel_delivery_cursor_deferred "
                    f"status={status if status is not None else '-'}",
                )
                return
            if (
                self.ports.metadata_enabled(metadata)
                and not self.ports.delivery_confirmed(handler)
            ):
                reason = str(
                    getattr(
                        handler,
                        "_ciel_runtime_channel_delivery_reason",
                        "unconfirmed",
                    )
                    or "unconfirmed"
                )
                self.ports.log(
                    "INFO",
                    f"channel_delivery_cursor_deferred reason={reason}",
                )
                return
        cursor = self._metadata_int(
            metadata,
            "ciel_runtime_channel_cursor_last_id",
        )
        self.ports.commit_if_newer(cursor)
