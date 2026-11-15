"""One privacy-safe wait-boost projection shared by all four product surfaces."""
from __future__ import annotations

import copy
from typing import Any, Mapping

SAFE_FIELDS = (
    "eligible", "ordinary_allowed", "behavior", "reason", "item_id", "title",
    "root_id", "priority", "mixed_version_best_effort",
    "mixed_version_warning",
)


def project_wait_status(value: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("wait status must be an object")
    projected = {name: copy.deepcopy(value.get(name)) for name in SAFE_FIELDS}
    if type(projected["eligible"]) is not bool:
        projected["eligible"] = False
    if type(projected["ordinary_allowed"]) is not bool:
        projected["ordinary_allowed"] = False
    if type(projected["mixed_version_best_effort"]) is not bool:
        projected["mixed_version_best_effort"] = False
    for name in ("behavior", "reason", "item_id", "title", "root_id", "priority", "mixed_version_warning"):
        value = projected[name]
        if value is not None and type(value) is not str:
            projected[name] = None
        elif type(value) is str:
            projected[name] = value[:500]
    return projected


def menu_projection(value):
    return project_wait_status(value)


def web_projection(value):
    return project_wait_status(value)


def rest_projection(value):
    return project_wait_status(value)


def ctl_projection(value):
    return project_wait_status(value)
