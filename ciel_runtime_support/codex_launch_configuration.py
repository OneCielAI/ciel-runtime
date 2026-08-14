"""Codex launch configuration and routed model catalog orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ciel_runtime_support.architecture import ProviderRuntimeCompactionPolicy
from ciel_runtime_support.codex_config import (
    codex_alternate_screen_value_from_config_text,
    codex_config_override_keys,
    codex_config_paths_for_launch,
    toml_string,
)
from ciel_runtime_support.codex_model_catalog import CodexModelCatalogSpec
from ciel_runtime_support.runtime_constants import (
    CODEX_NATIVE_PROVIDER_ID_ENV,
    CODEX_ROUTED_PROVIDER_ID,
    CODEX_RUNTIME_API_KEY_ENV,
    CODEX_RUNTIME_PROVIDER_ID,
    CODEX_TUI_ALTERNATE_SCREEN_KEY,
)


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


def build_default_codex_launch_constants() -> CodexLaunchConfigurationConstants:
    """Build immutable routed Codex provider configuration constants."""
    return CodexLaunchConfigurationConstants(
        runtime_provider_id=CODEX_RUNTIME_PROVIDER_ID,
        runtime_api_key_env=CODEX_RUNTIME_API_KEY_ENV,
        native_provider_id_env=CODEX_NATIVE_PROVIDER_ID_ENV,
        routed_provider_id=CODEX_ROUTED_PROVIDER_ID,
        alternate_screen_key=CODEX_TUI_ALTERNATE_SCREEN_KEY,
    )


def build_default_codex_launch_policy(
    has_option: Callable[..., bool],
) -> CodexLaunchPolicyPorts:
    """Build the standard Codex config-file and CLI projection policy."""
    return CodexLaunchPolicyPorts(
        has_option=has_option,
        config_override_keys=codex_config_override_keys,
        config_paths=codex_config_paths_for_launch,
        alternate_screen_value=codex_alternate_screen_value_from_config_text,
        toml_string=toml_string,
    )


@dataclass(frozen=True, slots=True)
class CodexLaunchModelPorts:
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    native_enabled: Callable[[str], bool]
    current_alias: Callable[[dict[str, Any]], str]
    context_window: Callable[[str, dict[str, Any]], int | None]
    compaction_policy: Callable[
        [str, dict[str, Any]], ProviderRuntimeCompactionPolicy
    ]


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
class CodexLaunchModelSnapshot:
    """Provider/model settings resolved exactly once for one Codex launch."""

    provider: str
    native: bool
    spec: CodexModelCatalogSpec | None
    auto_compact_token_limit: int | None


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

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _launch_model_snapshot(
        self, cfg: dict[str, Any]
    ) -> CodexLaunchModelSnapshot:
        provider, provider_config = self.model.current_provider(cfg)
        native = self.model.native_enabled(provider)
        alias = self.model.current_alias(cfg)
        known_context_window = self.model.context_window(provider, provider_config)
        # Ciel does not own the model catalog of a direct native Codex launch.
        # An unknown native context must stay unknown: inventing 272K here
        # would incorrectly clamp an operator's explicit compact threshold.
        context_window = known_context_window or (None if native else 272000)
        auto_compact_token_limit = self._positive_int(
            provider_config.get("codex_auto_compact_window")
        )
        if auto_compact_token_limit is None:
            policy = self.model.compaction_policy(provider, provider_config)
            percent = self._positive_int(policy.trigger_percent)
            if percent is not None and percent <= 100 and context_window:
                auto_compact_token_limit = max(
                    1, (context_window * percent) // 100
                )
        if auto_compact_token_limit is None and not native and context_window:
            auto_compact_token_limit = max(1, (context_window * 9) // 10)
        if auto_compact_token_limit is not None and context_window:
            auto_compact_token_limit = min(
                context_window, auto_compact_token_limit
            )
        metadata = provider_config.get("codex_model_catalog")
        if not isinstance(metadata, Mapping):
            metadata = None
        spec = None
        if alias and context_window:
            spec = CodexModelCatalogSpec(
                alias=alias,
                provider_label=self.catalog.provider_label(provider),
                context_window=context_window,
                effort=str(provider_config.get("effort_level") or "")
                .strip()
                .lower(),
                auto_compact_token_limit=auto_compact_token_limit,
                metadata=dict(metadata) if metadata is not None else None,
            )
        return CodexLaunchModelSnapshot(
            provider=provider,
            native=native,
            spec=spec,
            auto_compact_token_limit=auto_compact_token_limit,
        )

    def _write_runtime_model_catalog(
        self, codex: str, snapshot: CodexLaunchModelSnapshot
    ) -> Path | None:
        if snapshot.native or snapshot.spec is None:
            return None
        catalog_env = dict(self.effects.environ())
        catalog_env["PATH"] = self.catalog.path_value(catalog_env)
        return self.catalog.write(
            codex,
            snapshot.spec,
            catalog_env,
        )

    def write_runtime_model_catalog(
        self, codex: str, cfg: dict[str, Any]
    ) -> Path | None:
        return self._write_runtime_model_catalog(
            codex, self._launch_model_snapshot(cfg)
        )

    @staticmethod
    def _auto_compact_config_args(
        snapshot: CodexLaunchModelSnapshot,
    ) -> list[str]:
        """Move Codex's own compaction trigger to the operator's threshold.

        A session that crosses providers carries history built under whatever
        window was in force at the time. Codex compacts on its own, but only
        once its configured limit is reached — and by then a history grown
        under a larger window no longer fits the smaller one, so the compaction
        request itself is refused and the turn cannot recover.

        Passing the threshold keeps that trigger where the operator wants it
        rather than claiming a window size of our own: it only decides when
        Codex compacts, never how much context it believes it has.
        """

        limit = snapshot.auto_compact_token_limit
        if limit is None:
            return []
        return ["-c", f"model_auto_compact_token_limit={limit}"]

    def auto_compact_config_args(self, cfg: dict[str, Any]) -> list[str]:
        return self._auto_compact_config_args(self._launch_model_snapshot(cfg))

    def runtime_model_catalog_args(
        self,
        codex: str,
        cfg: dict[str, Any],
        passthrough: list[str] | None = None,
    ) -> list[str]:
        if self.passthrough_has_model_override(passthrough or []):
            # The explicit CLI model is the effective launch model.  Its
            # provider profile may not be known to Ciel, so applying the
            # persisted menu model's catalog/threshold would be worse than
            # deferring to Codex's own model catalog and compact defaults.
            self.effects.log(
                "INFO",
                "codex_compaction_launch_snapshot skipped=explicit_model_override",
            )
            return []
        snapshot = self._launch_model_snapshot(cfg)
        spec = snapshot.spec
        self.effects.log(
            "INFO",
            "codex_compaction_launch_snapshot "
            f"provider={snapshot.provider} "
            f"model={(spec.alias if spec is not None else '-')} "
            f"context={(spec.context_window if spec is not None else '-')} "
            f"limit={snapshot.auto_compact_token_limit or '-'}",
        )
        path = self._write_runtime_model_catalog(codex, snapshot)
        if path is None:
            # A native provider keeps its own bundled catalog; only the
            # compaction threshold is ours to set.
            return self._auto_compact_config_args(snapshot)
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
