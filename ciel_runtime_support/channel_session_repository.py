from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ChannelSessionRepository:
    path: Path
    default_protocol_version: str
    log: Callable[[str, str], None]
    process_id: Callable[[], int] = os.getpid
    timestamp: Callable[[], str] = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")
    chmod: Callable[[Path, int], None] = os.chmod

    def records(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except Exception as exc:
            self.log(
                "WARN",
                f"channel_http_session_record_read_failed error={type(exc).__name__}: {exc}",
            )
            return []
        records = data.get("sessions") if isinstance(data, dict) else data
        if not isinstance(records, list):
            return []
        return [item for item in records if isinstance(item, dict)]

    def write(self, records: list[dict[str, Any]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"sessions": records[-100:]}
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                self.chmod(self.path, 0o600)
            except Exception as exc:
                self.log(
                    "WARN",
                    f"channel_http_session_record_chmod_failed error={type(exc).__name__}: {exc}",
                )
        except Exception as exc:
            self.log(
                "WARN",
                f"channel_http_session_record_write_failed error={type(exc).__name__}: {exc}",
            )

    def record(
        self,
        name: str,
        url: str,
        session_id: str | None,
        protocol_version: str,
    ) -> None:
        session = str(session_id or "").strip()
        if not session:
            return
        url_text = str(url)
        records = [
            item
            for item in self.records()
            if str(item.get("url") or "") != url_text
            and str(item.get("session_id") or "") != session
        ]
        records.append(
            {
                "name": str(name),
                "url": url_text,
                "session_id": session,
                "protocol_version": str(protocol_version or self.default_protocol_version),
                "pid": self.process_id(),
                "recorded_at": self.timestamp(),
            }
        )
        self.write(records)

    def forget(self, session_id: str | None) -> None:
        session = str(session_id or "").strip()
        if not session:
            return
        records = self.records()
        kept = [item for item in records if str(item.get("session_id") or "") != session]
        if len(kept) != len(records):
            self.write(kept)
