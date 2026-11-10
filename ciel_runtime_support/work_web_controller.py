"""HTTP route controller for the standalone Work web console."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .work_web_ui import WORK_WEB_UI_PATH, render_work_web_ui


@dataclass(frozen=True, slots=True)
class WorkWebController:
    """Serve only the Work console shell; the v1 API is mounted separately."""

    write_text: Callable[..., None]
    render: Callable[[], str] = render_work_web_ui

    def handle_get(self, handler: Any, path: str) -> bool:
        if path not in {WORK_WEB_UI_PATH, WORK_WEB_UI_PATH + "/"}:
            return False
        self.write_text(
            handler,
            self.render(),
            content_type="text/html; charset=utf-8",
        )
        return True


__all__ = ["WorkWebController"]
