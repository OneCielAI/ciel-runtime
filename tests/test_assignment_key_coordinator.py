from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import pytest
except ImportError:  # unittest group runner without pytest installed
    import unittest
    raise unittest.SkipTest("pytest is not installed")

from ciel_runtime_support.work_assignment_key_coordinator import (
    AssignmentKeyCoordinator,
    AssignmentKeyCoordinatorError,
    capability_derivation_digest,
    derive_wait_capability,
    is_assignment_key_secret,
    p0_dependencies,
    wait_dependencies,
)
from ciel_runtime_support.work_p0_assignment_key import P0AssignmentKeyStore


class Reader:
    def __init__(self, lock_target, dependency=False, callback=None):
        self.lock_target = Path(lock_target)
        self.dependency = dependency
        self.callback = callback

    def has_dependency_locked(self):
        if self.callback:
            self.callback()
        return self.dependency


def coordinator(tmp_path, *, p0=False, wait=False, p0_callback=None, wait_callback=None):
    return AssignmentKeyCoordinator(
        tmp_path / "work-parallel.json",
        p0_reader=Reader(tmp_path / "work-parallel-p0.json", p0, p0_callback),
        wait_reader=Reader(
            tmp_path / "work-parallel-wait-boost-v1.json", wait, wait_callback
        ),
    )


def manifest():
    attempt = "1" * 32
    return {
        "scope": "scope",
        "root_id": "root",
        "root_created_at": 1.5,
        "item_id": "item",
        "attempt_id": attempt,
        "lease_id": "lease",
        "assignment_generation": 7,
        "dispatch_name": "work-wait-boost-" + attempt,
        "purpose": "worker_report",
    }


def test_zero_dependencies_create_once(tmp_path):
    first = coordinator(tmp_path).acquire_for_dependency()
    second = coordinator(tmp_path).acquire_for_dependency()
    assert first.state == second.state == "ready"
    assert first.key == second.key and len(first.key) == 32


@pytest.mark.parametrize("p0,wait", [(True, False), (False, True), (True, True)])
def test_missing_key_with_dependency_never_recreates(tmp_path, p0, wait):
    result = coordinator(tmp_path, p0=p0, wait=wait).acquire_for_dependency()
    assert result.state == "recovery_required" and result.reason == "missing"
    assert result.key is None
    assert not (tmp_path / "work-p0-assignment.key").exists()


def test_status_is_read_only_and_returns_no_key(tmp_path):
    result = coordinator(tmp_path).status()
    assert (result.state, result.reason, result.key) == (
        "not_required", None, None
    )
    assert not (tmp_path / "work-p0-assignment.key").exists()
    assert not (tmp_path / "work-assignment-key.creation.lock").exists()


def test_status_existing_key_without_dependency_is_not_required(tmp_path):
    created = coordinator(tmp_path).acquire_for_dependency()
    result = coordinator(tmp_path).status()
    assert result.state == "not_required" and result.key is None
    assert created.key.hex() not in repr(result)
    assert created.key.hex() not in json.dumps(result.public())


def test_status_existing_key_with_dependency_is_ready_without_key(tmp_path):
    created = coordinator(tmp_path).acquire_for_dependency()
    result = coordinator(tmp_path, p0=True).status()
    assert result.state == "ready" and result.key is None
    assert created.key.hex() not in repr(result)


def test_lock_order_exact(tmp_path, monkeypatch):
    import ciel_runtime_support.work_assignment_key_coordinator as module
    entered = []

    class Lock:
        def __init__(self, target): self.target = Path(target)
        def __enter__(self): entered.append(self.target.name)
        def __exit__(self, *_): return False

    monkeypatch.setattr(module, "exclusive_file_lock", lambda target: Lock(target))
    monkeypatch.setattr(P0AssignmentKeyStore, "read", lambda self: bytes.fromhex("a" * 64))
    assert coordinator(tmp_path).acquire_for_dependency().state == "ready"
    assert entered == [
        "work-assignment-key.creation",
        "work-parallel-p0.json",
        "work-parallel-wait-boost-v1.json",
        "work-p0-assignment.key",
    ]
    assert coordinator(tmp_path).physical_creation_lock_path.name == "work-assignment-key.creation.lock"


def test_status_lock_order_excludes_creation(tmp_path, monkeypatch):
    import ciel_runtime_support.work_assignment_key_coordinator as module
    entered = []

    class Lock:
        def __init__(self, target): self.target = Path(target)
        def __enter__(self): entered.append(self.target.name)
        def __exit__(self, *_): return False

    monkeypatch.setattr(module, "exclusive_file_lock", lambda target: Lock(target))
    monkeypatch.setattr(P0AssignmentKeyStore, "read", lambda self: bytes.fromhex("a" * 64))
    assert coordinator(tmp_path, p0=True).status().state == "ready"
    assert entered == [
        "work-parallel-p0.json",
        "work-parallel-wait-boost-v1.json",
        "work-p0-assignment.key",
    ]


def test_reader_targets_exact(tmp_path):
    with pytest.raises(AssignmentKeyCoordinatorError, match="lock target"):
        AssignmentKeyCoordinator(
            tmp_path / "work-parallel.json",
            p0_reader=Reader(tmp_path / "work-parallel-p0-v1.json"),
            wait_reader=Reader(tmp_path / "work-parallel-wait-boost-v1.json"),
        )


def test_key_store_path_is_not_caller_selectable(tmp_path):
    with pytest.raises(AssignmentKeyCoordinatorError, match="key path"):
        AssignmentKeyCoordinator(
            tmp_path / "work-parallel.json",
            p0_reader=Reader(tmp_path / "work-parallel-p0.json"),
            wait_reader=Reader(tmp_path / "work-parallel-wait-boost-v1.json"),
            key_store=P0AssignmentKeyStore(tmp_path / "alternate.key"),
        )


def test_dependency_state_coverage():
    for state in (
        "intent", "reserving", "reserved", "workspace_preparing",
        "workspace_attached", "spawning", "bound", "recovery_required",
    ):
        assert p0_dependencies({"intents": {"i": {"state": state}}, "sends": {}})
    for state in ("terminal", "cancelled"):
        assert not p0_dependencies({"intents": {"i": {"state": state}}, "sends": {}})
    for state in ("prepared", "slot_acquired", "uncertain"):
        assert p0_dependencies({"intents": {}, "sends": {"s": {"state": state}}})
    assert wait_dependencies({
        "rows": {"r": {"state": "consuming"}}, "bind_sagas": {},
        "workspaces": {}, "cleanups": {},
    })
    assert not wait_dependencies({
        "rows": {"r": {"state": "consumed"}},
        "bind_sagas": {"b": {"state": "done"}},
        "workspaces": {"w": {"state": "rolled_back"}},
        "cleanups": {"c": {"state": "done"}},
    })


def test_hmac_exact_vectors():
    key = bytes.fromhex("00" * 31 + "01")
    value = manifest()
    assert derive_wait_capability(key, value) == "3e012f508ec51660ce85a8568608b7060b2b70850baac92df068abb5cf06956b"
    assert capability_derivation_digest(value) == "e82cf47f5595b299a376e9ed262c424c2c0e951eddc86e45204d4bd453d4804c"


def test_backup_classifier():
    assert is_assignment_key_secret("work-p0-assignment.key")
    assert not is_assignment_key_secret("work-wait-assignment.key")


def test_generic_persist_callback_api_is_not_exposed(tmp_path):
    assert not hasattr(coordinator(tmp_path), "acquire_and_persist_dependency")


@pytest.mark.skip(reason="superseded by real concrete cross-feature creator race")
def test_process_race_actual_feature_intents(tmp_path):
    script = tmp_path / "race.py"
    src = Path(__file__).resolve().parents[1]
    script.write_text(
        """import json,sys,time\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom ciel_runtime_support.work_assignment_key_coordinator import AssignmentKeyCoordinator\nclass R:\n def __init__(self,p): self.lock_target=Path(p)\n def has_dependency_locked(self): return self.lock_target.exists()\nbase=Path(sys.argv[2]); feature=sys.argv[3]; hold=sys.argv[4]=='hold'\np=R(base/'work-parallel-p0.json'); w=R(base/'work-parallel-wait-boost-v1.json')\nc=AssignmentKeyCoordinator(base/'work-parallel.json',p0_reader=p,wait_reader=w)\ndef persist():\n target=p.lock_target if feature=='p0' else w.lock_target\n target.write_text(feature)\n if hold:\n  (base/'ready').write_text(feature)\n  while not (base/'release').exists(): time.sleep(.01)\nr=c.acquire_and_persist_dependency(persist)\nprint(json.dumps({'state':r.state,'key':None if r.key is None else r.key.hex()}))\n""",
        encoding="utf-8",
    )
    for first_feature, second_feature in (("p0", "wait"), ("wait", "p0")):
        for name in ("work-parallel-p0.json", "work-parallel-wait-boost-v1.json", "work-p0-assignment.key", "ready", "release"):
            (tmp_path / name).unlink(missing_ok=True)
        first = subprocess.Popen(
            [sys.executable, str(script), str(src), str(tmp_path), first_feature, "hold"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.time() + 5
        while not (tmp_path / "ready").exists():
            assert first.poll() is None and time.time() < deadline
            time.sleep(.01)
        second = subprocess.Popen(
            [sys.executable, str(script), str(src), str(tmp_path), second_feature, "go"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert second.poll() is None
        (tmp_path / "release").write_text("go")
        rows = []
        for process in (first, second):
            out, err = process.communicate(timeout=10)
            assert process.returncode == 0, err
            rows.append(json.loads(out))
        assert {row["state"] for row in rows} == {"ready"}
        assert len({row["key"] for row in rows}) == 1
        assert (tmp_path / "work-parallel-p0.json").exists()
        assert (tmp_path / "work-parallel-wait-boost-v1.json").exists()


def test_thread_race_converges(tmp_path):
    results = []
    threads = [threading.Thread(target=lambda: results.append(coordinator(tmp_path).acquire_for_dependency())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    assert len(results) == 8 and len({row.key for row in results}) == 1


def test_reader_crash_releases_creation_fence(tmp_path):
    calls = 0
    def crash():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("reader crash")
    with pytest.raises(RuntimeError, match="reader crash"):
        coordinator(tmp_path, p0_callback=crash).acquire_for_dependency()
    assert coordinator(tmp_path).acquire_for_dependency().state == "ready"


def test_invalid_existing_key_never_mutated(tmp_path):
    path = tmp_path / "work-p0-assignment.key"
    path.write_text("INVALID", encoding="ascii")
    before = path.read_bytes()
    result = coordinator(tmp_path).acquire_for_dependency()
    assert result.state == "recovery_required" and result.reason == "invalid_bytes"
    assert path.read_bytes() == before
