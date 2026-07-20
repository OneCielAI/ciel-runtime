"""Codex launch configuration and routed model catalog orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ciel_runtime_support.codex_model_catalog import CodexModelCatalogSpec


@dataclass(frozen=True, slots=True)
class CodexLaunchConfigurationConstants:
    runtime_provider_id: str
    runtime_api_key_env: str
    native_provider_id_env: str
    routed_provider_id: str
    alternate_screen_key: str


@dataclass(frozen=True, slots=True)
class CodexLaunchPolicyPorts:
    has_option: Callable[..., bool]
    config_override_keys: Callable[[list[str]], set[str]]
    config_paths: Callable[..., list[Path]]
    alternate_screen_value: Callable[[str], str | None]
    toml_string: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class CodexLaunchModelPorts:
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    native_enabled: Callable[[str], bool]
    current_alias: Callable[[dict[str, Any]], str]
    context_limit: Callable[[str, dict[str, Any]], int | None]
    context_capacity: Callable[[str, dict[str, Any]], int | None]


@dataclass(frozen=True, slots=True)
class CodexLaunchCatalogPorts:
    write: Callable[[str, CodexModelCatalogSpec, dict[str, str]], Path | None]
    provider_label: Callable[[str], str]
    path_value: Callable[[dict[str, str]], str]
    current_model_args: Callable[..., list[str]]
    native_routed_args: Callable[..., list[str]]


@dataclass(frozen=True, slots=True)
class CodexLaunchConfigurationEffects:
    environ: Callable[[], Mapping[str, str]]
    router_base: Callable[[], str]
    read_text: Callable[[Path], str]
    log: Callable[[str, str], None]
    output: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class CodexLaunchConfigurationService:
    constants: CodexLaunchConfigurationConstants
    policy: CodexLaunchPolicyPorts
    model: CodexLaunchModelPorts
    catalog: CodexLaunchCatalogPorts
    effects: CodexLaunchConfigurationEffects

    def alternate_screen_compat_args(
        self,
        passthrough: list[str],
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> list[str]:
        key = self.constants.alternate_screen_key
        if self.policy.has_option(passthrough, "--no-alt-screen"):
            return []
        if key in self.policy.config_override_keys(passthrough):
            return []
        for path in self.policy.config_paths(passthrough, env=env, cwd=cwd):
            try:
                text = self.effects.read_text(path)
            except Exception:
                continue
            value = self.policy.alternate_screen_value(text)
            if value:
                self.effects.log(
                    "WARN",
                    f"codex_compat_alternate_screen_override path={path} value={value}",
                )
                self.effects.output(
                    "Ciel Runtime warning: applying Codex config compatibility "
                    f'override {key}="{value}".'
                )
                return ["-c", f"{key}={self.policy.toml_string(value)}"]
        return []

    def runtime_config_args(self, router_base: str | None = None) -> list[str]:
        provider = self.constants.runtime_provider_id
        configured_base = (
            self.effects.router_base() if router_base is None else router_base
        )
        base = configured_base.rstrip("/") + "/v1"
        toml = self.policy.toml_string
        return [
            "-c",
            f"model_provider={toml(provider)}",
            "-c",
            f"model_providers.{provider}.name={toml('Ciel Runtime')}",
            "-c",
            f"model_providers.{provider}.base_url={toml(base)}",
            "-c",
            f"model_providers.{provider}.wire_api={toml('responses')}",
            "-c",
            f"model_providers.{provider}.env_key={toml(self.constants.runtime_api_key_env)}",
            "-c",
            f"model_providers.{provider}.request_max_retries=0",
            "-c",
            f"model_providers.{provider}.stream_max_retries=0",
        ]

    def write_runtime_model_catalog(
        self, codex: str, cfg: dict[str, Any]
    ) -> Path | None:
        provider, provider_config = self.model.current_provider(cfg)
        if self.model.native_enabled(provider):
            return None
        alias = self.model.current_alias(cfg)
        if not alias:
            return None
        context_window = (
            self.model.context_limit(provider, provider_config)
            or self.model.context_capacity(provider, provider_config)
            or 272000
        )
        catalog_env = dict(self.effects.environ())
        catalog_env["PATH"] = self.catalog.path_value(catalog_env)
        return self.catalog.write(
            codex,
            CodexModelCatalogSpec(
                alias=alias,
                provider_label=self.catalog.provider_label(provider),
                context_window=context_window,
                effort=str(provider_config.get("effort_level") or "").strip().lower(),
            ),
            catalog_env,
        )

    def runtime_model_catalog_args(
        self, codex: str, cfg: dict[str, Any]
    ) -> list[str]:
        path = self.write_runtime_model_catalog(codex, cfg)
        if path is None:
            return []
        value = self.policy.toml_string(str(path.resolve()))
        return ["-c", f"model_catalog_json={value}"]

    def native_routed_config_args(
        self, router_base: str | None = None
    ) -> list[str]:
        env = self.effects.environ()
        provider = str(env.get(self.constants.native_provider_id_env) or "").strip()
        provider = provider or self.constants.routed_provider_id
        configured_base = (
            self.effects.router_base() if router_base is None else router_base
        )
        return self.catalog.native_routed_args(
            configured_base,
            provider,
            toml_string=self.policy.toml_string,
        )

    def passthrough_has_model_override(self, passthrough: list[str]) -> bool:
        return self.policy.has_option(
            passthrough, "-m", "--model"
        ) or "model" in self.policy.config_override_keys(passthrough)

    def current_model_cli_args(
        self, provider_config: dict[str, Any], passthrough: list[str]
    ) -> list[str]:
        return self.catalog.current_model_args(
            provider_config,
            passthrough,
            overridden=self.passthrough_has_model_override,
        )

    def current_model_config_args(
        self, provider_config: dict[str, Any], passthrough: list[str]
    ) -> list[str]:
        return self.catalog.current_model_args(
            provider_config,
            passthrough,
            overridden=self.passthrough_has_model_override,
            config_style=True,
            toml_string=self.policy.toml_string,
        )
