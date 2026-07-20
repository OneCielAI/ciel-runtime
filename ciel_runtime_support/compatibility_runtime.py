"""Runtime diagnostics and cache repository for compatibility probes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CompatibilityRuntimePorts:
    provider_policy: Callable[[str], Any]
    runtime_info: Callable[..., dict[str, Any] | None]
    positive_int: Callable[[Any], int | None]


@dataclass(frozen=True, slots=True)
class CompatibilityCachePorts:
    save_config: Callable[[dict[str, Any]], None]
    timestamp: Callable[[], int]


class CompatibilityRuntimeProjection:
    def __init__(self, ports: CompatibilityRuntimePorts) -> None:
        self.ports = ports

    @staticmethod
    def vllm_tool_parser_hint(model: str) -> str | None:
        normalized = model.lower()
        if "qwen3-coder" in normalized or "qwen3_coder" in normalized:
            return "vLLM hint: Qwen3-Coder models should be served with --enable-auto-tool-choice --tool-call-parser qwen3_xml."
        if any(marker in normalized for marker in ("qwen2.5", "qwen2_5", "qwq")):
            return "vLLM hint: Qwen2.5/QwQ tool templates usually use --enable-auto-tool-choice --tool-call-parser hermes."
        if "glm-4.7" in normalized or "glm4.7" in normalized:
            return "vLLM hint: GLM-4.7 models should be served with --enable-auto-tool-choice --tool-call-parser glm47."
        if any(
            marker in normalized
            for marker in ("glm-4.5", "glm4.5", "glm-4.6", "glm4.6")
        ):
            return "vLLM hint: GLM-4.5/4.6 models should be served with --enable-auto-tool-choice --tool-call-parser glm45."
        if "deepseek-v3.1" in normalized:
            return "vLLM hint: DeepSeek-V3.1 models should be served with --enable-auto-tool-choice --tool-call-parser deepseek_v31."
        if "deepseek-v3" in normalized or "deepseek-r1" in normalized:
            return "vLLM hint: DeepSeek-V3/R1 models require the matching DeepSeek tool parser and chat template from vLLM examples."
        if "llama-3" in normalized or "llama3" in normalized:
            return "vLLM hint: Llama 3.x models usually need --enable-auto-tool-choice --tool-call-parser llama3_json and the matching tool chat template."
        if "hermes" in normalized:
            return "vLLM hint: Hermes models should be served with --enable-auto-tool-choice --tool-call-parser hermes."
        if "qwen3" in normalized or "qwen-3" in normalized:
            return (
                "vLLM hint: this looks like a Qwen3-family model. Verify its model card/tool format; "
                "Qwen3-Coder uses qwen3_xml, while older Hermes-style Qwen templates use hermes."
            )
        return None

    def lines(
        self,
        provider: str,
        config: dict[str, Any],
        native: bool,
    ) -> list[str]:
        policy = self.ports.provider_policy(provider)
        if not policy.exposes_runtime_info:
            return []
        lines: list[str] = []
        info = self.ports.runtime_info(provider, config, timeout=4.0)
        configured_context = self.ports.positive_int(config.get("context_window"))
        configured_output = self.ports.positive_int(config.get("max_output_tokens"))
        if info:
            lines.append(f"Runtime models URL: {info.get('models_url')}")
            if info.get("runtime_model"):
                lines.append(f"Runtime model id: {info.get('runtime_model')}")
            runtime_limit = self.ports.positive_int(info.get("max_model_len"))
            if runtime_limit:
                lines.append(f"Runtime max_model_len: {runtime_limit}")
            else:
                lines.append("Runtime max_model_len: not reported by /v1/models")
            lines.extend(policy.runtime_metadata(info))
        else:
            runtime_limit = None
            lines.append(
                "Runtime max_model_len: unavailable (/v1/models did not return model metadata)"
            )
        if configured_context:
            lines.append(f"Configured context_window: {configured_context}")
        if configured_output:
            lines.append(f"Configured max_output_tokens: {configured_output}")
        if runtime_limit and configured_context and configured_context != runtime_limit:
            lines.append(
                f"Context warning: configured context_window {configured_context} "
                f"differs from runtime max_model_len {runtime_limit}."
            )
        if runtime_limit and configured_output and configured_output >= runtime_limit:
            lines.append(
                "Context warning: max_output_tokens is greater than or equal to the full runtime context length."
            )
        if native:
            lines.append(
                "Runtime mode note: native mode sends Claude Code requests directly; "
                "ciel-runtime cannot shrink max_tokens per request."
            )
        else:
            lines.append(
                "Runtime mode note: router mode can cap max_tokens based on configured context_window."
            )
        return lines


class CompatibilityCacheRepository:
    def __init__(self, ports: CompatibilityCachePorts) -> None:
        self.ports = ports

    def record(
        self,
        config: dict[str, Any],
        provider: str,
        model: str,
        ok: bool,
        code: int | None = None,
        message: str = "",
        diagnosis: str = "",
    ) -> None:
        cache = config.setdefault("compatibility_cache", {})
        if not isinstance(cache, dict):
            cache = {}
            config["compatibility_cache"] = cache
        provider_cache = cache.setdefault(provider, {})
        if not isinstance(provider_cache, dict):
            provider_cache = {}
            cache[provider] = provider_cache
        provider_cache[model] = {
            "ok": ok,
            "code": code,
            "message": message[:500],
            "diagnosis": diagnosis[:500],
            "tested_at": self.ports.timestamp(),
        }
        self.ports.save_config(config)


@dataclass(frozen=True, slots=True)
class ClaudeCliCapabilityProbe:
    cache: dict[str, bool]
    run: Callable[..., Any]

    def supports_permission_mode(self, executable: str) -> bool:
        cache_key = str(executable or "")
        if cache_key in self.cache:
            return self.cache[cache_key]
        try:
            process = self.run(
                [executable, "--help"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            help_text = process.stdout or ""
            supported = (
                "--permission-mode" in help_text
                and "bypassPermissions" in help_text
            )
        except Exception:
            supported = False
        self.cache[cache_key] = supported
        return supported
