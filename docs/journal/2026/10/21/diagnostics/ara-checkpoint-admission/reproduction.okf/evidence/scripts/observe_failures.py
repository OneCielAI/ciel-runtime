"""Print the observed values behind the four reproduction classes.

Diagnostic evidence only: this script asserts nothing and changes nothing.
Run from anywhere: ``python -I <path>``. It uses temporary directories only.
"""

import dataclasses
import json
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[10]
sys.path.insert(0, str(ROOT))

from ciel_runtime_support.channel_compact_request_repository import ChannelCompactRequestRepository
from ciel_runtime_support.channel_transcript_repository import ChannelTranscriptRepository
from ciel_runtime_support.context_summary_policy import ContextSummaryPolicy
from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding
from ciel_runtime_support.runtime_compaction_control import (
    CompactionScope,
    RuntimeCompactionControl,
    RuntimeCompactionPolicy,
    RuntimeCompactionPorts,
    native_checkpoint_from_transcript,
)
from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository

NATIVE_COMPACT = {"messages": [{"role": "user", "content": "<command-name>/compact</command-name>"}]}


def stamp(when):
    return datetime.fromtimestamp(when, timezone.utc).isoformat()


def boundary(session, when, identity="cp", post=120, pad=0):
    record = {
        "type": "system", "subtype": "compact_boundary", "sessionId": session,
        "timestamp": stamp(when), "uuid": identity, "isSidechain": False,
        "compactMetadata": {"trigger": "auto", "preTokens": 1200, "postTokens": post},
    }
    if pad:
        record["padding"] = "x" * pad
    return record


def compacted(thread, when, content="handoff"):
    return {
        "type": "compacted", "timestamp": stamp(when),
        "payload": {
            "replacement_history": [{"type": "message", "role": "user", "content": content}],
            "latest_token_usage_record": {"thread_id": thread},
        },
    }


def claude_kind(body):
    policy = ContextSummaryPolicy(
        lambda _value: 1, lambda value: int(value), str, str,
        lambda value: str((value.get("messages") or [{}])[-1].get("content", "")),
    )
    return policy.compact_request_kind(body)


def codex_kind(body):
    return "native" if body.get("native") else None


class Harness:
    def __init__(self, runtime="claude", name="session-a.jsonl", kind=claude_kind):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = 1800000000.0
        clock_patch = patch(
            "ciel_runtime_support.channel_compact_request_repository.time.time",
            side_effect=lambda: self.clock,
        )
        clock_patch.start()
        self._clock_patch = clock_patch
        self.scope = CompactionScope(runtime, "session-a", "launch-a")
        self.logs = []
        self.trace = self.root / name
        self.trace.write_text("", encoding="utf-8")
        effects = SecureJsonEffects(log=lambda *args: self.logs.append(args))
        artifact = SecureJsonRepository(self.root / "compact.json", effects)
        self.repository = ChannelCompactRequestRepository(
            artifact.path, threading.Lock(), artifact.save,
            lambda value, limit: value[:limit], effects.log, lambda: 10,
        )
        self.state = SecureJsonRepository(self.root / "control.json", effects)
        self.lock = threading.Lock()
        self.kind = kind
        self.control = self.controller()

    def controller(self):
        return RuntimeCompactionControl(RuntimeCompactionPorts(
            load=lambda: self.state.load("compaction"),
            save=lambda value: self.state.save(value, "compaction"),
            lock=lambda: self.lock, scope=lambda: self.scope,
            checkpoint=lambda scope: native_checkpoint_from_transcript(self.trace, scope),
            queue=self.repository.queue_automatic, clear=self.repository.clear,
            compact_kind=self.kind, log=lambda *args: self.logs.append(args),
            now=lambda: self.clock,
        ), RuntimeCompactionPolicy(
            confirmation_timeout_seconds=10, retry_cooldown_seconds=3, max_attempts=2))

    def request(self, tokens=1200, body=None):
        with patch(
            "ciel_runtime_support.channel_compact_request_repository.time.time",
            return_value=self.clock,
        ):
            self.control.observe_request(
                body or {"messages": [{"role": "user", "content": "ordinary"}]}, tokens, 1000)

    def append(self, record):
        with self.trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")

    def state_value(self):
        return self.state.load("test") or {}


def main():
    print("tree: nightly-20261020 (working tree)")

    print("\n[1] parser tail window (claude compact_boundary)")
    probe = Path(tempfile.mkdtemp()) / "session-a.jsonl"
    scope = CompactionScope("claude", "session-a", "launch-a")
    probe.write_text(json.dumps(boundary("session-a", 1800000001)) + "\n", encoding="utf-8")
    print("  small boundary at end: found =",
          native_checkpoint_from_transcript(probe, scope) is not None)
    probe.write_text(
        json.dumps(boundary("session-a", 1800000001, pad=600 * 1024)) + "\n", encoding="utf-8")
    line = probe.read_text(encoding="utf-8").splitlines()[0]
    print("  boundary line bytes =", len(line.encode()), "> 512KiB:", len(line.encode()) > 512 * 1024,
          "-> found =", native_checkpoint_from_transcript(probe, scope) is not None)
    probe.write_text(
        json.dumps(boundary("session-a", 1800000001)) + "\n"
        + json.dumps({"type": "assistant", "pad": "y" * (600 * 1024)}) + "\n",
        encoding="utf-8")
    print("  boundary then 600KiB tail: file bytes =", probe.stat().st_size,
          "-> found =", native_checkpoint_from_transcript(probe, scope) is not None)

    print("\n[2] controller: 600KiB codex compacted line")
    harness = Harness("codex", "rollout-session-a.jsonl", codex_kind)
    harness.request(100, {"native": "codex"})
    harness.clock += 1
    record = compacted("session-a", harness.clock, content="h" * (600 * 1024))
    harness.append(record)
    print("  compacted line bytes =", len(json.dumps(record)),
          "phase before timeout =", harness.state_value().get("phase"),
          "paused =", harness.control.external_admission_paused())
    harness.clock += 11
    harness.control.external_admission_paused()
    state = harness.state_value()
    print("  after 10s deadline: phase =", state.get("phase"), "failure =", state.get("failure"),
          "paused =", harness.control.external_admission_paused())

    print("\n[3] controller: checkpoint pushed past tail")
    harness = Harness("codex", "rollout-session-a.jsonl", codex_kind)
    harness.request(100, {"native": "codex"})
    harness.clock += 1
    harness.append(compacted("session-a", harness.clock))
    harness.append({"type": "event_msg", "payload": {"type": "agent_message", "message": "y" * (600 * 1024)}})
    print("  file bytes =", harness.trace.stat().st_size,
          "-> found =", native_checkpoint_from_transcript(harness.trace, harness.scope) is not None)
    harness.control.external_admission_paused()
    harness.clock += 11
    harness.control.external_admission_paused()
    state = harness.state_value()
    print("  after 10s deadline: phase =", state.get("phase"), "failure =", state.get("failure"))

    print("\n[4] controller: persisted awaiting_shrink")
    harness = Harness()
    harness.request(100, NATIVE_COMPACT)
    harness.clock += 1
    harness.append(boundary("session-a", harness.clock, post=120))
    harness.control.external_admission_paused()
    state = harness.state_value()
    print("  after checkpoint: phase =", state.get("phase"), "baseline =", state.get("baseline_tokens"))
    harness.control = harness.controller()
    harness.request(120)
    paused = harness.control.external_admission_paused()
    print("  after post-checkpoint request(120, limit=1000): phase =", harness.state_value().get("phase"),
          "paused =", paused)
    harness.clock += 100000
    paused = harness.control.external_admission_paused()
    print("  after +100000s: phase =", harness.state_value().get("phase"), "paused =", paused)

    control = Harness()
    control.request(100, NATIVE_COMPACT)
    control.clock += 1
    control.append(boundary("session-a", control.clock, post=90))
    control.control.external_admission_paused()
    control.control = control.controller()
    control.request(90)
    control.control.external_admission_paused()
    print("  control request(90) below baseline: phase =", control.state_value().get("phase"))

    print("\n[5] codex session_meta > 64KiB")
    root = Path(tempfile.mkdtemp())
    codex = root / "codex"
    path = codex / "sessions" / "2026" / "10" / "21" / "rollout-thread.jsonl"
    path.parent.mkdir(parents=True)
    meta = {"type": "session_meta", "payload": {
        "id": "thread", "cwd": str(root), "timestamp": 1800000000.0,
        "base_instructions": "z" * (70 * 1024)}}
    path.write_text(
        json.dumps(meta) + "\n" + json.dumps(compacted("thread", 1800000001)) + "\n", encoding="utf-8")
    header = path.read_text(encoding="utf-8").splitlines()[0]
    print("  session_meta line bytes =", len(header.encode()), "> 64KiB:", len(header.encode()) > 64 * 1024,
          "| full parse id =", json.loads(header)["payload"]["id"])
    print("  _transcript_session =", repr(RuntimeCompactionBinding._transcript_session(path, "codex")),
          "| _codex_session_identity =", ChannelTranscriptRepository._codex_session_identity(path))

    logs = []
    effects = SecureJsonEffects(log=lambda *args: logs.append(args))
    artifact = SecureJsonRepository(root / "command.json", effects)
    requests = ChannelCompactRequestRepository(
        artifact.path, threading.Lock(), artifact.save,
        lambda value, limit: value[:limit], effects.log, lambda: 600)
    budget = SimpleNamespace(
        context_limit=lambda *_: 20000, positive_int=lambda value: int(value or 0),
        configured_output=lambda *_: 4000, cap_output_ratio=lambda *_: 4000,
        reserve=lambda *_: 1000, compact_kind=codex_kind)
    binding = RuntimeCompactionBinding(
        root, root / "home", requests, lambda: SimpleNamespace(budget=budget),
        lambda body: body.get("tokens", 100), lambda: ("other", {}),
        lambda *args: logs.append(args))
    binding.bind("codex", session_id="thread", codex_home=codex, cwd=root)
    print("  bound scope session =", binding.scope().session_id,
          "| transcript_path =", binding.transcript_path())
    binding.observe_request(
        "other", {}, {"tokens": 16000, "native": "codex"}, "codex", {"session_id": "thread"})
    print("  phase after native compact request =", binding.control.ports.load().get("phase"))
    deadline = float(binding.control.ports.load()["deadline"])
    binding.control.ports = dataclasses.replace(binding.control.ports, now=lambda: deadline + 1)
    binding.external_admission_paused()
    state = binding.control.ports.load()
    print("  after confirmation deadline: phase =", state.get("phase"), "failure =", state.get("failure"))


if __name__ == "__main__":
    main()
