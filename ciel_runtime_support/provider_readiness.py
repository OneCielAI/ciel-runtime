"""Provider launch-readiness application service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .architecture import ProviderAdapter, ProviderConfig, ProviderStatusPolicy


@dataclass(frozen=True, slots=True)
class ProviderReadinessMode:
    direct_native_anthropic: Callable[..., bool]
    native_agy: Callable[..., bool]
    native_codex: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class ProviderReadinessCapabilities:
    ultracode_enabled: Callable[..., bool]
    supported_capabilities: Callable[..., list[str] | tuple[str, ...] | set[str]]
    current_model: Callable[..., str]


@dataclass(frozen=True, slots=True)
class ProviderReadinessLmStudio:
    ensure_model_loaded: Callable[..., Any]
    save_config: Callable[..., Any]
    runtime_info: Callable[..., dict[str, Any]]
    positive_int: Callable[..., int | None]
    minimum_context: int


@dataclass(frozen=True, slots=True)
class ProviderReadinessServices:
    mode: ProviderReadinessMode
    capabilities: ProviderReadinessCapabilities
    lm_studio: ProviderReadinessLmStudio
    base_url_status: Callable[..., str]


def launch_readiness_errors(
    cfg: dict[str, Any],
    provider: str,
    pcfg: dict[str, Any],
    adapter: ProviderAdapter,
    contract_config: ProviderConfig,
    status_policy: ProviderStatusPolicy,
    *,
    services: ProviderReadinessServices,
) -> list[str]:
    mode = services.mode
    if (
        mode.direct_native_anthropic(provider, pcfg)
        or mode.native_agy(provider)
        or mode.native_codex(provider)
    ):
        return []
    status = services.base_url_status(provider, pcfg)
    errors: list[str] = []
    if any(marker in status.lower() for marker in ("unreachable", "placeholder", "missing")):
        errors.extend((f"Launch blocked: {status}", status_policy.unreachable_hint))
    api_key_error = adapter.launch_api_key_error(contract_config)
    if api_key_error:
        errors.append(api_key_error)
    capabilities = services.capabilities
    if capabilities.ultracode_enabled(provider, pcfg):
        model = capabilities.current_model(provider, pcfg)
        supported = set(capabilities.supported_capabilities(provider, pcfg, model))
        if "xhigh_effort" not in supported:
            errors.append(
                "Launch blocked: ultracode requires a Claude Code model capability set that includes xhigh_effort. "
                "Use a compatible Claude model or set claude_code_supported_capabilities after verifying the provider/model supports xhigh workflow thinking."
            )
    validators = {
        "none": lambda: None,
        "lm_studio": lambda: _validate_lm_studio(cfg, provider, pcfg, errors, services.lm_studio),
    }
    validators[status_policy.readiness_validation]()
    return errors


def _validate_lm_studio(
    cfg: dict[str, Any],
    provider: str,
    pcfg: dict[str, Any],
    errors: list[str],
    services: ProviderReadinessLmStudio,
) -> None:
    try:
        services.ensure_model_loaded(pcfg, timeout=1.5)
        services.save_config(cfg)
    except Exception as exc:
        errors.append(
            "Launch blocked: Ciel Runtime could not automatically load the selected LM Studio model "
            f"with the recommended context ({type(exc).__name__}: {exc})."
        )
        return
    info = services.runtime_info(provider, pcfg, timeout=1.5)
    loaded = services.positive_int(info.get("loaded_context_len")) if info else None
    state = str(info.get("state") or "") if info else ""
    if loaded and loaded < services.minimum_context:
        errors.append(
            "Launch blocked: LM Studio loaded context is "
            f"{loaded:,} tokens; Claude Code needs at least {services.minimum_context:,}. "
            "Reload the model with a larger context length."
        )
    elif state and state != "loaded":
        errors.append(
            "Launch blocked: selected LM Studio model is not loaded, so the active context length cannot be verified. "
            f"Load it with at least {services.minimum_context:,} context tokens."
        )
