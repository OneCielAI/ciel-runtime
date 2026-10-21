"""Isolated production binding functional acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

parser = argparse.ArgumentParser()
parser.add_argument("--runtime", default="", help="Exact installed runtime root")
parser.add_argument("--out", default="", help="Evidence JSON path")
ARGS = parser.parse_args()
ROOT = Path(ARGS.runtime).resolve() if ARGS.runtime else Path(__file__).resolve().parents[7]
for key in tuple(os.environ):
    upper = key.upper()
    if upper.startswith(("CIEL_RUNTIME_", "CLAUDE", "CODEX_", "ANTHROPIC", "OPENAI_")) or any(
        word in upper for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
    ):
        os.environ.pop(key, None)
sys.path.insert(0, str(ROOT))
from ciel_runtime_support.channel_compact_request_repository import ChannelCompactRequestRepository
from ciel_runtime_support.channel_cursor_repository import ChannelCursorRepository
from ciel_runtime_support.channel_message_repository import (
    ChannelMessageAppendPorts, ChannelMessageRepository, exclusive_file_lock,
)
from ciel_runtime_support.channel_terminal_proxy import terminal_recovery_admission_snapshot
from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding
from ciel_runtime_support.runtime_compaction_control import native_checkpoint_from_transcript
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository
from ciel_runtime_support.runtime_sse_recovery import router_recovery
from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository

SESSION = "acceptance-native-exact"
REQUESTED = 1700000000.0


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + bytes([10])


def digest(path):
    value = path.read_bytes()
    return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}


class Fixture:
    def __init__(self, root, capacity=1000, baseline=1200):
        self.root, self.capacity, self.baseline = root, capacity, baseline
        root.mkdir()
        self.logs = []
        self.home = root / "home"
        self.codex = self.home / ".codex"
        self.path = self.codex / "sessions" / ("rollout-" + SESSION + ".jsonl")
        self.path.parent.mkdir(parents=True)
        self.header = encoded({"type": "session_meta", "timestamp": stamp(REQUESTED - 10), "payload": {
            "base_instructions": "S" * (96 * 1024), "id": SESSION, "cwd": str(root)}})
        assert len(self.header) > 64 * 1024
        self.path.write_bytes(self.header)
        command = SecureJsonRepository(root / "session-command.json", SecureJsonEffects(self.log))
        self.requests = ChannelCompactRequestRepository(
            command.path, threading.RLock(), command.save, lambda text, limit: text[:limit], self.log, lambda: 600)
        self.binding = self.make_binding()
        self.binding.bind("codex", session_id=SESSION, codex_home=self.codex, cwd=root)
        self.scope = self.binding.scope()
        assert self.scope is not None and self.binding.transcript_path() == self.path
        self.binding.control.ports.save({
            "scope": self.scope.payload(), "phase": "blocked", "failure": "checkpoint_unconfirmed",
            "baseline_tokens": baseline, "context_limit": capacity,
            "requested_at": REQUESTED, "deadline": REQUESTED + 1200, "baseline_checkpoint_id": "",
            "checkpoint_id": "", "request_id": "", "attempts": 2})
        self.message_repo = ChannelMessageRepository(root / "runtime-inputs.jsonl", self.log)
        append = ChannelMessageAppendPorts(threading.Condition(),
            lambda: exclusive_file_lock(self.message_repo.path), lambda _message: None,
            lambda values: values if isinstance(values, list) else [str(values)])
        row = self.message_repo.append({"channel": "external:fixture", "message": "synthetic-pending"}, append)
        self.status = RuntimeInputStatusRepository(root / "runtime-input-status.jsonl", lambda **_kw: None,
                                                  self.log, threading.RLock())
        self.status.transition(row["id"], "queued")
        self.cursor = ChannelCursorRepository(root / "channel-llm-cursor.json", self.log)
        assert self.cursor.write(0)
        self.protected = [self.message_repo.path, self.status.path, self.cursor.path]
        self.before = self.protected_digests()

    def log(self, level, message):
        self.logs.append((level, message))

    def make_binding(self):
        # Only unrelated provider budgeting is synthetic; durable evidence is real.
        budget = SimpleNamespace(context_limit=lambda *_args: 0, positive_int=lambda value: int(value or 0),
            configured_output=lambda *_args: 0, cap_output_ratio=lambda *_args: 0,
            reserve=lambda *_args: 0, compact_kind=lambda _body: None)
        return RuntimeCompactionBinding(self.root, self.home, self.requests,
            lambda: SimpleNamespace(budget=budget), lambda _body: 0,
            lambda: ("acceptance-unused-provider", {}), self.log)

    def protected_digests(self):
        return {path.name: digest(path) for path in self.protected}

    def unchanged(self):
        after = self.protected_digests()
        assert after == self.before, "queue/history/cursor changed"
        return after

    def append(self, raw):
        with self.path.open("ab") as stream:
            stream.write(raw)

    def checkpoint(self, session=SESSION, when=REQUESTED + 1):
        value = encoded({"type": "compacted", "timestamp": stamp(when), "payload": {
            "replacement_history": [{"role": "user", "content": "C" * (600 * 1024)}],
            "latest_token_usage_record": {"thread_id": session}}})
        assert len(value) > 600 * 1024
        return value

    def tail(self):
        value = b"".join(encoded({"type": "response_item", "timestamp": stamp(REQUESTED + 2),
            "payload": {"type": "message", "text": "T" * (48 * 1024)}}) for _ in range(14))
        assert len(value) > 600 * 1024
        return value

    def usage(self, tokens=120, when=REQUESTED + 3):
        return encoded({"type": "event_msg", "timestamp": stamp(when), "payload": {
            "type": "token_count", "info": {"last_token_usage": {"input_tokens": tokens}}}})

    def observe(self, paused, phase):
        actual = self.binding.external_admission_paused()
        state = self.binding.control.ports.load()
        assert actual is paused and state["phase"] == phase, (actual, state["phase"])
        snapshot = terminal_recovery_admission_snapshot(time.time(), input_ready=True, manual_input=False,
            busy=False, compaction=actual, pending_state=None)
        assert snapshot["ready"] is (not paused) and snapshot["compaction"] is paused
        def forbidden_restart(**_kwargs):
            raise AssertionError("status must never restart a client")
        recovery = router_recovery({"action": "status"}, status_repository=self.status,
            compaction=self.binding, restart=forbidden_restart, root=self.root)
        assert recovery["ok"] and not recovery["reset"]
        assert recovery["evidence"]["admission_paused"] is paused
        assert recovery["evidence"]["known_queued_count"] == 1
        assert recovery["evidence"]["queued_coverage"] == "complete"
        self.unchanged()
        return {"admission_paused": actual, "phase": state["phase"],
            "next_raw_tokens": state.get("next_raw_tokens"), "checkpoint_id": state.get("checkpoint_id"),
            "terminal_ready": snapshot["ready"], "recovery_admission_paused": recovery["evidence"]["admission_paused"],
            "queue_history_cursor_unchanged": True, "protected_files": self.unchanged(),
            "baseline_tokens": state.get("baseline_tokens"), "context_limit": state.get("context_limit")}


def run(root):
    results = {}
    case = Fixture(root / "valid-large")
    before = case.observe(True, "blocked")
    checkpoint, tail = case.checkpoint(), case.tail()
    case.append(checkpoint + tail + case.usage())
    after = case.observe(False, "verified")
    assert after["next_raw_tokens"] == 120
    expected_epoch = case.scope.epoch
    case.binding = case.make_binding()
    assert case.binding.scope().epoch == expected_epoch
    restored = case.observe(False, "verified")
    assert restored["next_raw_tokens"] == 120
    results["large_checkpoint_and_large_tail"] = {
        "before": before, "after": after, "reconstructed_binding": restored,
        "same_epoch_restored": True, "session_meta_bytes": len(case.header),
        "checkpoint_bytes": len(checkpoint), "ordinary_tail_bytes": len(tail),
        "transcript": digest(case.path), "protected_files": case.unchanged()}

    case = Fixture(root / "incomplete")
    raw = case.checkpoint()
    case.append(raw[:-17])
    results["incomplete_json"] = case.observe(True, "blocked")
    case.append(raw[-17:-1])
    results["complete_json_without_newline"] = case.observe(True, "blocked")
    case.append(bytes([10]))
    results["completed_boundary_no_usage"] = case.observe(True, "awaiting_shrink")
    case.append(case.usage())
    results["completed_boundary_with_usage"] = case.observe(False, "verified")

    case = Fixture(root / "wrong-id")
    case.append(case.checkpoint(session="another-native-thread") + case.tail() + case.usage())
    results["wrong_checkpoint_thread_id"] = case.observe(True, "blocked")

    case = Fixture(root / "old-checkpoint")
    case.append(case.checkpoint(when=REQUESTED - 1) + case.tail() + case.usage())
    results["checkpoint_before_requested_at"] = case.observe(True, "blocked")

    case = Fixture(root / "old-usage")
    case.append(case.checkpoint() + case.tail() + case.usage(when=REQUESTED))
    results["usage_before_checkpoint_timestamp"] = case.observe(True, "awaiting_shrink")

    case = Fixture(root / "wrong-header")
    header = encoded({"type": "session_meta", "payload": {"id": "another-native-thread",
        "base_instructions": "X" * (96 * 1024)}})
    case.path.write_bytes(header + case.checkpoint() + case.usage())
    assert case.binding.transcript_path() is None
    results["replaced_file_wrong_header_id"] = case.observe(True, "blocked")

    case = Fixture(root / "unknown-capacity", capacity=0)
    case.append(case.checkpoint() + case.tail() + case.usage())
    results["unknown_capacity"] = case.observe(True, "awaiting_shrink")

    case = Fixture(root / "above-capacity")
    case.append(case.checkpoint() + case.tail() + case.usage(tokens=1100))
    results["above_capacity"] = case.observe(True, "awaiting_shrink")

    case = Fixture(root / "nonshrinking", capacity=2000)
    case.append(case.checkpoint() + case.tail() + case.usage(tokens=1200))
    results["not_below_baseline"] = case.observe(True, "awaiting_shrink")

    case = Fixture(root / "restart-between-writes")
    case.append(case.checkpoint() + case.usage())
    checkpoint = native_checkpoint_from_transcript(case.path, case.scope)
    assert checkpoint is not None
    persisted = case.binding.control.ports.load()
    persisted.update(phase="awaiting_shrink", checkpoint_id=checkpoint.identity)
    case.binding.control.ports.save(persisted)
    epoch = case.scope.epoch
    case.binding = case.make_binding()
    assert case.binding.scope().epoch == epoch
    results["persisted_awaiting_shrink_reconstructed"] = case.observe(False, "verified")
    assert results["persisted_awaiting_shrink_reconstructed"]["next_raw_tokens"] == 120
    return results


if __name__ == "__main__":
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ciel-ara-checkpoint-acceptance-") as temporary:
        isolated = Path(temporary)
        os.environ.update(CIEL_RUNTIME_CONFIG_DIR=str(isolated / "config"), CIEL_RUNTIME_TEST_ISOLATED="1",
            HOME=str(isolated / "home"), USERPROFILE=str(isolated / "home"),
            CLAUDE_CONFIG_DIR=str(isolated / "home" / ".claude"))
        result = {"status": "passed", "synthetic_isolated_production_binding": True,
            "live_cli_or_provider": False, "live_host_access": False,
            "source_root": str(ROOT), "cases": run(isolated)}
    result["temporary_fixture_deleted"] = not isolated.exists()
    result["elapsed_seconds"] = round(time.perf_counter() - start, 6)
    sources = ("runtime_compaction_binding.py", "runtime_compaction_control.py", "channel_transcript_repository.py")
    result["source_digests"] = {name: digest(ROOT / "ciel_runtime_support" / name) for name in sources}
    result["acceptance_script"] = digest(Path(__file__))
    output = Path(ARGS.out).resolve() if ARGS.out else Path(__file__).with_name("result.json")
    output.write_text(json.dumps(result, indent=2) + chr(10), encoding="utf-8")
    print(json.dumps({"status": result["status"], "cases": len(result["cases"]),
        "elapsed_seconds": result["elapsed_seconds"],
        "large_checkpoint_after": result["cases"]["large_checkpoint_and_large_tail"]["after"],
        "temporary_fixture_deleted": result["temporary_fixture_deleted"]}, indent=2))
