from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class LmStudioRuntimeServices:
    api_base: Callable[[dict[str, Any]], str | None]
    current_model: Callable[[str, dict[str, Any]], str]
    http_json: Callable[..., Any]
    join_url: Callable[[str, str], str]
    model_list_headers: Callable[[str, dict[str, Any]], dict[str, str]]
    model_id_matches: Callable[[str, str], bool]
    positive_int: Callable[[Any], int | None]
    model_context: Callable[[dict[str, Any]], int | None]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class LmStudioLifecyclePolicy:
    recommended_preset: Callable[[str, dict[str, Any]], str]
    required_context: Callable[[str, str], int]
    model_context_hint: Callable[[str], int | None]
    default_context: int
    minimum_context: int


class LmStudioModelLifecycle:
    def __init__(
        self,
        runtime: LmStudioRuntimeServices,
        policy: LmStudioLifecyclePolicy,
        post_json: Callable[..., Any],
    ) -> None:
        self.runtime = runtime
        self.policy = policy
        self.post_json = post_json

    def v1_model_info(
        self, config: dict[str, Any], timeout: float = 3.0
    ) -> dict[str, Any] | None:
        base = self.runtime.api_base(config)
        current = self.runtime.current_model("lm-studio", config)
        if not base or not current:
            return None
        data = self.runtime.http_json(
            self.runtime.join_url(base, "/api/v1/models"),
            headers=self.runtime.model_list_headers("lm-studio", config),
            timeout=timeout,
        )
        items = data.get("models") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        return next(
            (
                item
                for item in items
                if isinstance(item, dict)
                and self.runtime.model_id_matches(str(item.get("key") or ""), current)
            ),
            None,
        )

    def loaded_instance_ids(
        self, config: dict[str, Any], timeout: float = 3.0
    ) -> list[str]:
        try:
            item = self.v1_model_info(config, timeout)
        except Exception:
            return []
        instances = item.get("loaded_instances") if isinstance(item, dict) else None
        if not isinstance(instances, list):
            return []
        return [
            str(instance["id"])
            for instance in instances
            if isinstance(instance, dict) and instance.get("id")
        ]

    def target_context(
        self, config: dict[str, Any], info: dict[str, Any] | None = None
    ) -> int | None:
        target = self.runtime.positive_int(config.get("context_window"))
        if not target:
            preset = self.policy.recommended_preset("lm-studio", config)
            target = self.policy.required_context(preset, "lm-studio")
        target = target or self.policy.default_context
        max_length = self.runtime.positive_int(
            (info or {}).get("max_model_len")
        ) or self.policy.model_context_hint(str(config.get("current_model") or ""))
        return min(target, max_length) if max_length else target

    def load_timeout_seconds(self, config: dict[str, Any]) -> float:
        configured = self.runtime.positive_int(config.get("request_timeout_ms"))
        return max(60.0, min(600.0, configured / 1000.0)) if configured else 300.0

    def load_model(
        self,
        config: dict[str, Any],
        context_length: int,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        base = self.runtime.api_base(config)
        model = self.runtime.current_model("lm-studio", config)
        if not base or not model:
            raise RuntimeError("LM Studio model or base URL is not configured")
        return self.post_json(
            self.runtime.join_url(base, "/api/v1/models/load"),
            {
                "model": model,
                "context_length": context_length,
                "flash_attention": True,
                "echo_load_config": True,
            },
            headers=self.runtime.model_list_headers("lm-studio", config),
            timeout=timeout or self.load_timeout_seconds(config),
        )

    def unload_loaded_instances(
        self, config: dict[str, Any], timeout: float = 20.0
    ) -> list[str]:
        base = self.runtime.api_base(config)
        if not base:
            return []
        unloaded: list[str] = []
        for instance_id in self.loaded_instance_ids(config):
            try:
                self.post_json(
                    self.runtime.join_url(base, "/api/v1/models/unload"),
                    {"instance_id": instance_id},
                    headers=self.runtime.model_list_headers("lm-studio", config),
                    timeout=timeout,
                )
                unloaded.append(instance_id)
            except Exception:
                continue
        return unloaded

    def load_response_context(self, response: dict[str, Any], fallback: int) -> int:
        load_config = response.get("load_config") if isinstance(response, dict) else None
        if isinstance(load_config, dict):
            value = self.runtime.positive_int(load_config.get("context_length"))
            if value:
                return value
        return fallback

    def ensure_loaded_context(
        self, config: dict[str, Any], timeout: float = 3.0
    ) -> list[str]:
        info = discover_lm_studio_runtime(config, self.runtime, timeout=timeout)
        target = self.target_context(config, info)
        if not target:
            return []
        messages: list[str] = []
        loaded = self.runtime.positive_int((info or {}).get("loaded_context_len"))
        state = str((info or {}).get("state") or "")
        max_length = self.runtime.positive_int((info or {}).get("max_model_len"))
        if max_length:
            messages.append(f"LM Studio model max context: {max_length:,} tokens.")
        if loaded:
            messages.append(f"LM Studio loaded context: {loaded:,} tokens.")
        if loaded and state == "loaded" and loaded >= target:
            config["native_compat"] = True
            if not self.runtime.positive_int(config.get("context_window")):
                config["context_window"] = min(loaded, target)
            return messages
        if max_length and max_length < self.policy.minimum_context:
            config["native_compat"] = False
            config["context_window"] = max_length
            messages.append(
                "LM Studio selected model cannot provide enough context for Claude Code "
                f"({max_length:,} < {self.policy.minimum_context:,})."
            )
            return messages
        action = "reloading" if loaded else "loading"
        messages.append(
            f"LM Studio auto-{action} selected model with {target:,} context tokens."
        )
        try:
            response = self.load_model(config, target)
        except Exception:
            if not loaded:
                raise
            self.unload_loaded_instances(config)
            response = self.load_model(config, target)
        actual = self.load_response_context(response, target)
        config["context_window"] = actual
        if actual >= self.policy.minimum_context:
            config["native_compat"] = True
            messages.append(
                f"LM Studio loaded selected model with {actual:,} context tokens."
            )
        else:
            config["native_compat"] = False
            current_output = self.runtime.positive_int(config.get("max_output_tokens")) or 4096
            config["max_output_tokens"] = min(current_output, max(512, actual // 4))
            messages.append(
                "LM Studio loaded the model, but the applied context is still too small "
                f"for Claude Code ({actual:,} < {self.policy.minimum_context:,})."
            )
        return messages

    def context_guard(
        self,
        config: dict[str, Any],
        *,
        load: bool = False,
    ) -> list[str]:
        if load:
            try:
                return self.ensure_loaded_context(config, timeout=1.5)
            except Exception as exc:
                config["native_compat"] = False
                return [
                    "LM Studio could not automatically load the selected model with the recommended context.",
                    f"LM Studio load error: {type(exc).__name__}: {exc}",
                ]

        info: dict[str, Any] | None = None
        target = self.target_context(config, info)
        loaded = self.runtime.positive_int((info or {}).get("loaded_context_len"))
        state = str((info or {}).get("state") or "")
        max_length = self.runtime.positive_int((info or {}).get("max_model_len"))
        messages: list[str] = []
        if max_length:
            messages.append(f"LM Studio model max context: {max_length:,} tokens.")
        if target:
            messages.append(f"LM Studio target context: {target:,} tokens.")
        if max_length and max_length < self.policy.minimum_context:
            config["native_compat"] = False
            config["context_window"] = max_length
            messages.append(
                "LM Studio selected model cannot provide enough context for Claude Code "
                f"({max_length:,} < {self.policy.minimum_context:,})."
            )
            return messages
        config["native_compat"] = True
        if loaded:
            messages.append(
                f"LM Studio currently loaded context: {loaded:,} tokens."
            )
            if target and loaded < target:
                messages.append(
                    "LM Studio will reload this model with the target context when you launch or test."
                )
        elif state and state != "loaded":
            messages.append(
                "LM Studio will load this model with the target context when you launch or test."
            )
        elif target:
            messages.append(
                "LM Studio will prepare this model with the target context when you launch or test."
            )
        return messages


@dataclass(frozen=True, slots=True)
class LmStudioLifecycleApi:
    """Explicit public adapter for late-bound LM Studio lifecycle services."""

    lifecycle_factory: Callable[[], LmStudioModelLifecycle]

    def v1_model_info(self, pcfg: dict[str, Any], timeout: float = 3.0) -> dict[str, Any] | None:
        return self.lifecycle_factory().v1_model_info(pcfg, timeout)

    def loaded_instance_ids(self, pcfg: dict[str, Any], timeout: float = 3.0) -> list[str]:
        return self.lifecycle_factory().loaded_instance_ids(pcfg, timeout)

    def target_context(self, pcfg: dict[str, Any], info: dict[str, Any] | None = None) -> int | None:
        return self.lifecycle_factory().target_context(pcfg, info)

    def load_timeout_seconds(self, pcfg: dict[str, Any]) -> float:
        return self.lifecycle_factory().load_timeout_seconds(pcfg)

    def load_model(self, pcfg: dict[str, Any], context_length: int, timeout: float | None = None) -> dict[str, Any]:
        return self.lifecycle_factory().load_model(pcfg, context_length, timeout)

    def unload_loaded_instances(self, pcfg: dict[str, Any], timeout: float = 20.0) -> list[str]:
        return self.lifecycle_factory().unload_loaded_instances(pcfg, timeout)

    def load_response_context(self, response: dict[str, Any], fallback: int) -> int:
        return self.lifecycle_factory().load_response_context(response, fallback)

    def ensure_loaded_for_context(self, pcfg: dict[str, Any], timeout: float = 3.0) -> list[str]:
        return self.lifecycle_factory().ensure_loaded_context(pcfg, timeout)

    def apply_loaded_context_guard(self, pcfg: dict[str, Any], load: bool = False) -> list[str]:
        return self.lifecycle_factory().context_guard(pcfg, load=load)


def discover_lm_studio_runtime(
    provider_config: dict[str, Any],
    services: LmStudioRuntimeServices,
    *,
    timeout: float = 3.0,
) -> dict[str, Any] | None:
    base = services.api_base(provider_config)
    if not base:
        return None
    current = services.current_model("lm-studio", provider_config)
    headers = services.model_list_headers("lm-studio", provider_config)
    v0_url = services.join_url(base, "/api/v0/models")
    try:
        data = services.http_json(v0_url, headers=headers, timeout=timeout)
        runtime = _runtime_from_v0(data, v0_url, current, services)
        if runtime is not None:
            return runtime
    except Exception as exc:
        services.log(
            "WARN",
            f"lm_studio_runtime_discovery_failed api=v0 error={type(exc).__name__}: {exc}",
        )
    v1_url = services.join_url(base, "/api/v1/models")
    try:
        data = services.http_json(v1_url, headers=headers, timeout=timeout)
        return _runtime_from_v1(data, v1_url, current, services)
    except Exception as exc:
        services.log(
            "WARN",
            f"lm_studio_runtime_discovery_failed api=v1 error={type(exc).__name__}: {exc}",
        )
        return None


def _select_model(
    items: Any,
    current: str,
    id_key: str,
    matches: Callable[[str, str], bool],
) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    fallback: dict[str, Any] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if fallback is None:
            fallback = item
        if matches(str(item.get(id_key) or ""), current):
            return item
    return fallback if not current else None


def _runtime_from_v0(
    data: Any,
    models_url: str,
    current: str,
    services: LmStudioRuntimeServices,
) -> dict[str, Any] | None:
    items = data.get("data") if isinstance(data, dict) else None
    selected = _select_model(items, current, "id", services.model_id_matches)
    if selected is None:
        return None
    return {
        "models_url": models_url,
        "requested_model": current,
        "runtime_model": str(selected.get("id") or ""),
        "max_model_len": services.positive_int(selected.get("max_context_length"))
        or services.model_context(selected),
        "loaded_context_len": services.positive_int(selected.get("loaded_context_length")),
        "state": selected.get("state"),
        "capabilities": selected.get("capabilities"),
        "type": selected.get("type"),
        "root": selected.get("arch"),
    }


def _runtime_from_v1(
    data: Any,
    models_url: str,
    current: str,
    services: LmStudioRuntimeServices,
) -> dict[str, Any] | None:
    items = data.get("models") if isinstance(data, dict) else None
    selected = _select_model(items, current, "key", services.model_id_matches)
    if selected is None:
        return None
    loaded_context = None
    instance_ids: list[str] = []
    instances = selected.get("loaded_instances")
    if isinstance(instances, list) and instances:
        for instance in instances:
            if isinstance(instance, dict) and instance.get("id"):
                instance_ids.append(str(instance["id"]))
        config = instances[0].get("config") if isinstance(instances[0], dict) else None
        if isinstance(config, dict):
            loaded_context = services.positive_int(config.get("context_length"))
    return {
        "models_url": models_url,
        "requested_model": current,
        "runtime_model": str(selected.get("key") or ""),
        "max_model_len": services.positive_int(selected.get("max_context_length"))
        or services.model_context(selected),
        "loaded_context_len": loaded_context,
        "state": "loaded" if loaded_context else "not-loaded",
        "instance_ids": instance_ids,
        "capabilities": selected.get("capabilities"),
        "type": selected.get("type"),
        "root": selected.get("architecture"),
    }
