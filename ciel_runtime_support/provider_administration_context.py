"""Provider configuration and credential administration use cases."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Callable, Protocol


class CredentialRepository(Protocol):
    def store(self, key: str) -> None: ...
    def clear(self) -> None: ...


class OAuthRuntime(Protocol):
    def token(self) -> str: ...
    def action(self, action: str) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class ProviderAdministrationInfrastructure:
    nvidia_credentials: Callable[[], CredentialRepository]
    copilot_oauth: Callable[[], OAuthRuntime]
    zai_oauth: Callable[[], OAuthRuntime]
    output: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ProviderAdministrationSelection:
    provider_choice: Callable[[], Any]
    provider_endpoint: Callable[[], Any]
    model_selection: Callable[[], Any]
    advisor_model_selection: Callable[[], Any]


@dataclass(frozen=True, slots=True)
class ProviderAdministrationCredentials:
    management: Callable[[], Any]
    cli: Callable[[], Any]
    configured_keys: Callable[[str, dict[str, Any]], list[str]]
    mask: Callable[[str], str]
    fingerprint: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class ProviderAdministrationPresentation:
    status: Callable[[], Any]


@dataclass(frozen=True, slots=True)
class ProviderAdministrationContext:
    infrastructure: ProviderAdministrationInfrastructure
    selection: ProviderAdministrationSelection
    credentials: ProviderAdministrationCredentials
    presentation: ProviderAdministrationPresentation

    def store_nvidia_api_key(self, key: str) -> None:
        self.infrastructure.nvidia_credentials().store(key)

    def clear_nvidia_api_key(self) -> None:
        self.infrastructure.nvidia_credentials().clear()

    def github_copilot_oauth_token(self) -> str:
        return self.infrastructure.copilot_oauth().token()

    def run_copilot_oauth_action(self, action: str) -> list[str]:
        return self.infrastructure.copilot_oauth().action(action)

    def cmd_copilot_oauth(self, args: argparse.Namespace) -> None:
        for line in self.run_copilot_oauth_action(args.action):
            self.infrastructure.output(line, flush=True)

    def run_zai_oauth_action(
        self,
        action: str,
        *,
        no_browser: bool = False,
        profile: str = "coding-plan",
    ) -> list[str]:
        return self.infrastructure.zai_oauth().action(
            action, no_browser=no_browser, profile=profile
        )

    def cmd_zai_oauth(self, args: argparse.Namespace) -> None:
        try:
            lines = self.run_zai_oauth_action(
                args.action,
                no_browser=bool(getattr(args, "no_browser", False)),
                profile=str(getattr(args, "profile", "coding-plan")),
            )
        except RuntimeError as exc:
            raise SystemExit(f"Z.AI OAuth failed: {exc}") from exc
        for line in lines:
            self.infrastructure.output(line, flush=True)

    def set_provider_config(self, provider: str) -> list[str]:
        return self.selection.provider_choice().select_standard(provider)

    def set_provider_choice_config(self, choice: str) -> list[str]:
        return self.selection.provider_choice().select(choice)

    def set_base_url_config(self, provider: str, url: str) -> list[str]:
        return self.selection.provider_endpoint().set_base_url(provider, url)

    def set_model_config(self, value: str) -> list[str]:
        return self.selection.model_selection().select(value)

    def set_advisor_model_config(self, value: str) -> list[str]:
        return self.selection.advisor_model_selection().select(value)

    def store_api_key_config(self, provider: str, key: str) -> list[str]:
        return self.credentials.management().store_one(provider, key)

    def clear_api_key_config(self, provider: str) -> list[str]:
        return self.credentials.management().clear(provider)

    def store_api_keys_config(
        self, provider: str, keys: list[str]
    ) -> list[str]:
        return self.credentials.management().store_many(provider, keys)

    def stored_api_key_mask(self, provider: str, pcfg: dict[str, Any]) -> str:
        keys = self.credentials.configured_keys(provider, pcfg)
        if not keys:
            return "not set"
        primary = (
            f"{self.credentials.mask(keys[0])}; "
            f"fp {self.credentials.fingerprint(keys[0])}"
        )
        if len(keys) == 1:
            return primary
        return f"{len(keys)} keys (round-robin; primary {primary})"

    def store_api_key_input_config(
        self, provider: str, raw_value: str
    ) -> list[str]:
        return self.credentials.management().store_input(provider, raw_value)

    def cmd_set_api_key(self, args: argparse.Namespace) -> None:
        self.credentials.cli().set_one(args)

    def cmd_set_api_keys(self, args: argparse.Namespace) -> None:
        self.credentials.cli().set_many(args)

    def cmd_api_key(self, args: argparse.Namespace) -> None:
        self.credentials.cli().manage(args)

    def status_lines(self) -> list[str]:
        return self.presentation.status().lines()

    def cmd_status(self, _: argparse.Namespace) -> None:
        self.infrastructure.output("\n".join(self.status_lines()))


@dataclass(frozen=True, slots=True)
class ProviderAdministrationCompatibilityApi:
    context: Callable[[], ProviderAdministrationContext]

    def store_nvidia_api_key(self, key: str) -> None:
        self.context().store_nvidia_api_key(key)

    def clear_nvidia_api_key(self) -> None:
        self.context().clear_nvidia_api_key()

    def github_copilot_oauth_token(self) -> str:
        return self.context().github_copilot_oauth_token()

    def run_copilot_oauth_action(self, action: str) -> list[str]:
        return self.context().run_copilot_oauth_action(action)

    def cmd_copilot_oauth(self, args: argparse.Namespace) -> None:
        self.context().cmd_copilot_oauth(args)

    def run_zai_oauth_action(
        self,
        action: str,
        *,
        no_browser: bool = False,
        profile: str = "coding-plan",
    ) -> list[str]:
        return self.context().run_zai_oauth_action(
            action, no_browser=no_browser, profile=profile
        )

    def cmd_zai_oauth(self, args: argparse.Namespace) -> None:
        self.context().cmd_zai_oauth(args)

    def set_provider_config(self, provider: str) -> list[str]:
        return self.context().set_provider_config(provider)

    def set_provider_choice_config(self, choice: str) -> list[str]:
        return self.context().set_provider_choice_config(choice)

    def set_base_url_config(self, provider: str, url: str) -> list[str]:
        return self.context().set_base_url_config(provider, url)

    def set_model_config(self, value: str) -> list[str]:
        return self.context().set_model_config(value)

    def set_advisor_model_config(self, value: str) -> list[str]:
        return self.context().set_advisor_model_config(value)

    def store_api_key_config(self, provider: str, key: str) -> list[str]:
        return self.context().store_api_key_config(provider, key)

    def clear_api_key_config(self, provider: str) -> list[str]:
        return self.context().clear_api_key_config(provider)

    def store_api_keys_config(
        self, provider: str, keys: list[str]
    ) -> list[str]:
        return self.context().store_api_keys_config(provider, keys)

    def stored_api_key_mask(self, provider: str, pcfg: dict[str, Any]) -> str:
        return self.context().stored_api_key_mask(provider, pcfg)

    def store_api_key_input_config(
        self, provider: str, raw_value: str
    ) -> list[str]:
        return self.context().store_api_key_input_config(provider, raw_value)

    def cmd_set_api_key(self, args: argparse.Namespace) -> None:
        self.context().cmd_set_api_key(args)

    def cmd_set_api_keys(self, args: argparse.Namespace) -> None:
        self.context().cmd_set_api_keys(args)

    def cmd_api_key(self, args: argparse.Namespace) -> None:
        self.context().cmd_api_key(args)

    def status_lines(self) -> list[str]:
        return self.context().status_lines()

    def cmd_status(self, args: argparse.Namespace) -> None:
        self.context().cmd_status(args)


__all__ = [
    "ProviderAdministrationCompatibilityApi",
    "ProviderAdministrationContext",
    "ProviderAdministrationCredentials",
    "ProviderAdministrationInfrastructure",
    "ProviderAdministrationPresentation",
    "ProviderAdministrationSelection",
]
