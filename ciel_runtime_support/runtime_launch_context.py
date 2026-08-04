"""Runtime launch dispatch context with explicitly supplied service factories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .runtime_launch import (
    AgyLaunchServices,
    ClaudeLaunchServices,
    CodexAppServerLaunchServices,
    CodexLaunchServices,
)


@dataclass(frozen=True, slots=True)
class RuntimeLaunchRunners:
    claude: Callable[..., int]
    codex: Callable[..., int]
    codex_app_server: Callable[..., int]
    agy: Callable[..., int]


@dataclass(frozen=True, slots=True)
class RuntimeLaunchServiceFactories:
    claude: Callable[[], ClaudeLaunchServices]
    codex: Callable[[], CodexLaunchServices]
    codex_app_server: Callable[[], CodexAppServerLaunchServices]
    agy: Callable[[], AgyLaunchServices]


@dataclass(frozen=True, slots=True)
class RuntimeLaunchContext:
    runners: RuntimeLaunchRunners
    services: RuntimeLaunchServiceFactories

    def launch_claude(
        self,
        passthrough: list[str],
        skip_menu: bool = False,
        force_menu: bool = False,
        web_search_override: bool | None = None,
        update_check: bool = True,
        self_update_check: bool = True,
    ) -> int:
        return self.runners.claude(
            passthrough,
            skip_menu=skip_menu,
            force_menu=force_menu,
            web_search_override=web_search_override,
            update_check=update_check,
            self_update_check=self_update_check,
            services=self.services.claude(),
        )

    def launch_codex(
        self,
        passthrough: list[str],
        skip_menu: bool = False,
        force_menu: bool = False,
        update_check: bool = True,
        self_update_check: bool = True,
    ) -> int:
        return self.runners.codex(
            passthrough,
            skip_menu=skip_menu,
            force_menu=force_menu,
            update_check=update_check,
            self_update_check=self_update_check,
            services=self.services.codex(),
        )

    def launch_codex_app_server(
        self,
        passthrough: list[str],
        skip_menu: bool = True,
        force_menu: bool = False,
        update_check: bool = True,
        self_update_check: bool = True,
    ) -> int:
        return self.runners.codex_app_server(
            passthrough,
            skip_menu=skip_menu,
            force_menu=force_menu,
            update_check=update_check,
            self_update_check=self_update_check,
            services=self.services.codex_app_server(),
        )

    def launch_agy(
        self,
        passthrough: list[str],
        skip_menu: bool = False,
        force_menu: bool = False,
        update_check: bool = True,
        self_update_check: bool = True,
    ) -> int:
        return self.runners.agy(
            passthrough,
            skip_menu=skip_menu,
            force_menu=force_menu,
            update_check=update_check,
            self_update_check=self_update_check,
            services=self.services.agy(),
        )


@dataclass(frozen=True, slots=True)
class RuntimeLaunchCompatibilityApi:
    context: Callable[[], RuntimeLaunchContext]

    def launch_claude(self, *args: Any, **kwargs: Any) -> int:
        return self.context().launch_claude(*args, **kwargs)

    def launch_codex(self, *args: Any, **kwargs: Any) -> int:
        return self.context().launch_codex(*args, **kwargs)

    def launch_codex_app_server(self, *args: Any, **kwargs: Any) -> int:
        return self.context().launch_codex_app_server(*args, **kwargs)

    def launch_agy(self, *args: Any, **kwargs: Any) -> int:
        return self.context().launch_agy(*args, **kwargs)


__all__ = [
    "RuntimeLaunchCompatibilityApi",
    "RuntimeLaunchContext",
    "RuntimeLaunchRunners",
    "RuntimeLaunchServiceFactories",
]
