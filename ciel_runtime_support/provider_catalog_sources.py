"""Provider model-catalog response projection and remote source adapters."""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ciel_runtime_support.model_catalog_projection import (
    ModelCatalogProjectionServices,
    project_model_info,
)


@dataclass(frozen=True, slots=True)
class ModelCatalogProjectionPorts:
    normalize_model_id: Callable[[str, Any], str]
    model_context: Callable[[dict[str, Any]], Any]
    positive_int: Callable[[Any], int | None]
    provider_metadata: Callable[[str], Callable[..., dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ProviderCatalogHttpPorts:
    http_json: Callable[..., Any]
    join_url: Callable[[str, str], str]
    upstream_base: Callable[[str, dict[str, Any]], str]
    request_headers: Callable[[], dict[str, str]]
    urlopen: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ProviderCatalogPolicyPorts:
    unique_model_ids: Callable[[str, list[str]], list[str]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class AnthropicCatalogPolicy:
    docs_urls: tuple[str, ...]
    default_ids: tuple[str, ...]
    limited_ids: tuple[str, ...]
    fallback_ids: tuple[str, ...]
    public_id_pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class FireworksCatalogPolicy:
    default_account_id: str
    api_base_url: str
    inference_base_url: str
    max_pages: int = 20


@dataclass(frozen=True, slots=True)
class ProviderCatalogSourceService:
    projection: ModelCatalogProjectionPorts
    http: ProviderCatalogHttpPorts
    policy: ProviderCatalogPolicyPorts
    anthropic: AnthropicCatalogPolicy
    fireworks: FireworksCatalogPolicy

    @staticmethod
    def model_ids_from_response(data: Any) -> list[str]:
        ids: list[str] = []
        candidates: Any
        if isinstance(data, dict):
            candidates = data.get("data")
            if candidates is None:
                candidates = data.get("models")
            if candidates is None:
                candidates = data.get("model")
        else:
            candidates = data
        if isinstance(candidates, str):
            candidates = [candidates]
        if not isinstance(candidates, list):
            return ids
        for item in candidates:
            if isinstance(item, str):
                model_id = item
            elif isinstance(item, dict):
                model_id = (
                    item.get("id")
                    or item.get("key")
                    or item.get("name")
                    or item.get("model")
                )
            else:
                model_id = None
            if model_id and str(model_id).strip():
                ids.append(str(model_id).strip())
        return ids

    def model_info_from_response(
        self, provider: str, data: Any
    ) -> dict[str, dict[str, Any]]:
        return project_model_info(
            provider,
            data,
            ModelCatalogProjectionServices(
                normalize_model_id=self.projection.normalize_model_id,
                model_context=self.projection.model_context,
                positive_int=self.projection.positive_int,
                project_metadata=self.projection.provider_metadata(provider),
            ),
        )

    def fireworks_account_id(self, provider_config: dict[str, Any]) -> str:
        configured = str(provider_config.get("account_id") or "").strip()
        if configured:
            return configured
        values = (
            provider_config.get("current_model"),
            *(provider_config.get("custom_models", []) or []),
        )
        for value in values:
            match = re.match(
                r"^accounts/([^/]+)/models/[^/]+$", str(value or "")
            )
            if match:
                return match.group(1)
        return self.fireworks.default_account_id

    def fireworks_management_base_url(
        self, provider_config: dict[str, Any]
    ) -> str:
        configured = str(
            provider_config.get("model_api_base_url") or ""
        ).strip().rstrip("/")
        base = str(
            provider_config.get("base_url") or self.fireworks.inference_base_url
        ).strip().rstrip("/")
        parsed = urllib.parse.urlparse(base)
        if configured and (
            configured != self.fireworks.api_base_url
            or not (parsed.scheme and parsed.netloc)
            or parsed.netloc.endswith("fireworks.ai")
        ):
            return configured
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return configured or self.fireworks.api_base_url

    def fetch_fireworks_model_ids(
        self,
        provider_config: dict[str, Any],
        headers: dict[str, str],
        timeout: float = 8.0,
    ) -> tuple[list[str], dict[str, dict[str, Any]], str]:
        account_id = self.fireworks_account_id(provider_config)
        base = self.fireworks_management_base_url(provider_config)
        models: list[str] = []
        model_info: dict[str, dict[str, Any]] = {}
        page_token = ""
        source = f"fireworks:{account_id}"
        for _ in range(self.fireworks.max_pages):
            query = {"pageSize": "200"}
            if page_token:
                query["pageToken"] = page_token
            account = urllib.parse.quote(account_id, safe="")
            encoded_query = urllib.parse.urlencode(query)
            path = f"/v1/accounts/{account}/models?{encoded_query}"
            data = self.http.http_json(
                self.http.join_url(base, path),
                headers=headers,
                timeout=timeout,
                provider="fireworks",
                pcfg=provider_config,
            )
            ids = [
                self.projection.normalize_model_id("fireworks", model_id)
                for model_id in self.model_ids_from_response(data)
            ]
            for model_id in ids:
                if model_id and model_id not in models:
                    models.append(model_id)
            model_info.update(self.model_info_from_response("fireworks", data))
            if not isinstance(data, dict):
                break
            page_token = str(data.get("nextPageToken") or "").strip()
            if not page_token:
                break
        return models, model_info, source

    def fetch_text_url(self, url: str, timeout: float = 8.0) -> str:
        request = urllib.request.Request(url, headers=self.http.request_headers())
        with self.http.urlopen(request, timeout=timeout) as response:
            return response.read(5_000_000).decode("utf-8", errors="replace")

    def anthropic_model_ids_from_docs_text(self, text: str) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for match in self.anthropic.public_id_pattern.finditer(html.unescape(text or "")):
            model_id = match.group(0)
            key = model_id.casefold()
            if key in seen:
                continue
            seen.add(key)
            ids.append(model_id)
        return ids

    def filter_anthropic_default_model_ids(self, ids: list[str]) -> list[str]:
        allowed = set(self.anthropic.default_ids)
        limited = set(self.anthropic.limited_ids)
        out: list[str] = []
        seen: set[str] = set()
        for raw in ids:
            model_id = self.projection.normalize_model_id("anthropic", raw)
            key = model_id.casefold()
            if not model_id or key in seen or model_id in limited or model_id not in allowed:
                continue
            out.append(model_id)
            seen.add(key)
        return out

    def fetch_anthropic_public_model_ids(self, timeout: float = 8.0) -> list[str]:
        ids: list[str] = []
        errors: list[str] = []
        for url in self.anthropic.docs_urls:
            try:
                text = self.fetch_text_url(url, timeout=timeout)
                ids.extend(self.anthropic_model_ids_from_docs_text(text))
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
        unique = self.policy.unique_model_ids("anthropic", ids)
        out = self.filter_anthropic_default_model_ids(unique)
        if out:
            return out
        if errors:
            self.policy.log(
                "WARN", "anthropic model docs fetch failed: " + " ; ".join(errors)
            )
        return list(self.anthropic.fallback_ids)

    def fetch_anthropic_api_model_ids(
        self,
        provider_config: dict[str, Any],
        headers: dict[str, str],
        timeout: float = 6.0,
    ) -> tuple[list[str], str]:
        base = self.http.upstream_base("anthropic", provider_config)
        errors: list[str] = []
        for path in ("/v1/models", "/models"):
            try:
                data = self.http.http_json(
                    self.http.join_url(base, path),
                    headers=headers,
                    timeout=timeout,
                )
                ids = self.policy.unique_model_ids(
                    "anthropic", self.model_ids_from_response(data)
                )
                if ids:
                    return ids, f"api:{path}"
            except Exception as exc:
                errors.append(f"{path}: {type(exc).__name__}: {exc}")
        if errors:
            self.policy.log(
                "DEBUG", "anthropic model API fetch failed: " + " ; ".join(errors)
            )
        return [], ""


ANTHROPIC_PUBLIC_MODEL_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_.@:-])"
    r"(?:"
    r"claude-(?:fable|mythos)-\d+(?:-\d+)?(?:-\d{8})?|"
    r"claude-mythos-preview|"
    r"claude-(?:opus|sonnet|haiku)-\d+-\d+-\d{8}|"
    r"claude-(?:opus|sonnet|haiku)-\d+-\d{8}|"
    r"claude-(?:opus|sonnet|haiku)-\d+-\d+|"
    r"claude-(?:opus|sonnet|haiku)-\d+(?:-\d+)?-latest|"
    r"claude-\d+(?:-\d+){0,2}-(?:opus|sonnet|haiku)-(?:\d{8}|latest)"
    r")"
    r"(?![A-Za-z0-9_.@:-])"
)
