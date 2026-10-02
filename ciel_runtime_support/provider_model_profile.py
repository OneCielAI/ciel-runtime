"""Apply provider-owned model profiles to one provider configuration."""

from __future__ import annotations

from typing import Any, Callable, Mapping


def apply_adapter_model_profiles(
    adapter: Any,
    contract: Callable[[], Any],
    pcfg: dict[str, Any],
    selected_info: Callable[[], Mapping[str, Any]],
) -> list[str]:
    """Apply the adapter's model profile, then its cached-catalog profile.

    The catalog profile may remove a key by giving it the value ``None``.
    """

    messages: list[str] = []
    updates, notice = adapter.model_configuration_profile(contract())
    if updates:
        changed = any(pcfg.get(key) != value for key, value in updates.items())
        pcfg.update(updates)
        if changed and notice:
            messages.append(notice)
    catalog_updates, catalog_notice = adapter.catalog_model_configuration(
        contract(), selected_info()
    )
    changed = False
    for key, value in (catalog_updates or {}).items():
        if value is None:
            changed = pcfg.pop(key, None) is not None or changed
        elif pcfg.get(key) != value:
            pcfg[key] = value
            changed = True
    if changed and catalog_notice:
        messages.append(catalog_notice)
    return messages


def reapply_launch_catalog_profile(
    provider: str,
    pcfg: dict[str, Any],
    enabled: bool,
    apply_profile: Callable[[str, dict[str, Any]], list[str]],
    load_config: Callable[[], dict[str, Any]],
    save_config: Callable[[dict[str, Any]], None],
    log: Callable[[str, str], None],
) -> None:
    """Re-apply the selected model's catalog profile for this launch and persist it.

    The launching process uses ``pcfg``; the router and the status line read
    the saved configuration, so the stored provider entry is updated as well
    when it still selects the same model.
    """

    if not enabled:
        return
    messages = apply_profile(provider, pcfg)
    if not messages:
        return
    stored_config = load_config()
    providers = stored_config.get("providers")
    stored = providers.get(provider) if isinstance(providers, dict) else None
    if isinstance(stored, dict) and stored.get("current_model") == pcfg.get("current_model"):
        apply_profile(provider, stored)
        save_config(stored_config)
    for line in messages:
        log("INFO", f"launch_catalog_profile provider={provider} {line}")
