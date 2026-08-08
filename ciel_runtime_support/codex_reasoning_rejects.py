"""Remove sealed reasoning that the Codex backend itself refused to verify.

A Codex session can move between providers (routed GitHub Copilot, Alibaba,
back to an OpenAI account). Each provider seals its reasoning items with its
own keys, Codex persists them, and on resume it replays them with their item
IDs stripped — so a replayed reasoning item carries no provenance the router
could inspect. Only the upstream can tell whose ciphertext it is, and when it
is not its own the OpenAI backend rejects the whole turn:

    invalid_request_error: The encrypted content oPYR...Lj8= could not be
    verified. Reason: Encrypted content could not be decrypted or parsed.

This module turns that explicit verdict into the removal rule. The error names
the ciphertext's head and tail; the matching reasoning item is dropped and the
request retried, and the verdict is persisted (as a SHA-256 of the ciphertext)
so later turns strip it before sending. Nothing is guessed: every removal
traces back to an upstream rejection of that exact ciphertext, sealed
reasoning the upstream accepts is never touched, and the session file is
never modified.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

_UNVERIFIABLE_CONTENT = re.compile(
    r"encrypted content ([A-Za-z0-9+/=_-]+)\.\.\.([A-Za-z0-9+/=_-]+) "
    r"could not be verified"
)


def parse_unverifiable_encrypted_content(error_text: str) -> tuple[str, str] | None:
    """Extract the (head, tail) of the ciphertext an upstream rejected."""

    match = _UNVERIFIABLE_CONTENT.search(str(error_text or ""))
    return (match.group(1), match.group(2)) if match else None


def encrypted_content_digest(encrypted_content: str) -> str:
    return hashlib.sha256(encrypted_content.encode("utf-8")).hexdigest()


class RejectedReasoningStore:
    """Durable record of ciphertexts the upstream refused to verify."""

    def __init__(self, path: Path, log: Callable[[str, str], Any]) -> None:
        self._path = path
        self._log = log
        self._digests: set[str] | None = None

    def _load(self) -> set[str]:
        if self._digests is not None:
            return self._digests
        digests: set[str] = set()
        try:
            recorded = json.loads(self._path.read_text(encoding="utf-8"))
            entries = recorded.get("sha256") if isinstance(recorded, dict) else None
            if isinstance(entries, list):
                digests = {str(entry) for entry in entries}
        except FileNotFoundError:
            pass
        except Exception as exc:
            self._log(
                "WARN",
                f"rejected_reasoning_store_load_failed error={type(exc).__name__}: {exc}",
            )
        self._digests = digests
        return digests

    def contains(self, encrypted_content: str) -> bool:
        return encrypted_content_digest(encrypted_content) in self._load()

    def add(self, encrypted_content: str) -> None:
        digests = self._load()
        digest = encrypted_content_digest(encrypted_content)
        if digest in digests:
            return
        digests.add(digest)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"version": 1, "sha256": sorted(digests)}),
                encoding="utf-8",
            )
        except Exception as exc:
            self._log(
                "WARN",
                f"rejected_reasoning_store_write_failed error={type(exc).__name__}: {exc}",
            )


def drop_rejected_reasoning(
    body: dict[str, Any],
    is_rejected: Callable[[str], bool],
) -> tuple[dict[str, Any], int]:
    """Strip reasoning items whose ciphertext the upstream already rejected."""

    items = body.get("input")
    if not isinstance(items, list):
        return body, 0
    kept: list[Any] = []
    dropped = 0
    for item in items:
        if (
            isinstance(item, dict)
            and item.get("type") == "reasoning"
            and isinstance(item.get("encrypted_content"), str)
            and is_rejected(item["encrypted_content"])
        ):
            dropped += 1
            continue
        kept.append(item)
    if not dropped:
        return body, 0
    projected = dict(body)
    projected["input"] = kept
    return projected, dropped


def drop_reasoning_matching_verdict(
    body: dict[str, Any],
    head: str,
    tail: str,
) -> tuple[dict[str, Any], str | None]:
    """Drop the reasoning item whose ciphertext the upstream just named."""

    items = body.get("input")
    if not isinstance(items, list):
        return body, None
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        sealed = item.get("encrypted_content")
        if (
            isinstance(sealed, str)
            and sealed.startswith(head)
            and sealed.endswith(tail)
        ):
            projected = dict(body)
            projected["input"] = items[:index] + items[index + 1 :]
            return projected, sealed
    return body, None


__all__ = [
    "RejectedReasoningStore",
    "drop_rejected_reasoning",
    "drop_reasoning_matching_verdict",
    "encrypted_content_digest",
    "parse_unverifiable_encrypted_content",
]
