"""Claude Code model, environment, and runtime-setting projections.

This module keeps Claude Code launch policy independent from the composition
root.  Callers provide narrow ports for provider/catalog capabilities and keep
configuration I/O at the application boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable


@dataclass(frozen=True)
class ClaudeLimitPorts:
    positive_int: Callable[[Any], int | None]
    cap_output_tokens: Callable[..., int]
    ollama_options: Callable[[dict[str, Any]], dict[str, Any]]
    context_limit: Callable[[str, dict[str, Any]], int | None]


class ClaudeLimitPolicy:
    def __init__(self, ports: ClaudeLimitPorts) -> None:
        self._ports = ports

    def output_token_limit(self, provider: str, config: dict[str, Any]) -> int | None:
        configured = self._ports.positive_int(config.get("max_output_tokens"))
        if configured:
            return self._ports.cap_output_tokens(provider, config, configured)
        if provider in ("ollama", "ollama-cloud"):
            configured = self._ports.positive_int(self._ports.ollama_options(config).get("num_predict"))
            if configured:
                return self._ports.cap_output_tokens(provider, config, configured)
        return None

    def auto_compact_window(self, provider: str, config: dict[str, Any]) -> int | None:
        configured = self._ports.positive_int(config.get("auto_compact_window"))
        limit = self._ports.context_limit(provider, config)
        if configured:
            return min(configured, limit) if limit else configured
        return limit or None


@dataclass(frozen=True)
class ClaudeModelPorts:
    strip_context_suffix: Callable[[str | None], str]
    current_upstream_model: Callable[[str, dict[str, Any]], str]
    unslug_alias: Callable[..., str | None]
    model_map: Callable[..., dict[str, str]]
    context_hint: Callable[[str], int | None]
    anthropic_limit_hints: Callable[[str], dict[str, Any]]
    positive_int: Callable[[Any], int | None]
    configured_model_ids: Callable[[str, dict[str, Any]], list[str]]
    normalize_model_id: Callable[[str, str], str]
    alias_for: Callable[[str, str], str]


class ClaudeModelAliasPolicy:
    def __init__(self, ports: ClaudeModelPorts) -> None:
        self._ports = ports

    def claims_one_million_context(
        self,
        provider: str,
        config: dict[str, Any],
        model: str,
        *,
        include_current: bool = True,
        context_limit: int | None = None,
    ) -> bool:
        candidates = [str(model or "")]
        if include_current:
            candidates.extend([
                str(config.get("current_model") or ""),
                str(self._ports.current_upstream_model(provider, config) or ""),
            ])
        explicit_unknown_one_million = False
        for candidate in candidates:
            candidate = str(candidate or "").strip()
            if not candidate:
                continue
            if candidate.startswith(f"ciel-runtime-{provider}-"):
                resolved = self._ports.unslug_alias(
                    provider,
                    candidate,
                    self._ports.model_map(provider, config, fetch=False),
                )
                if not resolved:
                    continue
                candidate = resolved
            hint = self._ports.context_hint(self._ports.strip_context_suffix(candidate))
            if hint is None and provider == "anthropic":
                hint = self._ports.positive_int(self._ports.anthropic_limit_hints(candidate).get("context_window"))
            if hint is not None:
                if hint >= 1_000_000:
                    return True
                continue
            if "[1m]" in candidate.lower():
                explicit_unknown_one_million = True
        if explicit_unknown_one_million:
            return True
        if include_current:
            return bool(context_limit and context_limit >= 1_000_000)
        return False

    def context_model_alias(
        self,
        provider: str,
        config: dict[str, Any],
        model: str,
        upstream_model: str | None = None,
        *,
        context_limit: int | None = None,
    ) -> str:
        model = self._ports.strip_context_suffix(model)
        probe_model = upstream_model if upstream_model is not None else model
        include_current = upstream_model is None
        claims_one_million = self.claims_one_million_context(
            provider,
            config,
            probe_model,
            include_current=include_current,
            context_limit=context_limit,
        )
        if claims_one_million and "[1m]" not in model.lower():
            return f"{model}[1m]"
        return model

    def matches_family(self, model_id: str, family: str) -> bool:
        normalized = self._ports.strip_context_suffix(model_id).strip().lower()
        family = family.strip().lower()
        if not normalized or family not in ("opus", "sonnet", "haiku"):
            return False
        return bool(re.search(rf"(?:^|[-_./]){re.escape(family)}(?:[-_./]|$)", normalized))

    def default_model_aliases(
        self,
        provider: str,
        config: dict[str, Any],
        current_model_alias: str,
        *,
        context_limit: int | None = None,
    ) -> dict[str, str]:
        current_upstream = self._ports.current_upstream_model(provider, config)
        candidates = self._ports.configured_model_ids(provider, config)
        if current_upstream and current_upstream not in candidates:
            candidates.insert(0, current_upstream)
        result: dict[str, str] = {}
        for family, key in (
            ("haiku", "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
            ("opus", "ANTHROPIC_DEFAULT_OPUS_MODEL"),
            ("sonnet", "ANTHROPIC_DEFAULT_SONNET_MODEL"),
        ):
            selected = ""
            selected_from_config = False
            configured = str(config.get(f"{family}_model") or "").strip() if provider == "zai" else ""
            if configured:
                selected = self._ports.normalize_model_id(provider, configured)
                selected_from_config = bool(selected)
            if not selected and self.matches_family(current_upstream, family):
                selected = current_upstream
            if not selected:
                selected = next((item for item in candidates if self.matches_family(item, family)), "")
            alias = self._ports.alias_for(provider, selected) if selected else current_model_alias
            result[key] = self.context_model_alias(
                provider,
                config,
                alias,
                selected if selected_from_config or provider == "anthropic" else None,
                context_limit=context_limit,
            )
        return result


@dataclass(frozen=True)
class ClaudeEnvironmentSourcePorts:
    load_config: Callable[[], dict[str, Any]]
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    direct_native: Callable[[str, dict[str, Any]], bool]
    primary_api_key: Callable[[str, dict[str, Any]], str]
    meaningful_key: Callable[[Any], bool]
    current_alias: Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class ClaudeEnvironmentFeaturePorts:
    capability_string: Callable[..., str]
    current_upstream_model: Callable[[str, dict[str, Any]], str]
    resolve_requested_model: Callable[[str, dict[str, Any], str], str]
    workflows_enabled: Callable[[str, dict[str, Any]], bool]
    router_auth_token: Callable[[str, dict[str, Any]], str]
    context_limit: Callable[[str, dict[str, Any]], int | None]


class ClaudeEnvironmentProjection:
    def __init__(
        self,
        router_base: str,
        limits: ClaudeLimitPolicy,
        aliases: ClaudeModelAliasPolicy,
        sources: ClaudeEnvironmentSourcePorts,
        features: ClaudeEnvironmentFeaturePorts,
    ) -> None:
        self._router_base = router_base
        self._limits = limits
        self._aliases = aliases
        self._sources = sources
        self._features = features

    def apply_common(self, provider: str, config: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
        env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] = "1"
        output_tokens = self._limits.output_token_limit(provider, config)
        if output_tokens:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(output_tokens)
        compact_window = self._limits.auto_compact_window(provider, config)
        if compact_window:
            env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(compact_window)
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(compact_window)
        effort_level = str(config.get("effort_level") or "").strip().lower()
        if effort_level:
            env["CLAUDE_CODE_EFFORT_LEVEL"] = effort_level
        advisor_model = str(config.get("advisor_model") or "").strip()
        if advisor_model:
            env["CIEL_RUNTIME_ADVISOR_MODEL"] = advisor_model
        claude_model = str(env.get("ANTHROPIC_MODEL") or env.get("CIEL_RUNTIME_MODEL_ALIAS") or "").strip()
        capability_string = self._features.capability_string(
            provider,
            config,
            self._features.current_upstream_model(provider, config),
        )
        if claude_model and capability_string:
            env["ANTHROPIC_CUSTOM_MODEL_OPTION"] = claude_model
            env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"] = capability_string
        for key in (
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
        ):
            model_alias = str(env.get(key) or "").strip()
            if not model_alias:
                continue
            upstream_model = self._features.resolve_requested_model(provider, config, model_alias)
            default_caps = self._features.capability_string(provider, config, upstream_model)
            if default_caps:
                env[f"{key}_SUPPORTS"] = default_caps
                env[f"{key}_SUPPORTED_CAPABILITIES"] = default_caps
        if self._features.workflows_enabled(provider, config):
            env.pop("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", None)
        return env

    def build(self, config: dict[str, Any] | None = None) -> dict[str, str]:
        config = config or self._sources.load_config()
        provider, provider_config = self._sources.current_provider(config)
        if self._sources.direct_native(provider, provider_config):
            env = {"CIEL_RUNTIME_PROVIDER": provider}
            key = self._sources.primary_api_key(provider, provider_config)
            if self._sources.meaningful_key(key):
                env["ANTHROPIC_API_KEY"] = str(key)
            return env
        context_limit = self._features.context_limit(provider, provider_config)
        claude_model = self._aliases.context_model_alias(
            provider,
            provider_config,
            self._sources.current_alias(config),
            context_limit=context_limit,
        )
        defaults = self._aliases.default_model_aliases(
            provider,
            provider_config,
            claude_model,
            context_limit=context_limit,
        )
        env = {
            "CIEL_RUNTIME_PROVIDER": provider,
            "ANTHROPIC_BASE_URL": self._router_base,
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            "ANTHROPIC_MODEL": claude_model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": defaults["ANTHROPIC_DEFAULT_HAIKU_MODEL"],
            "ANTHROPIC_DEFAULT_OPUS_MODEL": defaults["ANTHROPIC_DEFAULT_OPUS_MODEL"],
            "ANTHROPIC_DEFAULT_SONNET_MODEL": defaults["ANTHROPIC_DEFAULT_SONNET_MODEL"],
            "CLAUDE_CODE_SUBAGENT_MODEL": claude_model,
            "CIEL_RUNTIME_MODEL_ALIAS": claude_model,
            "CIEL_RUNTIME_BYPASS_PERMISSIONS": "1",
        }
        auth_token = self._features.router_auth_token(provider, provider_config)
        if auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = auth_token
        return self.apply_common(provider, provider_config, env)


@dataclass(frozen=True)
class ClaudeRuntimeSettingsPorts:
    ultracode_enabled: Callable[[str, dict[str, Any]], bool]
    has_passthrough_option: Callable[[list[str], str], bool]
    log: Callable[[str, str], None]


class ClaudeRuntimeSettingsPolicy:
    def __init__(self, ports: ClaudeRuntimeSettingsPorts) -> None:
        self._ports = ports

    def settings(self, provider: str, config: dict[str, Any]) -> dict[str, Any]:
        return {"ultracode": True} if self._ports.ultracode_enabled(provider, config) else {}

    def append_args(
        self,
        extra_args: list[str],
        passthrough: list[str],
        provider: str,
        config: dict[str, Any],
    ) -> None:
        settings = self.settings(provider, config)
        if not settings:
            return
        if self._ports.has_passthrough_option(passthrough, "--settings"):
            self._ports.log("WARN", "claude_code_runtime_settings_skipped reason=passthrough_settings_present")
            return
        extra_args.extend(["--settings", json.dumps(settings, separators=(",", ":"))])


class ClaudeEnvironmentShellRenderer:
    OPTIONAL_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    PROJECTED_KEYS = (
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
        "CLAUDE_CODE_ATTRIBUTION_HEADER",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
        "CLAUDE_CODE_EFFORT_LEVEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_CUSTOM_MODEL_OPTION",
        "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTS",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTS",
        "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTS",
        "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CIEL_RUNTIME_MODEL_ALIAS",
        "CIEL_RUNTIME_PROVIDER",
    )

    @classmethod
    def lines(cls, env: dict[str, str]) -> list[str]:
        return [
            f"export {key}={json.dumps(env[key])}" if key in env else f"unset {key}"
            for key in (*cls.OPTIONAL_KEYS, *cls.PROJECTED_KEYS)
        ]


__all__ = [
    "ClaudeEnvironmentFeaturePorts",
    "ClaudeEnvironmentProjection",
    "ClaudeEnvironmentShellRenderer",
    "ClaudeEnvironmentSourcePorts",
    "ClaudeLimitPolicy",
    "ClaudeLimitPorts",
    "ClaudeModelAliasPolicy",
    "ClaudeModelPorts",
    "ClaudeRuntimeSettingsPolicy",
    "ClaudeRuntimeSettingsPorts",
]
