"""Channel tool-context and durable LLM delivery cursor bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Protocol

from .channel_cursor_repository import (
    ChannelCursorRepository,
    ChannelCursorStatePolicy,
)
from .channel_cursor_service import (
    ChannelDeliveryCursorCommitter,
    ChannelDeliveryCursorPorts,
)
from .channel_tool_context import (
    ChannelToolContextPolicy,
    ChannelToolContextPorts,
    ChannelToolContextRepository,
    ChannelToolContextService,
)


class LockPort(Protocol):
    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ChannelToolContextFactoryPorts:
    contexts: dict[str, dict[str, Any]]
    lock: LockPort
    context_limit: int
    max_inject: int
    prompt_limit: int
    content_to_text: Callable[[Any], str]
    truncate: Callable[..., str]
    now: Callable[[], float]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelLlmCursorPorts:
    repository: Callable[[Path], ChannelCursorRepository]
    cursor_path: Path
    clear_floor_path: Path
    lock: LockPort
    cache_read: Callable[[], int | None]
    cache_write: Callable[[int | None], None]
    scan_max_id: Callable[[], int]
    now: Callable[[], float]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelLaunchCursorPorts:
    recent_seconds: Callable[[], float]
    scan_max_before_epoch: Callable[[float], int]
    write_launch_guard: Callable[[int], None]


@dataclass(frozen=True, slots=True)
class ChannelDeliveryCommitPorts:
    response_status: Callable[[Any], int | None]
    metadata_enabled: Callable[[dict[str, Any] | None], bool]
    delivery_confirmed: Callable[[Any], bool]
    read_cursor: Callable[[], int]
    write_cursor: Callable[[int], None]


@dataclass(frozen=True, slots=True)
class ChannelDeliveryContext:
    tools: ChannelToolContextFactoryPorts
    cursor: ChannelLlmCursorPorts
    launch: ChannelLaunchCursorPorts
    commit: ChannelDeliveryCommitPorts

    def tool_context_service(self) -> ChannelToolContextService:
        ports = self.tools
        return ChannelToolContextService(
            repository=ChannelToolContextRepository(
                contexts=ports.contexts,
                lock=ports.lock,
                limit=ports.context_limit,
            ),
            policy=ChannelToolContextPolicy(
                max_inject=ports.max_inject,
                prompt_limit=ports.prompt_limit,
            ),
            ports=ChannelToolContextPorts(
                content_to_text=ports.content_to_text,
                truncate=ports.truncate,
                now=ports.now,
                log=ports.log,
            ),
        )

    def injected_prompt_text(self, body: dict[str, Any]) -> str:
        return self.tool_context_service().prompt_text(body)

    def remember_injected_tool_use(
        self,
        source_body: dict[str, Any] | None,
        tool_use_id: str,
        tool_name: str,
        tool_input: Any,
    ) -> None:
        self.tool_context_service().remember(
            source_body, tool_use_id, tool_name, tool_input
        )

    def remember_injected_tool_uses(
        self, source_body: dict[str, Any] | None, message: dict[str, Any]
    ) -> None:
        self.tool_context_service().remember_message(source_body, message)

    def take_tool_result_contexts(
        self, body: dict[str, Any]
    ) -> list[tuple[str, dict[str, Any]]]:
        return self.tool_context_service().repository.take_for_body(
            body, self.tools.max_inject
        )

    def body_with_tool_result_context(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self.tool_context_service().inject_followup(body)

    def write_cursor(self, last_id: int) -> None:
        self.cursor.repository(self.cursor.cursor_path).write(last_id)

    def read_cursor(self) -> int:
        resolution = ChannelCursorStatePolicy.resolve_read(
            self.cursor.repository(self.cursor.cursor_path).read(),
            self.cursor.cache_read(),
            self.cursor.scan_max_id,
        )
        self.cursor.cache_write(resolution.value)
        if resolution.rolled_back:
            self.cursor.log(
                "WARN",
                "channel_llm_cursor_queue_generation_reset "
                f"recovered_cursor={resolution.value}",
            )
        if resolution.persist:
            self.write_cursor(resolution.value)
        return resolution.value

    def read_clear_floor(self) -> int:
        return self.cursor.repository(self.cursor.clear_floor_path).read() or 0

    def write_clear_floor(self, last_id: int) -> None:
        self.cursor.repository(self.cursor.clear_floor_path).write(
            last_id, metadata={"updated_at": self.cursor.now()}
        )

    def clamp_to_clear_floor(self, recovered: int) -> int:
        clear_floor = self.read_clear_floor()
        if clear_floor > 0 and recovered < clear_floor:
            self.cursor.log(
                "INFO",
                "channel_stdin_proxy_recovery_clamped "
                f"recovered_cursor={recovered} clear_floor={clear_floor}",
            )
            return clear_floor
        return recovered

    def reset_cursor(self, last_id: int | None = None) -> int:
        with self.cursor.lock:
            target = max(
                0,
                int(
                    last_id
                    if last_id is not None
                    else self.cursor.scan_max_id()
                ),
            )
            self.cursor.cache_write(target)
            self.write_cursor(target)
            return target

    def ensure_cursor_initialized(self) -> int:
        with self.cursor.lock:
            return self.read_cursor()

    def prepare_for_launch(self) -> int:
        current = self.ensure_cursor_initialized()
        recent_seconds = self.launch.recent_seconds()
        if recent_seconds <= 0:
            target = self.cursor.scan_max_id()
        else:
            target = self.launch.scan_max_before_epoch(
                self.cursor.now() - recent_seconds
            )
        last_id = self.reset_cursor(max(current, target))
        self.launch.write_launch_guard(last_id)
        self.cursor.log(
            "INFO",
            "channel_llm_cursor_fast_forward_on_launch "
            f"last_id={last_id} previous_cursor={current} "
            f"recent_seconds={recent_seconds:g}",
        )
        return last_id

    def commit_if_newer(self, last_id: int | None) -> None:
        with self.cursor.lock:
            current = self.commit.read_cursor()
            target = ChannelCursorStatePolicy.newer(last_id, current)
            if target is None:
                return
            self.cursor.cache_write(target)
            try:
                self.commit.write_cursor(target)
            except Exception as exc:
                self.cursor.log(
                    "WARN",
                    "channel_llm_cursor_write_failed "
                    f"error={type(exc).__name__}: {exc}",
                )

    def commit_pending(
        self,
        body: dict[str, Any],
        handler: BaseHTTPRequestHandler | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        ChannelDeliveryCursorCommitter(
            ChannelDeliveryCursorPorts(
                response_status=self.commit.response_status,
                metadata_enabled=self.commit.metadata_enabled,
                delivery_confirmed=self.commit.delivery_confirmed,
                commit_if_newer=self.commit_if_newer,
                log=self.cursor.log,
            )
        ).commit(body, handler, metadata)


@dataclass(frozen=True, slots=True)
class ChannelDeliveryCompatibilityApi:
    context: Callable[[], ChannelDeliveryContext]

    def tool_context_service(self) -> ChannelToolContextService:
        return self.context().tool_context_service()

    def injected_prompt_text(self, body: dict[str, Any]) -> str:
        return self.context().injected_prompt_text(body)

    def remember_injected_tool_use(
        self,
        source_body: dict[str, Any] | None,
        tool_use_id: str,
        tool_name: str,
        tool_input: Any,
    ) -> None:
        self.context().remember_injected_tool_use(
            source_body, tool_use_id, tool_name, tool_input
        )

    def remember_injected_tool_uses(
        self, source_body: dict[str, Any] | None, message: dict[str, Any]
    ) -> None:
        self.context().remember_injected_tool_uses(source_body, message)

    def take_tool_result_contexts(
        self, body: dict[str, Any]
    ) -> list[tuple[str, dict[str, Any]]]:
        return self.context().take_tool_result_contexts(body)

    def body_with_tool_result_context(
        self, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self.context().body_with_tool_result_context(body)

    def write_cursor(self, last_id: int) -> None:
        self.context().write_cursor(last_id)

    def read_cursor(self) -> int:
        return self.context().read_cursor()

    def read_clear_floor(self) -> int:
        return self.context().read_clear_floor()

    def write_clear_floor(self, last_id: int) -> None:
        self.context().write_clear_floor(last_id)

    def clamp_to_clear_floor(self, recovered: int) -> int:
        return self.context().clamp_to_clear_floor(recovered)

    def reset_cursor(self, last_id: int | None = None) -> int:
        return self.context().reset_cursor(last_id)

    def ensure_cursor_initialized(self) -> int:
        return self.context().ensure_cursor_initialized()

    def prepare_for_launch(self) -> int:
        return self.context().prepare_for_launch()

    def commit_if_newer(self, last_id: int | None) -> None:
        self.context().commit_if_newer(last_id)

    def commit_pending(
        self,
        body: dict[str, Any],
        handler: BaseHTTPRequestHandler | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.context().commit_pending(body, handler, metadata)


__all__ = [
    "ChannelDeliveryCommitPorts",
    "ChannelDeliveryCompatibilityApi",
    "ChannelDeliveryContext",
    "ChannelLaunchCursorPorts",
    "ChannelLlmCursorPorts",
    "ChannelToolContextFactoryPorts",
    "LockPort",
]
