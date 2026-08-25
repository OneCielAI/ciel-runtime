"""Import native ZCode Coding Plan credentials into Ciel Runtime.

The public ``zcode-app-cli`` launcher owns OAuth authorization, callback
validation, token exchange, and Coding Plan key resolution. Ciel invokes that
launcher and imports only the resulting documented ZCode provider entry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ZAI_CODING_PLAN_ANTHROPIC_BASE_URL = "https://api.z.ai/api/anthropic"
ZAI_START_PLAN_ANTHROPIC_BASE_URL = (
    "https://zcode.z.ai/api/v1/zcode-plan/anthropic"
)


class ZaiOAuthError(RuntimeError):
    """A bounded, secret-free ZCode credential import diagnostic."""


@dataclass(frozen=True, slots=True)
class ZaiOAuthRuntimePorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    clear_model_cache: Callable[[], None]
    mask: Callable[[str], str]
    fingerprint: Callable[[str], str]
    output: Callable[..., Any]
    native_login: Callable[[bool], int] | None = None
    native_settings_path: Path | None = None
    native_v2_config_path: Path | None = None
    native_v2_settings_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ZaiOAuthRuntime:
    ports: ZaiOAuthRuntimePorts

    @staticmethod
    def _provider_id(profile: str) -> str:
        normalized = str(profile or "coding-plan").strip().lower()
        if normalized == "coding-plan":
            return "zai-coding-plan"
        if normalized == "start-plan":
            return "zai-start-plan"
        raise ZaiOAuthError(f"Unsupported Z.AI OAuth profile: {profile}")

    @staticmethod
    def _profile_label(profile: str) -> str:
        return "Start Plan" if str(profile).strip().lower() == "start-plan" else "Coding Plan"

    def _native_coding_plan_key(self) -> str:
        path = self.ports.native_settings_path
        if path is None:
            raise ZaiOAuthError("The native ZCode settings path is unavailable.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            provider = payload.get("provider", {}).get("zai", {})
            options = provider.get("options", {})
            kind = str(provider.get("kind") or "").strip().lower()
            base_url = str(options.get("baseURL") or "").rstrip("/")
            key = str(options.get("apiKey") or "").strip()
        except FileNotFoundError as exc:
            raise ZaiOAuthError(f"Native ZCode settings were not found at {path}.") from exc
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise ZaiOAuthError(f"Native ZCode settings are invalid at {path}.") from exc
        if kind != "anthropic" or base_url != ZAI_CODING_PLAN_ANTHROPIC_BASE_URL:
            raise ZaiOAuthError(
                "Native ZCode provider.zai is not the documented Z.AI "
                "Anthropic Coding Plan configuration."
            )
        if not key:
            raise ZaiOAuthError("Native ZCode settings contain no Coding Plan API key.")
        return key

    def _native_start_plan_key(self) -> str:
        config_path = self.ports.native_v2_config_path
        settings_path = self.ports.native_v2_settings_path
        if config_path is None or settings_path is None:
            raise ZaiOAuthError("The native ZCode Desktop settings paths are unavailable.")
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            provider = config.get("provider", {}).get("builtin:zai-start-plan", {})
            options = provider.get("options", {})
            mode = str(settings.get("modelProviderFamilyModes", {}).get("zai") or "").strip()
            selected = str(
                settings.get("modelProviderFamilySelectedKeys", {}).get("zai") or ""
            ).strip()
            kind = str(provider.get("kind") or "").strip().lower()
            base_url = str(options.get("baseURL") or "").rstrip("/")
            key = str(options.get("apiKey") or "").strip()
            enabled = provider.get("enabled") is not False
        except FileNotFoundError as exc:
            raise ZaiOAuthError(
                "Native ZCode Desktop Start Plan settings were not found."
            ) from exc
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise ZaiOAuthError(
                "Native ZCode Desktop Start Plan settings are invalid."
            ) from exc
        if mode != "oauth" or selected != "coding-plan:builtin:zai-start-plan":
            raise ZaiOAuthError("Native ZCode Desktop is not currently set to Start Plan.")
        if (
            kind != "anthropic"
            or base_url != ZAI_START_PLAN_ANTHROPIC_BASE_URL
            or not enabled
        ):
            raise ZaiOAuthError(
                "Native ZCode Desktop Start Plan provider contract does not match "
                "the installed ZCode endpoint."
            )
        if not key:
            raise ZaiOAuthError("Native ZCode Desktop contains no Start Plan credential.")
        return key

    def _native_key(self, profile: str) -> str:
        if str(profile or "coding-plan").strip().lower() == "start-plan":
            return self._native_start_plan_key()
        return self._native_coding_plan_key()

    def token(self, profile: str = "coding-plan") -> str:
        config = self.ports.load_config()
        provider = config.get("providers", {}).get(self._provider_id(profile), {})
        if provider.get("credential_source") != "zai-oauth":
            return ""
        return str(provider.get("api_key") or "").strip()

    def import_api_key(
        self,
        api_key: str,
        *,
        source: str = "zcode",
        profile: str = "coding-plan",
    ) -> list[str]:
        key = str(api_key or "").strip()
        label = self._profile_label(profile)
        if not key:
            return [f"Z.AI OAuth import skipped: no {label} credential was found."]
        config = self.ports.load_config()
        provider_id = self._provider_id(profile)
        provider = config.setdefault("providers", {}).setdefault(provider_id, {})
        if (
            str(provider.get("api_key") or "").strip() == key
            and provider.get("credential_source") == "zai-oauth"
        ):
            return [f"Z.AI OAuth {label} credential is already shared by all runtimes."]
        provider["api_key"] = key
        provider.pop("api_keys", None)
        provider["credential_source"] = "zai-oauth"
        provider["oauth_authenticated_at"] = datetime.now(timezone.utc).isoformat()
        provider["oauth_import_source"] = str(source or "zcode")
        config["current_provider"] = provider_id
        self.ports.save_config(config)
        self.ports.clear_model_cache()
        return [
            f"Z.AI OAuth {label} credential imported into Ciel Runtime for all clients.",
            f"Credential: {self.ports.mask(key)}; fp {self.ports.fingerprint(key)}",
        ]

    def action(
        self,
        action: str,
        *,
        no_browser: bool = False,
        profile: str = "coding-plan",
    ) -> list[str]:
        provider_id = self._provider_id(profile)
        label = self._profile_label(profile)
        if action == "status":
            token = self.token(profile)
            if not token:
                return [f"Z.AI OAuth {label}: not connected."]
            return [
                f"Z.AI OAuth {label}: connected.",
                f"Credential: {self.ports.mask(token)}; fp {self.ports.fingerprint(token)}",
            ]
        if action == "logout":
            config = self.ports.load_config()
            provider = config.get("providers", {}).get(provider_id, {})
            if provider.get("credential_source") != "zai-oauth":
                return [
                    f"Z.AI OAuth {label}: no OAuth-derived local credential to clear."
                ]
            for field in (
                "api_key",
                "api_keys",
                "credential_source",
                "oauth_authenticated_at",
                "oauth_user_id",
                "oauth_access_token",
                "oauth_import_source",
            ):
                provider.pop(field, None)
            self.ports.save_config(config)
            self.ports.clear_model_cache()
            return [
                "Z.AI OAuth-derived local credential cleared. "
                "Native ZCode authorization was not revoked."
            ]
        if action == "import":
            return self.import_api_key(
                self._native_key(profile),
                source=(
                    "native-zcode-desktop-config"
                    if str(profile).strip().lower() == "start-plan"
                    else "native-zcode-config"
                ),
                profile=profile,
            )
        if action != "login":
            return [f"Unsupported Z.AI OAuth action: {action}"]
        if str(profile).strip().lower() == "start-plan":
            try:
                return self.import_api_key(
                    self._native_start_plan_key(),
                    source="native-zcode-desktop-config",
                    profile=profile,
                )
            except ZaiOAuthError as exc:
                raise ZaiOAuthError(
                    "Start Plan login is owned by the official ZCode Desktop app; "
                    "sign in there, select Start Plan, then run import."
                ) from exc
        if self.ports.native_login is None:
            raise ZaiOAuthError("The native ZCode login command is unavailable.")
        exit_code = int(self.ports.native_login(no_browser))
        if exit_code != 0:
            raise ZaiOAuthError(
                f"Native ZCode OAuth login failed (exit {exit_code}); "
                "no credential was imported."
            )
        return self.import_api_key(
            self._native_coding_plan_key(), source="native-zcode-oauth"
        )


__all__ = [
    "ZAI_CODING_PLAN_ANTHROPIC_BASE_URL",
    "ZAI_START_PLAN_ANTHROPIC_BASE_URL",
    "ZaiOAuthError",
    "ZaiOAuthRuntime",
    "ZaiOAuthRuntimePorts",
]
