"""Prelaunch menu panel for provider fallback: every fallback operation is reachable here."""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from ciel_runtime_support.provider_failover import Backup, ProviderFailoverService, failover_service

Prompt = Callable[[str, str], str]


def _onoff(value: object) -> str:
    return "on" if value else "off"


def _when(epoch: object) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(epoch)))
    except (TypeError, ValueError):
        return "-"


def _providers(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    providers = cfg.get("providers")
    return providers if isinstance(providers, Mapping) else {}


def summary(cfg: Mapping[str, Any], service: ProviderFailoverService | None = None) -> str:
    service = service or failover_service()
    settings = service.settings()
    usable = len(service.usable_backups(cfg))
    active = service.active()
    now = f" · active: {service.status_line()}" if active else ""
    return f"auto {_onoff(settings['enabled'])} · {usable}/{len(settings['backups'])} backups ready{now}"


def ready_providers(cfg: Mapping[str, Any], service: ProviderFailoverService) -> list[str]:
    current = str(cfg.get("current_provider") or "")
    return [name for name, pcfg in sorted(_providers(cfg).items()) if name != current and service.provider_ready(name, pcfg)]


def panel_rows(cfg: Mapping[str, Any], service: ProviderFailoverService | None = None) -> tuple[list[str], list[str]]:
    service = service or failover_service()
    settings = service.settings()
    providers = _providers(cfg)
    current = str(cfg.get("current_provider") or "")
    rows = [
        f"Automatic fallback on 429 / usage limit  [{_onoff(settings['enabled'])}]",
        f"Stored OAuth tokens all limited: use the CLI's own sign-in first  [{_onoff(settings['cli_login_first'])}]",
        f"Cooldown when the provider gives no reset time  [{settings['default_cooldown_minutes']} min]",
        "── Backups, tried in this order (Enter: up · down · model · remove) ──",
    ]
    values = ["toggle-enabled", "toggle-cli", "cooldown", "__info__"]
    for index, item in enumerate(settings["backups"]):
        ready = service.provider_ready(item["provider"], providers.get(item["provider"]))
        model = item["model"] or str((providers.get(item["provider"]) or {}).get("current_model") or "-")
        rows.append(f"  {index + 1}. {item['provider']} · {model}  [{'credentials ✓' if ready else 'no credentials ✗'}]")
        values.append(f"backup:{index}")
    rows.append(f"  Add a backup…  [{len(ready_providers(cfg, service))} providers with stored credentials]")
    values.append("backup-add")
    active = service.active()
    if active:
        to = active.get("to") or {}
        rows += [
            f"── Now: {to.get('provider')}/{to.get('model')} serves {active.get('from')} until {_when(active.get('until'))} "
            f"({active.get('trigger')}: {str(active.get('reason') or '')[:60]}) ──",
            f"Return to {active.get('from')} now",
        ]
        values += ["__info__", "return"]
    else:
        rows.append(f"── Now: {current} serves requests (no fallback active) ──")
        values.append("__info__")
    rows += ["Switch now for a while…", "Recent switches", "Back"]
    values += ["switch", "history", "back"]
    return rows, values


def _number(prompt: Prompt, label: str, current: float) -> float | None:
    try:
        value = float(prompt(label, f"{current:g}").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _pick_provider(cfg: Mapping[str, Any], service: ProviderFailoverService, prompt: Prompt, label: str, default: str) -> str | None:
    choices = ready_providers(cfg, service)
    if not choices:
        return None
    answer = prompt(f"{label} ({' / '.join(choices)})", default if default in choices else choices[0]).strip()
    return answer if answer in choices else None


def apply(cfg: Mapping[str, Any], value: str, prompt: Prompt | None = None, service: ProviderFailoverService | None = None) -> list[str]:
    prompt = prompt or (lambda _label, default: default)
    service = service or failover_service()
    settings = service.settings()
    providers = _providers(cfg)
    current = str(cfg.get("current_provider") or "")
    if value == "toggle-enabled":
        service.update_settings(enabled=not settings["enabled"])
        lines = [f"Automatic fallback: {_onoff(not settings['enabled'])}"]
        if not settings["enabled"] and not service.usable_backups(cfg):
            lines.append("Add a backup provider below; without one a 429 still reaches the CLI.")
        return lines
    if value == "toggle-cli":
        service.update_settings(cli_login_first=not settings["cli_login_first"])
        return [f"CLI sign-in first when stored tokens are limited: {_onoff(not settings['cli_login_first'])}"]
    if value == "cooldown":
        minutes = _number(prompt, "Minutes to stay on the backup when the provider gives no reset time", float(settings["default_cooldown_minutes"]))
        if minutes is None:
            return ["Enter a number of minutes greater than 0."]
        service.update_settings(default_cooldown_minutes=minutes)
        return [f"Default cooldown: {minutes:g} min."]
    if value == "backup-add":
        provider = _pick_provider(cfg, service, prompt, "Backup provider", "")
        if provider is None:
            return ["No other provider has stored credentials. Set an API key for it first (menu 1 and 2)."]
        model = prompt(f"Model on {provider}", str((providers.get(provider) or {}).get("current_model") or "")).strip()
        backups = [item for item in settings["backups"] if item["provider"] != provider] + [{"provider": provider, "model": model}]
        service.update_settings(backups=backups)
        return [f"Added backup {len(backups)}: {provider} · {model or 'its current model'}."]
    if value.startswith("backup:"):
        index = int(value.split(":", 1)[1])
        backups = list(settings["backups"])
        if not 0 <= index < len(backups):
            return []
        item = backups[index]
        action = prompt(f"{item['provider']}: up / down / model / remove", "up").strip().lower()
        if action in ("up", "down"):
            target = index - 1 if action == "up" else index + 1
            if 0 <= target < len(backups):
                backups[index], backups[target] = backups[target], backups[index]
                service.update_settings(backups=backups)
            return [f"Order: {', '.join(entry['provider'] for entry in backups)}"]
        if action == "model":
            item["model"] = prompt(f"Model on {item['provider']}", item["model"]).strip()
            service.update_settings(backups=backups)
            return [f"{item['provider']} uses {item['model'] or 'its current model'}."]
        if action == "remove":
            backups.pop(index)
            service.update_settings(backups=backups)
            return [f"Removed {item['provider']}."]
        return [f"Unknown action {action!r}."]
    if value == "switch":
        default = settings["backups"][0]["provider"] if settings["backups"] else ""
        provider = _pick_provider(cfg, service, prompt, "Serve requests from", default)
        if provider is None:
            return ["No other provider has stored credentials."]
        configured = next((item["model"] for item in settings["backups"] if item["provider"] == provider), "")
        model = prompt(f"Model on {provider}", configured or str((providers.get(provider) or {}).get("current_model") or "")).strip()
        minutes = _number(prompt, "For how many minutes", float(settings["default_cooldown_minutes"]))
        if minutes is None:
            return ["Enter a number of minutes greater than 0."]
        try:
            active = service.switch(cfg, current, Backup(provider, model), minutes)
        except ValueError as error:
            return [str(error)]
        return [f"{provider}/{active['to']['model']} serves {current} requests until {_when(active['until'])}.",
                "Running sessions follow on their next request; they return at the next turn after that time."]
    if value == "return":
        active = service.return_now()
        if not active:
            return ["No fallback is active."]
        return [f"{active.get('from')} serves requests again (from the next request)."]
    if value == "history":
        history = service.load().get("history") or []
        if not history:
            return ["No switches yet."]
        lines = []
        for event in history[-8:]:
            if event.get("event") == "activated":
                to = event.get("to") or {}
                lines.append(f"{_when(event.get('at'))}  {event.get('from')} -> {to.get('provider')}/{to.get('model')} "
                             f"{event.get('trigger')} until {_when(event.get('until'))}: {str(event.get('reason') or '')[:70]}")
            else:
                lines.append(f"{_when(event.get('at'))}  back to {event.get('to')} ({event.get('why')})")
        return lines
    return []


__all__ = ["apply", "panel_rows", "ready_providers", "summary"]
