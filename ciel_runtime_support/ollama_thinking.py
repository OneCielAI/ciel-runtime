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


@dataclass(frozen=True, slots=True)
class OllamaReasoningProfile:
    native_levels: tuple[str, ...]
    codex_levels: tuple[str, ...]
    default_native: str
    default_codex: str
    always_on: bool = False


_GENERIC_CLOUD_LEVEL_MODELS = frozenset(
    {
        "gemma4:31b",
        "glm-5.1",
        "kimi-k2.6",
        "kimi-k2.7-code",
        "minimax-m2.7",
        "minimax-m3",
        "nemotron-3-nano:30b",
        "nemotron-3-super",
        "nemotron-3-ultra",
        "qwen3.5:397b",
    }
)


def _codex_level_description(level: str) -> str:
    return {
        "low": "Low reasoning effort",
        "medium": "Medium reasoning effort",
        "high": "High reasoning effort",
        "xhigh": "Maximum reasoning effort (sent to Ollama as max)",
    }.get(level, f"{level.title()} reasoning effort")


def official_model_card_url(model_id: str) -> str:
    model = normalized_model_id(model_id)
    base = model.split(":", 1)[0]
    return f"https://ollama.com/library/{base}" if base else ""


def ollama_cloud_reasoning_profile(
    model_id: str,
    *,
    architecture: str = "",
    capabilities: tuple[str, ...] | list[str] = (),
    configured_levels: tuple[str, ...] | list[str] = (),
) -> OllamaReasoningProfile | None:
    model = normalized_model_id(model_id)
    discovered = str(architecture or "").strip().lower()
    capability_set = {
        str(item).strip().lower() for item in capabilities if str(item).strip()
    }
    levels = tuple(
        str(item).strip().lower() for item in configured_levels if str(item).strip()
    )
    if capability_set and "thinking" not in capability_set:
        return None
    if discovered == "deepseek4" or model.startswith("deepseek-v4-"):
        default_native = (
            "max" if model == "deepseek-v4-flash:0731" else "high"
        )
        return OllamaReasoningProfile(
            native_levels=("high", "max"),
            codex_levels=("low", "high", "xhigh"),
            default_native=default_native,
            default_codex="xhigh" if default_native == "max" else "high",
        )
    if discovered == "gptoss" or model.startswith("gpt-oss"):
        return OllamaReasoningProfile(
            native_levels=("low", "medium", "high"),
            codex_levels=("low", "medium", "high"),
            default_native="medium",
            default_codex="medium",
            always_on=True,
        )
    if discovered == "glm5.2" or model == "glm-5.2":
        return OllamaReasoningProfile(
            native_levels=("high", "max"),
            codex_levels=("high", "xhigh"),
            default_native="high",
            default_codex="high",
            always_on=True,
        )
    if discovered == "glm5_next" or model == "glm-5.3-flash":
        return OllamaReasoningProfile(
            native_levels=("low", "high", "max"),
            codex_levels=("low", "high", "xhigh"),
            default_native="high",
            default_codex="high",
            always_on=True,
        )
    if discovered == "kimi-k3" or model == "kimi-k3":
        return OllamaReasoningProfile(
            native_levels=("low", "high", "max"),
            codex_levels=("low", "high", "xhigh"),
            default_native="max",
            default_codex="xhigh",
            always_on=True,
        )
    if levels:
        native_default = "medium" if "medium" in levels else levels[0]
        codex_levels = tuple("xhigh" if level == "max" else level for level in levels)
        return OllamaReasoningProfile(
            native_levels=levels,
            codex_levels=codex_levels,
            default_native=native_default,
            default_codex="xhigh" if native_default == "max" else native_default,
        )
    if model in _GENERIC_CLOUD_LEVEL_MODELS:
        return OllamaReasoningProfile(
            native_levels=("low", "medium", "high", "max"),
            codex_levels=("low", "medium", "high", "xhigh"),
            default_native="medium",
            default_codex="medium",
        )
    return None


def ollama_cloud_model_config_updates(
    model_id: str,
    *,
    architecture: str = "",
    capabilities: tuple[str, ...] | list[str] = (),
    context_window: int | None = None,
) -> dict[str, Any]:
    normalized_capabilities = tuple(
        dict.fromkeys(
            str(item).strip().lower()
            for item in capabilities
            if str(item).strip()
        )
    )
    profile = ollama_cloud_reasoning_profile(
        model_id,
        architecture=architecture,
        capabilities=normalized_capabilities,
    )
    model = normalized_model_id(model_id)
    card_url = official_model_card_url(model)
    detail_parts = []
    if context_window:
        detail_parts.append(f"{context_window:,}-token context")
    if normalized_capabilities:
        detail_parts.append(", ".join(normalized_capabilities))
    detail = f" ({'; '.join(detail_parts)})" if detail_parts else ""
    description = f"Ollama Cloud {model}{detail}."
    if card_url:
        description += f" Official model card: {card_url}"
    codex_levels = profile.codex_levels if profile else ()
    catalog = {
        "description": description,
        "input_modalities": [
            "text",
            *(["image"] if "vision" in normalized_capabilities else []),
        ],
        "supports_image_detail_original": False,
        "supports_search_tool": False,
        "supported_reasoning_levels": [
            {
                "effort": level,
                "description": _codex_level_description(level),
            }
            for level in codex_levels
        ],
        "default_reasoning_level": (
            profile.default_codex if profile else "medium"
        ),
    }
    return {
        "think": profile is not None,
        "effort_level": profile.default_native if profile else "",
        "ollama_think_levels": list(profile.native_levels) if profile else [],
        "ollama_thinking_always_on": bool(profile and profile.always_on),
        "ollama_model_card_url": card_url,
        "codex_model_catalog": catalog,
    }


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
    ) -> bool | str | None:
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

        profile = ollama_cloud_reasoning_profile(
            model_id,
            architecture=architecture,
            capabilities=tuple(options.get("ollama_model_capabilities") or ()),
            configured_levels=tuple(options.get("ollama_think_levels") or ()),
        )
        if profile is not None:
            if thinking_disabled(request):
                if profile.always_on:
                    return profile.native_levels[0]
                return False
            if options.get("think_explicit") and not bool(options.get("think", False)):
                return False
            selected = effort or str(
                options.get("effort_level") or profile.default_native
            ).strip().lower()
            if selected in {"xhigh", "ultra", "maximum"}:
                selected = "max"
            elif selected in {"minimal", "minimum", "light"}:
                selected = "low"
            if selected in profile.native_levels:
                return selected
            if selected == "medium" and "high" in profile.native_levels:
                return "high"
            if selected == "max" and "high" in profile.native_levels:
                return "high"
            return profile.native_levels[0]

        capabilities = {
            str(item).strip().lower()
            for item in options.get("ollama_model_capabilities") or []
        }
        if options.get("think_explicit"):
            return bool(options.get("think", False))
        if "thinking" in capabilities:
            if thinking_disabled(request):
                return False
            if effort:
                return True
        return None


__all__ = [
    "INTERNAL_REASONING_EFFORT_KEY",
    "OllamaReasoningProfile",
    "OllamaThinkingPolicy",
    "normalized_model_id",
    "official_model_card_url",
    "ollama_cloud_model_config_updates",
    "ollama_cloud_reasoning_profile",
    "request_effort",
    "thinking_disabled",
]
