"""Path-aware request-body limits and shared memory admission for the router."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from .request_limits_config import (
    GENERAL_CONTROL_REQUEST_MAX_BYTES,
    MIB,
    REQUEST_BODY_MEMORY_MULTIPLIER,
    REQUEST_LIMIT_SPEC_BY_KEY,
    REQUEST_LIMIT_SPECS,
    TTS_BATCH_REQUEST_MAX_BYTES,
    WorkspaceRequestLimits,
    base64_json_wire_max_bytes,
    resolve_workspace_request_limits,
)


# This is a transport circuit breaker, not a model/context limit.  Providers
# remain responsible for their own request and context-window validation.
MODEL_REQUEST_DEFAULT_BYTES = REQUEST_LIMIT_SPEC_BY_KEY[
    "model_request_max_bytes"
].default_bytes
MODEL_REQUEST_HARD_MAX_BYTES = REQUEST_LIMIT_SPEC_BY_KEY[
    "model_request_max_bytes"
].hard_max_bytes
GENERAL_REQUEST_MAX_BYTES = GENERAL_CONTROL_REQUEST_MAX_BYTES
WEBHOOK_REQUEST_MAX_BYTES = 1 * MIB
CHAT_FILE_REQUEST_MAX_BYTES = base64_json_wire_max_bytes(
    REQUEST_LIMIT_SPEC_BY_KEY["chat_attachment_max_bytes"].default_bytes
)
SPEECH_REFERENCE_REQUEST_MAX_BYTES = base64_json_wire_max_bytes(
    REQUEST_LIMIT_SPEC_BY_KEY["tts_reference_audio_max_bytes"].default_bytes
)
SPEECH_MEDIA_REQUEST_MAX_BYTES = base64_json_wire_max_bytes(
    REQUEST_LIMIT_SPEC_BY_KEY["speech_audio_max_bytes"].default_bytes
)
SPEECH_BATCH_REQUEST_MAX_BYTES = TTS_BATCH_REQUEST_MAX_BYTES
DEFAULT_INFLIGHT_REQUEST_BYTES = REQUEST_LIMIT_SPEC_BY_KEY[
    "inflight_request_max_bytes"
].default_bytes
INFLIGHT_REQUEST_HARD_MAX_BYTES = REQUEST_LIMIT_SPEC_BY_KEY[
    "inflight_request_max_bytes"
].hard_max_bytes

MODEL_REQUEST_MAX_ENV = "CIEL_RUNTIME_ROUTER_MODEL_REQUEST_MAX_BYTES"
INFLIGHT_REQUEST_MAX_ENV = "CIEL_RUNTIME_ROUTER_INFLIGHT_REQUEST_BYTES"

_MODEL_REQUEST_PATHS = frozenset(
    {
        "/v1/responses",
        "/v1/responses/compact",
        "/v1/messages",
        "/v1/messages/count_tokens",
        "/v1/chat/completions",
    }
)
_CHAT_FILE_PATHS = frozenset({"/ca/chat/files", "/ca/channel/files"})
_SPEECH_REFERENCE_PATHS = frozenset(
    {
        "/ca/speech/config",
        "/v1/audio/speech",
    }
)
_SPEECH_MEDIA_PATHS = frozenset(
    {
        "/v1/audio/transcriptions",
        "/v1/audio/translations",
    }
)


class RequestBodyTooLarge(ValueError):
    """The declared body exceeds the transport ceiling for its route."""

    def __init__(self, *, path: str, received: int, limit: int) -> None:
        super().__init__(
            f"request body for {path} is {received} bytes; maximum is {limit} bytes"
        )
        self.path = path
        self.received = received
        self.limit = limit


class RequestBodyCapacityExceeded(RuntimeError):
    """The shared in-flight byte budget cannot safely admit the request."""

    def __init__(self, *, path: str, received: int, limit: int) -> None:
        super().__init__(
            f"router request capacity is busy for {path}: "
            f"{received} body bytes require "
            f"{received * REQUEST_BODY_MEMORY_MULTIPLIER} reserved bytes; "
            f"{limit} bytes total budget"
        )
        self.path = path
        self.received = received
        self.limit = limit


class RouterRequestBodyPolicy:
    """Select route ceilings and reserve a bounded amount of in-flight memory."""

    def __init__(
        self,
        environment: Mapping[str, str] | None = None,
        *,
        limits: WorkspaceRequestLimits | None = None,
    ) -> None:
        source = os.environ if environment is None else environment
        resolved = limits or resolve_workspace_request_limits({}, "", source)
        self.model_request_max_bytes = resolved.model_request_max_bytes
        self.chat_file_request_max_bytes = resolved.chat_attachment_wire_max_bytes
        self.speech_audio_max_bytes = resolved.speech_audio_max_bytes
        self.speech_reference_request_max_bytes = resolved.tts_reference_wire_max_bytes
        self.speech_media_request_max_bytes = resolved.speech_audio_wire_max_bytes
        self.speech_batch_request_max_bytes = resolved.tts_batch_wire_max_bytes
        self.inflight_request_max_bytes = resolved.inflight_request_max_bytes
        warnings: list[str] = []
        for spec in REQUEST_LIMIT_SPECS:
            raw = str(source.get(spec.environment_name) or "").strip()
            if not raw:
                continue
            try:
                value = int(raw)
            except ValueError:
                warnings.append(
                    f"{spec.environment_name}={raw!r} is invalid; "
                    f"using {resolved.sources.get(spec.key, 'workspace/default')}"
                )
                continue
            clamped = min(spec.hard_max_bytes, max(spec.minimum_bytes, value))
            if clamped != value:
                warnings.append(
                    f"{spec.environment_name}={value} was clamped to {clamped}"
                )
        if (
            resolved.inflight_request_max_bytes
            > resolved.configured_inflight_request_max_bytes
        ):
            warnings.append(
                f"{INFLIGHT_REQUEST_MAX_ENV} effective limit was raised from "
                f"{resolved.configured_inflight_request_max_bytes} to "
                f"{resolved.inflight_request_max_bytes} to reserve "
                f"{REQUEST_BODY_MEMORY_MULTIPLIER}x the largest wire request"
            )
        self.configuration_warnings = tuple(warnings)
        self._inflight_bytes = 0
        self._lock = threading.Lock()

    def limit_for(self, path: str, content_type: str = "application/json") -> int:
        if path.startswith("/ca/events/webhooks/"):
            return WEBHOOK_REQUEST_MAX_BYTES
        if (
            path in _MODEL_REQUEST_PATHS
            or path == "/backend-api/codex"
            or path.startswith("/backend-api/codex/")
            or path == "/v1/audio/voices"
        ):
            return self.model_request_max_bytes
        if path in _CHAT_FILE_PATHS:
            return self.chat_file_request_max_bytes
        if path in _SPEECH_REFERENCE_PATHS:
            return self.speech_reference_request_max_bytes
        if path == "/v1/audio/speech/batch":
            return self.speech_batch_request_max_bytes
        if path in _SPEECH_MEDIA_PATHS:
            if "application/json" not in str(content_type).casefold():
                return self.speech_audio_max_bytes + MIB
            return self.speech_media_request_max_bytes
        return GENERAL_REQUEST_MAX_BYTES

    def validate_parsed_body(
        self,
        path: str,
        content_length: int,
        body: Mapping[str, object],
    ) -> None:
        """Reserved for parsed-body route-specific limits."""
        del path, content_length, body

    @property
    def inflight_bytes(self) -> int:
        with self._lock:
            return self._inflight_bytes

    @contextmanager
    def admit(
        self,
        path: str,
        content_length: int,
        content_type: str = "application/json",
    ) -> Iterator[None]:
        limit = self.limit_for(path, content_type)
        if content_length > limit:
            raise RequestBodyTooLarge(
                path=path,
                received=content_length,
                limit=limit,
            )
        reserved_bytes = content_length * REQUEST_BODY_MEMORY_MULTIPLIER
        reserved = False
        if content_length:
            with self._lock:
                if self._inflight_bytes + reserved_bytes > self.inflight_request_max_bytes:
                    raise RequestBodyCapacityExceeded(
                        path=path,
                        received=content_length,
                        limit=self.inflight_request_max_bytes,
                    )
                self._inflight_bytes += reserved_bytes
                reserved = True
        try:
            yield
        finally:
            if reserved:
                with self._lock:
                    self._inflight_bytes -= reserved_bytes

    @contextmanager
    def admit_transformed(
        self,
        path: str,
        original_length: int,
        transformed_length: int,
        content_type: str = "application/json",
    ) -> Iterator[None]:
        """Validate an expanded body and reserve only its unreserved delta."""
        original = max(0, int(original_length))
        transformed = max(0, int(transformed_length))
        limit = self.limit_for(path, content_type)
        if transformed > limit:
            raise RequestBodyTooLarge(
                path=path,
                received=transformed,
                limit=limit,
            )
        additional_body_bytes = max(0, transformed - original)
        additional_memory_bytes = (
            additional_body_bytes * REQUEST_BODY_MEMORY_MULTIPLIER
        )
        reserved = False
        if additional_memory_bytes:
            with self._lock:
                if (
                    self._inflight_bytes + additional_memory_bytes
                    > self.inflight_request_max_bytes
                ):
                    raise RequestBodyCapacityExceeded(
                        path=path,
                        received=additional_body_bytes,
                        limit=self.inflight_request_max_bytes,
                    )
                self._inflight_bytes += additional_memory_bytes
                reserved = True
        try:
            yield
        finally:
            if reserved:
                with self._lock:
                    self._inflight_bytes -= additional_memory_bytes


__all__ = [
    "CHAT_FILE_REQUEST_MAX_BYTES",
    "DEFAULT_INFLIGHT_REQUEST_BYTES",
    "GENERAL_REQUEST_MAX_BYTES",
    "INFLIGHT_REQUEST_HARD_MAX_BYTES",
    "INFLIGHT_REQUEST_MAX_ENV",
    "MODEL_REQUEST_DEFAULT_BYTES",
    "MODEL_REQUEST_HARD_MAX_BYTES",
    "MODEL_REQUEST_MAX_ENV",
    "RequestBodyCapacityExceeded",
    "RequestBodyTooLarge",
    "REQUEST_BODY_MEMORY_MULTIPLIER",
    "RouterRequestBodyPolicy",
    "SPEECH_MEDIA_REQUEST_MAX_BYTES",
    "SPEECH_BATCH_REQUEST_MAX_BYTES",
    "SPEECH_REFERENCE_REQUEST_MAX_BYTES",
    "WEBHOOK_REQUEST_MAX_BYTES",
]
