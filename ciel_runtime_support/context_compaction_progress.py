"""Private, bounded journal of completed semantic map work.

A runtime chunked compaction runs its map summaries one after another. When
the downstream client cancelled (Kobe 47/56, Emily 12/15, Regina 40/44 on
2026-10-23/24), every finished summary was discarded and the retried compact
paid for all of them again. This journal keeps each validated map outcome so
an identical retry continues where the previous attempt stopped.

The journal stores prompt digests, split decisions and summary text only:
never source history, prompts, image bytes, headers or credentials. A run is
keyed by an exact identity of the request (scope, provider, resolved model,
wire, endpoint, budgets, prompts and digests of the full source); any
difference starts a new run. Replay verifies every prompt digest in order
and stops reusing at the first mismatch. Map completion is never native
compaction success: the final generation, the native checkpoint and the
admission verifier remain the only completion evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable

from .channel_message_repository import exclusive_file_lock
from .secure_json_repository import SecureJsonEffects, SecureJsonRepository

PROGRESS_VERSION = 1
MAX_BYTES = 8 * 1024 * 1024
MAX_RUNS = 4
MAX_STEPS = 1024
MAX_SUMMARY_BYTES = 256 * 1024
TTL_SECONDS = 3600.0


def stable_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8", "surrogatepass")).hexdigest()


def _valid_step(step: Any) -> bool:
    if not isinstance(step, dict) or not isinstance(step.get("prompt"), str) or len(step["prompt"]) != 64:
        return False
    if step.get("kind") == "split":
        return set(step) == {"prompt", "kind"}
    text = step.get("text")
    return (step.get("kind") == "summary" and set(step) == {"prompt", "kind", "text"}
            and isinstance(text, str) and bool(text.strip())
            and len(text.encode("utf-8", "surrogatepass")) <= MAX_SUMMARY_BYTES)


class MapProgress:
    """Replay one run's recorded map steps in order, then append new ones."""

    def __init__(self, root: Path, identity: dict[str, Any], log: Callable[[str, str], Any],
                 now: Callable[[], float] = time.time) -> None:
        self.run = stable_digest({"version": PROGRESS_VERSION, **identity})
        self.path = root / "context-compact-progress.json"
        self.repository = SecureJsonRepository(self.path, SecureJsonEffects(log))
        self.log = log
        self.now = now
        self.steps: list[dict[str, Any]] = []
        self.position = 0
        self.enabled = True
        try:
            with exclusive_file_lock(self.path):
                steps = self._runs().get(self.run, {}).get("steps", [])
            if isinstance(steps, list) and len(steps) <= MAX_STEPS and all(_valid_step(step) for step in steps):
                self.steps = steps
            else:
                self._disable("invalid_steps")
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            self._disable(f"read_failed:{type(exc).__name__}")

    @property
    def recorded(self) -> int:
        return len(self.steps)

    def _disable(self, reason: str) -> None:
        if self.enabled:
            self.enabled = False
            self.log("WARN", f"context_compact_progress_disabled run={self.run[:16]} reason={reason}")

    def _runs(self) -> dict[str, Any]:
        """Live runs on disk; the caller holds the journal lock."""

        if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
            self.repository.quarantine("compact_progress")
            return {}
        state = self.repository.load("compact_progress")
        if state is None:
            # Unreadable bytes are kept aside for diagnosis; a fresh journal starts.
            self.repository.quarantine("compact_progress")
            return {}
        if state.get("version") != PROGRESS_VERSION or not isinstance(state.get("runs"), dict):
            return {}
        now = self.now()
        return {key: row for key, row in state["runs"].items()
                if isinstance(row, dict) and isinstance(row.get("at"), (int, float))
                and 0 <= now - row["at"] < TTL_SECONDS}

    def take(self, prompt: str) -> dict[str, Any] | None:
        """The recorded outcome for this exact next prompt, or None."""

        if not self.enabled or self.position >= len(self.steps):
            return None
        step = self.steps[self.position]
        if step["prompt"] != stable_digest(prompt):
            # The recorded plan diverged; finish without further reuse or writes.
            self._disable("prompt_mismatch")
            return None
        self.position += 1
        return step

    def record(self, prompt: str, kind: str, text: str = "") -> None:
        """Durably append one validated outcome; failures only disable reuse."""

        if not self.enabled:
            return
        step: dict[str, Any] = {"prompt": stable_digest(prompt), "kind": kind}
        if kind == "summary":
            step["text"] = text
        if not _valid_step(step) or len(self.steps) >= MAX_STEPS or self.position != len(self.steps):
            self._disable("step_rejected")
            return
        try:
            with exclusive_file_lock(self.path):
                runs = self._runs()
                if runs.get(self.run, {}).get("steps", []) != self.steps:
                    # Another attempt of the same run moved on; never overwrite it.
                    self._disable("revision_conflict")
                    return
                updated = [*self.steps, step]
                runs[self.run] = {"at": self.now(), "steps": updated}
                order = sorted(runs, key=lambda key: (key != self.run, -runs[key]["at"]))[:MAX_RUNS]
                while order:
                    state = {"version": PROGRESS_VERSION, "runs": {key: runs[key] for key in order}}
                    # Measure the bytes SecureJsonRepository writes: indented
                    # JSON through a text-mode file, which translates newlines.
                    text = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
                    encoded = text.replace("\n", os.linesep)
                    if len(encoded.encode("utf-8", "surrogatepass")) <= MAX_BYTES:
                        break
                    order.pop()
                if self.run not in order:
                    self._disable("byte_limit")
                    return
                self.repository.save(state, "compact_progress")
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            self._disable(f"write_failed:{type(exc).__name__}")
            return
        self.steps = updated
        self.position = len(updated)


__all__ = ["MapProgress", "stable_digest"]
