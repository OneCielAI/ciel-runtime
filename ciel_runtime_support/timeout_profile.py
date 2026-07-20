"""User-selectable timeout profile projection and mutation service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


TimeoutPreset = tuple[int, str, str]
LocalizedText = tuple[str, str]


@dataclass(frozen=True, slots=True)
class TimeoutProfileSettings:
    default_timeout_ms: int
    profiles: Mapping[str, TimeoutPreset]
    localized_profiles: Mapping[str, Mapping[str, LocalizedText]]
    llm_preset_timeouts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class TimeoutProfilePorts:
    positive_int: Callable[[Any], int | None]
    pad_cells: Callable[[str, int], str]
    ui_text: Callable[[str, str], str]
    format_minutes: Callable[[int, str], str]


class TimeoutProfileService:
    def __init__(
        self,
        settings: TimeoutProfileSettings,
        ports: TimeoutProfilePorts,
    ) -> None:
        self.settings = settings
        self.ports = ports

    def llm_preset_timeout(self, preset_id: str) -> int:
        return self.settings.llm_preset_timeouts.get(
            preset_id,
            self.settings.default_timeout_ms,
        )

    def active_llm_preset_timeout(self, config: dict[str, Any]) -> int | None:
        preset_id = str(config.get("llm_preset") or "").strip()
        if not preset_id:
            return None
        return self.ports.positive_int(
            self.settings.llm_preset_timeouts.get(preset_id)
        )

    def profile_id(self, milliseconds: int | None) -> str | None:
        if not milliseconds:
            return None
        for profile_id, (profile_ms, _label, _description) in self.settings.profiles.items():
            if milliseconds == profile_ms:
                return profile_id
        return None

    def text(self, profile_id: str, language: str) -> LocalizedText:
        if profile_id == "__custom__":
            return {
                "ko": ("사용자 지정", "직접 입력한 timeout 값"),
                "ja": ("カスタム", "直接入力した timeout 値"),
                "zh": ("自定义", "手动输入的 timeout 值"),
            }.get(language, ("Custom", "manually entered timeout value"))
        fallback = self.settings.profiles[profile_id]
        return self.settings.localized_profiles.get(language, {}).get(
            profile_id,
            (fallback[1], fallback[2]),
        )

    def status(self, config: dict[str, Any], language: str) -> str:
        milliseconds = (
            self.ports.positive_int(config.get("request_timeout_ms"))
            or self.settings.default_timeout_ms
        )
        profile_id = self.profile_id(milliseconds)
        label = self.text(profile_id or "__custom__", language)[0]
        idle = self.ports.positive_int(config.get("stream_idle_timeout_ms"))
        idle_text = f"; idle {idle}ms" if idle and idle != milliseconds else ""
        return f"{label}; {milliseconds}ms{idle_text}"

    @staticmethod
    def idle_timeout(milliseconds: int) -> int:
        return min(milliseconds, 300000)

    def panel_rows(
        self,
        config: dict[str, Any],
        language: str,
    ) -> tuple[list[str], list[str]]:
        current_ms = (
            self.ports.positive_int(config.get("request_timeout_ms"))
            or self.settings.default_timeout_ms
        )
        rows = [
            f"Current timeout: {current_ms} ms = "
            f"{self.ports.format_minutes(current_ms, language)}"
        ]
        values = ["__info__"]
        current_profile = self.profile_id(current_ms)
        for profile_id, (milliseconds, _label, _description) in self.settings.profiles.items():
            label, description = self.text(profile_id, language)
            mark = "*" if profile_id == current_profile else " "
            rows.append(
                f"{mark} {self.ports.pad_cells(label, 22)} "
                f"{milliseconds:>7} ms  {description}"
            )
            values.append(profile_id)
        rows.append(self.ports.ui_text("back", language))
        values.append("back")
        return rows, values

    def apply(
        self,
        config: dict[str, Any],
        profile_id: str,
        language: str,
    ) -> list[str]:
        if profile_id not in self.settings.profiles:
            raise SystemExit(f"Unknown timeout preset: {profile_id}")
        milliseconds, _label, _description = self.settings.profiles[profile_id]
        idle_ms = self.idle_timeout(milliseconds)
        config["request_timeout_ms"] = milliseconds
        config["stream_idle_timeout_ms"] = idle_ms
        label = self.text(profile_id, language)[0]
        return [
            f"Timeout preset: {label}",
            f"request_timeout_ms: {milliseconds}",
            f"stream_idle_timeout_ms: {idle_ms}",
        ]

    def with_llm_preset_timeout(
        self,
        tokens: list[str],
        preset_id: str,
    ) -> list[str]:
        filtered = [
            token
            for token in tokens
            if not token.startswith(
                (
                    "timeout=",
                    "timeout_ms=",
                    "request_timeout=",
                    "request_timeout_ms=",
                    "stream_idle_timeout=",
                    "stream_idle_timeout_ms=",
                )
            )
        ]
        timeout_ms = self.llm_preset_timeout(preset_id)
        idle_ms = self.idle_timeout(timeout_ms)
        filtered.append(f"timeout={timeout_ms}")
        filtered.append(f"stream_idle_timeout_ms={idle_ms}")
        return filtered


@dataclass(frozen=True, slots=True)
class TimeoutProfileApi:
    """Explicit compatibility API with an injected default-language source."""

    service_factory: Callable[[], TimeoutProfileService]
    default_language: Callable[[], str]

    def _language(self, language: str | None) -> str:
        return language or self.default_language()

    def llm_preset_timeout_ms(self, preset_id: str) -> int:
        return self.service_factory().llm_preset_timeout(preset_id)

    def active_llm_preset_timeout_ms(self, pcfg: dict[str, Any]) -> int | None:
        return self.service_factory().active_llm_preset_timeout(pcfg)

    def timeout_profile_id_for_ms(self, ms: int | None) -> str | None:
        return self.service_factory().profile_id(ms)

    def timeout_profile_text(self, profile_id: str, lang: str | None = None) -> LocalizedText:
        return self.service_factory().text(profile_id, self._language(lang))

    def timeout_profile_status(self, pcfg: dict[str, Any], lang: str | None = None) -> str:
        return self.service_factory().status(pcfg, self._language(lang))

    def timeout_profile_idle_ms(self, request_timeout_ms: int) -> int:
        return self.service_factory().idle_timeout(request_timeout_ms)

    def timeout_profile_panel_rows(self, pcfg: dict[str, Any], lang: str | None = None) -> tuple[list[str], list[str]]:
        return self.service_factory().panel_rows(pcfg, self._language(lang))

    def apply_timeout_profile_to_provider(self, pcfg: dict[str, Any], profile_id: str, lang: str | None = None) -> list[str]:
        return self.service_factory().apply(pcfg, profile_id, self._language(lang))

    def with_preset_timeout_tokens(self, tokens: list[str], preset_id: str) -> list[str]:
        return self.service_factory().with_llm_preset_timeout(tokens, preset_id)
