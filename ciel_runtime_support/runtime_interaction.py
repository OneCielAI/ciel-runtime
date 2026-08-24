"""Cross-process runtime interaction state and terminal presentation."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class RuntimeInteractionEvent:
    request_id: str
    kind: str
    status: str
    created_at: float
    updated_at: float
    expires_at: float
    url: str = ""
    message: str = ""

    @classmethod
    def from_mapping(cls, value: Any) -> "RuntimeInteractionEvent | None":
        if not isinstance(value, Mapping):
            return None
        try:
            event = cls(
                request_id=str(value.get("request_id") or "").strip(),
                kind=str(value.get("kind") or "").strip(),
                status=str(value.get("status") or "").strip(),
                created_at=float(value.get("created_at") or 0.0),
                updated_at=float(value.get("updated_at") or 0.0),
                expires_at=float(value.get("expires_at") or 0.0),
                url=str(value.get("url") or "").strip(),
                message=str(value.get("message") or "").strip(),
            )
        except (TypeError, ValueError):
            return None
        if (
            not event.request_id
            or not event.kind
            or event.status not in {"pending", "completed", "failed"}
        ):
            return None
        return event


@dataclass(slots=True)
class RuntimeInteractionRepository:
    path: Path
    clock: Callable[[], float] = time.time
    log: Callable[[str, str], None] = lambda _level, _message: None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def publish_pending(
        self,
        *,
        request_id: str,
        kind: str,
        url: str,
        timeout_seconds: float,
        message: str = "",
    ) -> RuntimeInteractionEvent:
        now = self.clock()
        event = RuntimeInteractionEvent(
            request_id=request_id,
            kind=kind,
            status="pending",
            created_at=now,
            updated_at=now,
            expires_at=now + max(0.0, float(timeout_seconds)),
            url=url,
            message=message,
        )
        self.write(event)
        return event

    def publish_status(
        self,
        event: RuntimeInteractionEvent,
        status: str,
        *,
        message: str = "",
    ) -> RuntimeInteractionEvent:
        updated = RuntimeInteractionEvent(
            request_id=event.request_id,
            kind=event.kind,
            status=status,
            created_at=event.created_at,
            updated_at=self.clock(),
            expires_at=event.expires_at,
            url=event.url,
            message=message,
        )
        self.write(updated)
        return updated

    def write(self, event: RuntimeInteractionEvent) -> None:
        path = self.path
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(
                    json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                temporary.replace(path)
            except OSError as exc:
                self.log(
                    "WARN",
                    "runtime_interaction_write_failed "
                    f"path={path} error={type(exc).__name__}: {exc}",
                )
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def read(self) -> RuntimeInteractionEvent | None:
        try:
            return RuntimeInteractionEvent.from_mapping(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            self.log(
                "WARN",
                "runtime_interaction_read_failed "
                f"path={self.path} error={type(exc).__name__}: {exc}",
            )
            return None


@dataclass(slots=True)
class RuntimeInteractionDisplayState:
    last_revision: tuple[str, str, float] | None = None
    last_pending_display_at: float = 0.0


def runtime_interaction_notice(event: RuntimeInteractionEvent) -> str:
    if event.kind != "zai-start-plan-captcha":
        return ""
    if event.status == "pending":
        return (
            "[ciel-runtime] Z.AI Start Plan verification required.\n"
            f"Open this URL to continue: {event.url}\n"
            "The active model request is paused and will continue automatically "
            "after verification."
        )
    if event.status == "completed":
        return (
            "[ciel-runtime] Z.AI Start Plan verification received; "
            "continuing the active model request."
        )
    detail = f": {event.message}" if event.message else "."
    return f"[ciel-runtime] Z.AI Start Plan verification failed{detail}"


def runtime_interaction_is_pending(
    event: RuntimeInteractionEvent | None,
    now: float,
) -> bool:
    return bool(
        event is not None
        and event.status == "pending"
        and (not event.expires_at or now <= event.expires_at)
    )


def poll_runtime_interaction(
    now: float,
    state: RuntimeInteractionDisplayState,
    read_event: Callable[[], RuntimeInteractionEvent | None],
    display: Callable[[str], None],
    *,
    reminder_seconds: float = 30.0,
) -> RuntimeInteractionDisplayState:
    event = read_event()
    if event is None:
        return state
    revision = (event.request_id, event.status, event.updated_at)
    if event.status == "pending" and not runtime_interaction_is_pending(event, now):
        return state
    should_display = revision != state.last_revision
    if (
        event.status == "pending"
        and not should_display
        and now - state.last_pending_display_at >= max(1.0, reminder_seconds)
    ):
        should_display = True
    if not should_display:
        return state
    notice = runtime_interaction_notice(event)
    if notice:
        display(notice)
    state.last_revision = revision
    if event.status == "pending":
        state.last_pending_display_at = now
    return state


__all__ = [
    "RuntimeInteractionDisplayState",
    "RuntimeInteractionEvent",
    "RuntimeInteractionRepository",
    "poll_runtime_interaction",
    "runtime_interaction_is_pending",
    "runtime_interaction_notice",
]
