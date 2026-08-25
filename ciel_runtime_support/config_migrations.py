"""Configuration schema migrations independent from persistence and runtime I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ConfigMigrationPolicy:
    default_request_timeout_ms: int
    kimi_k3_model: str
    opencode_provider_names: tuple[str, ...]
    is_qwen36_plus_model_id: Callable[..., Any]
    normalize_channel_delivery: Callable[..., Any]
    normalize_model_id: Callable[..., Any]
    nvidia_hosted_context_default: Callable[..., Any]
    positive_int: Callable[..., Any]
    strip_claude_context_suffix: Callable[..., Any]


def apply_config_migrations(cfg: dict[str, Any], *, policy: ConfigMigrationPolicy) -> None:
    DEFAULT_REQUEST_TIMEOUT_MS = policy.default_request_timeout_ms
    KIMI_K3_MODEL = policy.kimi_k3_model
    OPENCODE_PROVIDER_NAMES = policy.opencode_provider_names
    is_qwen36_plus_model_id = policy.is_qwen36_plus_model_id
    normalize_channel_delivery = policy.normalize_channel_delivery
    normalize_model_id = policy.normalize_model_id
    nvidia_hosted_context_default = policy.nvidia_hosted_context_default
    positive_int = policy.positive_int
    strip_claude_context_suffix = policy.strip_claude_context_suffix
    migrations = cfg.setdefault("migrations", {})
    if not isinstance(migrations, dict):
        migrations = {}
        cfg["migrations"] = migrations

    marker = "zcode_wire_version_0163_20260824"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        for provider_name in (
            "zai",
            "zai-api",
            "zai-coding-plan",
            "zai-start-plan",
        ):
            pcfg = providers.get(provider_name)
            if not isinstance(pcfg, dict):
                continue
            if str(pcfg.get("zcode_app_version") or "").strip() == "3.8.1":
                pcfg["zcode_app_version"] = "0.16.3"
        migrations[marker] = True

    marker = "ox_alpha_provider_catalogs_20260823"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        additions = {
            "openrouter": ("stealth/ox-alpha", None),
            "opencode": ("x-preview-f-free", "openai-chat"),
            "opencode-go": ("ox-alpha-free", "openai-chat"),
        }
        for provider_name, (model, endpoint) in additions.items():
            pcfg = providers.get(provider_name)
            if not isinstance(pcfg, dict):
                continue
            custom = pcfg.get("custom_models")
            if not isinstance(custom, list):
                custom = []
                pcfg["custom_models"] = custom
            known = {
                normalize_model_id(provider_name, str(item))
                for item in custom
                if str(item).strip()
            }
            if normalize_model_id(provider_name, model) not in known:
                custom.append(model)
            if endpoint:
                model_endpoints = pcfg.get("model_endpoints")
                if not isinstance(model_endpoints, dict):
                    model_endpoints = {}
                    pcfg["model_endpoints"] = model_endpoints
                model_endpoints.setdefault(model, endpoint)
        migrations[marker] = True

    marker = "opencode_catalog_protocol_refresh_20260823"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        zen = providers.get("opencode")
        if isinstance(zen, dict):
            custom = zen.get("custom_models")
            if isinstance(custom, list):
                zen["custom_models"] = [
                    model
                    for model in custom
                    if normalize_model_id("opencode", str(model))
                    != "qwen3.6-plus-free"
                ]
        migrations[marker] = True

    marker = "alibaba_provider_catalogs_20260806"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        catalogs = {
            "alicode": (
                "qwen3.7-plus", "qwen3.6-plus", "kimi-k2.5", "glm-5",
                "MiniMax-M2.5", "qwen3.5-plus", "qwen3-max-2026-01-23",
                "qwen3-coder-next", "qwen3-coder-plus", "glm-4.7",
            ),
            "alicode-intl": (
                "qwen3.7-plus", "qwen3.6-plus", "kimi-k2.5", "glm-5",
                "MiniMax-M2.5", "qwen3.5-plus", "qwen3-max-2026-01-23",
                "qwen3-coder-next", "qwen3-coder-plus", "glm-4.7",
            ),
            "alims-intl": (
                "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus",
                "qwen3.6-flash", "qwen3.5-plus", "qwen3-coder-plus",
                "qwen3-coder-flash", "deepseek-v4-pro", "deepseek-v4-flash",
                "glm-5.2", "kimi-k2.7-code", "MiniMax-M2.5",
            ),
        }
        for provider_name, models in catalogs.items():
            pcfg = providers.get(provider_name)
            if not isinstance(pcfg, dict):
                continue
            custom = pcfg.get("custom_models")
            if not isinstance(custom, list):
                custom = []
                pcfg["custom_models"] = custom
            known = {
                normalize_model_id(provider_name, str(model))
                for model in custom
                if str(model).strip()
            }
            for model in models:
                if normalize_model_id(provider_name, model) not in known:
                    custom.append(model)
        migrations[marker] = True

    marker = "alibaba_qwen38_singapore_20260821"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        model_studio = providers.get("alims-intl")
        if isinstance(model_studio, dict):
            custom = model_studio.get("custom_models")
            if not isinstance(custom, list):
                custom = []
                model_studio["custom_models"] = custom
            known = {
                normalize_model_id("alims-intl", str(model))
                for model in custom
                if str(model).strip()
            }
            if normalize_model_id("alims-intl", "qwen3.8-max") not in known:
                custom.insert(0, "qwen3.8-max")
            model_studio["region"] = "ap-southeast-1"
            old_default_profile = (
                normalize_model_id(
                    "alims-intl", str(model_studio.get("current_model") or "")
                )
                == "qwen3.7-max"
                and normalize_model_id(
                    "alims-intl", str(model_studio.get("opus_model") or "")
                )
                == "qwen3.7-max"
                and (positive_int(model_studio.get("max_output_tokens")) or 0) == 65_536
                and str(model_studio.get("effort_level") or "").strip().lower()
                == "high"
            )
            if old_default_profile:
                model_studio.update(
                    {
                        "current_model": "qwen3.8-max",
                        "opus_model": "qwen3.8-max",
                        "context_window": 1_000_000,
                        "max_model_len": 1_000_000,
                        "max_output_tokens": 131_072,
                        "effort_level": "xhigh",
                    }
                )
        for provider_name in ("alitoken", "alitoken-individual"):
            pcfg = providers.get(provider_name)
            if not isinstance(pcfg, dict):
                continue
            selected = normalize_model_id(
                provider_name, str(pcfg.get("current_model") or "")
            )
            if selected != "qwen3.8-max":
                continue
            if (positive_int(pcfg.get("context_window")) or 0) == 1_048_576:
                pcfg["context_window"] = 1_000_000
            if (positive_int(pcfg.get("max_model_len")) or 0) == 1_048_576:
                pcfg["max_model_len"] = 1_000_000
        migrations[marker] = True

    marker = "alibaba_token_plan_singapore_20260806"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        pcfg = providers.get("alitoken")
        if isinstance(pcfg, dict):
            custom = pcfg.get("custom_models")
            if not isinstance(custom, list):
                custom = []
                pcfg["custom_models"] = custom
            models = (
                "qwen3.8-max", "qwen3.7-max", "qwen3.7-plus",
                "qwen3.6-plus", "qwen3.6-flash", "deepseek-v4-pro",
                "deepseek-v4-flash", "deepseek-v3.2", "kimi-k2.7-code",
                "kimi-k2.6", "kimi-k2.5", "glm-5.2", "glm-5.1", "glm-5",
                "MiniMax-M2.5",
            )
            known = {
                normalize_model_id("alitoken", str(model))
                for model in custom
                if str(model).strip()
            }
            custom.extend(
                model
                for model in models
                if normalize_model_id("alitoken", model) not in known
            )
            pcfg["region"] = "ap-southeast-1"
        migrations[marker] = True

    marker = "alibaba_native_anthropic_routes_20260806"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        for provider_name in (
            "alicode", "alicode-intl", "alims-intl", "alitoken",
            "alitoken-individual",
        ):
            pcfg = providers.get(provider_name)
            if isinstance(pcfg, dict):
                pcfg["native_compat"] = True
        migrations[marker] = True

    marker = "alibaba_token_plan_individual_20260806"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        pcfg = providers.get("alitoken-individual")
        if isinstance(pcfg, dict):
            pcfg["region"] = "ap-southeast-1"
            pcfg["native_compat"] = True
        migrations[marker] = True

    marker = "ollama_cloud_deepseek_v4_flash_0731_20260803"
    if not migrations.get(marker):
        pcfg = cfg.get("providers", {}).get("ollama-cloud", {})
        if isinstance(pcfg, dict):
            model = strip_claude_context_suffix(
                str(pcfg.get("current_model") or "")
            ).lower()
            if model in {
                "deepseek-v4-flash:0731",
                "deepseek-v4-flash:0731-cloud",
            }:
                pcfg.setdefault("think", True)
                pcfg.setdefault("effort_level", "max")
                pcfg["model_context_max"] = 1_000_000
                pcfg["model_context_model"] = "deepseek-v4-flash:0731"
                if positive_int(pcfg.get("num_ctx_max")) in {
                    0,
                    131072,
                    524288,
                    999424,
                    1048576,
                }:
                    pcfg["num_ctx_max"] = 1_000_000
        migrations[marker] = True

    marker = "ollama_cloud_glm52_thinking_context_20260711"
    if not migrations.get(marker):
        pcfg = cfg.get("providers", {}).get("ollama-cloud", {})
        if isinstance(pcfg, dict):
            model = strip_claude_context_suffix(str(pcfg.get("current_model") or "")).lower()
            if model in {"glm-5.2", "glm-5.2:cloud"}:
                pcfg["think"] = True
                if positive_int(pcfg.get("num_ctx_max")) in {0, 131072, 1048576}:
                    pcfg["num_ctx_max"] = 999424
        migrations[marker] = True

    marker = "ollama_cloud_provider_context_default_20260724"
    if not migrations.get(marker):
        pcfg = cfg.get("providers", {}).get("ollama-cloud", {})
        if isinstance(pcfg, dict):
            model = strip_claude_context_suffix(
                str(pcfg.get("current_model") or "")
            ).lower()
            if model in {"glm-5.2", "glm-5.2:cloud"}:
                detected_limit = 1_000_000
                legacy_auto_512k = (
                    str(pcfg.get("llm_preset") or "") == "long-context-512k"
                    and str(pcfg.get("num_ctx") or "auto").strip().lower()
                    in {"", "auto", "dynamic"}
                    and positive_int(pcfg.get("num_ctx_max")) == 524288
                )
                if legacy_auto_512k:
                    pcfg.pop("llm_preset", None)
                    pcfg["num_ctx_max"] = detected_limit
                elif not pcfg.get("llm_preset") and positive_int(
                    pcfg.get("num_ctx_max")
                ) in {131072, 999424, 1048576}:
                    pcfg["num_ctx_max"] = detected_limit
                pcfg["model_context_max"] = detected_limit
                pcfg["model_context_model"] = "glm-5.2"
                pcfg["think"] = True
        migrations[marker] = True

    marker = "nvidia_hosted_router_default_20260509"
    if not migrations.get(marker):
        pcfg = cfg.get("providers", {}).get("nvidia-hosted", {})
        if isinstance(pcfg, dict) and bool(pcfg.get("native_compat", False)):
            pcfg["native_compat"] = False
        migrations[marker] = True

    marker = "lm_studio_native_default_20260523"
    if not migrations.get(marker):
        pcfg = cfg.get("providers", {}).get("lm-studio", {})
        if isinstance(pcfg, dict):
            pcfg["native_compat"] = True
        migrations[marker] = True

    marker = "default_timeout_5m_20260513"
    if not migrations.get(marker):
        # Historical marker kept for configs that have already recorded it.
        # Do not rewrite 10/30 minute values here anymore: those can now be
        # deliberate long-running timeout profiles.
        migrations[marker] = True

    marker = "default_timeout_2m_20260514"
    if not migrations.get(marker):
        # Historical marker only. The 2-minute default was too short for
        # 50k+ token hosted requests, so newer migrations no longer apply it.
        migrations[marker] = True

    marker = "default_timeout_restore_5m_20260515"
    if not migrations.get(marker):
        for pcfg in (cfg.get("providers") or {}).values():
            if not isinstance(pcfg, dict):
                continue
            if positive_int(pcfg.get("request_timeout_ms")) == 120000 and not pcfg.get("llm_preset"):
                pcfg["request_timeout_ms"] = DEFAULT_REQUEST_TIMEOUT_MS
                if "stream_idle_timeout_ms" not in pcfg:
                    pcfg["stream_idle_timeout_ms"] = DEFAULT_REQUEST_TIMEOUT_MS
        migrations[marker] = True

    marker = "nvidia_context_window_32k_20260513"
    if not migrations.get(marker):
        pcfg = cfg.get("providers", {}).get("nvidia-hosted", {})
        if isinstance(pcfg, dict) and not positive_int(pcfg.get("context_window")):
            pcfg["context_window"] = nvidia_hosted_context_default(str(pcfg.get("current_model") or ""))
        migrations[marker] = True

    marker = "nvidia_context_window_unforce_32k_20260513"
    if not migrations.get(marker):
        pcfg = cfg.get("providers", {}).get("nvidia-hosted", {})
        if isinstance(pcfg, dict) and positive_int(pcfg.get("context_window")) == 32768:
            pcfg["context_window"] = nvidia_hosted_context_default(str(pcfg.get("current_model") or ""))
        migrations[marker] = True

    marker = "stream_enabled_default_true_20260513"
    if not migrations.get(marker):
        for pcfg in (cfg.get("providers") or {}).values():
            if isinstance(pcfg, dict) and "stream_enabled" not in pcfg:
                pcfg["stream_enabled"] = True
        migrations[marker] = True

    marker = "default_channel_delivery_native_20260520"
    if not migrations.get(marker):
        ccfg = cfg.setdefault("claude_code", {})
        if not isinstance(ccfg, dict):
            ccfg = {}
            cfg["claude_code"] = ccfg
        if normalize_channel_delivery(ccfg.get("channel_delivery")) == "stdin":
            ccfg["channel_delivery"] = "native"
        migrations[marker] = True

    marker = "default_channel_delivery_llm_20260523"
    if not migrations.get(marker):
        ccfg = cfg.setdefault("claude_code", {})
        if not isinstance(ccfg, dict):
            ccfg = {}
            cfg["claude_code"] = ccfg
        if normalize_channel_delivery(ccfg.get("channel_delivery")) == "native":
            ccfg["channel_delivery"] = "llm"
        migrations[marker] = True

    marker = "remove_ciel_owned_external_mcp_20260812"
    if not migrations.get(marker):
        ccfg = cfg.setdefault("claude_code", {})
        if not isinstance(ccfg, dict):
            ccfg = {}
            cfg["claude_code"] = ccfg
        for key in ("channels", "development_channels", "channel_delivery"):
            ccfg.pop(key, None)
        migrations[marker] = True

    marker = "rate_limit_defaults_off_20260526"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        for provider_name in ("ollama", "ollama-cloud", "nvidia-hosted", "self-hosted-nim"):
            pcfg = providers.get(provider_name)
            if not isinstance(pcfg, dict):
                continue
            old_default_rpm = str(pcfg.get("rate_limit_rpm", "")).strip() == "40"
            old_default_status = bool(pcfg.get("rate_limit_status", True))
            if old_default_rpm and old_default_status:
                pcfg["rate_limit_rpm"] = 0
                pcfg["rate_limit_status"] = False
        pcfg = providers.get("lm-studio")
        if (
            isinstance(pcfg, dict)
            and str(pcfg.get("rate_limit_rpm", "")).strip() == "0"
            and bool(pcfg.get("rate_limit_status", True))
        ):
            pcfg["rate_limit_status"] = False
        migrations[marker] = True

    marker = "opencode_go_qwen36_plus_context_1m_20260530"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        pcfg = providers.get("opencode-go")
        if (
            isinstance(pcfg, dict)
            and is_qwen36_plus_model_id(str(pcfg.get("current_model") or ""))
            and positive_int(pcfg.get("context_window")) == 262144
        ):
            pcfg["context_window"] = 1048576
        migrations[marker] = True

    marker = "opencode_zen_qwen36_plus_free_model_20260614"
    if not migrations.get(marker):
        migrations[marker] = True

    marker = "opencode_qwen36_plus_parameters_20260614"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        for provider_name in OPENCODE_PROVIDER_NAMES:
            pcfg = providers.get(provider_name)
            if not isinstance(pcfg, dict):
                continue
            if not is_qwen36_plus_model_id(str(pcfg.get("current_model") or "")):
                continue
            if (positive_int(pcfg.get("context_window")) or 0) < 1048576:
                pcfg["context_window"] = 1048576
            if (positive_int(pcfg.get("context_reserve_tokens")) or 0) < 16384:
                pcfg["context_reserve_tokens"] = 16384
            if (positive_int(pcfg.get("max_output_tokens")) or 0) < 8192:
                pcfg["max_output_tokens"] = 8192
        migrations[marker] = True

    marker = "anthropic_drop_preset_output_tokens_20260610"
    if not migrations.get(marker):
        # Older anthropic presets force-wrote max_output_tokens (2048/4096/6144/8192),
        # which pinned CLAUDE_CODE_MAX_OUTPUT_TOKENS and overrode Claude Code's native
        # per-model default in routed mode. Those values are no longer written. Drop a
        # stale preset-origin value so existing routed configs recover the native cap.
        # Trade-off (matches the rate_limit precedent): a user who deliberately set one
        # of these exact round numbers via the CLI is cleared once; re-setting persists.
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        pcfg = providers.get("anthropic")
        if isinstance(pcfg, dict) and positive_int(pcfg.get("max_output_tokens")) in (2048, 4096, 6144, 8192):
            pcfg.pop("max_output_tokens", None)
        migrations[marker] = True

    marker = "opencode_default_ipv6_preferred_20260611"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        for provider_name in OPENCODE_PROVIDER_NAMES:
            pcfg = providers.get(provider_name)
            if isinstance(pcfg, dict) and not pcfg.get("ip_family"):
                pcfg["ip_family"] = "ipv6-preferred"
        migrations[marker] = True

    marker = "kimi_tool_choice_auto_only_20260625"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        pcfg = providers.get("kimi")
        if isinstance(pcfg, dict):
            pcfg["normalize_anthropic_tool_use"] = True
            pcfg["supports_tool_choice"] = False
        migrations[marker] = True

    marker = "kimi_forward_tool_choice_20260628"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        pcfg = providers.get("kimi")
        if isinstance(pcfg, dict):
            pcfg["normalize_anthropic_tool_use"] = True
            pcfg["supports_tool_choice"] = True
        migrations[marker] = True

    marker = "kimi_k3_model_20260716"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        pcfg = providers.get("kimi")
        if isinstance(pcfg, dict):
            custom = pcfg.get("custom_models")
            if not isinstance(custom, list):
                custom = []
                pcfg["custom_models"] = custom
            normalized_custom = {normalize_model_id("kimi", str(mid)) for mid in custom if str(mid).strip()}
            if KIMI_K3_MODEL not in normalized_custom:
                custom.append(KIMI_K3_MODEL)
        migrations[marker] = True

    marker = "kimi_k3_official_profile_20260722"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        pcfg = providers.get("kimi")
        if isinstance(pcfg, dict):
            custom = pcfg.get("custom_models")
            if not isinstance(custom, list):
                custom = []
                pcfg["custom_models"] = custom
            normalized_custom = {normalize_model_id("kimi", str(mid)) for mid in custom if str(mid).strip()}
            for model_id in (f"{KIMI_K3_MODEL}[1m]", "kimi-for-coding-highspeed"):
                if normalize_model_id("kimi", model_id) not in normalized_custom:
                    custom.append(model_id)
            current = normalize_model_id("kimi", str(pcfg.get("current_model") or ""))
            if current in {KIMI_K3_MODEL, f"{KIMI_K3_MODEL}[1m]"}:
                pcfg["context_window"] = 1048576
                pcfg["max_model_len"] = 1048576
                pcfg["effort_level"] = "high"
                pcfg["model_profile"] = "kimi-k3-1m"
        migrations[marker] = True

    marker = "kimi_k3_default_1m_20260723"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        pcfg = providers.get("kimi")
        if isinstance(pcfg, dict):
            current = normalize_model_id("kimi", str(pcfg.get("current_model") or ""))
            if current in {KIMI_K3_MODEL, f"{KIMI_K3_MODEL}[1m]"}:
                pcfg["context_window"] = 1048576
                pcfg["max_model_len"] = 1048576
                pcfg["effort_level"] = "high"
                pcfg["model_profile"] = "kimi-k3-1m"
        migrations[marker] = True

    marker = "kimi_k3_official_max_effort_20260806"
    if not migrations.get(marker):
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        pcfg = providers.get("kimi")
        if isinstance(pcfg, dict):
            current = normalize_model_id("kimi", str(pcfg.get("current_model") or ""))
            if (
                current in {KIMI_K3_MODEL, f"{KIMI_K3_MODEL}[1m]"}
                and pcfg.get("model_profile") == "kimi-k3-1m"
                and str(pcfg.get("effort_level") or "").lower() == "high"
            ):
                pcfg["effort_level"] = "max"
        migrations[marker] = True

__all__ = ["ConfigMigrationPolicy", "apply_config_migrations"]
