from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class PresetServices:
    CONTEXT_HEAVY_PRESETS: Any
    LLM_PRESETS: Any
    apply_lm_studio_loaded_context_guard: Callable[..., Any]
    apply_ollama_option: Callable[..., Any]
    apply_ollama_runtime_output_guard: Callable[..., Any]
    apply_provider_option: Callable[..., Any]
    apply_recommended_timeout_for_model_context: Callable[..., Any]
    cap_context_settings_to_model_capacity: Callable[..., Any]
    cap_output_settings_to_context_ratio: Callable[..., Any]
    llm_preset_text: Callable[..., Any]
    load_config: Callable[..., Any]
    model_family_text: Callable[..., Any]
    model_option_family: Callable[..., Any]
    ollama_extra_options: Callable[..., Any]
    ollama_num_ctx_status: Callable[..., Any]
    positive_int: Callable[..., Any]
    provider_model_context_capacity: Callable[..., Any]
    required_context_for_preset: Callable[..., Any]
    sync_ollama_library_context_limit: Callable[..., Any]
    ui_text: Callable[..., Any]
    upstream_model_context_limit: Callable[..., Any]
    with_preset_timeout_tokens: Callable[..., Any]


def apply_preset_to_provider(
    provider: str,
    pcfg: dict[str, Any],
    preset_id: str,
    lang: str | None = None,
    sync_ollama_context: bool = True,
    load_lm_studio: bool = False,
    *,
    services: PresetServices,
) -> list[str]:
    CONTEXT_HEAVY_PRESETS = services.CONTEXT_HEAVY_PRESETS
    LLM_PRESETS = services.LLM_PRESETS
    apply_lm_studio_loaded_context_guard = services.apply_lm_studio_loaded_context_guard
    apply_ollama_option = services.apply_ollama_option
    apply_ollama_runtime_output_guard = services.apply_ollama_runtime_output_guard
    apply_provider_option = services.apply_provider_option
    apply_recommended_timeout_for_model_context = services.apply_recommended_timeout_for_model_context
    cap_context_settings_to_model_capacity = services.cap_context_settings_to_model_capacity
    cap_output_settings_to_context_ratio = services.cap_output_settings_to_context_ratio
    llm_preset_text = services.llm_preset_text
    load_config = services.load_config
    model_family_text = services.model_family_text
    model_option_family = services.model_option_family
    ollama_extra_options = services.ollama_extra_options
    ollama_num_ctx_status = services.ollama_num_ctx_status
    positive_int = services.positive_int
    provider_model_context_capacity = services.provider_model_context_capacity
    required_context_for_preset = services.required_context_for_preset
    sync_ollama_library_context_limit = services.sync_ollama_library_context_limit
    ui_text = services.ui_text
    upstream_model_context_limit = services.upstream_model_context_limit
    with_preset_timeout_tokens = services.with_preset_timeout_tokens
    if preset_id not in LLM_PRESETS:
        raise SystemExit(f"Unknown preset: {preset_id}")
    lang = lang or load_config().get("language", "en")
    label = llm_preset_text(preset_id, lang)[0]
    pcfg["llm_preset"] = preset_id
    context_msgs: list[str] = []
    if provider in ("ollama", "ollama-cloud"):
        tokens_by_preset = {
            "balanced": [
                "num_ctx=auto",
                "num_ctx_min=32768",
                "num_ctx_max=65536",
                "num_predict=4096",
                "temperature=0.3",
                "top_p=0.9",
                "top_k=40",
                "think=false",
                "keep_alive=5m",
                "timeout=300000",
            ],
            "coding": [
                "num_ctx=auto",
                "num_ctx_min=32768",
                "num_ctx_max=65536",
                "num_predict=4096",
                "temperature=0.2",
                "top_p=0.8",
                "top_k=40",
                "think=false",
                "keep_alive=5m",
                "timeout=300000",
            ],
            "fast": [
                "num_ctx=32768",
                "num_predict=2048",
                "temperature=0.2",
                "top_p=0.8",
                "top_k=40",
                "think=false",
                "keep_alive=5m",
                "timeout=300000",
            ],
            "long-context-65k": [
                "num_ctx=auto",
                "num_ctx_min=65536",
                "num_ctx_max=131072",
                "num_predict=4096",
                "temperature=0.3",
                "top_p=0.9",
                "top_k=40",
                "think=false",
                "keep_alive=10m",
                "timeout=300000",
            ],
            "long-context-128k": [
                "num_ctx=auto",
                "num_ctx_min=65536",
                "num_ctx_max=131072",
                "num_predict=8192",
                "temperature=0.3",
                "top_p=0.9",
                "top_k=40",
                "think=false",
                "keep_alive=10m",
                "timeout=300000",
            ],
            "long-context-256k": [
                "num_ctx=auto",
                "num_ctx_min=131072",
                "num_ctx_max=262144",
                "num_predict=8192",
                "temperature=0.3",
                "top_p=0.9",
                "top_k=40",
                "think=false",
                "keep_alive=15m",
                "timeout=300000",
            ],
            "long-context-300k": [
                "num_ctx=auto",
                "num_ctx_min=131072",
                "num_ctx_max=307200",
                "num_predict=8192",
                "temperature=0.3",
                "top_p=0.9",
                "top_k=40",
                "think=false",
                "keep_alive=15m",
                "timeout=300000",
            ],
            "long-context-512k": [
                "num_ctx=auto",
                "num_ctx_min=262144",
                "num_ctx_max=524288",
                "num_predict=8192",
                "temperature=0.3",
                "top_p=0.9",
                "top_k=40",
                "think=false",
                "keep_alive=15m",
                "timeout=300000",
            ],
            "million-context-1m": [
                "num_ctx=auto",
                "num_ctx_min=262144",
                "num_ctx_max=1048576",
                "num_predict=8192",
                "temperature=0.3",
                "top_p=0.9",
                "top_k=40",
                "think=false",
                "keep_alive=15m",
                "timeout=300000",
            ],
            "large-output": [
                "num_ctx=auto",
                "num_ctx_min=65536",
                "num_ctx_max=131072",
                "num_predict=8192",
                "temperature=0.3",
                "top_p=0.9",
                "top_k=40",
                "think=false",
                "keep_alive=10m",
                "timeout=300000",
            ],
            "reasoning": [
                "num_ctx=auto",
                "num_ctx_min=65536",
                "num_ctx_max=131072",
                "num_predict=4096",
                "temperature=0.6",
                "top_p=0.95",
                "top_k=40",
                "think=true",
                "keep_alive=10m",
                "timeout=300000",
            ],
            "novelist": [
                "num_ctx=auto",
                "num_ctx_min=65536",
                "num_ctx_max=262144",
                "num_predict=8192",
                "temperature=0.85",
                "top_p=0.95",
                "top_k=80",
                "think=false",
                "keep_alive=10m",
                "timeout=300000",
            ],
            "humanities-researcher": [
                "num_ctx=auto",
                "num_ctx_min=131072",
                "num_ctx_max=524288",
                "num_predict=8192",
                "temperature=0.45",
                "top_p=0.9",
                "top_k=50",
                "think=false",
                "keep_alive=15m",
                "timeout=300000",
            ],
            "mathematician": [
                "num_ctx=auto",
                "num_ctx_min=65536",
                "num_ctx_max=262144",
                "num_predict=8192",
                "temperature=0.15",
                "top_p=0.85",
                "top_k=40",
                "think=true",
                "keep_alive=15m",
                "timeout=300000",
            ],
            "product-architect": [
                "num_ctx=auto",
                "num_ctx_min=65536",
                "num_ctx_max=262144",
                "num_predict=8192",
                "temperature=0.25",
                "top_p=0.85",
                "top_k=40",
                "think=false",
                "keep_alive=10m",
                "timeout=300000",
            ],
            "teacher": [
                "num_ctx=auto",
                "num_ctx_min=32768",
                "num_ctx_max=131072",
                "num_predict=6144",
                "temperature=0.55",
                "top_p=0.9",
                "top_k=60",
                "think=false",
                "keep_alive=10m",
                "timeout=300000",
            ],
        }
        for token in with_preset_timeout_tokens(tokens_by_preset[preset_id], preset_id):
            apply_ollama_option(pcfg, token)
        model_id = str(pcfg.get("current_model") or "").strip()
        if model_id and sync_ollama_context:
            context_msgs = sync_ollama_library_context_limit(provider, pcfg, model_id)
        context_msgs.extend(apply_ollama_runtime_output_guard(provider, pcfg))
    elif provider == "anthropic":
        # Anthropic presets intentionally do NOT set max_output_tokens. Forcing it
        # would pin CLAUDE_CODE_MAX_OUTPUT_TOKENS and override Claude Code's native
        # per-model default (e.g. Fable 5 / Opus = 64000, Sonnet = 32000). Claude
        # Code chooses that per-model cap itself; ciel-runtime must not degrade it.
        # Only an explicit user value set via the options screen should emit the
        # env var. Clear any preset-origin value so a stale forced cap cannot linger
        # (older builds wrote 2048/4096/6144/8192 here).
        pcfg.pop("max_output_tokens", None)
        tokens_by_preset = {
            "balanced": ["timeout=300000"],
            "coding": ["timeout=300000"],
            "fast": ["timeout=300000"],
            "long-context-65k": ["timeout=300000"],
            "long-context-128k": ["timeout=300000"],
            "long-context-256k": ["timeout=300000"],
            "long-context-300k": ["timeout=300000"],
            "long-context-512k": ["timeout=300000"],
            "million-context-1m": ["timeout=300000"],
            "large-output": ["timeout=300000"],
            "reasoning": ["timeout=300000"],
            "novelist": ["timeout=300000"],
            "humanities-researcher": ["timeout=300000"],
            "mathematician": ["timeout=300000"],
            "product-architect": ["timeout=300000"],
            "teacher": ["timeout=300000"],
        }
        for token in with_preset_timeout_tokens(tokens_by_preset[preset_id], preset_id):
            apply_provider_option(provider, pcfg, token)
    else:
        native_default = "false" if provider == "nvidia-hosted" else "true"
        if provider == "lm-studio":
            server_limit = provider_model_context_capacity(provider, pcfg)
        else:
            server_limit = upstream_model_context_limit(provider, pcfg) if provider in ("vllm", "self-hosted-nim") else None
        if provider == "nvidia-hosted":
            tokens_by_preset = {
                "balanced": [
                    "context_window=65536",
                    "reserve=4096",
                    "max_output_tokens=4096",
                    "timeout=300000",
                    "temperature=0.3",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "coding": [
                    "context_window=65536",
                    "reserve=4096",
                    "max_output_tokens=4096",
                    "timeout=300000",
                    "temperature=0.2",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "fast": [
                    "context_window=65536",
                    "reserve=2048",
                    "max_output_tokens=2048",
                    "timeout=300000",
                    "temperature=0.2",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "long-context-65k": [
                    "context_window=131072",
                    "reserve=8192",
                    "max_output_tokens=4096",
                    "timeout=300000",
                    "temperature=0.3",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "long-context-128k": [
                    "context_window=131072",
                    "reserve=8192",
                    "max_output_tokens=8192",
                    "timeout=300000",
                    "temperature=0.3",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "long-context-256k": [
                    "context_window=262144",
                    "reserve=8192",
                    "max_output_tokens=8192",
                    "timeout=300000",
                    "temperature=0.3",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "long-context-300k": [
                    "context_window=307200",
                    "reserve=8192",
                    "max_output_tokens=8192",
                    "timeout=300000",
                    "temperature=0.3",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "long-context-512k": [
                    "context_window=524288",
                    "reserve=16384",
                    "max_output_tokens=8192",
                    "timeout=300000",
                    "temperature=0.3",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "million-context-1m": [
                    "context_window=1048576",
                    "reserve=16384",
                    "max_output_tokens=8192",
                    "timeout=300000",
                    "temperature=0.3",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "large-output": [
                    "context_window=262144",
                    "reserve=8192",
                    "max_output_tokens=8192",
                    "timeout=300000",
                    "temperature=0.3",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "reasoning": [
                    "context_window=262144",
                    "reserve=8192",
                    "max_output_tokens=4096",
                    "timeout=300000",
                    "temperature=0.6",
                    "unset:top_p",
                    "unset:top_k",
                ],
                "novelist": [
                    "context_window=262144",
                    "reserve=8192",
                    "max_output_tokens=8192",
                    "timeout=300000",
                    "temperature=0.85",
                    "top_p=0.95",
                    "top_k=80",
                ],
                "humanities-researcher": [
                    "context_window=262144",
                    "reserve=8192",
                    "max_output_tokens=8192",
                    "timeout=300000",
                    "temperature=0.45",
                    "top_p=0.9",
                    "top_k=50",
                ],
                "mathematician": [
                    "context_window=262144",
                    "reserve=8192",
                    "max_output_tokens=8192",
                    "timeout=300000",
                    "temperature=0.15",
                    "top_p=0.85",
                    "top_k=40",
                ],
                "product-architect": [
                    "context_window=262144",
                    "reserve=8192",
                    "max_output_tokens=8192",
                    "timeout=300000",
                    "temperature=0.25",
                    "top_p=0.85",
                    "top_k=40",
                ],
                "teacher": [
                    "context_window=131072",
                    "reserve=4096",
                    "max_output_tokens=6144",
                    "timeout=300000",
                    "temperature=0.55",
                    "top_p=0.9",
                    "top_k=60",
                ],
            }
        else:
            tokens_by_preset = {
            "balanced": [
                "context_window=32768",
                "reserve=2048",
                "max_output_tokens=4096",
                "timeout=300000",
                "temperature=0.3",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "coding": [
                "context_window=32768",
                "reserve=2048",
                "max_output_tokens=4096",
                "timeout=300000",
                "temperature=0.2",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "fast": [
                "context_window=32768",
                "reserve=1024",
                "max_output_tokens=2048",
                "timeout=300000",
                "temperature=0.2",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "long-context-65k": [
                "context_window=65536",
                "reserve=4096",
                "max_output_tokens=4096",
                "timeout=300000",
                "temperature=0.3",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "long-context-128k": [
                "context_window=131072",
                "reserve=8192",
                "max_output_tokens=8192",
                "timeout=300000",
                "temperature=0.3",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "long-context-256k": [
                "context_window=262144",
                "reserve=8192",
                "max_output_tokens=8192",
                "timeout=300000",
                "temperature=0.3",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "long-context-300k": [
                "context_window=307200",
                "reserve=8192",
                "max_output_tokens=8192",
                "timeout=300000",
                "temperature=0.3",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "long-context-512k": [
                "context_window=524288",
                "reserve=16384",
                "max_output_tokens=8192",
                "timeout=300000",
                "temperature=0.3",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "million-context-1m": [
                "context_window=1048576",
                "reserve=16384",
                "max_output_tokens=8192",
                "timeout=300000",
                "temperature=0.3",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "large-output": [
                "context_window=65536",
                "reserve=4096",
                "max_output_tokens=8192",
                "timeout=300000",
                "temperature=0.3",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "reasoning": [
                "context_window=65536",
                "reserve=4096",
                "max_output_tokens=4096",
                "timeout=300000",
                "temperature=0.6",
                "unset:top_p",
                "unset:top_k",
                f"native={native_default}",
            ],
            "novelist": [
                "context_window=262144",
                "reserve=8192",
                "max_output_tokens=8192",
                "timeout=300000",
                "temperature=0.85",
                "top_p=0.95",
                "top_k=80",
                f"native={native_default}",
            ],
            "humanities-researcher": [
                "context_window=262144",
                "reserve=8192",
                "max_output_tokens=8192",
                "timeout=300000",
                "temperature=0.45",
                "top_p=0.9",
                "top_k=50",
                f"native={native_default}",
            ],
            "mathematician": [
                "context_window=262144",
                "reserve=8192",
                "max_output_tokens=8192",
                "timeout=300000",
                "temperature=0.15",
                "top_p=0.85",
                "top_k=40",
                f"native={native_default}",
            ],
            "product-architect": [
                "context_window=262144",
                "reserve=8192",
                "max_output_tokens=8192",
                "timeout=300000",
                "temperature=0.25",
                "top_p=0.85",
                "top_k=40",
                f"native={native_default}",
            ],
            "teacher": [
                "context_window=131072",
                "reserve=4096",
                "max_output_tokens=6144",
                "timeout=300000",
                "temperature=0.55",
                "top_p=0.9",
                "top_k=60",
                f"native={native_default}",
            ],
            }
            if provider == "kimi":
                tokens_by_preset["long-context-128k"] = [
                    "context_window=262144",
                    "reserve=32768",
                    "max_output_tokens=32768",
                    "timeout=600000",
                    "temperature=0.3",
                    "unset:top_p",
                    "unset:top_k",
                    "native=true",
                ]
                tokens_by_preset["long-context-256k"] = [
                    "context_window=262144",
                    "reserve=32768",
                    "max_output_tokens=32768",
                    "timeout=600000",
                    "temperature=0.3",
                    "unset:top_p",
                    "unset:top_k",
                    "native=true",
                ]
        for token in with_preset_timeout_tokens(tokens_by_preset[preset_id], preset_id):
            if provider == "nvidia-hosted" and token.startswith("native="):
                continue
            apply_provider_option(provider, pcfg, token)
        if server_limit:
            requested_context = positive_int(pcfg.get("context_window"))
            if requested_context and requested_context > server_limit:
                pcfg["context_window"] = server_limit
                if server_limit <= 32768:
                    pcfg["max_output_tokens"] = min(positive_int(pcfg.get("max_output_tokens")) or 2048, 2048)
                else:
                    pcfg["max_output_tokens"] = min(positive_int(pcfg.get("max_output_tokens")) or 4096, max(1024, server_limit // 8))
    context_msgs.extend(cap_context_settings_to_model_capacity(provider, pcfg))
    context_msgs.extend(cap_output_settings_to_context_ratio(provider, pcfg))
    if provider == "lm-studio":
        context_msgs.extend(apply_lm_studio_loaded_context_guard(pcfg, load=load_lm_studio))
    context_msgs.extend(apply_recommended_timeout_for_model_context(provider, pcfg))
    family = model_option_family(provider, pcfg)
    lines = [
        f"{ui_text('apply_preset', lang)}: {label}",
        f"Provider: {provider}; {ui_text('model_family', lang)}: {model_family_text(family, lang)}",
    ]
    lines.extend(context_msgs)
    if provider in ("vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim"):
        server_limit = provider_model_context_capacity(provider, pcfg) if provider == "lm-studio" else upstream_model_context_limit(provider, pcfg)
        required_context = required_context_for_preset(preset_id, provider) or 65536
        if server_limit:
            label = "Model max context" if provider == "lm-studio" else "Server max_model_len"
            lines.append(f"{label}: {server_limit}")
            if preset_id in CONTEXT_HEAVY_PRESETS and server_limit < required_context:
                lines.append(f"This preset requires restarting the server with --max-model-len {required_context} or higher.")
                lines.append("Client settings were capped to the server-reported context length.")
        elif preset_id in CONTEXT_HEAVY_PRESETS and provider != "lm-studio":
            lines.append("Could not verify server max_model_len; vLLM/NIM must be started with a matching context limit.")
    if provider in ("vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim"):
        lines.append(
            "Applied options: "
            f"context_window={pcfg.get('context_window', 'default')}, "
            f"reserve={pcfg.get('context_reserve_tokens', 'default')}, "
            f"max_output_tokens={pcfg.get('max_output_tokens', 'default')}, "
            f"timeout={pcfg.get('request_timeout_ms', 'default')}ms"
        )
    elif provider in ("ollama", "ollama-cloud"):
        opts = ollama_extra_options(pcfg)
        lines.append(
            "Applied options: "
            f"num_ctx={ollama_num_ctx_status(pcfg)}, "
            f"num_predict={opts.get('num_predict', 'default')}, "
            f"timeout={pcfg.get('request_timeout_ms', 'default')}ms"
        )
    elif provider == "anthropic":
        lines.append(
            "Applied options: "
            f"max_output_tokens={pcfg.get('max_output_tokens', 'default')}, "
            f"timeout={pcfg.get('request_timeout_ms', 'default')}ms"
        )
    return lines


__all__ = ["PresetServices", "apply_preset_to_provider"]
