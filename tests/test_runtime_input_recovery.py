import json
import tempfile
import unittest
import threading
from unittest.mock import patch
from pathlib import Path

from ciel_runtime_support.runtime_input_recovery import (
    OfflineHistoryRecovery,
    RecoveryRefused,
    recovery_generation,
)


class OfflineHistoryRecoveryTests(unittest.TestCase):
    def make(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        state = root / "state"
        state.mkdir()
        registry = root / "registry.json"
        workspace = root / "workspace"
        workspace.mkdir()
        digest = __import__(
            "ciel_runtime_support.workspace_router_selection",
            fromlist=["workspace_digest"],
        ).workspace_digest(workspace)
        instance = root / "router-instances" / f"9464-{digest}"
        (instance / "router-clients").mkdir(parents=True)
        (instance / "router.pid").write_text("101")
        (instance / "router-clients" / "202.json").write_text(json.dumps({"pid": 202}))
        registry.write_text(
            json.dumps(
                {
                    "workspaces": {
                        digest: {
                            "workspace": str(workspace),
                            "port": 9464,
                            "launch_lease": {
                                "pid": 303,
                                "identity": "x",
                                "expires_at": 1,
                            },
                        }
                    }
                }
            )
        )
        def dead(pid):
            return ""
        recovery = OfflineHistoryRecovery(
            state,
            registry,
            workspace,
            process_identity=dead,
            port_available=lambda port: True,
            process_inventory=lambda: [],
        )
        (state / "runtime-inputs.jsonl").write_bytes(b'{"id":41,"message":"old"}\n')
        (state / "runtime-input-status.jsonl").write_bytes(
            b'{"request_id":41,"status":"queued"}\n'
        )
        return root, recovery

    def test_preview_is_read_only_and_contains_both_fingerprints(self):
        root, recovery = self.make()
        before = {p: p.read_bytes() for p in (recovery.queue, recovery.status)}
        preview = recovery.preview()
        self.assertEqual(preview["max_known_id"], 41)
        self.assertEqual(preview["known_pending"]["queued"], 1)
        self.assertEqual(
            preview["files"]["runtime-inputs.jsonl"]["size"],
            len(before[recovery.queue]),
        )
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(preview["offline"]["verified_stopped"], True)

    def test_confirmation_requires_explicit_data_change_and_quarantines_without_replay(
        self,
    ):
        root, recovery = self.make()
        preview = recovery.preview()
        with self.assertRaises(RecoveryRefused):
            recovery.confirm(preview["confirmation_token"])
        result = recovery.confirm(
            preview["confirmation_token"], quarantine_old_inputs=True
        )
        self.assertEqual(result["replayed_old_inputs"], 0)
        self.assertTrue((Path(result["archive"]) / "runtime-inputs.jsonl").exists())
        self.assertEqual(recovery.queue.read_bytes(), b"")
        self.assertEqual(recovery.status.read_bytes(), b"")
        generation = recovery_generation(recovery.queue)
        self.assertEqual(generation["id_floor"], 42)

    def append(self, recovery):
        from ciel_runtime_support.channel_message_repository import (
            ChannelMessageRepository,
            ChannelMessageAppendPorts,
            exclusive_file_lock,
        )

        repository = ChannelMessageRepository(recovery.queue, lambda *a: None)
        ports = ChannelMessageAppendPorts(
            threading.Condition(),
            lambda: exclusive_file_lock(recovery.queue),
            lambda message: None,
            lambda value: [],
        )
        return repository.append({"message": "new"}, ports)

    def statuses(self, recovery):
        from ciel_runtime_support.runtime_input_status import (
            RuntimeInputStatusRepository,
        )

        return RuntimeInputStatusRepository(
            recovery.status, lambda **kw: None, lambda *a: None, threading.RLock()
        )

    def test_new_gateway_input_gets_high_id_and_only_new_ownership(self):
        from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway

        root, recovery = self.make()
        # A malformed NUL prefix remains untrusted; its recognizable numeric
        # ID still raises the new generation floor conservatively.
        recovery.status.write_bytes(
            bytes([0]) + b'{"request_id":900,"status":"submitted"}' + bytes([10])
        )
        recovery.queue.with_suffix(".jsonl.1").write_text(
            json.dumps({"id": 1000}) + chr(10)
        )
        before = recovery.status.read_bytes()
        preview = recovery.preview()
        self.assertFalse(preview["counts_complete"])
        self.assertEqual(preview["max_known_id"], 1000)
        result = recovery.confirm(
            preview["confirmation_token"], quarantine_old_inputs=True
        )
        self.assertEqual(
            (Path(result["archive"]) / recovery.status.name).read_bytes(), before
        )
        status = self.statuses(recovery)
        gateway = RuntimeInputGateway(
            append=lambda payload: self.append(recovery), status=status
        )
        message = gateway._append({"message": "new"})
        self.assertEqual(message["id"], 1001)
        self.assertEqual(message["_ciel_runtime_generation"], result["generation"])
        self.assertEqual(status.get(1001)["status"], "queued")
        attempt = status.begin_attempt([1001], "fresh-child")
        self.assertTrue(attempt)
        self.assertEqual(status.get(1001)["status"], "submitted")
        with self.assertRaises(RecoveryRefused):
            status.begin_attempt([900], "fresh-child")
        with self.assertRaises(RecoveryRefused):
            status.transition(900, "queued")
        self.assertEqual(len(status.list_latest()), 1)
        self.assertEqual(self.append(recovery)["id"], 1002)

    def test_stale_preview_rejects_both_changed_files(self):
        for which in ("queue", "status"):
            root, recovery = self.make()
            preview = recovery.preview()
            path = getattr(recovery, which)
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaises(RecoveryRefused):
                recovery.confirm(
                    preview["confirmation_token"], quarantine_old_inputs=True
                )
            self.assertFalse((recovery.state_dir / "runtime-input-quarantine").exists())

    def test_live_and_reused_pids_refuse_even_with_expired_lease(self):
        for identity in (None, "reused-birth-token", "live-birth-token"):
            root, recovery = self.make()
            recovery.process_identity = lambda pid: identity
            with self.assertRaises(RecoveryRefused):
                recovery.confirm(
                    recovery.preview()["confirmation_token"], quarantine_old_inputs=True
                )
            self.assertIn(b"old", recovery.queue.read_bytes())

    def test_busy_registry_and_occupied_endpoint_refuse(self):
        root, recovery = self.make()
        token = recovery.preview()["confirmation_token"]
        lock = recovery.registry.with_suffix(".json.lock")
        lock.write_text("123")
        with self.assertRaises(RecoveryRefused):
            recovery.confirm(token, quarantine_old_inputs=True)
        self.assertEqual(lock.read_text(), "123")
        lock.unlink()
        recovery.port_available = lambda port: False
        with self.assertRaises(RecoveryRefused):
            recovery.confirm(token, quarantine_old_inputs=True)

    def test_crash_blocks_append_admission_and_explicit_resume_finishes(self):
        from ciel_runtime_support import runtime_input_recovery as module

        root, recovery = self.make()
        token = recovery.preview()["confirmation_token"]
        original = module._durable

        def crash(path, data, **kw):
            if path == recovery.status and data == b"":
                raise OSError("fixture interruption")
            return original(path, data, **kw)

        with patch.object(module, "_durable", side_effect=crash):
            with self.assertRaises(OSError):
                recovery.confirm(token, quarantine_old_inputs=True)
        with self.assertRaises(RecoveryRefused):
            self.append(recovery)
        with self.assertRaises(RecoveryRefused):
            self.statuses(recovery).begin_attempt([42], "new")
        with self.assertRaises(RecoveryRefused):
            recovery.resume_prepared(token)
        result = recovery.resume_prepared(token, quarantine_old_inputs=True)
        self.assertEqual(result["id_floor"], 42)
        self.assertEqual(self.append(recovery)["id"], 42)

    def test_backup_failure_leaves_originals_untouched(self):
        from ciel_runtime_support import runtime_input_recovery as module

        root, recovery = self.make()
        token = recovery.preview()["confirmation_token"]
        before = recovery.queue.read_bytes(), recovery.status.read_bytes()
        with patch.object(module, "_durable", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                recovery.confirm(token, quarantine_old_inputs=True)
        self.assertEqual(
            (recovery.queue.read_bytes(), recovery.status.read_bytes()), before
        )

    def test_second_recovery_keeps_id_floor_and_archives_old_marker(self):
        root, recovery = self.make()
        first = recovery.confirm(
            recovery.preview()["confirmation_token"], quarantine_old_inputs=True
        )
        self.assertEqual(self.append(recovery)["id"], 42)
        second = recovery.confirm(
            recovery.preview()["confirmation_token"], quarantine_old_inputs=True
        )
        self.assertNotEqual(first["generation"], second["generation"])
        self.assertEqual(second["id_floor"], 43)
        self.assertTrue((Path(second["archive"]) / "previous-generation.json").exists())

    def test_unregistered_runtime_process_refuses(self):
        root, recovery = self.make()
        recovery.process_inventory = lambda: [{"pid": 999, "name": "codex"}]
        with self.assertRaises(RecoveryRefused):
            recovery.confirm(
                recovery.preview()["confirmation_token"], quarantine_old_inputs=True
            )

    def test_unknown_owner_refuses(self):
        root, recovery = self.make()
        recovery.process_identity = lambda pid: None
        self.assertFalse(recovery.preview()["offline"]["verified_stopped"])
        with self.assertRaises(RecoveryRefused):
            recovery.confirm(
                recovery.preview()["confirmation_token"], quarantine_old_inputs=True
            )


if __name__ == "__main__":
    unittest.main()
