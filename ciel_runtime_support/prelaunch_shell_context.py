"""Prelaunch terminal shell and provider launch-preference bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .architecture import ProviderAdapter, ProviderConfig, ProviderUiPolicy
from .prelaunch_terminal import (
    PrelaunchInputStyle,
    PrelaunchRenderServices,
    TerminalSelectionServices,
)


@dataclass(frozen=True, slots=True)
class PrelaunchVisualPorts:
    ansi: Callable[[str, str], str]
    fit_cells: Callable[[Any, int], str]
    stdout_isatty: Callable[[], bool]
    render_intro: Callable[[int, str, str], list[str]]
    app_name: str
    credits: str


@dataclass(frozen=True, slots=True)
class PrelaunchInputPorts:
    enable_ansi: Callable[[], None]
    status_lines: Callable[[], list[str]]
    read_terminal_key: Callable[..., str]
    write_debug: Callable[[Path, str], None]
    debug_path: Path
    run_select: Callable[..., int | None]


@dataclass(frozen=True, slots=True)
class PrelaunchProviderPorts:
    configured_adapter: Callable[[str, dict[str, Any]], ProviderAdapter]
    contract_config: Callable[[str, dict[str, Any]], ProviderConfig]
    labels: Mapping[str, str]
    supports_runtime: Callable[[str, str], bool]
    load_config: Callable[[], dict[str, Any]]
    preferred_action: Callable[
        [
            dict[str, Any],
            str,
            Callable[[str], bool],
            Callable[[str], bool],
            Callable[[str], bool],
        ],
        str,
    ]


@dataclass(frozen=True, slots=True)
class PrelaunchPromptPorts:
    render_services: PrelaunchRenderServices
    input_style: PrelaunchInputStyle
    render_screen: Callable[..., bool]
    read_value_raw: Callable[..., str | None]
    read_value: Callable[..., str]
    read_multiline_raw: Callable[..., str | None]
    read_multiline: Callable[..., str]


@dataclass(frozen=True, slots=True)
class PrelaunchShellContext:
    visual: PrelaunchVisualPorts
    input: PrelaunchInputPorts
    provider: PrelaunchProviderPorts
    prompt: PrelaunchPromptPorts
    main_menu_actions: tuple[str, ...]

    def color_line(self, text: str, code: str, width: int) -> str:
        return self.visual.ansi(self.visual.fit_cells(text, width), code)

    def clean_render_lines(self, lines: list[str], width: int) -> list[str]:
        return [self.visual.fit_cells(line, width) for line in lines]

    def clear_screen(self) -> None:
        if self.visual.stdout_isatty():
            print("\033[2J\033[H", end="")

    def intro_panel_lines(self, width: int) -> list[str]:
        return self.visual.render_intro(
            width, self.visual.app_name, self.visual.credits
        )

    def print_intro_panel(self, width: int) -> None:
        print("\n".join(self.intro_panel_lines(width)))

    def append_menu_key_debug_log(self, line: str) -> None:
        self.input.write_debug(self.input.debug_path, line)

    def read_menu_key(self, fd: int | None = None) -> str:
        return self.input.read_terminal_key(
            fd, debug_log=self.append_menu_key_debug_log
        )

    def portable_select(
        self,
        title: str,
        rows: list[str],
        current: int = 0,
        footer: str = "",
        info_lines: list[str] | None = None,
        show_intro: bool = False,
    ) -> int | None:
        return self.input.run_select(
            title,
            rows,
            current,
            footer,
            info_lines,
            show_intro,
            services=TerminalSelectionServices(
                enable_ansi=self.input.enable_ansi,
                ansi=self.visual.ansi,
                intro_panel_lines=self.intro_panel_lines,
                status_lines=self.input.status_lines,
                read_key=self.read_menu_key,
            ),
        )

    def compact_text(self, value: Any, width: int = 72) -> str:
        return self.visual.fit_cells(value, width)

    def provider_ui_policy(
        self, provider: str, config: dict[str, Any]
    ) -> ProviderUiPolicy:
        adapter = self.provider.configured_adapter(provider, config)
        return adapter.ui_policy(self.provider.contract_config(provider, config))

    def provider_menu_label(self, provider: str, config: dict[str, Any]) -> str:
        policy = self.provider_ui_policy(provider, config)
        if config.get("route_through_router") and policy.routed_menu_label:
            return policy.routed_menu_label
        return policy.menu_label or self.provider.labels.get(provider, provider)

    def current_provider_panel_choice(
        self, provider: str, config: dict[str, Any]
    ) -> str:
        policy = self.provider_ui_policy(provider, config)
        if config.get("route_through_router") and policy.routed_choice:
            return policy.routed_choice
        return policy.native_choice or provider

    def launch_enabled(self, runtime: str, provider: str) -> bool:
        return self.provider.supports_runtime(runtime, provider)

    def default_prelaunch_action(self, provider: str) -> str:
        config = self.provider.load_config()
        if provider == "kimi":
            remembered = str(config.get("last_launch_action") or "").strip()
            if remembered == "launch":
                return remembered
            if remembered in {"launch-codex", "launch-codex-app-server"}:
                return remembered
            return "launch-kimi"
        return self.provider.preferred_action(
            config,
            provider,
            lambda name: self.launch_enabled("agy", name),
            lambda name: self.launch_enabled("claude", name),
            lambda name: self.launch_enabled("codex", name),
        )

    def prelaunch_action_index(self, action: str) -> int:
        if action in {"launch", "launch-codex", "launch-codex-app-server", "launch-agy", "launch-kimi"}:
            action = "launch-menu"
        try:
            return self.main_menu_actions.index(action)
        except ValueError:
            return 0

    def prelaunch_render_services(self) -> PrelaunchRenderServices:
        return self.prompt.render_services

    def prelaunch_input_style(self) -> PrelaunchInputStyle:
        return self.prompt.input_style

    def render_prelaunch_screen(
        self,
        main_index: int,
        panel: str | None,
        panel_index: int,
        panel_rows: list[str],
        checks: list[str],
        messages: list[str],
        first_render: bool,
    ) -> bool:
        return self.prompt.render_screen(
            main_index,
            panel,
            panel_index,
            panel_rows,
            checks,
            messages,
            first_render,
            services=self.prompt.render_services,
        )

    def prompt_menu_value_raw(
        self, label: str, default: str = "", secret: bool = False
    ) -> str | None:
        return self.prompt.read_value_raw(
            label, default, secret, style=self.prompt.input_style
        )

    def prompt_menu_value(
        self,
        prompt: str,
        default: str = "",
        secret: bool = False,
        restore_tty: Callable[[], None] | None = None,
        raw_tty: Callable[[], None] | None = None,
    ) -> str:
        return self.prompt.read_value(
            prompt,
            default,
            secret,
            restore_tty,
            raw_tty,
            style=self.prompt.input_style,
        )

    def prompt_menu_multiline_value_raw(
        self, label: str, secret: bool = False
    ) -> str | None:
        return self.prompt.read_multiline_raw(
            label, secret, style=self.prompt.input_style
        )

    def prompt_menu_multiline_value(
        self,
        prompt: str,
        restore_tty: Callable[[], None] | None = None,
        raw_tty: Callable[[], None] | None = None,
        secret: bool = True,
    ) -> str:
        return self.prompt.read_multiline(
            prompt,
            restore_tty,
            raw_tty,
            secret,
            style=self.prompt.input_style,
        )


@dataclass(frozen=True, slots=True)
class PrelaunchShellCompatibilityApi:
    context: Callable[[], PrelaunchShellContext]

    def color_line(self, text: str, code: str, width: int) -> str:
        return self.context().color_line(text, code, width)

    def clean_render_lines(self, lines: list[str], width: int) -> list[str]:
        return self.context().clean_render_lines(lines, width)

    def clear_screen(self) -> None:
        self.context().clear_screen()

    def intro_panel_lines(self, width: int) -> list[str]:
        return self.context().intro_panel_lines(width)

    def print_intro_panel(self, width: int) -> None:
        self.context().print_intro_panel(width)

    def append_menu_key_debug_log(self, line: str) -> None:
        self.context().append_menu_key_debug_log(line)

    def read_menu_key(self, fd: int | None = None) -> str:
        return self.context().read_menu_key(fd)

    def portable_select(
        self,
        title: str,
        rows: list[str],
        current: int = 0,
        footer: str = "",
        info_lines: list[str] | None = None,
        show_intro: bool = False,
    ) -> int | None:
        return self.context().portable_select(
            title, rows, current, footer, info_lines, show_intro
        )

    def compact_text(self, value: Any, width: int = 72) -> str:
        return self.context().compact_text(value, width)

    def provider_ui_policy(
        self, provider: str, config: dict[str, Any]
    ) -> ProviderUiPolicy:
        return self.context().provider_ui_policy(provider, config)

    def provider_menu_label(self, provider: str, config: dict[str, Any]) -> str:
        return self.context().provider_menu_label(provider, config)

    def current_provider_panel_choice(
        self, provider: str, config: dict[str, Any]
    ) -> str:
        return self.context().current_provider_panel_choice(provider, config)

    def claude_launch_enabled(
        self, provider: str, config: dict[str, Any] | None = None
    ) -> bool:
        del config
        return self.context().launch_enabled("claude", provider)

    def agy_launch_enabled(
        self, provider: str, config: dict[str, Any] | None = None
    ) -> bool:
        del config
        return self.context().launch_enabled("agy", provider)

    def codex_launch_enabled(
        self, provider: str, config: dict[str, Any] | None = None
    ) -> bool:
        del config
        return self.context().launch_enabled("codex", provider)

    def default_prelaunch_action(self, provider: str) -> str:
        return self.context().default_prelaunch_action(provider)

    def prelaunch_action_index(self, action: str) -> int:
        return self.context().prelaunch_action_index(action)

    def prelaunch_render_services(self) -> PrelaunchRenderServices:
        return self.context().prelaunch_render_services()

    def prelaunch_input_style(self) -> PrelaunchInputStyle:
        return self.context().prelaunch_input_style()

    def render_prelaunch_screen(
        self,
        main_index: int,
        panel: str | None,
        panel_index: int,
        panel_rows: list[str],
        checks: list[str],
        messages: list[str],
        first_render: bool,
    ) -> bool:
        return self.context().render_prelaunch_screen(
            main_index,
            panel,
            panel_index,
            panel_rows,
            checks,
            messages,
            first_render,
        )

    def prompt_menu_value_raw(
        self, label: str, default: str = "", secret: bool = False
    ) -> str | None:
        return self.context().prompt_menu_value_raw(label, default, secret)

    def prompt_menu_value(
        self,
        prompt: str,
        default: str = "",
        secret: bool = False,
        restore_tty: Callable[[], None] | None = None,
        raw_tty: Callable[[], None] | None = None,
    ) -> str:
        return self.context().prompt_menu_value(
            prompt, default, secret, restore_tty, raw_tty
        )

    def prompt_menu_multiline_value_raw(
        self, label: str, secret: bool = False
    ) -> str | None:
        return self.context().prompt_menu_multiline_value_raw(label, secret)

    def prompt_menu_multiline_value(
        self,
        prompt: str,
        restore_tty: Callable[[], None] | None = None,
        raw_tty: Callable[[], None] | None = None,
        secret: bool = True,
    ) -> str:
        return self.context().prompt_menu_multiline_value(
            prompt, restore_tty, raw_tty, secret
        )


__all__ = [
    "PrelaunchInputPorts",
    "PrelaunchProviderPorts",
    "PrelaunchPromptPorts",
    "PrelaunchShellCompatibilityApi",
    "PrelaunchShellContext",
    "PrelaunchVisualPorts",
]
