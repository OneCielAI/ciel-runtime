"""Atomic repository for router and context activity snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class RuntimeActivityPaths:
    router: Path
    context_compact: Path
    context_usage: Path


@dataclass(frozen=True, slots=True)
class RuntimeActivityClock:
    epoch: Callable[[], float]
    display: Callable[[], str]
    temporary_suffix: Callable[[], str]


@dataclass(frozen=True, slots=True)
class RuntimeActivityEffects:
    publish: Callable[..., Any]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class RuntimeActivityRepository:
    paths: RuntimeActivityPaths
    clock: RuntimeActivityClock
    effects: RuntimeActivityEffects

    def router_activity(
        self,
        event: str,
        provider: str,
        model: str | None = None,
        **fields: Any,
    ) -> None:
        try:
            level = "error" if event == "error" else ("warn" if event in {"retry", "wait"} else "info")
            self.effects.publish(
                level=level,
                category=f"router.{event}",
                message=f"{event} {provider} {model or ''}".strip(),
                provider=provider,
                model=model,
                data=fields,
            )
            self._write(
                self.paths.router,
                {"event": event, "provider": provider, "model": model or "", **fields},
            )
        except Exception as exc:
            self._report_failure("router", exc)

    def context_compact(
        self,
        provider: str,
        model: str | None = None,
        **fields: Any,
    ) -> None:
        try:
            self._write(
                self.paths.context_compact,
                {"event": "compact", "provider": provider, "model": model or "", **fields},
            )
            self.effects.publish(
                level="info",
                category="context.compact",
                message=f"compact {provider} {model or ''}".strip(),
                provider=provider,
                model=model,
                data=fields,
            )
        except Exception as exc:
            self._report_failure("context_compact", exc)

    def context_usage(
        self,
        provider: str,
        provider_config: dict[str, Any],
        body: dict[str, Any],
        source: str,
        *,
        estimate_tokens: Callable[[dict[str, Any]], int],
        context_limit: Callable[[str, dict[str, Any]], int | None],
    ) -> None:
        try:
            tokens = estimate_tokens(body)
            limit = context_limit(provider, provider_config)
            percent = round((tokens / limit) * 100.0, 1) if limit else None
            model = str(body.get("model") or provider_config.get("current_model") or "")
            details = {
                "source": source,
                "tokens": tokens,
                "context_limit": limit,
                "percent": percent,
                "messages": len(body.get("messages") or []),
                "tools": len(body.get("tools") or []),
            }
            self.effects.publish(
                level="debug",
                category="context.usage",
                message=f"context usage {tokens}/{limit or '?'} tokens",
                provider=provider,
                model=model,
                data=details,
            )
            self._write(self.paths.context_usage, {"provider": provider, "model": model, **details})
        except Exception as exc:
            self._report_failure("context_usage", exc)

    def _write(self, path: Path, data: dict[str, Any]) -> None:
        record = {"updated_at": self.clock.epoch(), "time": self.clock.display(), **data}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{self.clock.temporary_suffix()}.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _report_failure(self, operation: str, exc: BaseException) -> None:
        try:
            self.effects.log(
                "WARN",
                f"runtime_activity_{operation}_failed error={type(exc).__name__}: {exc}",
            )
        except Exception:
            return


__all__ = [
    "RuntimeActivityClock",
    "RuntimeActivityEffects",
    "RuntimeActivityPaths",
    "RuntimeActivityRepository",
]
