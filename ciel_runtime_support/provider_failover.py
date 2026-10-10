"""Provider failover: serve a rate-limited provider's requests from a stored backup for a cooldown.

When the current provider answers 429 or a usage-limit error (and its own key/token
rotation has nothing left), the router asks this service for a backup provider whose
credentials are already stored, records an active failover until the provider's reset
time (Retry-After / reset headers, else the configured cooldown), and sends the *same*
request again through the backup before anything was written to the client.  Later
requests keep using the backup; once the cooldown is over the router returns to the
original provider at the next turn start (never inside a tool loop, so a provider that
signs its reasoning never sees a turn it did not start).

Settings, the active failover and a short history live in
``<workspace state>/provider-failover.json`` (one router per workspace reads it on
every request); the shared config.json is never written.
"""

from __future__ import annotations

import copy
import email.utils
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ciel_runtime_support.credentials import provider_config_api_keys

STATE_FILE = "provider-failover.json"
HISTORY_LIMIT = 20
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,            # automatic failover on 429 / usage limits
    "cli_login_first": True,     # Claude/Codex OAuth pool exhausted: try the CLI's own sign-in first
    "default_cooldown_minutes": 30,
    "backups": [],               # ordered [{"provider": ..., "model": ...}]
}
# Providers that only work with client-local auth cannot serve a routed request.
INCOMPATIBLE_PROVIDERS = frozenset({"codex", "agy", "zai-start-plan"})
MAX_COOLDOWN_SECONDS = 8 * 24 * 3600
HANDLER_TRIED_ATTRIBUTE = "_ciel_failover_tried"


class FailoverRequested(Exception):
    """Raised at a 429 point that may still answer the client: retry through a backup."""

    def __init__(self, from_provider: str, to_provider: str, reason: str) -> None:
        super().__init__(f"failover {from_provider} -> {to_provider}: {reason}")
        self.from_provider = from_provider
        self.to_provider = to_provider
        self.reason = reason


@dataclass(frozen=True)
class Backup:
    provider: str
    model: str


def _retry_after_seconds(headers: Any) -> float | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter("retry-after") or getter("Retry-After")
    if value:
        text = str(value).strip()
        try:
            return max(0.0, float(text))
        except ValueError:
            try:
                return max(0.0, email.utils.parsedate_to_datetime(text).timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    resets = []
    for name in ("anthropic-ratelimit-unified-reset", "anthropic-ratelimit-requests-reset",
                 "anthropic-ratelimit-tokens-reset", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens",
                 "x-ratelimit-reset"):
        raw = getter(name)
        if not raw:
            continue
        text = str(raw).strip()
        try:
            number = float(text)
            resets.append(number - time.time() if number > 1e9 else number)
            continue
        except ValueError:
            pass
        try:
            resets.append(email.utils.parsedate_to_datetime(text).timestamp() - time.time())
        except (TypeError, ValueError):
            if text.endswith("s") and text[:-1].replace(".", "", 1).isdigit():
                resets.append(float(text[:-1]))
    return max(0.0, max(resets)) if resets else None


def rate_limit_reason(raw: Any) -> str:
    """A short, secret-free reason from an upstream error body."""

    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    try:
        payload = json.loads(text)
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            text = str(error.get("message") or error.get("type") or text)
        elif isinstance(error, str):
            text = error
    except ValueError:
        pass
    return " ".join(text.split())[:160]


class ProviderFailoverService:
    def __init__(
        self,
        state_dir: Path,
        *,
        oauth_has_tokens: Callable[[str], bool] = lambda _provider: False,
        log: Callable[[str, str], Any] = lambda _level, _message: None,
        publish: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(state_dir) / STATE_FILE
        self.oauth_has_tokens = oauth_has_tokens
        self.log = log
        self.publish = publish
        self.clock = clock
        self._lock = threading.Lock()

    # ---- storage --------------------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["settings"] = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}
        data["settings"]["backups"] = [
            {"provider": str(item.get("provider") or ""), "model": str(item.get("model") or "")}
            for item in data["settings"].get("backups") or []
            if isinstance(item, dict) and item.get("provider")
        ]
        data.setdefault("active", None)
        data.setdefault("history", [])
        return data

    def save(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def settings(self) -> dict[str, Any]:
        return self.load()["settings"]

    def update_settings(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            data["settings"].update(values)
            self.save(data)
            return data["settings"]

    # ---- credentials ----------------------------------------------------------------------

    def provider_ready(self, provider: str, pcfg: Mapping[str, Any] | None) -> bool:
        if not provider or provider in INCOMPATIBLE_PROVIDERS or not isinstance(pcfg, Mapping):
            return False
        if provider_config_api_keys(dict(pcfg)):
            return True
        return provider == "anthropic" and self.oauth_has_tokens("claude")

    def usable_backups(self, cfg: Mapping[str, Any], exclude: set[str] = frozenset()) -> list[Backup]:
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), Mapping) else {}
        result = []
        for item in self.settings()["backups"]:
            provider = item["provider"]
            if provider in exclude or not self.provider_ready(provider, providers.get(provider)):
                continue
            model = item["model"] or str((providers.get(provider) or {}).get("current_model") or "")
            if model:
                result.append(Backup(provider, model))
        return result

    # ---- routing --------------------------------------------------------------------------

    @staticmethod
    def _overlay(cfg: Mapping[str, Any], backup: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), Mapping) else {}
        base = providers.get(backup["provider"])
        if not isinstance(base, Mapping):
            return None
        pcfg = copy.deepcopy(dict(base))
        if backup.get("model"):
            pcfg["current_model"] = backup["model"]
        return backup["provider"], pcfg

    def effective(self, cfg: Mapping[str, Any], provider: str, pcfg: dict[str, Any], *, turn_start: bool) -> tuple[str, dict[str, Any]]:
        """The provider that serves this request: the active backup, or the configured one."""

        data = self.load()
        active = data.get("active")
        if not isinstance(active, dict) or active.get("from") != provider:
            return provider, pcfg
        now = self.clock()
        if now >= float(active.get("until") or 0) and turn_start:
            self._finish(data, "cooldown over")
            return provider, pcfg
        overlay = self._overlay(cfg, active.get("to") or {})
        return overlay or (provider, pcfg)

    def active(self) -> dict[str, Any] | None:
        active = self.load().get("active")
        return active if isinstance(active, dict) else None

    def _record(self, data: dict[str, Any], event: dict[str, Any]) -> None:
        data["history"] = [*data.get("history", []), event][-HISTORY_LIMIT:]

    def _finish(self, data: dict[str, Any], why: str) -> None:
        with self._lock:
            active = data.get("active")
            data["active"] = None
            if isinstance(active, dict):
                self._record(data, {"at": self.clock(), "event": "returned", "to": active.get("from"), "why": why})
            self.save(data)
        if isinstance(active, dict):
            self.log("INFO", f"provider_failover_returned provider={active.get('from')} from={active.get('to', {}).get('provider')} why={why}")
            self._emit("returned", active, why)

    def _emit(self, event: str, active: Mapping[str, Any], why: str) -> None:
        if self.publish is None:
            return
        try:
            self.publish(level="warn" if event == "activated" else "info", category="router.fallback",
                         message=f"Provider fallback {event}", data={"event": event, "active": dict(active), "why": why})
        except Exception:  # noqa: BLE001 - events never break routing
            pass

    def activate(self, from_provider: str, backup: Backup, *, reason: str, trigger: str, until: float) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            active = {
                "from": from_provider,
                "to": {"provider": backup.provider, "model": backup.model},
                "reason": reason,
                "trigger": trigger,
                "since": self.clock(),
                "until": until,
            }
            data["active"] = active
            self._record(data, {"at": self.clock(), "event": "activated", **active})
            self.save(data)
        self.log("WARN" if trigger == "auto" else "INFO",
                 f"provider_failover_activated from={from_provider} to={backup.provider}/{backup.model} trigger={trigger} "
                 f"reason={reason} until={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(until))}")
        self._emit("activated", active, reason)
        return active

    def cooldown_until(self, headers: Any) -> tuple[float, str]:
        seconds = _retry_after_seconds(headers)
        if seconds is None:
            minutes = float(self.settings().get("default_cooldown_minutes") or 30)
            return self.clock() + minutes * 60, f"default {minutes:g} min"
        seconds = min(max(seconds, 30.0), MAX_COOLDOWN_SECONDS)
        return self.clock() + seconds, "reset time from the provider"

    def on_rate_limited(self, handler: Any, cfg: Mapping[str, Any], provider: str, status: int, headers: Any, reason: str) -> Backup | None:
        """At a 429 point: choose and activate the next backup, or None to relay the error.

        Only while nothing has been written to the client; each provider is tried
        at most once per client request.
        """

        if getattr(handler, "_ciel_runtime_response_status", None) is not None:
            return None
        settings = self.settings()
        if not settings.get("enabled"):
            return None
        tried: set[str] = getattr(handler, HANDLER_TRIED_ATTRIBUTE, None) or set()
        tried.add(provider)
        setattr(handler, HANDLER_TRIED_ATTRIBUTE, tried)
        active = self.active()
        original = active.get("from") if active and active.get("to", {}).get("provider") == provider else provider
        candidates = [backup for backup in self.usable_backups(cfg, exclude={*tried, original}) if backup.provider != provider]
        if not candidates:
            self.log("WARN", f"provider_failover_skipped provider={provider} status={status} reason=no usable backup left")
            return None
        until, basis = self.cooldown_until(headers)
        if active and active.get("until"):
            until = max(until, float(active["until"]))
        backup = candidates[0]
        self.activate(original, backup, reason=f"{provider} {status}: {reason} ({basis})", trigger="auto", until=until)
        tried.add(backup.provider)
        return backup

    def switch(self, cfg: Mapping[str, Any], provider: str, backup: Backup, minutes: float) -> dict[str, Any]:
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), Mapping) else {}
        if not self.provider_ready(backup.provider, providers.get(backup.provider)):
            raise ValueError(f"{backup.provider} has no stored credentials")
        model = backup.model or str((providers.get(backup.provider) or {}).get("current_model") or "")
        return self.activate(provider, Backup(backup.provider, model), reason=f"manual for {minutes:g} min",
                             trigger="manual", until=self.clock() + minutes * 60)

    def return_now(self) -> dict[str, Any] | None:
        data = self.load()
        active = data.get("active")
        if not isinstance(active, dict):
            return None
        self._finish(data, "returned by the user")
        return active

    def status_line(self) -> str:
        active = self.active()
        if not active:
            return ""
        to = active.get("to") or {}
        until = time.strftime("%H:%M", time.localtime(float(active.get("until") or 0)))
        return f"{to.get('provider')}/{to.get('model')} until {until} ({active.get('from')} {active.get('trigger')})"


HANDLER_CONFIG_ATTRIBUTE = "_ciel_failover_cfg"


def maybe_fail_over(
    handler: Any,
    provider: str,
    *,
    status: int | None,
    headers: Any = None,
    reason: Any = "",
    rate_limited: bool | None = None,
) -> None:
    """Call where a rate-limit error is about to be written: raises FailoverRequested
    when a backup takes the request, returns otherwise (the caller relays the error)."""

    limited = (status == 429) if rate_limited is None else bool(rate_limited)
    cfg = getattr(handler, HANDLER_CONFIG_ATTRIBUTE, None)
    if not limited or not isinstance(cfg, Mapping):
        return
    backup = failover_service().on_rate_limited(handler, cfg, provider, int(status or 429), headers, rate_limit_reason(reason))
    if backup is not None:
        raise FailoverRequested(provider, backup.provider, str(status or 429))


FALLBACK_COMMAND_MARKER = "CIEL_RUNTIME_PROVIDER_FALLBACK"


def _last_user_text(body: Mapping[str, Any]) -> str:
    for message in reversed(body.get("messages") or []):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(str(block.get("text") or "") for block in content if isinstance(block, Mapping) and block.get("type") == "text")
        return ""
    return ""


def fallback_command_arguments(body: Mapping[str, Any]) -> str | None:
    """The /fallback arguments when the latest user message is that command, else None."""

    text = _last_user_text(body)
    if FALLBACK_COMMAND_MARKER not in text:
        return None
    for line in text.splitlines():
        if line.strip().lower().startswith("arguments:"):
            return line.split(":", 1)[1].strip()
    return ""


def fallback_command(cfg: Mapping[str, Any], arguments: str, service: "ProviderFailoverService | None" = None) -> list[str]:
    """/fallback · /fallback on|off · /fallback return · /fallback PROVIDER [MINUTES] [MODEL]."""

    service = service or failover_service()
    words = arguments.split()
    current = str(cfg.get("current_provider") or "")
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), Mapping) else {}
    if words[:1] in (["on"], ["off"]):
        service.update_settings(enabled=words[0] == "on")
        return [f"Automatic provider fallback: {words[0]}."]
    if words[:1] == ["return"]:
        active = service.return_now()
        return [f"{active.get('from')} serves requests again." if active else "No fallback is active."]
    if words and words[0] not in ("status", "help"):
        provider = words[0]
        try:
            minutes = float(words[1]) if len(words) > 1 else float(service.settings()["default_cooldown_minutes"])
        except ValueError:
            return [f"Minutes must be a number: /fallback {provider} 30"]
        model = words[2] if len(words) > 2 else next(
            (item["model"] for item in service.settings()["backups"] if item["provider"] == provider), "")
        if provider not in providers:
            return [f"Unknown provider {provider!r}."]
        try:
            active = service.switch(cfg, current, Backup(provider, model), minutes)
        except ValueError as error:
            return [str(error)]
        until = time.strftime("%H:%M", time.localtime(float(active["until"])))
        return [f"{provider}/{active['to']['model']} serves {current} requests until {until} (from the next request).",
                "It returns at the first new turn after that time; `/fallback return` returns now."]
    settings = service.settings()
    ready = [name for name, pcfg in sorted(providers.items()) if name != current and service.provider_ready(name, pcfg)]
    lines = [f"Provider fallback: automatic {'on' if settings['enabled'] else 'off'}; "
             f"CLI sign-in first {'on' if settings['cli_login_first'] else 'off'}; "
             f"default cooldown {settings['default_cooldown_minutes']:g} min."]
    lines.append("Active: " + (service.status_line() or f"none - {current} serves requests."))
    lines.append("Backups: " + (", ".join(f"{item['provider']}/{item['model'] or '-'}" for item in settings["backups"]) or "none (menu 19 adds them)"))
    lines.append("Providers with stored credentials: " + (", ".join(ready) or "none"))
    lines.append("Use: /fallback PROVIDER [MINUTES] [MODEL] · /fallback return · /fallback on|off")
    return lines


_LAST_CONFIG: dict[str, Any] = {}


def remember_config(cfg: Mapping[str, Any]) -> None:
    """The router's config as read for the current request (for the MCP tool)."""

    global _LAST_CONFIG
    if isinstance(cfg, Mapping):
        _LAST_CONFIG = dict(cfg)


def mcp_provider_fallback(args: Mapping[str, Any]) -> dict[str, Any]:
    """The ``provider_fallback`` router tool: status, switch, return, enable/disable."""

    action = str(args.get("action") or "status")
    words = {"status": "status", "return": "return", "enable": "on", "disable": "off"}.get(action)
    if action == "switch":
        if not args.get("provider"):
            return {"ok": False, "error": "switch needs provider"}
        words = " ".join(str(part) for part in (args.get("provider"), args.get("minutes") or "", args.get("model") or "") if str(part))
        if args.get("model") and not args.get("minutes"):
            words = f"{args['provider']} {failover_service().settings()['default_cooldown_minutes']} {args['model']}"
    if words is None:
        return {"ok": False, "error": f"unsupported action {action!r}; use status, switch, return, enable or disable"}
    lines = fallback_command(_LAST_CONFIG, words)
    service = failover_service()
    return {"ok": True, "lines": lines, "active": service.active(), "settings": service.settings()}


_SERVICE: ProviderFailoverService | None = None
_SERVICE_LOCK = threading.Lock()


def failover_service(log: Callable[[str, str], Any] | None = None, publish: Callable[..., Any] | None = None) -> ProviderFailoverService:
    """The router process's service for its workspace."""

    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            from ciel_runtime_support.runtime_paths import WORKSPACE_STATE_DIR

            def oauth_has_tokens(provider: str) -> bool:
                try:
                    from ciel_runtime_support.oauth_routing import oauth_routing

                    return oauth_routing().store.has_tokens(provider)
                except Exception:  # noqa: BLE001
                    return False

            _SERVICE = ProviderFailoverService(Path(WORKSPACE_STATE_DIR), oauth_has_tokens=oauth_has_tokens)
            from ciel_runtime_support import oauth_routing as _oauth_routing

            service = _SERVICE
            _oauth_routing.CLI_LOGIN_FALLBACK[0] = lambda: bool(service.settings().get("cli_login_first", True))
        if log is not None:
            _SERVICE.log = log
        if publish is not None:
            _SERVICE.publish = publish
        return _SERVICE


def use_failover_service(service: ProviderFailoverService | None) -> None:
    """Replace the process service (tests, or a router serving another workspace)."""

    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = service


__all__ = [
    "Backup",
    "DEFAULT_SETTINGS",
    "FailoverRequested",
    "HANDLER_CONFIG_ATTRIBUTE",
    "HANDLER_TRIED_ATTRIBUTE",
    "ProviderFailoverService",
    "FALLBACK_COMMAND_MARKER",
    "failover_service",
    "fallback_command",
    "fallback_command_arguments",
    "maybe_fail_over",
    "mcp_provider_fallback",
    "remember_config",
    "rate_limit_reason",
    "use_failover_service",
]
