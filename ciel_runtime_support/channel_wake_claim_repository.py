from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable


_PROMPT_IDS_RE = re.compile(r"\b(?:id|ids|pending_ids|message_ids)\s*=\s*([0-9][0-9,\s]*)")


def prompt_match_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def prompt_contains(candidate: str, prompt: str) -> bool:
    needle = prompt_match_text(prompt)
    if not needle:
        return False
    haystack = prompt_match_text(candidate)
    return bool(haystack and needle in haystack)


def prompt_message_ids(text: str) -> set[int]:
    ids: set[int] = set()
    for match in _PROMPT_IDS_RE.finditer(str(text or "")):
        for raw in re.split(r"\D+", match.group(1)):
            if not raw:
                continue
            try:
                message_id = int(raw)
            except (TypeError, ValueError):
                continue
            if message_id > 0:
                ids.add(message_id)
    return ids


def prompt_references_message_id(
    text: str,
    message_id: int,
    prompt_texts: list[str] | tuple[str, ...] = (),
) -> bool:
    if message_id in prompt_message_ids(text):
        return True
    return any(prompt_contains(str(text or ""), prompt) for prompt in prompt_texts)


@dataclass(frozen=True, slots=True)
class ChannelWakeClaimRepository:
    path: Path
    file_lock: Callable[[], AbstractContextManager[Any]]
    now: Callable[[], float]
    ttl_seconds: Callable[[], float]
    log: Callable[[str, str], None]

    def _read_locked(self) -> dict[str, Any]:
        current = self.now()
        ttl = self.ttl_seconds()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            self.log(
                "WARN",
                f"channel_stdin_wake_claims_read_failed error={type(exc).__name__}: {exc}",
            )
            return {}
        claims = data.get("claims") if isinstance(data, dict) else {}
        if not isinstance(claims, dict):
            return {}
        out: dict[str, Any] = {}
        for key, value in claims.items():
            if not isinstance(value, dict):
                continue
            try:
                claimed_at = float(value.get("claimed_at") or 0)
            except (TypeError, ValueError):
                claimed_at = 0.0
            if claimed_at > 0 and current - claimed_at > ttl:
                continue
            out[str(key)] = dict(value)
        return out

    def _write_locked(self, claims: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"claims": claims}, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def prompt(self, message_id: int) -> str:
        if message_id <= 0:
            return ""
        with self.file_lock():
            claim = self._read_locked().get(str(message_id))
        if not isinstance(claim, dict):
            return ""
        prompt = claim.get("prompt")
        return str(prompt) if isinstance(prompt, str) else ""

    def claim(self, message_id: int, prompt: str) -> bool:
        if message_id <= 0 or not prompt_match_text(prompt):
            return True
        with self.file_lock():
            claims = self._read_locked()
            if isinstance(claims.get(str(message_id)), dict):
                return False
            claims[str(message_id)] = {"claimed_at": self.now(), "prompt": prompt}
            self._write_locked(claims)
        return True

    def clear(self, message_id: int) -> None:
        if message_id <= 0:
            return
        with self.file_lock():
            claims = self._read_locked()
            if str(message_id) not in claims:
                return
            claims.pop(str(message_id), None)
            self._write_locked(claims)
