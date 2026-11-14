from pathlib import Path
import json
import subprocess
import sys

import pytest

from ciel_runtime_support.work_assignment_key_coordinator import (
    AssignmentKeyCoordinatorError,
    P0StoreDependencyReader,
)
from ciel_runtime_support.work_parallel_p0 import WorkParallelP0Store


def facts():
    return {
        "scope": "scope",
        "root_id": "root",
        "root_created_at": 1.0,
        "item_id": "item",
        "continuation": "initial",
        "predecessor_lease_id": None,
    }


def activate(store):
    state = store.read()
    state, _ = store.activate_root(
        "scope", "root", 1.0,
        set_by="user", expected_version=state["version"], now=1.0,
    )
    return state


def test_concrete_p0_creator_publishes_key_before_validated_intent(tmp_path):
    ledger = tmp_path / "work-parallel.json"
    store = WorkParallelP0Store.beside(ledger)
    state = activate(store)
    after, intent = store.create_intent(facts(), expected_version=state["version"], now=2.0)
    assert intent["state"] == "intent"
    assert after["intents"][intent["intent_id"]] == intent
    key = tmp_path / "work-p0-assignment.key"
    assert len(key.read_text(encoding="ascii")) == 64


def test_missing_key_with_prior_p0_authority_refuses_recreation(tmp_path):
    ledger = tmp_path / "work-parallel.json"
    store = WorkParallelP0Store.beside(ledger)
    state = activate(store)
    after, first = store.create_intent(facts(), expected_version=state["version"], now=2.0)
    (tmp_path / "work-p0-assignment.key").unlink()
    second = dict(facts())
    second["item_id"] = "item-2"
    with pytest.raises(AssignmentKeyCoordinatorError, match="recovery required"):
        store.create_intent(second, expected_version=after["version"], now=3.0)
    current = store.read()
    assert set(current["intents"]) == {first["intent_id"]}
    assert not (tmp_path / "work-p0-assignment.key").exists()


def test_creator_rejects_wrong_p0_store_identity(tmp_path):
    ledger = tmp_path / "work-parallel.json"
    store = WorkParallelP0Store(tmp_path / "alternate-p0.json")
    reader = P0StoreDependencyReader(store)
    assert reader.lock_target == Path(store.path)
    with pytest.raises(Exception):
        store.create_intent(facts(), expected_version=0, now=2.0, ledger_path=ledger)


def test_real_p0_creator_race_is_exactly_once(tmp_path):
    script = tmp_path / "creator.py"
    src = Path(__file__).resolve().parents[1]
    script.write_text(
        """import json,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom ciel_runtime_support.work_parallel_p0 import WorkParallelP0Store\nbase=Path(sys.argv[2]); store=WorkParallelP0Store.beside(base/'work-parallel.json')\nstate=store.read()\ntry:\n state,_=store.activate_root('scope','root',1.0,set_by='user',expected_version=state['version'],now=1.0)\nexcept Exception:\n state=store.read()\nf={'scope':'scope','root_id':'root','root_created_at':1.0,'item_id':'item','continuation':'initial','predecessor_lease_id':None}\nfor _ in range(50):\n try:\n  after,row=store.create_intent(f,expected_version=state['version'],now=2.0); print(json.dumps({'id':row['intent_id'],'version':after['version']})); break\n except Exception:\n  state=store.read()\nelse: raise SystemExit(2)\n""",
        encoding="utf-8",
    )
    processes = [
        subprocess.Popen(
            [sys.executable, str(script), str(src), str(tmp_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(2)
    ]
    rows = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        rows.append(json.loads(stdout))
    assert len({row["id"] for row in rows}) == 1
    state = WorkParallelP0Store.beside(tmp_path / "work-parallel.json").read()
    assert len(state["intents"]) == 1
    assert len((tmp_path / "work-p0-assignment.key").read_text()) == 64
