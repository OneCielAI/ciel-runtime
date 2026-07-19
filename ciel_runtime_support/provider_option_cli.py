"""CLI controller for provider and Ollama option configuration."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderOptionCliConfig:
    load: Callable[[], dict[str, Any]]
    save: Callable[[dict[str, Any]], None]
    normalize_provider: Callable[[str], str]
    clear_model_cache: Callable[[], None]
    output: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class OllamaOptionCommands:
    apply: Callable[[dict[str, Any], str], None]
    apply_timeout: Callable[[str, dict[str, Any]], list[str]]
    context_status: Callable[[dict[str, Any]], str]
    rate_usage: Callable[[str, dict[str, Any]], tuple[int, int | None]]
    options_status: Callable[[dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class ProviderOptionCommands:
    apply: Callable[[str, dict[str, Any], str], None]
    cap_context: Callable[[str, dict[str, Any]], list[str]]
    cap_output: Callable[[str, dict[str, Any]], list[str]]
    apply_timeout: Callable[[str, dict[str, Any]], list[str]]
    status: Callable[[str, dict[str, Any]], str]


_CONTEXT_KEYS = frozenset(("context_window", "context", "max_model_len"))
_OLLAMA_CONTEXT_KEYS = frozenset(
    (
        "num_ctx",
        "ctx",
        "num_ctx_min",
        "ctx_min",
        "min",
        "num_ctx_max",
        "ctx_max",
        "max",
    )
)
_TIMEOUT_KEYS = frozenset(
    (
        "timeout",
        "timeout_ms",
        "request_timeout",
        "request_timeout_ms",
        "stream_idle_timeout",
        "stream_idle_timeout_ms",
        "idle_timeout",
        "idle_timeout_ms",
    )
)
_COMMON_PROVIDER_NOTES = (
    "  max_output_tokens is passed to Claude Code as CLAUDE_CODE_MAX_OUTPUT_TOKENS.",
    "  context_window is a ciel-runtime/router cap; native mode still cannot raise the real server limit.",
    "  temperature/top_p/top_k are injected by ciel-runtime router mode when the provider supports them.",
)
_PROVIDER_EXAMPLES = (
    "  ciel-runtimectl provider-options deepseek max_output_tokens=8192 context_window=1048576",
    "  ciel-runtimectl provider-options opencode-go endpoint:custom-model=chat",
    "  ciel-runtimectl provider-options opencode ip_family=ipv6-preferred",
    "  ciel-runtimectl provider-options fireworks account_id=fireworks model_api_base_url=https://api.fireworks.ai",
    "  ciel-runtimectl provider-options nvidia-hosted max_output_tokens=4096 temperature=0.7 top_p=0.8 timeout=300000 rate_limit_rpm=40",
    "  ciel-runtimectl provider-options vllm max_output_tokens=4096 context_window=65536 timeout=300000",
    "  ciel-runtimectl provider-options self-hosted-nim native=true max_output_tokens=4096",
)
_OLLAMA_EXAMPLES = (
    "  ciel-runtimectl ollama-options num_ctx=auto min=32768 max=131072",
    "  ciel-runtimectl ollama-options num_ctx=65536 temperature=0.7 top_p=0.8 max_tokens=32768 timeout=300000",
    "  ciel-runtime --ca-ollama-option temperature=0.7 --ca-ollama-num-ctx 65536",
)


class ProviderOptionCliController:
    def __init__(
        self,
        config: ProviderOptionCliConfig,
        ollama: OllamaOptionCommands,
        provider: ProviderOptionCommands,
        *,
        supported_providers: Collection[str],
        ollama_providers: Collection[str],
        provider_notes: Mapping[str, Sequence[str]],
        unsupported_message: str,
    ) -> None:
        self.config = config
        self.ollama = ollama
        self.provider = provider
        self.supported_providers = frozenset(supported_providers)
        self.ollama_providers = frozenset(ollama_providers)
        self.provider_notes = provider_notes
        self.unsupported_message = unsupported_message

    def native(self, args: Any) -> None:
        config = self.config.load()
        provider_config = config["providers"]["ollama"]
        raw = getattr(args, "value", None)
        if raw:
            value = raw.lower()
            if value in ("on", "enable", "enabled", "true", "1"):
                provider_config["native_compat"] = True
            elif value in ("off", "disable", "disabled", "false", "0"):
                provider_config["native_compat"] = False
            else:
                raise SystemExit("Use: ciel-runtime ollama-native on|off|status")
            self.config.save(config)
        state = "on" if provider_config.get("native_compat", True) else "off"
        self._lines(
            (
                f"ollama_native_compat: {state}",
                f"base_url: {provider_config.get('base_url')}",
                f"model: {provider_config.get('current_model')}",
                'launch_env: ANTHROPIC_BASE_URL=<ollama>, ANTHROPIC_AUTH_TOKEN=ollama, ANTHROPIC_API_KEY=""',
            )
        )

    def ollama_options(self, args: Any) -> None:
        config = self.config.load()
        values = list(getattr(args, "values", []) or [])
        provider = self._selected_provider(
            values,
            str(config.get("current_provider", "ollama")),
            self.ollama_providers,
            "ollama",
        )
        provider_config = config["providers"][provider]
        if values:
            context_changed = self._contains_key(values, _OLLAMA_CONTEXT_KEYS)
            explicit_timeout = self._contains_key(values, _TIMEOUT_KEYS)
            for token in values:
                self.ollama.apply(provider_config, token)
            timeout_lines = (
                self.ollama.apply_timeout(provider, provider_config)
                if context_changed and not explicit_timeout
                else []
            )
            self._persist(config)
            self.config.output(f"Ollama options updated for {provider}.")
            self._lines(timeout_lines)
        self._ollama_status(provider, provider_config)

    def provider_options(self, args: Any) -> None:
        config = self.config.load()
        values = list(getattr(args, "values", []) or [])
        provider = self._selected_provider(
            values,
            str(config.get("current_provider", "vllm")),
            self.supported_providers,
        )
        if provider not in self.supported_providers:
            raise SystemExit(self.unsupported_message)
        provider_config = config["providers"][provider]
        if values:
            context_changed = self._contains_key(values, _CONTEXT_KEYS)
            explicit_timeout = self._contains_key(values, _TIMEOUT_KEYS)
            for token in values:
                self.provider.apply(provider, provider_config, token)
            cap_lines = self.provider.cap_context(provider, provider_config)
            cap_lines.extend(self.provider.cap_output(provider, provider_config))
            timeout_lines = (
                self.provider.apply_timeout(provider, provider_config)
                if context_changed and not explicit_timeout
                else []
            )
            self._persist(config)
            self.config.output(f"Provider options updated for {provider}.")
            self._lines((*cap_lines, *timeout_lines))
        self.config.output(f"provider: {provider}")
        self.config.output(
            f"provider_options: {self.provider.status(provider, provider_config)}"
        )
        self.config.output("Notes:")
        self._lines(_COMMON_PROVIDER_NOTES)
        self._lines(self.provider_notes.get(provider, ()))
        self.config.output("Examples:")
        self._lines(_PROVIDER_EXAMPLES)

    def _ollama_status(self, provider: str, config: dict[str, Any]) -> None:
        self._lines(
            (
                f"provider: {provider}",
                f"num_ctx: {self.ollama.context_status(config)}",
                f"keep_alive: {config.get('keep_alive', 'default')}",
                f"think: {bool(config.get('think', False))}",
                f"request_timeout_ms: {config.get('request_timeout_ms', 'default')}",
            )
        )
        used, limit = self.ollama.rate_usage(provider, config)
        if limit is not None:
            self.config.output(f"rate_limit_rpm: {limit}")
            if bool(config.get("rate_limit_status", False)):
                suffix = f"{used}/{limit}" if limit > 0 else f"{used}/min (unmanaged)"
                self.config.output(f"rpm_used: {suffix}")
        self.config.output(f"ollama_options: {self.ollama.options_status(config)}")
        self.config.output("Examples:")
        self._lines(_OLLAMA_EXAMPLES)

    def _selected_provider(
        self,
        values: list[str],
        current: str,
        allowed: Collection[str],
        fallback: str | None = None,
    ) -> str:
        provider = current if current in allowed else fallback or current
        if values:
            try:
                candidate = self.config.normalize_provider(values[0])
                if candidate in allowed:
                    provider = candidate
                    values.pop(0)
            except SystemExit:
                pass
        return provider

    @staticmethod
    def _contains_key(values: Sequence[str], keys: Collection[str]) -> bool:
        return any(
            token.split("=", 1)[0].replace("unset:", "").strip() in keys
            for token in values
        )

    def _persist(self, config: dict[str, Any]) -> None:
        self.config.save(config)
        self.config.clear_model_cache()

    def _lines(self, lines: Sequence[str]) -> None:
        for line in lines:
            self.config.output(line)
