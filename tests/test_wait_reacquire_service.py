from __future__ import annotations

import copy

from ciel_runtime_support.work_wait_reacquire_service import WaitReacquireService
from test_wait_boost_sidecar import boost_key, consuming


class Ledger:
    def __init__(self, lease):
        self.state = {"version": 1, "leases": {lease["id"]: copy.deepcopy(lease)}}
        self.calls = []

    def read(self):
        return copy.deepcopy(self.state)

    def reacquire(self, ident, *, expected_version):
        self.calls.append("reacquire")
        assert expected_version == self.state["version"]
        self.state["version"] += 1
        self.state["leases"][ident]["slot"] = True
        return self.read()

    def rollback_wait_reacquire(
        self, ident, *, attempt_id, slot_acquired_version, expected_version
    ):
        assert slot_acquired_version == expected_version == self.state["version"]
        self.calls.append("rollback")
        self.state["version"] += 1
        self.state["leases"][ident]["slot"] = False
        return self.read()


class Wait:
    def __init__(self, row):
        self.state = {"version": 1, "rows": {boost_key("scope", "item"): copy.deepcopy(row)}}

    def read(self):
        return copy.deepcopy(self.state)

    def change(self, expected, mutate, *, now):
        assert expected == self.state["version"]
        result = mutate(self.state)
        self.state["version"] += 1
        return self.read(), copy.deepcopy(result)


def fixture():
    row = consuming(kind="reacquire")
    row["ledger_before_version"] = 1
    row["lease_id"] = "lease"
    row["send_state"] = "pending"
    lease = {
        "id": "lease", "state": "waiting_input", "slot": False,
        "host_id": "host", "priority": "P1", "item_id": "item",
    }
    return row, lease


def test_reacquire_slot_commit_and_response_loss_reconcile():
    row, lease = fixture()
    ledger, wait = Ledger(lease), Wait(row)
    service = WaitReacquireService(ledger, wait)
    state, acquired = service.acquire_slot(
        scope="scope", item_id="item", attempt_id=row["attempt_id"],
        wait_expected_version=1, now=3.0,
    )
    assert acquired["send_state"] == "send_pending"
    assert ledger.calls == ["reacquire"]
    # Lost sidecar response replay sees exact ledger commit and does not reacquire.
    wait.state["version"] = 1
    wait.state["rows"][boost_key("scope", "item")].update(
        send_state="pending", slot_acquired_version=None, slot_acquire_digest=None
    )
    service.acquire_slot(
        scope="scope", item_id="item", attempt_id=row["attempt_id"],
        wait_expected_version=1, now=4.0,
    )
    assert ledger.calls == ["reacquire"]


def test_old_failed_send_cannot_rollback_later_ledger_generation():
    row, lease = fixture()
    row.update(send_state="send_pending", slot_acquired_version=2, slot_acquire_digest="a" * 64)
    lease["slot"] = True
    ledger, wait = Ledger(lease), Wait(row)
    ledger.state["version"] = 3
    import pytest
    with pytest.raises(Exception, match="version changed"):
        WaitReacquireService(ledger, wait).classify_send(
            scope="scope", item_id="item", attempt_id=row["attempt_id"],
            delivered=False, wait_expected_version=1, now=5.0,
        )
    assert ledger.calls == [] and ledger.state["leases"]["lease"]["slot"] is True


def test_send_true_false_unknown_classification():
    for delivered, expected, calls in ((True, "delivered", []), (False, "rolled_back", ["rollback"]), (None, "uncertain", [])):
        row, lease = fixture()
        row.update(send_state="send_pending", slot_acquired_version=2, slot_acquire_digest="a" * 64)
        lease["slot"] = True
        ledger, wait = Ledger(lease), Wait(row)
        ledger.state["version"] = 2
        result, _ = WaitReacquireService(ledger, wait).classify_send(
            scope="scope", item_id="item", attempt_id=row["attempt_id"],
            delivered=delivered, wait_expected_version=1, now=5.0,
        )
        assert result == expected
        assert ledger.calls == calls
