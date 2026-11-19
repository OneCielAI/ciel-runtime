"""Approval workflow REST views and operator actions (spec 9, 9.1, 10).

Mounted by WorkHttpController: GET /ca/work/api/v1/workflow (goals, stage,
owner, blocking reason, proposal, approval, QA, reworks, main's queue,
metrics), /workflow/prompts and /workflow/metrics; operator commands
workflow-resolve, workflow-delegate, workflow-prompt-set and
workflow-prompt-reset. The web console, ciel-runtimectl (in-process controller)
and the menu (through ctl) run these same functions.
"""
from __future__ import annotations

from typing import Any

from . import work_plan_tree as tree

PATHS = ("/ca/work/api/v1/workflow", "/ca/work/api/v1/workflow/prompts", "/ca/work/api/v1/workflow/metrics")
ACTIONS = ("workflow_resolve", "workflow_delegate", "workflow_prompt_set", "workflow_prompt_reset")


def get_payload(controller: Any, identity: str, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
    from .work_workflow_prompts import listing
    from .work_workflow_tools import status
    services = controller.services
    if path.endswith("/prompts"):
        return {"ok": True, "schema_version": 1, "session_id": identity, **listing(services.state_dir)}
    store = controller._store(identity)
    root = controller._single_query(query, "root_id", "")
    view = status(store, services.state_dir, identity, services.load_config(), root_id=root or None,
                  include_body=bool(root))
    if path.endswith("/metrics"):
        return {"ok": True, "schema_version": 1, "session_id": identity, **view["metrics"]}
    view.pop("next_for_main", None)
    return {"ok": True, "schema_version": 1, "session_id": identity, **view}


def effect(controller: Any, identity: str, action: str, body: dict[str, Any], source: str) -> dict[str, Any]:
    from .work_workflow_prompts import PromptConflict, reset_prompt, set_prompt
    from .work_workflow_tools import delegate, resolve
    services = controller.services
    if action == "workflow_resolve":
        root = body.get("root_id")
        if not isinstance(root, str) or not root:
            raise tree.WorkPlanError("root_id required")
        return resolve(controller._store(identity), root, str(body.get("action") or ""), body, actor=source)
    if action == "workflow_delegate":
        root = body.get("root_id")
        if not isinstance(root, str) or not root:
            raise tree.WorkPlanError("root_id required")
        return delegate(controller._store(identity), root, str(body.get("role") or ""), body.get("item_id"))
    try:
        if action == "workflow_prompt_set":
            return set_prompt(services.state_dir, str(body.get("name") or ""), body.get("value"),
                              body.get("expected_fingerprint"), source=source)
        if action == "workflow_prompt_reset":
            return reset_prompt(services.state_dir, str(body.get("name") or ""), body.get("expected_fingerprint"),
                                source=source)
    except PromptConflict as error:
        raise WorkflowPromptConflict(str(error)) from None
    except ValueError as error:
        raise tree.WorkPlanError(str(error)) from None
    raise tree.WorkPlanError("unknown workflow action")


class WorkflowPromptConflict(tree.WorkPlanError):
    """Answered 409 prompt_conflict: the prompt changed since the editor read it."""


__all__ = ["ACTIONS", "PATHS", "WorkflowPromptConflict", "effect", "get_payload"]
