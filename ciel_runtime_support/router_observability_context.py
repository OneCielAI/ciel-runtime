"""Router request, response, and SSE observability bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .request_trace import (
    RequestTracePolicy,
    RequestTraceProjection,
    RequestTraceServices,
    ResponseTraceController,
    RouterMessagePreviewPolicy,
    dump_request_for_trace,
    summarize_messages_for_trace,
)
from .sse_trace import (
    SseTraceConfig,
    SseTracePorts,
    SseTraceRepository,
    summarize_payload,
)


@dataclass(frozen=True, slots=True)
class RouterPreviewPorts:
    environment: Mapping[str, str]
    load_config: Callable[[], dict[str, Any]]
    positive_int: Callable[..., int]
    latest_user_text: Callable[..., str]
    redact_sensitive_text: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class RequestTraceConfiguration:
    request_path: Path
    response_path: Path
    request_max_bytes: int
    response_max_bytes: int
    response_text_limit: int
    trace_level: int


@dataclass(frozen=True, slots=True)
class RequestTracePorts:
    current_log_level: Callable[[], int]
    content_to_text: Callable[..., str]
    thinking_block_count: Callable[..., int]
    tool_continuation_block_count: Callable[..., int]
    log: Callable[[str, str], None]
    usage_record: Callable[..., Any]
    event_publish: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class SseTraceConfiguration:
    config_dir: Path
    last_path: Path
    trace_path: Path
    tool_call_path: Path
    event_limit: int
    payload_limit: int
    max_bytes: int
    trace_level: int


@dataclass(frozen=True, slots=True)
class SseObservabilityPorts:
    environment: Mapping[str, str]
    current_log_level: Callable[[], int]
    truncate: Callable[[str, int], str]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class RouterObservabilityContext:
    preview: RouterPreviewPorts
    request_config: RequestTraceConfiguration
    request: RequestTracePorts
    sse_config: SseTraceConfiguration
    sse: SseObservabilityPorts

    def message_preview_policy(self) -> RouterMessagePreviewPolicy:
        return RouterMessagePreviewPolicy(
            self.preview.environment,
            self.preview.load_config,
            self.preview.positive_int,
            self.preview.latest_user_text,
            self.preview.redact_sensitive_text,
        )

    def preview_chars(self, cfg: dict[str, Any] | None = None) -> int:
        return self.message_preview_policy().configured_chars(cfg)

    def event_preview(
        self, body: dict[str, Any], cfg: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.message_preview_policy().project(body, cfg)

    def request_projection(self) -> RequestTraceProjection:
        return RequestTraceProjection(
            content_to_text=self.request.content_to_text,
            thinking_block_count=self.request.thinking_block_count,
            tool_continuation_block_count=(
                self.request.tool_continuation_block_count
            ),
        )

    def request_services(self) -> RequestTraceServices:
        return RequestTraceServices(
            policy=RequestTracePolicy(
                enabled=lambda: self.request.current_log_level()
                >= self.request_config.trace_level,
                request_path=self.request_config.request_path,
                response_path=self.request_config.response_path,
                request_max_bytes=self.request_config.request_max_bytes,
                response_max_bytes=self.request_config.response_max_bytes,
                response_text_limit=self.request_config.response_text_limit,
            ),
            projection=self.request_projection(),
            log=self.request.log,
        )

    def summarize_messages(
        self, messages: Any, max_messages: int = 30
    ) -> list[dict[str, Any]]:
        return summarize_messages_for_trace(
            messages,
            self.request_projection(),
            max_messages=max_messages,
        )

    def dump_request(
        self, provider: str, path: str, body: dict[str, Any]
    ) -> None:
        dump_request_for_trace(
            provider, path, body, self.request_services()
        )

    def response_controller(self) -> ResponseTraceController:
        return ResponseTraceController(
            self.request.usage_record,
            self.request.event_publish,
            self.request_services,
            self.request.log,
        )

    def dump_response(self, *args: Any, **kwargs: Any) -> None:
        self.response_controller().write(*args, **kwargs)

    def sse_enabled(self) -> bool:
        value = str(
            self.sse.environment.get("CIEL_RUNTIME_SSE_TRACE", "")
        ).strip().lower()
        if value in {"1", "true", "yes", "on", "trace"}:
            return True
        return self.sse.current_log_level() >= self.sse_config.trace_level

    def sse_repository(self) -> SseTraceRepository:
        return SseTraceRepository(
            SseTraceConfig(
                self.sse_config.config_dir,
                self.sse_config.last_path,
                self.sse_config.trace_path,
                self.sse_config.tool_call_path,
                self.sse_config.event_limit,
                self.sse_config.payload_limit,
                self.sse_config.max_bytes,
            ),
            SseTracePorts(self.sse_enabled, self.sse.truncate, self.sse.log),
        )

    def summarize_sse_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return summarize_payload(payload, self.sse.truncate)

    def begin_sse(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.sse_repository().begin(*args, **kwargs)

    def record_sse(self, *args: Any, **kwargs: Any) -> None:
        self.sse_repository().record(*args, **kwargs)

    def finish_sse(self, *args: Any, **kwargs: Any) -> None:
        self.sse_repository().finish_stream(*args, **kwargs)

    def append_tool_call(self, *args: Any, **kwargs: Any) -> None:
        self.sse_repository().append_tool_call(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class RouterObservabilityCompatibilityApi:
    context: Callable[[], RouterObservabilityContext]

    def message_preview_policy(self) -> RouterMessagePreviewPolicy:
        return self.context().message_preview_policy()

    def preview_chars(self, *args: Any, **kwargs: Any) -> int:
        return self.context().preview_chars(*args, **kwargs)

    def event_preview(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.context().event_preview(*args, **kwargs)

    def request_projection(self) -> RequestTraceProjection:
        return self.context().request_projection()

    def request_services(self) -> RequestTraceServices:
        return self.context().request_services()

    def summarize_messages(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return self.context().summarize_messages(*args, **kwargs)

    def dump_request(self, *args: Any, **kwargs: Any) -> None:
        self.context().dump_request(*args, **kwargs)

    def response_controller(self) -> ResponseTraceController:
        return self.context().response_controller()

    def dump_response(self, *args: Any, **kwargs: Any) -> None:
        self.context().dump_response(*args, **kwargs)

    def sse_enabled(self) -> bool:
        return self.context().sse_enabled()

    def sse_repository(self) -> SseTraceRepository:
        return self.context().sse_repository()

    def summarize_sse_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.context().summarize_sse_payload(payload)

    def begin_sse(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.context().begin_sse(*args, **kwargs)

    def record_sse(self, *args: Any, **kwargs: Any) -> None:
        self.context().record_sse(*args, **kwargs)

    def finish_sse(self, *args: Any, **kwargs: Any) -> None:
        self.context().finish_sse(*args, **kwargs)

    def append_tool_call(self, *args: Any, **kwargs: Any) -> None:
        self.context().append_tool_call(*args, **kwargs)


__all__ = [
    "RequestTraceConfiguration",
    "RequestTracePorts",
    "RouterObservabilityCompatibilityApi",
    "RouterObservabilityContext",
    "RouterPreviewPorts",
    "SseObservabilityPorts",
    "SseTraceConfiguration",
]
