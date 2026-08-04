"""Durable channel message queue and launch-dedupe bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .channel_launch_guard_repository import ChannelLaunchGuardRepository
from .channel_message_dedupe import (
    ChannelMessageDedupePorts,
    ChannelMessageDedupeService,
)
from .channel_message_repository import (
    ChannelMessageAppendPorts,
    ChannelMessageRepository,
)


@dataclass(frozen=True, slots=True)
class ChannelMessageStoragePorts:
    path: Callable[[], Path]
    max_bytes: int
    condition: Any
    file_lock: Callable[[Path], Any]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelMessageIdentityPorts:
    stable_key: Callable[[dict[str, Any]], Any]
    fallback_key: Callable[[dict[str, Any]], Any]
    timestamp_seconds: Callable[[Any], float]
    normalize_recipients: Callable[[Any], list[str]]


@dataclass(frozen=True, slots=True)
class ChannelMessageLaunchPorts:
    guard_path: Callable[[], Path]
    now: Callable[[], float]
    recent_scan_limit: int
    fallback_ttl_seconds: float


@dataclass(frozen=True, slots=True)
class ChannelMessageCachePorts:
    read_next_id: Callable[[], int | None]
    write_next_id: Callable[[int], None]


@dataclass(frozen=True, slots=True)
class ChannelMessageContext:
    storage: ChannelMessageStoragePorts
    identity: ChannelMessageIdentityPorts
    launch: ChannelMessageLaunchPorts
    cache: ChannelMessageCachePorts

    def repository(self) -> ChannelMessageRepository:
        return ChannelMessageRepository(
            path=self.storage.path(),
            log=self.storage.log,
            max_bytes=self.storage.max_bytes,
        )

    def max_id(self) -> int:
        return self.repository().max_id()

    def max_id_before_epoch(self, cutoff_epoch: float) -> int:
        return self.repository().max_id_before_epoch(cutoff_epoch)

    def file_lock(self) -> Any:
        return self.storage.file_lock(self.storage.path())

    def read(
        self,
        after_id: int = 0,
        channel: str | None = None,
        recipient: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.repository().read(after_id, channel, recipient, limit)

    def read_before(
        self,
        before_id: int = 0,
        channel: str | None = None,
        recipient: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.repository().read_before(before_id, channel, recipient, limit)

    def recent_rows(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self.repository().recent_rows(
            self.launch.recent_scan_limit if limit is None else limit
        )

    def launch_guard_repository(self) -> ChannelLaunchGuardRepository:
        return ChannelLaunchGuardRepository(
            path=self.launch.guard_path(),
            now=self.launch.now,
            log=self.storage.log,
        )

    def launch_guard(self) -> dict[str, Any] | None:
        return self.launch_guard_repository().read()

    def write_launch_guard(
        self, max_existing_id: int, ttl_seconds: float = 180.0
    ) -> None:
        self.launch_guard_repository().write(max_existing_id, ttl_seconds)

    def duplicate(self, message: dict[str, Any]) -> dict[str, Any] | None:
        return ChannelMessageDedupeService(
            ports=ChannelMessageDedupePorts(
                stable_key=self.identity.stable_key,
                fallback_key=self.identity.fallback_key,
                recent_rows=self.recent_rows,
                launch_guard=self.launch_guard,
                timestamp_seconds=self.identity.timestamp_seconds,
                now=self.launch.now,
            ),
            fallback_ttl_seconds=self.launch.fallback_ttl_seconds,
        ).duplicate(message)

    def initialize_next_id(self) -> int:
        cached = self.cache.read_next_id()
        if cached is not None:
            return cached
        next_id = self.max_id() + 1
        self.cache.write_next_id(next_id)
        return next_id

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = self.repository().append(
            payload,
            ChannelMessageAppendPorts(
                self.storage.condition,
                self.file_lock,
                self.duplicate,
                self.identity.normalize_recipients,
            ),
        )
        self.cache.write_next_id(int(message.get("id") or 0) + 1)
        return message


@dataclass(frozen=True, slots=True)
class ChannelMessageCompatibilityApi:
    context: Callable[[], ChannelMessageContext]

    def repository(self) -> ChannelMessageRepository:
        return self.context().repository()

    def max_id(self) -> int:
        return self.context().max_id()

    def max_id_before_epoch(self, cutoff_epoch: float) -> int:
        return self.context().max_id_before_epoch(cutoff_epoch)

    def file_lock(self) -> Any:
        return self.context().file_lock()

    def read(
        self,
        after_id: int = 0,
        channel: str | None = None,
        recipient: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.context().read(after_id, channel, recipient, limit)

    def read_before(
        self,
        before_id: int = 0,
        channel: str | None = None,
        recipient: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.context().read_before(before_id, channel, recipient, limit)

    def recent_rows(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self.context().recent_rows(limit)

    def launch_guard_repository(self) -> ChannelLaunchGuardRepository:
        return self.context().launch_guard_repository()

    def launch_guard(self) -> dict[str, Any] | None:
        return self.context().launch_guard()

    def write_launch_guard(
        self, max_existing_id: int, ttl_seconds: float = 180.0
    ) -> None:
        self.context().write_launch_guard(max_existing_id, ttl_seconds)

    def duplicate(self, message: dict[str, Any]) -> dict[str, Any] | None:
        return self.context().duplicate(message)

    def initialize_next_id(self) -> int:
        return self.context().initialize_next_id()

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.context().append(payload)


__all__ = [
    "ChannelMessageCachePorts",
    "ChannelMessageCompatibilityApi",
    "ChannelMessageContext",
    "ChannelMessageIdentityPorts",
    "ChannelMessageLaunchPorts",
    "ChannelMessageStoragePorts",
]
