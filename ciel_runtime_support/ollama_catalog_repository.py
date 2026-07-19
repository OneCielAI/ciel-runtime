"""Filesystem and HTTP adapters for the Ollama model catalog."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ciel_runtime_support.ollama_catalog import library_model_parts, parse_library_context_map


@dataclass(frozen=True, slots=True)
class OllamaCatalogRepository:
    path: Path
    log: Callable[[str, str], None]
    request_headers: Callable[[], dict[str, str]]

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, catalog: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp")
            temporary.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
        except Exception as error:
            self.log("WARN", f"ollama catalog: failed to save cache: {error}")

    def fetch_json(self, url: str, timeout: float = 12.0) -> Any:
        request = urllib.request.Request(url, headers=self.request_headers())
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(5_000_000).decode("utf-8", errors="replace"))

    def fetch_library_context_map(
        self,
        base_model: str,
        timeout: float = 10.0,
    ) -> tuple[dict[str, int], str | None]:
        parts = library_model_parts(base_model)
        if not parts:
            return {}, None
        base, _tag = parts
        urls = (
            f"https://ollama.com/library/{urllib.parse.quote(base, safe='')}/tags",
            f"https://ollama.com/library/{urllib.parse.quote(base, safe='')}",
        )
        merged: dict[str, int] = {}
        source_url: str | None = None
        for url in urls:
            request = urllib.request.Request(url, headers=self.request_headers())
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read(3_000_000)
            except Exception:
                continue
            page_map = parse_library_context_map(raw.decode("utf-8", errors="replace"), base)
            for tag, tokens in page_map.items():
                merged[tag] = max(merged.get(tag, 0), tokens)
            if page_map and not source_url:
                source_url = url
        return merged, source_url
