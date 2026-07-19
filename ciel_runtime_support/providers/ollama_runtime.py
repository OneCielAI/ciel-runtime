"""Ollama-specific runtime inspection and context output guard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class OllamaRuntimeServices:
    request_base: Callable[[str, dict[str, Any]], str]
    post_json: Callable[..., Any]
    http_json: Callable[..., Any]
    join_url: Callable[[str, str], str]
    model_headers: Callable[[str, dict[str, Any]], dict[str, str]]
    current_model: Callable[[str, dict[str, Any]], str]
    positive_int: Callable[[Any], int | None]
    model_context: Callable[[dict[str, Any]], int | None]
    format_context: Callable[[int | None], str]


class OllamaRuntimeService:
    def __init__(self, services: OllamaRuntimeServices) -> None:
        self.services = services

    def api_base(self, provider: str, config: dict[str, Any]) -> str:
        base = self.services.request_base(provider, config)
        return base[:-4].rstrip("/") if base.endswith("/api") else base.rstrip("/")

    @staticmethod
    def show_parameters(data: dict[str, Any]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        raw = data.get("parameters")
        if isinstance(raw, dict):
            output.update(raw)
        elif isinstance(raw, str):
            for line in raw.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2 and not line.strip().startswith("#"):
                    output[parts[0].strip()] = parts[1].strip().strip('"')
        modelfile = data.get("modelfile")
        if isinstance(modelfile, str):
            for line in modelfile.splitlines():
                parts = line.strip().split(None, 2)
                if (
                    len(parts) == 3
                    and not line.strip().startswith("#")
                    and parts[0].lower() == "parameter"
                ):
                    output.setdefault(parts[1].strip(), parts[2].strip().strip('"'))
        return output

    def fetch_model_specs(
        self,
        provider: str,
        config: dict[str, Any],
        model_id: str,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        if provider not in ("ollama", "ollama-cloud") or not model_id:
            return {}
        base = self.api_base(provider, config)
        if not base:
            return {}
        data = self.services.post_json(
            self.services.join_url(base, "/api/show"),
            {"model": model_id},
            headers=self.services.model_headers(provider, config),
            timeout=timeout,
            provider=provider,
            pcfg=config,
        )
        if not isinstance(data, dict):
            return {}
        model_info = data.get("model_info") if isinstance(data.get("model_info"), dict) else {}
        parameters = self.show_parameters(data)
        max_context = (
            self.services.model_context(data)
            or self.services.model_context(model_info)
            or self.services.positive_int(parameters.get("num_ctx"))
            or self.services.positive_int(parameters.get("context_length"))
        )
        num_predict = self.services.positive_int(parameters.get("num_predict"))
        output: dict[str, Any] = {}
        if max_context:
            output["max_model_len"] = max_context
        if num_predict:
            output["num_predict"] = num_predict
        return output

    @staticmethod
    def model_id_matches(left: str, right: str) -> bool:
        lhs = (left or "").strip().lower()
        rhs = (right or "").strip().lower()
        if lhs == rhs:
            return True
        return (lhs if ":" in lhs else f"{lhs}:latest") == (
            rhs if ":" in rhs else f"{rhs}:latest"
        )

    def runtime_info(
        self, config: dict[str, Any], timeout: float = 1.5
    ) -> dict[str, Any] | None:
        base = self.api_base("ollama", config)
        current = self.services.current_model("ollama", config)
        if not base or not current:
            return None
        data = self.services.http_json(
            self.services.join_url(base, "/api/ps"),
            headers=self.services.model_headers("ollama", config),
            timeout=timeout,
        )
        items = data.get("models") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        selected = next(
            (
                item
                for item in items
                if isinstance(item, dict)
                and any(
                    self.model_id_matches(str(item.get(key) or ""), current)
                    for key in ("name", "model", "id")
                )
            ),
            None,
        )
        if not isinstance(selected, dict):
            return None
        details = selected.get("details") if isinstance(selected.get("details"), dict) else {}
        return {
            "requested_model": current,
            "runtime_model": str(selected.get("name") or selected.get("model") or ""),
            "loaded_context_len": self.services.positive_int(selected.get("context_length"))
            or self.services.model_context(selected),
            "size_vram": self.services.positive_int(selected.get("size_vram")),
            "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level"),
            "family": details.get("family"),
            "families": details.get("families"),
        }

    def apply_output_guard(
        self,
        provider: str,
        config: dict[str, Any],
        runtime_info: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> list[str]:
        if provider != "ollama":
            return []
        try:
            info = runtime_info(config) if runtime_info else self.runtime_info(config)
        except Exception:
            return []
        loaded_context = self.services.positive_int((info or {}).get("loaded_context_len"))
        cap = self.output_cap(loaded_context)
        if not cap:
            return []
        options = config.setdefault("ollama_options", {})
        configured = self.services.positive_int(
            options.get("num_predict")
        ) or self.services.positive_int(config.get("max_output_tokens"))
        if not configured or configured <= cap:
            return []
        options["num_predict"] = cap
        config["max_output_tokens"] = cap
        model = str((info or {}).get("runtime_model") or config.get("current_model") or "")
        return [
            f"Ollama runtime context {self.services.format_context(loaded_context)} "
            f"for {model or 'current model'}; output capped to {cap:,} tokens."
        ]

    def output_cap(self, context_length: int | None) -> int | None:
        context = self.services.positive_int(context_length)
        return max(2048, min(8192, context // 16)) if context else None
