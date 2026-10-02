"""Confirm input acceptance from newly appended structured user records."""

from collections.abc import Callable
from datetime import datetime
import json
import os
from pathlib import Path
import time

# Clock slack between the CLI's record timestamps and this process.
RECORD_TIME_SLACK_SECONDS = 2.0


def record_epoch(record: dict) -> float | None:
    value = record.get("timestamp")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class TranscriptSubmissionReceipt:
    """Accept once a user record containing the prompt is appended.

    ``from_start`` reads a transcript that appeared after the prompt was typed
    from its first byte, accepting only records stamped at or after
    ``not_before`` so an older conversation cannot confirm a new prompt.
    """

    def __init__(
        self,
        path: Path,
        prompt: str,
        *,
        from_start: bool = False,
        not_before: float | None = None,
    ) -> None:
        self.path = path
        stat = path.stat()
        self.offset = 0 if from_start else stat.st_size
        self.identity = (stat.st_dev, stat.st_ino)
        self.invalidated = False
        self.expected = " ".join(prompt.split())
        self.pending = bytearray()
        self.accepted = False
        self.not_before = not_before
        # Diagnostics for a prompt_not_submitted verdict (celly, 2026-10-01:
        # the record was on disk inside the window yet never matched).
        self.started_offset = self.offset
        self.last_size = stat.st_size
        self.checks = 0
        self.read_errors = 0
        self.user_records = 0
        self.last_user_head = ""
        self.invalidated_reason = ""

    def __call__(self) -> bool:
        if self.accepted:
            return True
        if self.invalidated:
            return False
        self.checks += 1
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                self.last_size = stat.st_size
                if (stat.st_dev, stat.st_ino) != self.identity or stat.st_size < self.offset:
                    self.invalidated = True
                    self.invalidated_reason = (
                        "identity" if (stat.st_dev, stat.st_ino) != self.identity else "truncated"
                    )
                    return False  # Do not match historical records after truncation.
                stream.seek(self.offset)
                chunk = stream.read(1024 * 1024)
                self.offset += len(chunk)
        except OSError:
            self.read_errors += 1
            return False
        self.pending.extend(chunk)
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(record, dict):
                continue
            if self.not_before is not None:
                stamped = record_epoch(record)
                if stamped is None or stamped < self.not_before - RECORD_TIME_SLACK_SECONDS:
                    continue
            payload = record.get("payload") if record.get("type") == "response_item" else record.get("message")
            if not isinstance(payload, dict) or payload.get("role") != "user":
                continue
            content = payload.get("content")
            text = content if isinstance(content, str) else "\n".join(
                str(block.get("text") or "") for block in content
                if isinstance(block, dict) and block.get("type") in {"text", "input_text"}
            ) if isinstance(content, list) else ""
            self.user_records += 1
            self.last_user_head = " ".join(text.split())[:60]
            if self.expected and self.expected in " ".join(text.split()):
                self.accepted = True
                return True
        if len(self.pending) > 16 * 1024 * 1024:
            # Fail closed instead of accumulating an unbounded malformed line.
            self.pending.clear()
            self.invalidated = True
            self.invalidated_reason = "oversized_line"
        return False

    def describe(self) -> str:
        return (
            f"path={self.path} start={self.started_offset} offset={self.offset} "
            f"size={self.last_size} checks={self.checks} read_errors={self.read_errors} "
            f"user_records={self.user_records} accepted={self.accepted} "
            f"invalidated={self.invalidated_reason or self.invalidated} "
            f"expected_len={len(self.expected)} last_user={self.last_user_head!r}"
        )


class LatestTranscriptSubmissionReceipt:
    """A receipt that follows the session to the transcript it writes next.

    A CLI writes the prompt into a transcript that may not exist yet when the
    prompt is typed: Codex creates its rollout when a conversation's first
    turn starts, and ``/new``/``/clear`` move the session to a new file.  The
    newest transcript is resolved again on every check; one first seen after
    typing is read from its start (records stamped after typing only).
    Measured 2026-10-01 (journal runtime-control): without this the first
    message of a fresh Codex session and the first after ``/new`` were shown
    in the TUI yet recorded ``prompt_not_submitted``.
    """

    def __init__(
        self,
        resolve: Callable[[], Path | None],
        prompt: str,
        *,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.resolve = resolve
        self.prompt = prompt
        self.started_at = now()
        self.receipts: dict[Path, TranscriptSubmissionReceipt] = {}
        initial = resolve()
        self.last_resolved: Path | None = initial
        if initial is not None:
            self.receipts[initial] = TranscriptSubmissionReceipt(initial, prompt)

    @property
    def watching(self) -> bool:
        return bool(self.receipts)

    def __call__(self) -> bool:
        try:
            path = self.resolve()
        except OSError:
            path = None
        self.last_resolved = path
        if path is not None and path not in self.receipts:
            try:
                self.receipts[path] = TranscriptSubmissionReceipt(
                    path, self.prompt, from_start=True, not_before=self.started_at
                )
            except OSError:
                pass
        return any(receipt() for receipt in list(self.receipts.values()))

    def describe(self) -> str:
        watched = " | ".join(receipt.describe() for receipt in self.receipts.values())
        return f"resolved={self.last_resolved} receipts={len(self.receipts)} {watched or 'none'}"

