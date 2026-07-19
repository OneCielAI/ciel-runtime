"""HTTP controller and repository adapter for shared plan artifacts."""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class PlanArtifactServices:
    directory: Path
    router_base: str
    safe_segment: Callable[[str, str], str]
    write_json: Callable[..., None]
    write_text: Callable[..., None]
    announce: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PlanArtifactController:
    services: PlanArtifactServices

    def get(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        if path == "/ca/plan/artifacts":
            self.services.directory.mkdir(parents=True, exist_ok=True)
            items = [
                {
                    "name": item.name,
                    "bytes": item.stat().st_size,
                    "url": f"{self.services.router_base}/ca/plan/artifacts/{urllib.parse.quote(item.name)}",
                }
                for item in sorted(self.services.directory.glob("*"))
                if item.is_file()
            ]
            self.services.write_json(handler, {"ok": True, "artifacts": items})
            return True
        if not path.startswith("/ca/plan/artifacts/"):
            return False
        name = self.services.safe_segment(
            urllib.parse.unquote(path[len("/ca/plan/artifacts/") :]), "plan.md"
        )
        target = self.services.directory / name
        if not target.exists() or not target.is_file():
            self.services.write_json(handler, {"ok": False, "error": "not_found"}, 404)
            return True
        content_type = (
            "text/markdown; charset=utf-8"
            if target.suffix.lower() in (".md", ".markdown")
            else "text/plain; charset=utf-8"
        )
        self.services.write_text(
            handler,
            target.read_text(encoding="utf-8", errors="replace"),
            content_type=content_type,
        )
        return True

    def post(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        body: dict[str, Any],
    ) -> bool:
        if path != "/ca/plan/artifacts":
            return False
        self.services.directory.mkdir(parents=True, exist_ok=True)
        title = str(body.get("title") or "plan")
        content = str(body.get("content") or body.get("message") or "")
        name = self.services.safe_segment(
            str(body.get("name") or f"{int(time.time())}-{title}.md"), "plan.md"
        )
        if "." not in name:
            name += ".md"
        target = self.services.directory / name
        target.write_text(content, encoding="utf-8")
        latest = self.services.directory / "latest.md"
        if target.name != latest.name:
            latest.write_text(content, encoding="utf-8")
        url = f"{self.services.router_base}/ca/plan/artifacts/{urllib.parse.quote(name)}"
        if body.get("announce", True):
            self.services.announce(
                {
                    "channel": body.get("channel", "plan"),
                    "sender_id": body.get("sender_id", "plan"),
                    "recipients": body.get("recipients", "all"),
                    "kind": "plan",
                    "message": url,
                    "meta": {"title": title, "url": url, "name": name},
                }
            )
        self.services.write_json(
            handler,
            {
                "ok": True,
                "name": name,
                "url": url,
                "latest_url": f"{self.services.router_base}/ca/plan/artifacts/latest.md",
            },
        )
        return True
