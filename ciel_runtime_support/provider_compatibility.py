"""Provider compatibility strategies kept separate from transport adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from .architecture import ProviderConfig


FailureDiagnosis = Callable[[int | None, str], str | None]
ToolUseBlocker = Callable[[str], str]
RuntimeMetadataProjection = Callable[[Mapping[str, object]], tuple[str, ...]]
AutoWebSearchPolicy = Callable[[ProviderConfig], bool]


def _no_diagnosis(code: int | None, message: str) -> str | None:
    del code, message
    return None


def _no_tool_use_blocker(model: str) -> str:
    del model
    return ""


def _no_runtime_metadata(info: Mapping[str, object]) -> tuple[str, ...]:
    del info
    return ()


def _allow_auto_web_search(config: ProviderConfig) -> bool:
    del config
    return True


@dataclass(frozen=True)
class ProviderCompatibilityPolicy:
    advisor_transport: str = ""
    runtime_model_info_strategy: str = ""
    auto_web_search: AutoWebSearchPolicy = _allow_auto_web_search
    requires_compat_prompt: bool = True
    failure_diagnosis: FailureDiagnosis = _no_diagnosis
    tool_use_blocker: ToolUseBlocker = _no_tool_use_blocker
    runtime_metadata: RuntimeMetadataProjection = _no_runtime_metadata

    @property
    def exposes_runtime_info(self) -> bool:
        return bool(self.runtime_model_info_strategy)


@dataclass(frozen=True)
class ProviderCompatibilityRegistry:
    policies: Mapping[str, ProviderCompatibilityPolicy] = field(default_factory=dict)
    default: ProviderCompatibilityPolicy = ProviderCompatibilityPolicy()

    def resolve(self, provider: str) -> ProviderCompatibilityPolicy:
        return self.policies.get(provider, self.default)


def _vllm_failure_diagnosis(code: int | None, message: str) -> str | None:
    del code
    lower = message.lower()
    if "tool" not in lower and "parse" not in lower and "parser" not in lower:
        return None
    return (
        "Diagnosis: vLLM tool calling depends on the server's model-specific --tool-call-parser and chat template. "
        "For Qwen3-Coder models, current vLLM docs recommend --tool-call-parser qwen3_xml; Hermes is for Hermes-style models "
        "and some older Qwen tool templates."
    )


def _nvidia_failure_diagnosis(code: int | None, message: str) -> str | None:
    lower = message.lower()
    if code == 404:
        return (
            "Diagnosis: NVIDIA API Catalog does not expose this request path/model for the current account. "
            "Use the default router mode for nvidia-hosted, or pick another hosted model."
        )
    if code in (502, 503, 504):
        return (
            "Diagnosis: NVIDIA API Catalog or the hosted model backend returned a transient upstream error. "
            "Retry the compatibility test, or choose another NVIDIA hosted model if it repeats."
        )
    if any(
        marker in lower
        for marker in (
            "remotedisconnected",
            "remote end closed connection",
            "connection reset",
            "gateway timeout",
        )
    ):
        return (
            "Diagnosis: the NVIDIA hosted upstream closed the request without a complete response. "
            "This is usually a transient API Catalog/backend issue rather than a local ciel-runtime configuration error. "
            "Retry the test, or choose another hosted model if it repeats."
        )
    if "function" in lower and "not found" in lower:
        return (
            "Diagnosis: NVIDIA returned a missing function for this hosted model. The model is visible in /v1/models "
            "but is not callable with the current account."
        )
    return None


def _zai_tool_use_blocker(model: str) -> str:
    if str(model or "").strip().lower() != "glm-4.7-flash":
        return ""
    return (
        "Z.AI GLM-4.7-Flash responds to text requests but direct Anthropic tool-use probes time out. "
        "Claude Code requires tool calling for normal work; use glm-4.5-flash for a Flash/free model, "
        "or use glm-4.7/glm-5.2 if your Z.AI account can access them."
    )


def _lm_studio_runtime_metadata(info: Mapping[str, object]) -> tuple[str, ...]:
    lines: list[str] = []
    if info.get("loaded_context_len"):
        lines.append(f"Runtime loaded_context_length: {info['loaded_context_len']}")
    if info.get("state"):
        lines.append(f"Runtime model state: {info['state']}")
    return tuple(lines)


def _zai_auto_web_search(config: ProviderConfig) -> bool:
    return not bool(config.options.get("managed_mcp", True))


PROVIDER_COMPATIBILITY = ProviderCompatibilityRegistry(
    policies={
        "anthropic": ProviderCompatibilityPolicy(
            advisor_transport="anthropic",
            auto_web_search=lambda _config: False,
            requires_compat_prompt=False,
        ),
        "ollama": ProviderCompatibilityPolicy(advisor_transport="ollama"),
        "ollama-cloud": ProviderCompatibilityPolicy(advisor_transport="ollama"),
        "lm-studio": ProviderCompatibilityPolicy(
            advisor_transport="openai-compatible",
            runtime_model_info_strategy="lm_studio",
            runtime_metadata=_lm_studio_runtime_metadata,
        ),
        "vllm": ProviderCompatibilityPolicy(
            runtime_model_info_strategy="openai",
            failure_diagnosis=_vllm_failure_diagnosis,
        ),
        "nvidia-hosted": ProviderCompatibilityPolicy(
            advisor_transport="openai-compatible",
            failure_diagnosis=_nvidia_failure_diagnosis,
        ),
        "self-hosted-nim": ProviderCompatibilityPolicy(runtime_model_info_strategy="openai"),
        "zai": ProviderCompatibilityPolicy(
            auto_web_search=_zai_auto_web_search,
            tool_use_blocker=_zai_tool_use_blocker,
        ),
    }
)


__all__ = [
    "PROVIDER_COMPATIBILITY",
    "ProviderCompatibilityPolicy",
    "ProviderCompatibilityRegistry",
]
