import pytest

from ciel_runtime_support.work_parallel_wait_boost import WaitBoostError
from ciel_runtime_support.work_wait_host_api import PUBLIC_FIELDS, WaitHostApi


class Orchestrator:
    def __init__(self):
        self.calls = []
    def reserve(self, **args):
        self.calls.append(("reserve", args))
        return {"pin": "secret"}
    def workspace_and_bind(self, **args):
        self.calls.append(("bind", args))
        return {"ok": 1}
    def reacquire_slot(self, **args):
        self.calls.append(("slot", args))
        return {"ok": 1}
    def reacquire_result(self, **args):
        self.calls.append(("result", args))
        return {"ok": 1}
    def cleanup_to_done(self, **args):
        self.calls.append(("cleanup", args))
        return {"ok": 1}


def row(item, *, state="boosted", parked=None, release=1.0, receipt="a"):
    return {"item_id": item, "root_id": "root", "state": state,
            "parked_lease_id": parked, "release_at": release,
            "release_receipt": receipt * 64}


def test_selector_orders_p0_parked_other_ordinary_and_filters_stale():
    items = {name: {"done": False, "waiting_on": ""} for name in ("a", "b", "c")}
    p0 = {"item_id": "p0"}
    api = WaitHostApi(Orchestrator())
    assert api.select(p0=p0, rows=[], board_items=items, ordinary_allowed=True)["kind"] == "p0"
    rows = [row("b", parked=None, release=5), row("a", parked="lease", release=2)]
    selected = api.select(p0=None, rows=rows, board_items=items, ordinary_allowed=True)
    assert selected["candidate"]["item_id"] == "a"
    items["a"]["done"] = True
    selected = api.select(p0=None, rows=rows, board_items=items, ordinary_allowed=True)
    assert selected["candidate"]["item_id"] == "b"
    items["b"]["waiting_on"] = "blocked"
    assert api.select(p0=None, rows=rows, board_items=items, ordinary_allowed=True)["kind"] == "ordinary"
    assert api.select(p0=None, rows=rows, board_items=items, ordinary_allowed=False)["kind"] == "none"


def test_selector_cancels_stale_rows_durably_through_port():
    stale = []
    api = WaitHostApi(Orchestrator(), cancel_stale=lambda row, reason: stale.append((row["item_id"], reason)))
    rows = [row("missing"), row("done"), row("blocked")]
    items = {
        "done": {"done": True, "waiting_on": ""},
        "blocked": {"done": False, "waiting_on": "again"},
    }
    assert api.select(p0=None, rows=rows, board_items=items, ordinary_allowed=False)["kind"] == "none"
    assert stale == [("missing", "identity_mismatch"), ("done", "terminal"), ("blocked", "reblocked")]


def test_selector_consuming_is_launch_pending_and_planning_ineligible():
    items = {"a": {"done": False, "waiting_on": ""}}
    api = WaitHostApi(Orchestrator())
    selected = api.select(
        p0=None, rows=[row("a", state="consuming")], board_items=items,
        ordinary_allowed=True,
    )
    assert selected["candidate"]["state"] == "consuming"
    assert api.select(
        p0=None, rows=[row("a")], board_items=items,
        ordinary_allowed=True, planning_roots=("root",),
    )["kind"] == "ordinary"


def test_host_auth_schema_dispatch_and_public_privacy():
    orchestrator = Orchestrator()
    api = WaitHostApi(orchestrator)
    args = {
        "scope": "scope", "item_id": "item", "attempt_id": "1" * 32,
        "candidate": {}, "ledger_expected_version": 1,
        "wait_expected_version": 1, "now": 1.0,
    }
    response = api.dispatch({
        "event": "reserve", "args": args,
        "_host": {"internal": True, "scope": "scope"},
    })
    assert response["ok"] and orchestrator.calls == [("reserve", args)]
    assert "secret" not in repr(response)
    for bad in (
        {"event": "reserve", "args": {}, "_host": {"internal": False, "scope": "s"}},
        {"event": "unknown", "args": {}, "_host": {"internal": True, "scope": "s"}},
        {"event": "reserve", "args": {}, "_host": {"internal": True, "scope": "s"}, "extra": 1},
        {"event": "reserve", "args": {**args, "scope": "other"}, "_host": {"internal": True, "scope": "scope"}},
    ):
        with pytest.raises(WaitBoostError):
            api.dispatch(bad)
    public = api.public_status({
        "eligible": True, "behavior": "boosted", "pin": {"secret": 1},
        "lease_id": "secret", "operation_digest": "secret", "item_id": "item",
    })
    assert set(public) == set(PUBLIC_FIELDS)
    assert "secret" not in repr(public)
