"""Provider compatibility-test application service."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import sys
from typing import Any, Callable
import urllib.error


@dataclass(frozen=True, slots=True)
class CompatibilityTestConstants:
    api_key_probe_error: type[Exception]
    compatibility_test_header: str
    lm_studio_min_context: int
    opencode_provider_names: tuple[str, ...]
    router_base: str


@dataclass(frozen=True, slots=True)
class CompatibilityTestConfig:
    current_alias: Callable[..., Any]
    current_upstream_model_id: Callable[..., Any]
    ensure_current_model: Callable[..., Any]
    get_current_provider: Callable[..., Any]
    launch_model_id: Callable[..., Any]
    load_config: Callable[..., Any]
    normalize_model_id: Callable[..., Any]
    positive_int: Callable[..., Any]
    save_config: Callable[..., Any]
    upstream_model_runtime_info: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class CompatibilityTestMode:
    ensure_lm_studio_model_loaded: Callable[..., Any]
    lm_studio_native_enabled: Callable[..., Any]
    native_anthropic_base_url: Callable[..., Any]
    nim_native_enabled: Callable[..., Any]
    nvidia_native_enabled: Callable[..., Any]
    ollama_native_enabled: Callable[..., Any]
    provider_native_enabled: Callable[..., Any]
    upstream_api_model_id: Callable[..., Any]
    vllm_native_enabled: Callable[..., Any]
    vllm_tool_parser_hint: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class CompatibilityTestRequest:
    compatibility_endpoint_probe_lines: Callable[..., Any]
    compatibility_failure_diagnosis: Callable[..., Any]
    compatibility_http_error_message: Callable[..., Any]
    post_json: Callable[..., Any]
    provider_headers: Callable[..., Any]
    provider_ip_family_probe_lines: Callable[..., Any]
    run_api_key_probes: Callable[..., Any]
    start_router: Callable[..., Any]
    stop_router: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class CompatibilityTestProtocol:
    compatibility_text_request: Callable[..., Any]
    compatibility_tool_request: Callable[..., Any]
    compatibility_tool_result_request: Callable[..., Any]
    find_compat_tool_use: Callable[..., Any]
    known_tool_use_blocker: Callable[..., Any]
    normalize_thinking: Callable[..., Any]
    normalize_tool_choice: Callable[..., Any]
    ollama_chat_request: Callable[..., Any]
    resolve_requested_model: Callable[..., Any]
    response_text_preview: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class CompatibilityTestOutput:
    compatibility_runtime_lines: Callable[..., Any]
    join_url: Callable[..., Any]
    set_compatibility_cache: Callable[..., Any]
    summarize_compat_response: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class CompatibilityTestServices:
    constants: CompatibilityTestConstants
    config: CompatibilityTestConfig
    mode: CompatibilityTestMode
    request: CompatibilityTestRequest
    protocol: CompatibilityTestProtocol
    output: CompatibilityTestOutput


def run_compatibility_test(
    args: argparse.Namespace, *, services: CompatibilityTestServices
) -> None:
    constants = services.constants
    config = services.config
    mode = services.mode
    request = services.request
    protocol = services.protocol
    output = services.output
    COMPATIBILITY_TEST_HEADER = constants.compatibility_test_header
    CompatibilityApiKeyProbeError = constants.api_key_probe_error
    LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT = constants.lm_studio_min_context
    OPENCODE_PROVIDER_NAMES = constants.opencode_provider_names
    ROUTER_BASE = constants.router_base
    current_alias = config.current_alias
    current_upstream_model_id = config.current_upstream_model_id
    ensure_current_model_from_provider_list = config.ensure_current_model
    get_current_provider = config.get_current_provider
    launch_model_id = config.launch_model_id
    load_config = config.load_config
    normalize_model_id = config.normalize_model_id
    positive_int = config.positive_int
    save_config = config.save_config
    upstream_model_runtime_info = config.upstream_model_runtime_info
    ensure_lm_studio_model_loaded_for_context = mode.ensure_lm_studio_model_loaded
    lm_studio_native_compat_enabled = mode.lm_studio_native_enabled
    native_anthropic_base_url = mode.native_anthropic_base_url
    nim_native_compat_enabled = mode.nim_native_enabled
    nvidia_hosted_native_compat_enabled = mode.nvidia_native_enabled
    ollama_native_compat_enabled = mode.ollama_native_enabled
    provider_native_compat_enabled = mode.provider_native_enabled
    upstream_api_model_id = mode.upstream_api_model_id
    vllm_native_compat_enabled = mode.vllm_native_enabled
    vllm_tool_parser_hint = mode.vllm_tool_parser_hint
    compatibility_endpoint_probe_lines = request.compatibility_endpoint_probe_lines
    compatibility_failure_diagnosis = request.compatibility_failure_diagnosis
    compatibility_http_error_message = request.compatibility_http_error_message
    post_json = request.post_json
    provider_headers = request.provider_headers
    provider_ip_family_probe_lines = request.provider_ip_family_probe_lines
    run_compatibility_api_key_probes = request.run_api_key_probes
    start_router_if_needed = request.start_router
    stop_router_processes = request.stop_router
    compatibility_text_request = protocol.compatibility_text_request
    compatibility_tool_request = protocol.compatibility_tool_request
    compatibility_tool_result_request = protocol.compatibility_tool_result_request
    find_compat_tool_use = protocol.find_compat_tool_use
    known_compatibility_tool_use_blocker = protocol.known_tool_use_blocker
    normalize_thinking_for_non_anthropic_provider = protocol.normalize_thinking
    normalize_tool_choice_for_provider = protocol.normalize_tool_choice
    ollama_chat_request = protocol.ollama_chat_request
    resolve_requested_model = protocol.resolve_requested_model
    response_text_preview = protocol.response_text_preview
    compatibility_runtime_lines = output.compatibility_runtime_lines
    join_url = output.join_url
    set_compatibility_cache = output.set_compatibility_cache
    summarize_compat_response = output.summarize_compat_response
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    test_mode = getattr(args, "mode", "auto") or "auto"
    if test_mode not in ("auto", "quick", "smoke", "full"):
        raise SystemExit("test mode must be auto, quick, smoke, or full")
    effective_mode = "quick" if test_mode == "auto" and provider == "nvidia-hosted" else ("full" if test_mode == "auto" else test_mode)
    lm_studio_preflight_lines: list[str] = []
    if provider == "lm-studio":
        try:
            lm_studio_preflight_lines = ensure_lm_studio_model_loaded_for_context(pcfg, timeout=1.5)
            save_config(cfg)
        except Exception as exc:
            print("Compatibility: FAIL")
            print("Reason: CielRuntime could not automatically load the selected LM Studio model with the recommended context.")
            print(f"Diagnosis: LM Studio load failed ({type(exc).__name__}: {exc}).")
            sys.exit(1)
    selected, selection_lines = ensure_current_model_from_provider_list(provider, pcfg)
    for line in selection_lines:
        print(line)
    if selection_lines:
        save_config(cfg)
    if not selected:
        print("Compatibility: FAIL")
        print("Reason: No concrete provider model is selected.")
        print("Diagnosis: choose a model from the provider model list, then retry the compatibility test.")
        set_compatibility_cache(cfg, provider, normalize_model_id(provider, str(pcfg.get("current_model") or "")) or "(unset)", False, None, "No concrete provider model is selected.", "Choose a model from the provider model list.")
        raise SystemExit(1)
    ollama_native = ollama_native_compat_enabled(provider, pcfg)
    provider_native = provider_native_compat_enabled(provider, pcfg)
    native = ollama_native or provider_native
    model = current_upstream_model_id(provider, pcfg) if provider_native else (launch_model_id(provider, pcfg) if ollama_native else current_alias(cfg))
    request_model = upstream_api_model_id(provider, model) if native else model
    base = native_anthropic_base_url(provider, pcfg) if native else ROUTER_BASE
    if not native:
        # Compatibility tests must exercise the currently installed router.
        # Older long-running routers can keep stale NVIDIA proxy code alive
        # across npm upgrades, producing false nvd-claude-proxy failures.
        stop_router_processes(quiet=True)
        start_router_if_needed()
    url = join_url(base, "/v1/messages")
    headers = provider_headers(provider, pcfg)
    headers[COMPATIBILITY_TEST_HEADER] = "1"
    if ollama_native:
        headers = {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "authorization": "Bearer ollama",
            "x-api-key": "ollama",
            COMPATIBILITY_TEST_HEADER: "1",
        }
    text_body = normalize_tool_choice_for_provider(
        provider,
        pcfg,
        normalize_thinking_for_non_anthropic_provider(provider, pcfg, compatibility_text_request(request_model)),
    )
    tool_body = normalize_tool_choice_for_provider(
        provider,
        pcfg,
        normalize_thinking_for_non_anthropic_provider(provider, pcfg, compatibility_tool_request(request_model)),
    )
    print(f"Testing provider: {provider}")
    print(f"Test mode: {effective_mode}")
    if ollama_native:
        mode = "ollama-native"
    elif vllm_native_compat_enabled(provider, pcfg):
        mode = "vllm-native"
    elif lm_studio_native_compat_enabled(provider, pcfg):
        mode = "lm-studio-native"
    elif nim_native_compat_enabled(provider, pcfg):
        mode = "nim-native"
    elif nvidia_hosted_native_compat_enabled(provider, pcfg):
        mode = "nvidia-native"
    else:
        mode = "ciel-runtime-router"
    print(f"Mode: {mode}")
    print(f"Claude API URL: {url}")
    if not native:
        print(f"Upstream base URL: {pcfg.get('base_url')}")
        for line in provider_ip_family_probe_lines(provider, pcfg):
            print(line)
        if provider in ("ollama", "ollama-cloud"):
            req_preview = ollama_chat_request(resolve_requested_model(provider, pcfg, model), tool_body, pcfg, stream=False, provider=provider)
            print(f"Ollama num_ctx: {req_preview.get('options', {}).get('num_ctx', 'default')}")
    elif provider in OPENCODE_PROVIDER_NAMES:
        for line in provider_ip_family_probe_lines(provider, pcfg):
            print(line)
    print(f"Model: {model}")
    if request_model != model:
        print(f"API model: {request_model}")
    for line in lm_studio_preflight_lines:
        print(line)
    for line in compatibility_runtime_lines(provider, pcfg, native):
        print(line)
    for line in compatibility_endpoint_probe_lines(provider, pcfg, timeout=min(float(args.timeout or 1.5), 3.0)):
        print(line)
    if provider == "lm-studio":
        info = upstream_model_runtime_info(provider, pcfg, timeout=1.5)
        loaded = positive_int(info.get("loaded_context_len")) if info else None
        state = str(info.get("state") or "") if info else ""
        if loaded and loaded < LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT:
            print("Compatibility: FAIL")
            print(
                "Reason: LM Studio loaded context is "
                f"{loaded:,} tokens; Claude Code needs at least {LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT:,}."
            )
            print("Diagnosis: reload the model in LM Studio with a larger context length, then retry.")
            sys.exit(1)
        if state and state != "loaded":
            print("Compatibility: FAIL")
            print("Reason: the selected LM Studio model is not loaded, so the active context length cannot be verified.")
            print(f"Diagnosis: load the model in LM Studio with at least {LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT:,} context tokens, then retry.")
            sys.exit(1)
    if provider == "vllm":
        hint = vllm_tool_parser_hint(model)
        if hint:
            print(hint)

    def fail(message: str, code: int | None = None, diagnosis: str = "") -> None:
        print("Compatibility: FAIL")
        if code is not None:
            print(f"HTTP: {code}")
        print(f"Reason: {message[:1000]}")
        if diagnosis:
            print(diagnosis)
        set_compatibility_cache(cfg, provider, model, False, code, message, diagnosis)
        raise SystemExit(1)

    def run_phase(label: str, request_body: dict[str, Any]) -> Any:
        print(f"{label}: running")
        try:
            return post_json(
                url,
                request_body,
                headers=headers,
                timeout=args.timeout,
                provider=provider if native else None,
                pcfg=pcfg if native else None,
            )
        except urllib.error.HTTPError as exc:
            msg = compatibility_http_error_message(exc)
            diagnosis = compatibility_failure_diagnosis(provider, exc.code, msg)
            fail(f"{label}: {msg}", exc.code, diagnosis or "")
        except TimeoutError:
            print("Compatibility: TIMEOUT")
            print(f"Reason: {label} did not respond before the {args.timeout:g}s compatibility-test timeout.")
            print("Diagnosis: this timeout was not saved as a model failure. Retry the test or choose another model if it repeats.")
            sys.stdout.flush()
            sys.exit(1)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            if "timed out" in msg.lower() or "timeout" in msg.lower():
                print("Compatibility: TIMEOUT")
                print(f"Reason: {label}: {msg}")
                print("Diagnosis: this timeout was not saved as a model failure. Retry the test or choose another model if it repeats.")
                sys.stdout.flush()
                sys.exit(1)
            fail(f"{label}: {msg}")

    try:
        for line in run_compatibility_api_key_probes(provider, pcfg, model, text_body, args.timeout):
            print(line)
    except CompatibilityApiKeyProbeError as exc:
        fail(f"API key check: {exc}", exc.code, exc.diagnosis)

    text_data = run_phase("Text response", text_body)
    for line in summarize_compat_response(text_data, "Text response"):
        print(line)

    if effective_mode == "quick":
        set_compatibility_cache(cfg, provider, model, True, 200, "text quick OK", "")
        print("Compatibility: OK")
        print("Note: quick mode checked text only; run `ciel-runtime test 120 smoke` for tool_use or `ciel-runtime test 180 full` for tool_result.")
        return

    tool_blocker = known_compatibility_tool_use_blocker(provider, request_model)
    if tool_blocker:
        fail(
            f"Tool use: {tool_blocker}",
            diagnosis=(
                "Diagnosis: the selected model is not suitable for Claude Code through the Anthropic "
                "compatibility path because Claude Code depends on reliable tool_use responses."
            ),
        )

    tool_data = run_phase("Tool use", tool_body)
    tool_use, tool_error = find_compat_tool_use(tool_data)
    if not tool_use:
        diagnosis = (
            "Diagnosis: the model/server did not return a valid Anthropic tool_use block. "
            "Claude Code can fail with 'tool call could not be parsed' on this provider/model."
        )
        if provider == "vllm":
            hint = vllm_tool_parser_hint(model)
            if hint:
                diagnosis = f"{diagnosis} {hint}"
        fail(f"Tool use: {tool_error}", diagnosis=diagnosis)
    for line in summarize_compat_response(tool_data, "Tool use"):
        print(line)

    if effective_mode == "smoke":
        set_compatibility_cache(cfg, provider, model, True, 200, "text/tool_use smoke OK", "")
        print("Compatibility: OK")
        print("Note: smoke mode checked text and tool_use only; run `ciel-runtime test 180 full` for tool_result round trip.")
        return

    result_body = compatibility_tool_result_request(request_model, tool_use)
    result_data = run_phase("Tool result", result_body)
    result_preview = response_text_preview(result_data)
    if not result_preview:
        fail(
            "Tool result: no final text response after tool_result.",
            diagnosis="Diagnosis: the provider accepted tool_use but did not complete the tool_result round trip.",
        )
    for line in summarize_compat_response(result_data, "Tool result"):
        print(line)
    print(f"Tool result text: {result_preview[:120]}")

    set_compatibility_cache(cfg, provider, model, True, 200, "text/tool_use/tool_result OK", "")
    print("Compatibility: OK")

