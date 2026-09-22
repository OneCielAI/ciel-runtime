"""Root-cause guards for channel wake delivery.

Live 2026-09-22 (instance 9469, workspace g:\\ciel-Walkie): 32 messages sat at
``queued`` behind a durable cursor that had advanced past them, delivered
messages were recorded ``failed/stale_unconfirmed`` while the CLI still held
them, and the instance log went silent at its rotation cap.  These tests pin
the three repairs: the cursor may not move past a deferred message, a failed
record may not stall the scan, and a log line is never dropped.
"""

import json
import pathlib
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from ciel_runtime_support.channel_pending_injection import (
    ChannelInjectionIO,
    ChannelInjectionPolicy,
    ChannelInjectionPrompts,
    ChannelInjectionServices,
    ChannelInjectionState,
    ChannelInjectionWakeStore,
    inject_pending_channel_messages,
)
from ciel_runtime_support.channel_runtime_environment import (
    ChannelRuntimeEnvironmentPolicy,
)
from ciel_runtime_support.channel_transcript import (
    ChannelWakeTranscriptServices,
    queued_dropped_from_text,
)
from ciel_runtime_support.channel_wake_delivery_repository import (
    ChannelWakeDeliveryRepository,
)
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository
from ciel_runtime_support.runtime_logging import LogLevelRepository, RouterFileLogger


def _message(message_id: int, *, channel: str = "external:default") -> dict:
    return {"id": message_id, "channel": channel, "message": f"body-{message_id}"}


class _RecordingRepository(ChannelWakeDeliveryRepository):
    """Delivery repository that also records the cursor commits."""

    def __init__(self) -> None:
        super().__init__(
            lock=threading.Lock(),
            delivered=set(),
            prompts={},
            batches={},
            clear_claim=lambda _message_id: None,
            commit_cursor=lambda _message_id: None,
        )
        self.commits: list[int] = []

    def commit(self, message_id: int) -> None:
        self.commits.append(message_id)


class _InjectionHarness:
    def __init__(self, messages: list[dict], *, claim_ids: set[int] | None = None) -> None:
        self.messages = {int(message["id"]): message for message in messages}
        self.claim_ids = set(claim_ids or set())
        self.delivery = _RecordingRepository()
        self.written: list[str] = []
        self.logs: list[tuple[str, str]] = []

    def services(self) -> ChannelInjectionServices:
        def read_messages(last_id: int, *_args, **_kwargs) -> list[dict]:
            return [
                self.messages[message_id]
                for message_id in sorted(self.messages)
                if message_id > last_id
            ]

        def claim_for_nonblocking_scan(message_id: int) -> bool:
            return message_id in self.claim_ids

        return ChannelInjectionServices(
            state=ChannelInjectionState(
                active_tool_call=lambda: False,
                active_turn=lambda: False,
                recover_cursor=lambda last_id: last_id,
                pending_scan_limit=lambda: 50,
                superseded_ids=lambda _candidates: set(),
                message_is_web_chat=lambda _message: False,
                message_skip_reason=lambda _message: "",
                event_identity_key=lambda _message: (),
                wake_state_for_message=lambda _message, _prompt=None: "missing",
                queued_wake_is_stale=lambda _message, _prompt=None: False,
            ),
            prompts=ChannelInjectionPrompts(
                llm_delivery=lambda messages: " ".join(str(m.get("message")) for m in messages),
                visible_llm_delivery=lambda messages: " ".join(str(m.get("message")) for m in messages),
                web_chat=lambda messages: " ".join(str(m.get("message")) for m in messages),
                standard=lambda messages: " ".join(str(m.get("message")) for m in messages),
                enter_bytes=lambda _value: b"\r",
                enter_label=lambda _value: "cr",
            ),
            wake_store=ChannelInjectionWakeStore(
                claim_for_nonblocking_scan=claim_for_nonblocking_scan,
                claim_prompt=lambda _message_id, _prompt: True,
                clear_claim=lambda _message_id: None,
                release_stale=lambda _message_id, _commit: None,
                lifecycle=self.delivery,
                commit_cursor=self.delivery.commit,
            ),
            io=ChannelInjectionIO(
                inject_lock=threading.Lock(),
                read_messages=read_messages,
                write_prompt=self._write_prompt,
                log=lambda level, message: self.logs.append((level, message)),
            ),
            policy=ChannelInjectionPolicy(
                wake_batch_limit=lambda: 1,
                replay_skip_reason=lambda _message: "",
            ),
        )

    def _write_prompt(self, _master_fd, prompt: str, *_args, **_kwargs) -> bool:
        self.written.append(prompt)
        return True


class DeferralFloorTests(unittest.TestCase):
    def test_cursor_stops_before_a_skipped_message(self) -> None:
        harness = _InjectionHarness([_message(10), _message(11)], claim_ids={10})

        returned = inject_pending_channel_messages(
            99,
            9,
            None,
            skip_blocking_wake_states=True,
            services=harness.services(),
        )

        self.assertEqual(harness.written, ["body-11"])
        # The scan reached id 11, but id 10 still has to be delivered, so the
        # durable cursor must stay below it - 9, not 11.
        self.assertEqual(harness.delivery.commits, [9])
        self.assertEqual(returned, 9)
        self.assertTrue(
            any("stdin_wake_claimed_continue" in message for _, message in harness.logs)
        )

    def test_cursor_advances_when_nothing_is_deferred(self) -> None:
        harness = _InjectionHarness([_message(20)])

        returned = inject_pending_channel_messages(
            99,
            19,
            None,
            skip_blocking_wake_states=True,
            services=harness.services(),
        )

        self.assertEqual(harness.delivery.commits, [20])
        self.assertEqual(returned, 20)


class FailedRecordTests(unittest.TestCase):
    def test_failed_message_does_not_stall_later_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = pathlib.Path(temp_dir) / "runtime-input-status.jsonl"
            status = RuntimeInputStatusRepository(
                status_path,
                publish_event=Mock(),
                log=lambda *_args: None,
                lock=threading.Lock(),
            )
            status.transition(30, "queued", data={"input_transport": "session_socket"})
            status.transition(30, "failed", reason="stale_unconfirmed")

            harness = _InjectionHarness([_message(30), _message(31)])
            harness.delivery.status = status

            inject_pending_channel_messages(99, 29, None, services=harness.services())

        self.assertEqual(harness.written, ["body-31"])
        self.assertEqual(harness.delivery.commits, [31])
        self.assertTrue(
            any("prior_submission_failed" in message for _, message in harness.logs)
        )


class QueuedStalePolicyTests(unittest.TestCase):
    def test_queued_is_not_stale_by_age(self) -> None:
        policy = ChannelRuntimeEnvironmentPolicy(environment={}, launch_recent_default=0.0)
        self.assertFalse(policy.inflight_is_stale("queued", 100.0, 100.0 + 3600.0, 180.0))
        self.assertTrue(policy.inflight_is_stale("unknown", 100.0, 100.0 + 181.0, 180.0))
        self.assertFalse(policy.inflight_is_stale("unknown", 100.0, 100.0 + 60.0, 180.0))


class QueuedDroppedEvidenceTests(unittest.TestCase):
    def _services(self) -> ChannelWakeTranscriptServices:
        return ChannelWakeTranscriptServices(
            claim_prompt=lambda _message_id: "",
            prompt_references_message_id=(
                lambda text, message_id, _prompts: f"id={message_id}" in str(text or "")
            ),
            prompt_message_ids=lambda _text: [],
            now=time.time,
        )

    def test_removed_queue_command_is_dropped(self) -> None:
        text = "\n".join(
            [
                json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "wake id=7"}),
                json.dumps({"type": "queue-operation", "operation": "remove", "content": "wake id=7"}),
            ]
        )
        self.assertTrue(queued_dropped_from_text(7, text, None, self._services()))

    def test_queued_command_without_removal_is_in_flight(self) -> None:
        text = json.dumps(
            {"type": "queue-operation", "operation": "enqueue", "content": "wake id=7"}
        )
        self.assertFalse(queued_dropped_from_text(7, text, None, self._services()))

    def test_re_enqueued_command_is_in_flight_again(self) -> None:
        text = "\n".join(
            [
                json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "wake id=7"}),
                json.dumps({"type": "queue-operation", "operation": "remove", "content": "wake id=7"}),
                json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "wake id=7"}),
            ]
        )
        self.assertFalse(queued_dropped_from_text(7, text, None, self._services()))

    def test_real_user_turn_clears_the_drop(self) -> None:
        text = "\n".join(
            [
                json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "wake id=7"}),
                json.dumps({"type": "queue-operation", "operation": "remove", "content": "wake id=7"}),
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": [{"type": "text", "text": "wake id=7"}]},
                    }
                ),
            ]
        )
        self.assertFalse(queued_dropped_from_text(7, text, None, self._services()))


class LateCompletionStatusTests(unittest.TestCase):
    def _status(self, path: pathlib.Path) -> RuntimeInputStatusRepository:
        return RuntimeInputStatusRepository(
            path, publish_event=Mock(), log=lambda *_args: None, lock=threading.Lock()
        )

    def test_failed_record_is_corrected_by_a_late_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status = self._status(pathlib.Path(temp_dir) / "status.jsonl")
            status.transition(41, "queued")
            status.transition(41, "submitted")
            status.transition(41, "failed", reason="stale_unconfirmed")

            record = status.transition(41, "replied")

            self.assertEqual(record["status"], "replied")
            self.assertEqual(status.get(41)["status"], "replied")

    def test_replied_record_stays_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status = self._status(pathlib.Path(temp_dir) / "status.jsonl")
            status.transition(42, "queued")
            status.transition(42, "submitted")
            status.transition(42, "replied")

            status.transition(42, "failed", reason="stale_unconfirmed")

            self.assertEqual(status.get(42)["status"], "replied")


class RouterLogRotationTests(unittest.TestCase):
    def _logger(self, path: pathlib.Path, max_bytes: int) -> RouterFileLogger:
        levels = LogLevelRepository(
            config_dir=path.parent,
            path=path.parent / "log-level",
            cache={"value": None, "checked_at": 0.0, "file_mtime": 0.0},
            default_level=3,
            environ={},
        )
        return RouterFileLogger(path.parent, path, max_bytes, levels)

    def test_line_survives_a_failed_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            path = root / "router.log"
            path.write_text("x" * 64, encoding="utf-8")
            # A directory at the rotation target makes the plain replace fail,
            # standing in for the Windows handle that froze instance 9469.
            (root / "router.log.1").mkdir()
            logger = self._logger(path, max_bytes=16)

            logger.write("INFO", "survives")

            self.assertIn("survives", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
