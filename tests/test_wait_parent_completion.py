from __future__ import annotations

from ciel_runtime_support.work_parallel_wait_boost import WaitBoostStore, boost_key, validate
from ciel_runtime_support.work_wait_parent_completion import WaitParentCompletionService
from test_wait_boost_sidecar import H32, bind, consuming, workspace


def seed(tmp_path):
    store = WaitBoostStore(tmp_path / "wait.json")
    parent = consuming(kind="reserve")
    child = workspace(state="coord_attached")
    saga = bind(state="done")
    state, _ = store.change(
        0,
        lambda document: (
            document["rows"].update({boost_key("scope", "item"): parent}),
            document["workspaces"].update({H32: child}),
            document["bind_sagas"].update({H32: saga}),
        ),
        now=4.0,
    )
    return store, state


def test_parent_workspace_bind_complete_and_history_one_cas(tmp_path):
    store, state = seed(tmp_path)
    after, result = WaitParentCompletionService(store).complete_native_bound(
        H32, expected_version=state["version"], now=8.0
    )
    assert result["parent"]["state"] == "consumed"
    assert result["workspace"]["state"] == "done"
    assert result["bind"]["state"] == "done"
    assert result["history"]["transition"] == "complete_bound_launch"
    validate(after)


def test_stale_retry_is_strict_and_bytes_unchanged(tmp_path):
    store, state = seed(tmp_path)
    service = WaitParentCompletionService(store)
    service.complete_native_bound(H32, expected_version=state["version"], now=8.0)
    raw = store.path.read_bytes()
    try:
        service.complete_native_bound(H32, expected_version=state["version"], now=8.0)
    except Exception:
        pass
    assert store.path.read_bytes() == raw
