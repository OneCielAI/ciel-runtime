"""Provider-native Ollama thinking-level policy.

Ollama's structured ``/api/show`` response identifies model capabilities and
architecture, but does not currently publish the accepted ``think`` levels.
This policy combines that discovered metadata with small architecture-level
contracts documented by Ollama. Unknown thinking architectures retain the
boolean behavior supported by the generic Ollama API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


INTERNAL_REASONING_EFFORT_KEY = "ciel_runtime_reasoning_effort"


def normalized_model_id(model_id: str) -> str:
    model = str(model_id or "").strip().lower()
    prefix = "ciel-runtime-ollama-cloud-"
    if model.startswith(prefix):
        model = model[len(prefix) :]
    if model.endswith("[1m]"):
        model = model[:-4]
    if model.endswith("-cloud") and ":" in model:
        model = model[:-6]
    elif model.endswith(":cloud"):
        model = model[:-6]
    return model


def request_effort(request: Mapping[str, Any]) -> str:
    for key in ("thinking", "output_config", "reasoning"):
        value = request.get(key)
        if isinstance(value, Mapping) and value.get("effort") is not None:
            return str(value["effort"]).strip().lower()
    metadata = request.get("metadata")
    if isinstance(metadata, Mapping):
        value = metadata.get(INTERNAL_REASONING_EFFORT_KEY)
        if value is not None:
            return str(value).strip().lower()
    return ""


def thinking_disabled(request: Mapping[str, Any]) -> bool:
    thinking = request.get("thinking")
    return isinstance(thinking, Mapping) and str(
        thinking.get("type") or ""
    ).strip().lower() in {"disabled", "none", "off", "false"}


@dataclass(frozen=True, slots=True)
class OllamaThinkingPolicy:
    """Resolve Claude/Codex effort into an Ollama model's native mode."""

    def architecture(self, options: Mapping[str, Any], model_id: str) -> str:
        model = normalized_model_id(model_id)
        metadata_model = normalized_model_id(
            str(options.get("ollama_model_metadata_model") or "")
        )
        discovered = str(options.get("ollama_model_architecture") or "").lower()
        if discovered and metadata_model == model:
            return discovered
        if model.startswith("deepseek-v4-"):
            return "deepseek4"
        if model.startswith("gpt-oss"):
            return "gptoss"
        if model in {"glm-5.2", "glm-5.2:cloud"}:
            return "glm5.2"
        return ""

    def value(
        self,
        options: Mapping[str, Any],
        model_id: str,
        request: Mapping[str, Any],
    ) -> bool | str:
        architecture = self.architecture(options, model_id)
        effort = request_effort(request)

        if architecture == "gptoss":
            # Ollama documents that GPT-OSS ignores booleans and cannot fully
            # disable thinking; only low, medium, and high are accepted.
            if not effort:
                effort = str(options.get("effort_level") or "medium").lower()
            if effort in {"medium"}:
                return "medium"
            if effort in {"high", "xhigh", "max", "ultra", "maximum"}:
                return "high"
            return "low"

        if architecture == "glm5.2":
            # The Ollama model card documents two effort levels: High and Max.
            if not effort:
                effort = str(options.get("effort_level") or "high").lower()
            return "max" if effort in {
                "max",
                "xhigh",
                "ultra",
                "maximum",
            } else "high"

        if architecture == "deepseek4":
            if thinking_disabled(request):
                return False
            if not effort and not bool(options.get("think", True)):
                return False
            if not effort:
                default = (
                    "max"
                    if normalized_model_id(model_id) == "deepseek-v4-flash:0731"
                    else "high"
                )
                effort = str(options.get("effort_level") or default).lower()
            if effort in {
                "none",
                "off",
                "disabled",
                "minimal",
                "minimum",
                "low",
                "light",
            }:
                return False
            if effort in {"max", "xhigh", "ultra", "maximum"}:
                return "max"
            return "high"

        return bool(options.get("think", False))


__all__ = [
    "INTERNAL_REASONING_EFFORT_KEY",
    "OllamaThinkingPolicy",
    "normalized_model_id",
    "request_effort",
    "thinking_disabled",
]
