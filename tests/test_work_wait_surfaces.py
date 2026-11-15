from ciel_runtime_support.work_wait_surfaces import (
    ctl_projection, menu_projection, rest_projection, web_projection,
)


def test_four_surfaces_have_identical_privacy_projection():
    source = {
        "eligible": True, "ordinary_allowed": False, "behavior": "boosted",
        "reason": None, "item_id": "item", "title": "Readable item title", "root_id": "root", "priority": "P2",
        "mixed_version_best_effort": True, "mixed_version_warning": "older scheduler",
        "consume_pin": {"secret": 1}, "lease_id": "secret", "agent_id": "secret",
        "dispatch_name": "secret", "operation_digest": "secret", "worker_token": "secret",
    }
    rows = [fn(source) for fn in (menu_projection, web_projection, rest_projection, ctl_projection)]
    assert rows == [rows[0]] * 4
    assert "secret" not in repr(rows)
    assert rows[0]["title"] == "Readable item title"


def test_projection_is_bounded_and_fail_closed():
    row = menu_projection({"eligible": "yes", "reason": "x" * 1000})
    assert row["eligible"] is False
    assert len(row["reason"]) == 500
