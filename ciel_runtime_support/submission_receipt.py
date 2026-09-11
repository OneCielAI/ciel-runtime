"""Confirm input acceptance from newly appended structured user records."""

import json
import os
from pathlib import Path


class TranscriptSubmissionReceipt:
    def __init__(self, path: Path, prompt: str) -> None:
        self.path = path
        stat = path.stat()
        self.offset = stat.st_size
        self.identity = (stat.st_dev, stat.st_ino)
        self.invalidated = False
        self.expected = " ".join(prompt.split())
        self.pending = bytearray()
        self.accepted = False

    def __call__(self) -> bool:
        if self.accepted:
            return True
        if self.invalidated:
            return False
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                if (stat.st_dev, stat.st_ino) != self.identity or stat.st_size < self.offset:
                    self.invalidated = True
                    return False  # Do not match historical records after truncation.
                stream.seek(self.offset)
                chunk = stream.read(1024 * 1024)
                self.offset += len(chunk)
        except OSError:
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
            payload = record.get("payload") if record.get("type") == "response_item" else record.get("message")
            if not isinstance(payload, dict) or payload.get("role") != "user":
                continue
            content = payload.get("content")
            text = content if isinstance(content, str) else "\n".join(
                str(block.get("text") or "") for block in content
                if isinstance(block, dict) and block.get("type") in {"text", "input_text"}
            ) if isinstance(content, list) else ""
            if self.expected and self.expected in " ".join(text.split()):
                self.accepted = True
                return True
        if len(self.pending) > 16 * 1024 * 1024:
            # Fail closed instead of accumulating an unbounded malformed line.
            self.pending.clear()
            self.invalidated = True
        return False
