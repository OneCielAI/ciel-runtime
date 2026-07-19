"""Provider-neutral context setup projection and mutation service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .architecture import ProviderContextPolicy


ContextMode = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class ContextSetupPorts:
    context_capacity: Callable[[str, dict[str, Any]], int | None]
    context_policy: Callable[[str, dict[str, Any]], ProviderContextPolicy]
    positive_int: Callable[[Any], int | None]
    format_context: Callable[[int | None], str]
    ui_text: Callable[[str, str], str]
    pad_cells: Callable[[str, int], str]
    cap_context: Callable[[str, dict[str, Any]], list[str]]
    cap_output: Callable[[str, dict[str, Any]], list[str]]
    apply_timeout: Callable[[str, dict[str, Any]], list[str]]
    context_status: Callable[[str, dict[str, Any]], str]


_ORDERED_MODES = (
    "context-compact",
    "context-balanced",
    "context-project",
    "context-full",
)
_TEXT = {
    "en": {
        "context-compact": ("Compact / fast", "small context, faster and cheaper"),
        "context-balanced": (
            "Balanced",
            "good default for normal coding sessions",
        ),
        "context-project": (
            "Large project",
            "more files/history, slower but safer for big work",
        ),
        "context-full": (
            "Full model window",
            "use the selected model's maximum context",
        ),
    },
    "ko": {
        "context-compact": ("컴팩트/빠름", "작은 컨텍스트, 빠르고 가벼움"),
        "context-balanced": ("균형형", "일반 코딩 세션의 권장 기본값"),
        "context-project": (
            "대형 프로젝트",
            "파일/히스토리를 더 많이 사용, 큰 작업에 안정적",
        ),
        "context-full": (
            "모델 최대 컨텍스트",
            "선택한 모델의 최대 컨텍스트 사용",
        ),
    },
    "ja": {
        "context-compact": (
            "コンパクト/高速",
            "小さなコンテキストで高速かつ軽量",
        ),
        "context-balanced": (
            "バランス",
            "通常のコーディングセッション向けの既定値",
        ),
        "context-project": (
            "大規模プロジェクト",
            "より多くのファイル/履歴を使う大型作業向け",
        ),
        "context-full": (
            "モデル最大コンテキスト",
            "選択モデルの最大コンテキストを使用",
        ),
    },
    "zh": {
        "context-compact": ("紧凑/快速", "较小上下文，更快更轻"),
        "context-balanced": ("均衡", "普通编码会话的推荐默认值"),
        "context-project": ("大型项目", "使用更多文件/历史，适合大任务"),
        "context-full": ("模型最大上下文", "使用所选模型的最大上下文"),
    },
}


class ContextSetupService:
    def __init__(self, ports: ContextSetupPorts) -> None:
        self.ports = ports

    @staticmethod
    def mode_values(capacity: int | None) -> dict[str, ContextMode]:
        cap = capacity or 131072

        def clamp(value: int) -> int:
            return max(8192, min(cap, value))

        compact = clamp(32768)
        balanced = clamp(65536 if cap <= 131072 else 131072)
        project = clamp(262144 if cap >= 262144 else cap)
        full = clamp(cap)
        return {
            "context-compact": (
                compact,
                min(2048, max(1024, compact // 16)),
                4096,
            ),
            "context-balanced": (
                balanced,
                min(4096, max(2048, balanced // 16)),
                4096,
            ),
            "context-project": (
                project,
                min(8192, max(4096, project // 16)),
                8192,
            ),
            "context-full": (
                full,
                min(16384, max(4096, full // 16)),
                8192,
            ),
        }

    @staticmethod
    def text(key: str, language: str) -> tuple[str, str]:
        entries = _TEXT.get(language, _TEXT["en"])
        return entries.get(key, _TEXT["en"][key])

    def panel_rows(
        self,
        provider: str,
        config: dict[str, Any],
        language: str,
    ) -> tuple[list[str], list[str]]:
        capacity = self.ports.context_capacity(provider, config)
        rows = [f"Model context capacity: {self.ports.format_context(capacity)}"]
        values = ["__info__"]
        strategy = self.ports.context_policy(provider, config).settings_strategy
        if strategy == "managed":
            rows.append("Claude Code manages Anthropic context automatically.")
            values.append("__info__")
            rows.append(self.ports.ui_text("back", language))
            values.append("back")
            return rows, values
        current_window = self.ports.positive_int(
            config.get("num_ctx_max" if strategy == "ollama" else "context_window")
        )
        choices = self.mode_values(capacity)
        visible_modes: list[str] = []
        seen_windows: set[int] = set()
        for key in reversed(_ORDERED_MODES):
            window = choices[key][0]
            if window not in seen_windows:
                seen_windows.add(window)
                visible_modes.append(key)
        for key in reversed(visible_modes):
            window, _reserve, output = choices[key]
            label, description = self.text(key, language)
            mark = "*" if current_window == window else " "
            rows.append(
                f"{mark} {self.ports.pad_cells(label, 22)} "
                f"{self.ports.format_context(window):>6}  "
                f"out {self.ports.format_context(output):>5}  {description}"
            )
            values.append(key)
        rows.append(self.ports.ui_text("back", language))
        values.append("back")
        return rows, values

    def apply(
        self,
        provider: str,
        config: dict[str, Any],
        mode: str,
        language: str,
    ) -> list[str]:
        choices = self.mode_values(self.ports.context_capacity(provider, config))
        if mode not in choices:
            raise SystemExit(f"Unknown context mode: {mode}")
        window, reserve, output = choices[mode]
        label = self.text(mode, language)[0]
        strategy = self.ports.context_policy(provider, config).settings_strategy
        if strategy == "ollama":
            config["num_ctx"] = "auto"
            config["num_ctx_max"] = window
            config["num_ctx_min"] = min(
                window,
                32768 if window <= 65536 else 65536,
            )
            config.setdefault("ollama_options", {})["num_predict"] = output
        elif strategy == "standard":
            config["context_window"] = window
            config["context_reserve_tokens"] = reserve
            config["max_output_tokens"] = output
        else:
            return ["Context setup is managed by Claude Code for this provider."]
        messages = self.ports.cap_context(provider, config)
        messages.extend(self.ports.cap_output(provider, config))
        messages.extend(self.ports.apply_timeout(provider, config))
        return [
            f"{self.ports.ui_text('context_setup', language)}: {label}",
            f"Applied context: {self.ports.context_status(provider, config)}",
            *messages,
        ]
