"""Pure provider option mutations independent from CLI and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .architecture import ProviderConfigurationPolicy
from .provider_sampling_policy import ProviderSamplingPolicy


@dataclass(frozen=True, slots=True)
class ProviderOptionPolicy:
    normalize_claude_code_supported_capabilities: Callable[..., Any]
    normalize_ip_family: Callable[..., Any]
    normalize_model_id: Callable[..., Any]
    normalize_opencode_endpoint_kind: Callable[..., Any]
    parse_bool: Callable[..., Any]
    parse_config_value: Callable[..., Any]
    positive_int: Callable[..., Any]
    sampling: ProviderSamplingPolicy


def apply_ollama_option(
    pcfg: dict[str, Any], token: str, *, policy: ProviderOptionPolicy
) -> None:
    parse_bool = policy.parse_bool
    parse_config_value = policy.parse_config_value
    positive_int = policy.positive_int
    if token.startswith("unset:"):
        key = token.split(":", 1)[1].strip()
        if key in ("num_ctx", "ctx"):
            pcfg["num_ctx"] = "auto"
        elif key in ("context_window", "context", "max_model_len"):
            pcfg.pop("context_window", None)
            pcfg["num_ctx"] = "auto"
            pcfg.pop("num_ctx_max", None)
        elif key in ("num_ctx_min", "ctx_min", "min"):
            pcfg.pop("num_ctx_min", None)
        elif key in ("num_ctx_max", "ctx_max", "max"):
            pcfg.pop("num_ctx_max", None)
        elif key in ("max_output_tokens", "max_tokens", "maxtoken", "max_token", "num_predict"):
            pcfg.pop("max_output_tokens", None)
            pcfg.setdefault("ollama_options", {}).pop("num_predict", None)
        elif key in ("keep_alive", "keepalive"):
            pcfg.pop("keep_alive", None)
        elif key == "think":
            pcfg["think"] = False
        elif key in ("stream", "stream_enabled"):
            pcfg["stream_enabled"] = True
        elif key in ("stream_word_chunking", "word_chunking", "stream_chunk", "stream_words"):
            pcfg["stream_word_chunking"] = False
        elif key in ("stream_idle_timeout", "stream_idle_timeout_ms", "idle_timeout", "idle_timeout_ms"):
            pcfg.pop("stream_idle_timeout_ms", None)
        elif key in ("rate_limit", "rate_limit_rpm", "rpm"):
            pcfg["rate_limit_rpm"] = 0
            pcfg["rate_limit_status"] = False
        elif key in ("rate_limit_status", "rpm_status"):
            pcfg["rate_limit_status"] = False
        else:
            pcfg.setdefault("ollama_options", {}).pop(key, None)
        return
    if "=" not in token:
        raise SystemExit(f"Expected key=value or unset:key, got: {token}")
    key, raw_value = token.split("=", 1)
    key = key.strip()
    value = parse_config_value(raw_value)
    if key in ("num_ctx", "ctx"):
        if isinstance(value, str) and value.lower() in ("auto", "dynamic"):
            pcfg["num_ctx"] = "auto"
        else:
            fixed = positive_int(value)
            if not fixed:
                raise SystemExit("num_ctx must be auto or a positive integer")
            pcfg["num_ctx"] = fixed
        return
    if key in ("context_window", "context", "max_model_len"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("context_window must be a positive integer")
        pcfg["context_window"] = fixed
        pcfg["num_ctx"] = "auto"
        pcfg["num_ctx_max"] = fixed
        pcfg["num_ctx_min"] = min(fixed, 32768 if fixed <= 65536 else 65536)
        return
    if key in ("num_ctx_min", "ctx_min", "min"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("num_ctx_min must be a positive integer")
        pcfg["num_ctx_min"] = fixed
        return
    if key in ("num_ctx_max", "ctx_max", "max"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("num_ctx_max must be a positive integer")
        pcfg["num_ctx_max"] = fixed
        return
    if key in ("keep_alive", "keepalive"):
        if value is None:
            pcfg.pop("keep_alive", None)
        else:
            pcfg["keep_alive"] = str(value)
        return
    if key in ("timeout", "timeout_ms", "request_timeout", "request_timeout_ms"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("timeout must be a positive integer; values above 10000 are treated as milliseconds")
        pcfg["request_timeout_ms"] = fixed if key.endswith("_ms") or fixed > 10000 else fixed * 1000
        return
    if key in ("stream_idle_timeout", "stream_idle_timeout_ms", "idle_timeout", "idle_timeout_ms"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("stream_idle_timeout_ms must be a positive integer; values above 10000 are treated as milliseconds")
        pcfg["stream_idle_timeout_ms"] = fixed if key.endswith("_ms") or fixed > 10000 else fixed * 1000
        return
    if key in ("max_output_tokens", "max_tokens", "maxtoken", "max_token", "num_predict"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("max_tokens/num_predict must be a positive integer")
        pcfg["max_output_tokens"] = fixed
        pcfg.setdefault("ollama_options", {})["num_predict"] = fixed
        return
    if key == "think":
        pcfg["think"] = bool(value)
        return
    if key in ("stream", "stream_enabled"):
        pcfg["stream_enabled"] = parse_bool(value, default=True)
        return
    if key in ("stream_word_chunking", "word_chunking", "stream_chunk", "stream_words"):
        pcfg["stream_word_chunking"] = parse_bool(value, default=False)
        return
    if key in ("rate_limit", "rate_limit_rpm", "rpm"):
        fixed = positive_int(value)
        if not fixed:
            if str(value).lower() in ("0", "false", "off", "disable", "disabled", "none", "unset"):
                pcfg["rate_limit_rpm"] = 0
                pcfg["rate_limit_status"] = False
                return
            raise SystemExit("rate_limit_rpm must be a positive integer, or 0 to disable")
        pcfg["rate_limit_rpm"] = fixed
        return
    if key in ("rate_limit_status", "rpm_status"):
        pcfg["rate_limit_status"] = parse_bool(value, default=False)
        return
    opts = pcfg.setdefault("ollama_options", {})
    if value is None:
        opts.pop(key, None)
    else:
        opts[key] = value


def apply_provider_option(
    provider: str,
    pcfg: dict[str, Any],
    token: str,
    *,
    policy: ProviderOptionPolicy,
    capabilities: ProviderConfigurationPolicy,
) -> None:
    normalize_claude_code_supported_capabilities = policy.normalize_claude_code_supported_capabilities
    normalize_ip_family = policy.normalize_ip_family
    normalize_model_id = policy.normalize_model_id
    normalize_opencode_endpoint_kind = policy.normalize_opencode_endpoint_kind
    parse_bool = policy.parse_bool
    parse_config_value = policy.parse_config_value
    positive_int = policy.positive_int
    sampling = policy.sampling
    if capabilities.supports_model_endpoint_overrides and token.startswith("endpoint:") and "=" in token:
        key, raw_value = token.split("=", 1)
        model_id = key.split(":", 1)[1].strip()
        endpoint = normalize_opencode_endpoint_kind(raw_value)
        if not model_id:
            raise SystemExit("endpoint override requires endpoint:<model-id>=<messages|chat|responses|gemini>")
        if not endpoint:
            raise SystemExit("endpoint override must be one of: messages, chat, responses, gemini")
        endpoints = pcfg.setdefault("model_endpoints", {})
        if not isinstance(endpoints, dict):
            endpoints = {}
            pcfg["model_endpoints"] = endpoints
        endpoints[normalize_model_id(provider, model_id)] = endpoint
        return
    token_key = ""
    if token.startswith("unset:"):
        token_key = token.split(":", 1)[1].strip()
    elif "=" in token:
        token_key = token.split("=", 1)[0].strip()
    provider_common_keys = {
        "force_query_string",
        "force_query",
        "upstream_query",
        "test_query_string",
        "supports_tool_choice",
        "tool_choice",
        "tool-choice",
        "auto_tool_choice",
    }
    if capabilities.mutation_strategy == "ollama" and token_key not in provider_common_keys:
        apply_ollama_option(pcfg, token, policy=policy)
        return
    if token.startswith("unset:"):
        key = token.split(":", 1)[1].strip()
        if key in ("context_window", "context", "max_model_len"):
            pcfg.pop("context_window", None)
        elif key in ("context_reserve_tokens", "reserve"):
            pcfg.pop("context_reserve_tokens", None)
        elif key in ("max_output_tokens", "max_tokens", "maxtoken", "max_token"):
            pcfg.pop("max_output_tokens", None)
        elif key in ("timeout", "timeout_ms", "request_timeout", "request_timeout_ms"):
            pcfg.pop("request_timeout_ms", None)
        elif key in ("stream_idle_timeout", "stream_idle_timeout_ms", "idle_timeout", "idle_timeout_ms"):
            pcfg.pop("stream_idle_timeout_ms", None)
        elif key in ("rate_limit", "rate_limit_rpm", "rpm"):
            pcfg["rate_limit_rpm"] = 0
            pcfg["rate_limit_status"] = False
        elif key in ("rate_limit_status", "rpm_status"):
            pcfg["rate_limit_status"] = False
        elif key in ("native", "native_compat"):
            if capabilities.native_compat_error:
                raise SystemExit(capabilities.native_compat_error)
            pcfg["native_compat"] = True
        elif key in ("route", "routed", "route_through_router", "router"):
            pcfg["route_through_router"] = False
        elif key in ("stream", "stream_enabled"):
            pcfg["stream_enabled"] = True
        elif key in ("stream_word_chunking", "word_chunking", "stream_chunk", "stream_words"):
            pcfg["stream_word_chunking"] = False
        elif key in ("ip_family", "network_family", "address_family", "addr_family"):
            pcfg.pop("ip_family", None)
        elif key in ("force_query_string", "force_query", "upstream_query", "test_query_string"):
            pcfg.pop("force_query_string", None)
        elif key in ("supports_tool_choice", "tool_choice", "tool-choice", "auto_tool_choice"):
            pcfg.pop("supports_tool_choice", None)
        elif key in capabilities.text_option_aliases:
            pcfg.pop(capabilities.text_option_aliases[key], None)
        elif key in ("workflows_enabled", "workflow", "workflows"):
            pcfg["workflows_enabled"] = False
        elif key in ("ultracode_enabled", "ultracode"):
            pcfg["ultracode_enabled"] = False
        elif key in ("claude_code_supported_capabilities", "supported_capabilities", "capabilities"):
            pcfg.pop("claude_code_supported_capabilities", None)
        elif capabilities.supports_model_endpoint_overrides and key.startswith("endpoint:"):
            model_id = normalize_model_id(provider, key.split(":", 1)[1].strip())
            endpoints = pcfg.get("model_endpoints")
            if isinstance(endpoints, dict):
                endpoints.pop(model_id, None)
        elif sampling.option_key(key):
            pcfg.pop(sampling.option_key(key), None)
        else:
            raise SystemExit(f"Unknown provider option: {key}")
        return
    if "=" not in token:
        raise SystemExit(f"Expected key=value or unset:key, got: {token}")
    key, raw_value = token.split("=", 1)
    key = key.strip()
    value = parse_config_value(raw_value)
    if key in ("force_query_string", "force_query", "upstream_query", "test_query_string"):
        # Operator-controlled raw query string for upstream /v1/messages. A
        # leading "?" is tolerated and empty/default-like values clear it.
        text = "" if value is None else str(value).strip().lstrip("?").strip()
        if not text or text.lower() in ("default", "unset", "none", "null"):
            pcfg.pop("force_query_string", None)
        else:
            pcfg["force_query_string"] = text
        return
    if key in ("supports_tool_choice", "tool_choice", "tool-choice", "auto_tool_choice"):
        if value is None or str(value).strip().lower() in ("", "auto", "default", "unset", "none", "null"):
            pcfg.pop("supports_tool_choice", None)
        else:
            pcfg["supports_tool_choice"] = parse_bool(value, default=True)
        return
    if key in ("context_window", "context", "max_model_len"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("context_window must be a positive integer")
        pcfg["context_window"] = fixed
        return
    if key in ("context_reserve_tokens", "reserve"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("context_reserve_tokens must be a positive integer")
        pcfg["context_reserve_tokens"] = fixed
        return
    if key in ("max_output_tokens", "max_tokens", "maxtoken", "max_token"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("max_output_tokens must be a positive integer")
        pcfg["max_output_tokens"] = fixed
        return
    if key in ("timeout", "timeout_ms", "request_timeout", "request_timeout_ms"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("timeout must be a positive integer; values above 10000 are treated as milliseconds")
        pcfg["request_timeout_ms"] = fixed if key.endswith("_ms") or fixed > 10000 else fixed * 1000
        return
    if key in ("stream_idle_timeout", "stream_idle_timeout_ms", "idle_timeout", "idle_timeout_ms"):
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("stream_idle_timeout_ms must be a positive integer; values above 10000 are treated as milliseconds")
        pcfg["stream_idle_timeout_ms"] = fixed if key.endswith("_ms") or fixed > 10000 else fixed * 1000
        return
    if key in ("rate_limit", "rate_limit_rpm", "rpm"):
        fixed = positive_int(value)
        if value in (0, "0", False, None) or str(value).lower() in ("false", "off", "disable", "disabled", "none", "unset"):
            pcfg["rate_limit_rpm"] = 0
            pcfg["rate_limit_status"] = False
            return
        if not fixed:
            raise SystemExit("rate_limit_rpm must be a positive integer, or 0/unset to disable")
        pcfg["rate_limit_rpm"] = fixed
        return
    if key in ("native", "native_compat"):
        if capabilities.native_compat_error:
            raise SystemExit(capabilities.native_compat_error)
        pcfg["native_compat"] = bool(value)
        return
    if key in ("route", "routed", "route_through_router", "router"):
        if not capabilities.supports_route_through_router:
            raise SystemExit("route_through_router is only available for the Anthropic provider")
        pcfg["route_through_router"] = parse_bool(value, default=False)
        return
    if key in ("stream", "stream_enabled"):
        pcfg["stream_enabled"] = parse_bool(value, default=True)
        return
    if key in ("stream_word_chunking", "word_chunking", "stream_chunk", "stream_words"):
        pcfg["stream_word_chunking"] = parse_bool(value, default=False)
        return
    if key in ("ip_family", "network_family", "address_family", "addr_family"):
        pcfg["ip_family"] = normalize_ip_family(value)
        return
    if key in capabilities.text_option_aliases:
        target = capabilities.text_option_aliases[key]
        text = "" if value is None else str(value).strip()
        if target in capabilities.strip_trailing_slash_fields:
            text = text.rstrip("/")
        if not text:
            pcfg.pop(target, None)
        else:
            pcfg[target] = text
        return
    if key in ("rate_limit_status", "rpm_status"):
        pcfg["rate_limit_status"] = parse_bool(value, default=False)
        return
    if key in ("workflows_enabled", "workflow", "workflows"):
        pcfg["workflows_enabled"] = parse_bool(value, default=False)
        return
    if key in ("ultracode_enabled", "ultracode"):
        pcfg["ultracode_enabled"] = parse_bool(value, default=False)
        if pcfg["ultracode_enabled"]:
            pcfg["workflows_enabled"] = True
        return
    if key in ("claude_code_supported_capabilities", "supported_capabilities", "capabilities"):
        pcfg["claude_code_supported_capabilities"] = normalize_claude_code_supported_capabilities(value)
        return
    sample_key = sampling.option_key(key)
    if sample_key:
        if value is None:
            pcfg.pop(sample_key, None)
        else:
            pcfg[sample_key] = sampling.validate(sample_key, value)
        return
    raise SystemExit(f"Unknown provider option: {key}")
