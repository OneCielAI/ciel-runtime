"""Router MCP tools for the shared hierarchical Work plan.

The tools are listed on the ``ciel-runtime-router`` MCP server while Work mode
is on.  Claude (through the bundled mod) and Codex call the same tools, so one
plan is scoped to the exact runtime conversation across restarts and compaction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_mode_launch import WORK_TOOL_NAMES as WORK_TOOL_NAMES  # re-exported for the router
from ciel_runtime_support.work_mode_launch import work_auto_continue_enabled, work_mode_enabled, work_plan_approval_ask
from ciel_runtime_support.work_plan_store import WorkPlanStore, session_work_plan_store

_NODE_SPEC = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "note": {"type": "string"},
        "ref": {"type": "string", "maxLength": tree.MAX_REF_CHARS},
        "waiting_on": {"type": "string", "maxLength": tree.MAX_WAIT_CHARS},
        "children": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["title"],
}
_EXPECTED_VERSION = {
    "type": "integer",
    "minimum": 0,
    "description": "Optional: the plan version you last saw; the call fails if the plan changed since.",
}


def work_tool_schemas() -> list[dict[str, Any]]:
    schemas = [
        {
            "name": "work_status",
            "description": (
                "Show the Ciel Work plan: phase (planning/executing), the active goal as an indented tree with "
                "statuses, progress and the next step. format=json returns a machine-readable snapshot."
            ),
            "inputSchema": {"type": "object", "properties": {
                "format": {"type": "string", "enum": ["text", "json"]},
                "detail": {"type": "boolean", "description": "Include active-goal nodes with notes and evidence for read-only UI inspection."},
            }},
        },
        {
            "name": "work_plan_set",
            "description": (
                "Create a goal (a root-level item) with its whole hierarchical plan in one call: goal -> phases -> "
                "tasks -> steps, nested through children. Keep decomposing until every leaf is one verifiable action. "
                "One independent user request is one goal; where possible start its title with its item number "
                "(\"1. ...\", \"2. ...\") so it stays easy to reference. A request that changes or extends an existing "
                "item goes under that item with work_node_add and parent_id, not into a new goal. While another goal "
                "is unfinished, a new goal is queued behind it and becomes active when that goal is done. "
                "With root_id, replace that goal's plan; the active goal returns to planning, a queued goal stays queued."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "The goal title."},
                    "note": {"type": "string"},
                    "ref": _NODE_SPEC["properties"]["ref"],
                    "waiting_on": _NODE_SPEC["properties"]["waiting_on"],
                    "children": {"type": "array", "items": _NODE_SPEC},
                    "root_id": {"type": "string", "description": "Replace the plan of this existing goal."},
                    "expected_version": _EXPECTED_VERSION,
                },
                "required": ["goal"],
            },
        },
        {
            "name": "work_node_add",
            "description": (
                "Add a node (optionally with nested children) under parent_id: a request that depends on an existing "
                "item goes under that item. Without parent_id it adds a new goal (root-level item) for an independent "
                "request; while another goal is unfinished the new goal is queued behind it."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "parent_id": {"type": "string"},
                    "title": {"type": "string"},
                    "note": {"type": "string"},
                    "ref": _NODE_SPEC["properties"]["ref"],
                    "waiting_on": _NODE_SPEC["properties"]["waiting_on"],
                    "children": {"type": "array", "items": _NODE_SPEC},
                    "expected_version": _EXPECTED_VERSION,
                },
                "required": ["title"],
            },
        },
        {
            "name": "work_node_update",
            "description": (
                "Update a node: status (pending, in_progress, done, blocked), title, note or evidence. "
                "Mark a step done only with evidence that it was verified; parents complete automatically."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "status": {"type": "string", "enum": list(tree.STATUSES)},
                    "title": {"type": "string"},
                    "note": {"type": "string"},
                    "ref": _NODE_SPEC["properties"]["ref"],
                    "waiting_on": _NODE_SPEC["properties"]["waiting_on"],
                    "evidence": {"type": "string"},
                    "expected_version": _EXPECTED_VERSION,
                },
                "required": ["id"],
            },
        },
        {
            "name": "work_node_remove",
            "description": "Remove a node and everything below it.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "expected_version": _EXPECTED_VERSION},
                "required": ["id"],
            },
        },
        {
            "name": "work_select",
            "description": (
                "Make another goal the active one now (returns to the planning phase). Queued goals become active "
                "by themselves, oldest first, when the active goal is done."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"root_id": {"type": "string"}},
                "required": ["root_id"],
            },
        },
        {
            "name": "work_execute",
            "description": (
                "Approve the active goal's plan and switch Work to the executing phase. The plan must reach at least "
                "goal -> phase -> task. When user approval is configured, call it again with user_confirmed=true "
                "only after the user explicitly agreed to the plan."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"user_confirmed": {"type": "boolean"}, "expected_version": _EXPECTED_VERSION},
            },
        },
        {
            "name": "work_replan",
            "description": "Return to the planning phase to revise the active goal's plan.",
            "inputSchema": {"type": "object", "properties": {"reason": {"type": "string"}}},
        },
    ]
    schemas.extend([
        {
            "name": "work_reorder",
            "description": "Move an unfinished root goal to a 1-based position (1 is active). Reorder queued goals freely; changing the active slot requires an explicit user request and user_requested=true.",
            "inputSchema": {"type": "object", "properties": {
                "root_id": {"type": "string"}, "position": {"type": "integer", "minimum": 1},
                "user_requested": {"type": "boolean"}, "expected_version": _EXPECTED_VERSION,
            }, "required": ["root_id", "position"]},
        },
        {
            "name": "work_urgent",
            "description": "On an explicit user request, create a top-priority urgent goal and hold all other goals until it finishes; the former active goal resumes next. Break down the ad-hoc request with children and call work_execute before executing.",
            "inputSchema": {"type": "object", "properties": {
                "goal": {"type": "string"}, "note": {"type": "string"},
                "ref": _NODE_SPEC["properties"]["ref"],
                "children": {"type": "array", "items": _NODE_SPEC},
                "user_requested": {"type": "boolean"}, "expected_version": _EXPECTED_VERSION,
            }, "required": ["goal", "user_requested"]},
        },
    ])
    for schema in schemas:
        inputs = schema["inputSchema"]
        inputs.setdefault("properties", {})["session_id"] = {
            "type": "string",
            "pattern": "^claude:[A-Za-z0-9_-]{1,200}$",
            "description": "Claude mod supplies the exact conversation identity. Codex identity comes from client metadata; omit this argument.",
        }
    return schemas


def dispatch_work_tool(
    request_id: Any,
    name: str,
    args: dict[str, Any],
    *,
    load_config: Callable[[], dict[str, Any]],
    state_dir: Path,
    respond: Callable[..., dict[str, Any]],
    log: Callable[[str, str], Any] | None = None,
    store: WorkPlanStore | None = None,
    metadata: Any = None,
) -> dict[str, Any]:
    config = load_config()
    if not work_mode_enabled(config):
        if name == "work_status" and args.get("format") == "json":
            return respond(request_id, json.dumps(tree.snapshot(tree.empty_document(), enabled=False)))
        return respond(request_id, "Ciel Work mode is off (prelaunch menu: LLM options -> Work mode).", True)
    approval_ask = work_plan_approval_ask(config)
    try:
        if isinstance(metadata, dict) and "sessionId" in metadata:
            native_id = metadata["sessionId"]
            if not isinstance(native_id, str) or not native_id:
                raise tree.WorkPlanError("Codex conversation metadata is invalid")
            identity = "codex:" + native_id
        else:
            identity = args.get("session_id")
            if not isinstance(identity, str) or not identity.startswith("claude:"):
                raise tree.WorkPlanError("conversation identity is unavailable; no shared Work fallback is allowed")
        scoped = session_work_plan_store(state_dir, identity, log=log)
        store = store or scoped
        return respond(request_id, _run(name, args, store, approval_ask, auto_continue=work_auto_continue_enabled(config)))
    except tree.WorkPlanError as error:
        return respond(request_id, str(error), True)


def _run(name: str, args: dict[str, Any], store: WorkPlanStore, approval_ask: bool, *, auto_continue: bool = True) -> str:
    expected = _expected_version(args)
    if name == "work_status":
        doc = store.read()
        if args.get("format") == "json":
            state = tree.snapshot(doc, approval_ask=approval_ask)
            state["auto_continue"] = auto_continue
            if args.get("detail") is True:
                active = {doc["active_root"]} if doc.get("active_root") else set()
                for _ in range(tree.MAX_DEPTH):
                    active.update(node["id"] for node in doc["nodes"] if node["parent_id"] in active)
                state["nodes"] = [node for node in doc["nodes"] if node["id"] in active]
            return json.dumps(state, ensure_ascii=False)
        return tree.render(doc, max_lines=tree.MAX_NODES + 4)
    if name == "work_plan_set":
        spec = {"title": args.get("goal"), "note": args.get("note", ""), "children": args.get("children") or []}
        spec.update({key: args[key] for key in ("ref", "waiting_on") if key in args})
        root_id = args.get("root_id")
        doc, root = store.transaction(
            lambda d: tree.set_plan(d, spec, str(root_id) if root_id else None), expected_version=expected
        )
        if doc.get("active_root") != root:
            return _after(doc, f"Plan for goal {root} saved and queued behind the active goal {doc['active_root']}; "
                               "it becomes active when that goal is done, or earlier with work_select.")
        return _after(doc, f"Plan for goal {root} saved; Work is in the planning phase.")
    if name == "work_node_add":
        parent = args.get("parent_id")
        spec = {"title": args.get("title"), "note": args.get("note", ""), "children": args.get("children") or [],
                "ref": args.get("ref", ""), "waiting_on": args.get("waiting_on", "")}
        doc, node = store.transaction(
            lambda d: tree.add_subtree(d, str(parent) if parent else None, spec), expected_version=expected
        )
        if not parent and doc.get("active_root") != node:
            return _after(doc, f"Added goal {node}, queued behind the active goal {doc['active_root']}; "
                               "it becomes active when that goal is done, or earlier with work_select.")
        return _after(doc, f"Added {node}.")
    if name == "work_node_update":
        node_id = str(args.get("id") or "")

        def update(d: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
            before = d.get("active_root")
            return tree.update_node(
                d, node_id, status=args.get("status"), title=args.get("title"),
                note=args.get("note"), evidence=args.get("evidence"),
                ref=args.get("ref"), waiting_on=args.get("waiting_on"),
            ), before

        doc, (node, before) = store.transaction(update, expected_version=expected)
        message = f"Updated {node['id']} ({node['status']})."
        if before and doc.get("active_root") != before:
            message += (f" Goal {before} is done; the next queued goal {doc['active_root']} is now active in the "
                        "planning phase. Review its plan and call work_execute.")
        elif doc.get("active_root") and tree.progress(doc)[1] and tree.next_leaf(doc) is None:
            message += " Every step of the active goal is done; Work returned to the planning phase for the next goal."
        return _after(doc, message)
    if name == "work_node_remove":
        node_id = str(args.get("id") or "")
        doc, removed = store.transaction(lambda d: tree.remove_node(d, node_id), expected_version=expected)
        return _after(doc, f"Removed {removed} node(s).")
    if name == "work_select":
        root_id = str(args.get("root_id") or "")
        doc, _ = store.transaction(lambda d: tree.select_root(d, root_id), expected_version=expected)
        return _after(doc, f"Goal {root_id} is active; Work is in the planning phase.")
    if name == "work_reorder":
        doc, _ = store.transaction(lambda d: tree.reorder_root(
            d, str(args.get("root_id") or ""), args.get("position"),
            user_requested=args.get("user_requested") is True), expected_version=expected)
        return _after(doc, "Goal priority updated.")
    if name == "work_urgent":
        spec = {"title": args.get("goal"), "note": args.get("note", ""),
                "ref": args.get("ref", ""), "children": args.get("children") or []}
        doc, root = store.transaction(lambda d: tree.add_urgent(
            d, spec, user_requested=args.get("user_requested") is True), expected_version=expected)
        return _after(doc, f"Urgent goal {root} is active; all other goals are held until it finishes. Review its plan and call work_execute.")
    if name == "work_execute":
        return _execute(store, approval_ask, args.get("user_confirmed") is True, expected_version=expected)
    if name == "work_replan":
        doc, _ = store.transaction(lambda d: d.__setitem__("phase", tree.PHASE_PLANNING), expected_version=expected)
        return _after(doc, "Work returned to the planning phase.")
    raise tree.WorkPlanError(f"unknown Work tool {name}")


def _execute(store: WorkPlanStore, approval_ask: bool, user_confirmed: bool, *, expected_version: int | None = None) -> str:
    def mutate(doc: dict[str, Any]) -> str:
        ready, reason = tree.readiness(doc)
        if not ready:
            raise tree.WorkPlanError(f"The plan is not ready to execute: {reason}.")
        if doc["phase"] == tree.PHASE_EXECUTING:
            return "already"
        if approval_ask and not user_confirmed:
            return "pending"
        doc["phase"] = tree.PHASE_EXECUTING
        return "started"

    doc, outcome = store.transaction(mutate, expected_version=expected_version)
    if outcome == "pending":
        return _after(doc, (
            "User confirmation is required before executing. Show the user this plan, ask for approval, "
            "and call work_execute with user_confirmed=true only after they explicitly agree."
        ))
    if outcome == "already":
        return _after(doc, "Work is already executing this plan.")
    return _after(doc, "Plan approved; Work is in the executing phase. Start with the next step and record evidence.")


def _after(doc: dict[str, Any], message: str) -> str:
    return f"{message}\n\n{tree.render(doc)}"


def _expected_version(args: dict[str, Any]) -> int | None:
    value = args.get("expected_version")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise tree.WorkPlanError("expected_version must be an integer") from error
