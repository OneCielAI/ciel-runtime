"""Pure Codex command-line launch argument policies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def native_routed_config_args(
    router_base: str,
    provider: str,
    *,
    toml_string: Callable[[str], str],
) -> list[str]:
    base = router_base.rstrip("/") + "/backend-api/codex"
    if provider == "openai":
        return ["-c", 'model_provider="openai"', "-c", f"openai_base_url={toml_string(base)}"]
    return [
        "-c",
        f"model_provider={toml_string(provider)}",
        "-c",
        f"model_providers.{provider}.name={toml_string('Ciel Runtime Codex')}",
        "-c",
        f"model_providers.{provider}.base_url={toml_string(base)}",
        "-c",
        f"model_providers.{provider}.wire_api={toml_string('responses')}",
        "-c",
        f"model_providers.{provider}.requires_openai_auth=true",
        "-c",
        f"model_providers.{provider}.supports_websockets=false",
    ]


def passthrough_has_model_override(
    passthrough: list[str],
    *,
    has_option: Callable[..., bool],
    config_override_keys: Callable[[list[str]], set[str]],
) -> bool:
    return has_option(passthrough, "-m", "--model") or "model" in config_override_keys(
        passthrough
    )


def current_model_args(
    config: dict[str, Any],
    passthrough: list[str],
    *,
    overridden: Callable[[list[str]], bool],
    config_style: bool = False,
    toml_string: Callable[[str], str] = repr,
) -> list[str]:
    model = str(config.get("current_model") or "").strip()
    if not model or overridden(passthrough):
        return []
    return ["-c", f"model={toml_string(model)}"] if config_style else ["-m", model]


def help_requested(passthrough: list[str]) -> bool:
    return any(argument in ("help", "--help", "-h") for argument in passthrough)


def yolo_launch_args(
    passthrough: list[str], *, has_option: Callable[..., bool]
) -> list[str]:
    return [] if has_option(passthrough, "--yolo") else ["--yolo"]
