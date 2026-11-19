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
from ciel_runtime_support.side_intake_limits import SIDE_INTAKE_TEXT_MAX_CHARS
from ciel_runtime_support.work_mode_launch import WORK_TOOL_NAMES as WORK_TOOL_NAMES  # re-exported for the router
from ciel_runtime_support.work_mode_launch import (WORK_MODE_KEY, WORK_MODE_SWITCH_TOOL, work_auto_continue_enabled,
                                                   work_mode_enabled, work_plan_approval_ask)
from ciel_runtime_support.work_plan_store import WorkPlanStore, session_work_plan_store
from ciel_runtime_support.work_retention import retention_status

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
_PRIORITY = {
    "type": "string",
    "enum": ["", *tree.PRIORITIES],
    "description": "Queue priority for a goal (root item only): P1 first, then P2..P5; empty keeps the existing unranked order. Does not change the active goal or running workers.",
}
_ROOT_PRIORITY = {
    "type": "string",
    "enum": ["P0", "", *tree.PRIORITIES],
    "description": ("Effective root priority. P0 immediately adds a Work fork and bypasses enabled, paused, "
                    "approval, per-priority capacity and launch-rate gates. On a new goal (work_plan_set without "
                    "root_id) it creates the goal and activates it as P0 in the same call."),
}
_PRIORITY_OPERATION_ID = {
    "type": "string", "pattern": "^[0-9a-f]{32}$",
    "description": "Optional exact replay id for a root priority operation; omit to mint one for this call.",
}
_EXPECTED_VERSION = {
    "type": "integer",
    "minimum": 0,
    "description": "Optional: the plan version you last saw; the call fails if the plan changed since.",
}


def work_tool_schemas(*, enabled: bool = True) -> list[dict[str, Any]]:
    """Every Work tool while Work mode is on; only the switch while it is off."""

    schemas = [
        {"name": "work_live_settings", "description": "Host-only Work slash settings, applied live.",
         "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}}},
        {"name": "work_priority_command", "description": "Host-only root priority command: a new P0 goal or an existing goal's priority.",
         "inputSchema": {"type": "object", "properties": {
             "command": {"type": "string", "enum": ["work_plan_set", "work_node_update"]},
             "payload": {"type": "object"}}, "required": ["command", "payload"]}},
        {"name": "work_side_settings", "description": "Host-only live side-intake settings; default disabled, selected type btw_with_mcp.",
         "inputSchema": {"type": "object", "properties": {"enabled": {"type": "boolean"}}}},
        {"name": "work_side_status", "description": "Coordinator-only read of durable side inbox receipts, independent of Work mode.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "work_side_discard_failed", "description": "Host-only operator action: discard every failed side receipt (quarantined, never retried, not forwarded to main; kept for audit). category limits it to one failure category.",
         "inputSchema": {"type": "object", "properties": {"category": {"type": "string", "maxLength": 80}}}},
        {"name": "work_side_drain", "description": "Coordinator-only: retry pending durable side inbox admission without approving execution. Full board remains queued.",
         "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}}},
        {"name": "work_side_gate_check", "description": "Host-only: queue one side gate re-check of blocked/waiting Work (P0 first, then P1, then the rest) when it is due, also while main is busy and Work forks run; one round at a time. Or claim/record the host's own round (host_report). idle reports whether main's turn has ended; it no longer defers the check.",
         "inputSchema": {"type": "object", "properties": {"idle": {"type": "boolean"}, "forks_running": {"type": "integer", "minimum": 0},
                                                          "host_report": {"type": "object"},
                                                          "thread_reads": {"type": "boolean", "description": "The host reads each gate thread after its cursor (two calls per tick)."},
                                                          "threads_read": {"type": "array", "items": {"type": "object"}},
                                                          "approvals_read": {"type": "array", "items": {"type": "object"}, "description": "Approval workflow: the request messages the host read for approvals_due this tick."}},
                         "required": ["idle", "forks_running"]}},
        {"name": "work_side_intake", "description": "Durably queue an untrusted side request without approving execution or changing the active goal. Stable event_key deduplicates retries; host coordinator only.",
         "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "maxLength": SIDE_INTAKE_TEXT_MAX_CHARS},
             "event_key": {"type": "string", "maxLength": 300}}, "required": ["text", "event_key"]}},
        {
            "name": "work_status",
            "description": (
                "Show the Ciel Work plan: phase (planning/executing), the active goal as an indented tree with "
                "statuses, progress and the next step. format=json returns a machine-readable snapshot. "
                "Use root_id for one exact goal only; detail=true includes its full notes/evidence without other goals."
            ),
            "inputSchema": {"type": "object", "properties": {
                "format": {"type": "string", "enum": ["text", "json"]},
                "detail": {"type": "boolean", "description": "Include selected-goal nodes with notes and evidence for read-only inspection."},
                "root_id": {"type": "string", "description": "Read only this exact goal; does not select it or change execution."},
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
                    "priority": _ROOT_PRIORITY,
                    "operation_id": _PRIORITY_OPERATION_ID,
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
                "request; while another goal is unfinished the new goal is queued behind it. "
                "An active-goal addition returns it to planning unless within_scope=true explicitly marks a "
                "refinement wholly inside its already approved scope. This flag is not approval for new scope."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "parent_id": {"type": "string"},
                    "within_scope": {"type": "boolean", "description": "True only for a refinement within the existing approved goal; preserves its phase. Omit for new scope requiring review."},
                    "title": {"type": "string"},
                    "note": {"type": "string"},
                    "ref": _NODE_SPEC["properties"]["ref"],
                    "waiting_on": _NODE_SPEC["properties"]["waiting_on"],
                    "priority": _PRIORITY,
                    "children": {"type": "array", "items": _NODE_SPEC},
                    "expected_version": _EXPECTED_VERSION,
                },
                "required": ["title"],
            },
        },
        {
            "name": "work_node_update",
            "description": (
                "Update a node: status (pending, in_progress, done, blocked), title, note, progress or evidence. "
                "Use progress for implementation, CI, review and cleanup narrative; it preserves execution approval. "
                "Mark a step done only with evidence that it was verified; parents complete automatically. "
                "Title and note are approved scope: changing either returns only that goal to planning for scope review. "
                "At least one mutation field is REQUIRED; id/expected_version alone is rejected. "
                "waiting_on sets a dependency; explicitly waiting_on=\"\" clears that node's verified wait. "
                "When you wait on git, a build/CI/deploy result, a review or a person's approval or answer, record it on the "
                "waiting node in one short line naming the signal and its source (e.g. \"approval from Andy via Walkie\", "
                "\"CI run 4312 result\"): the side actor matches incoming SSE events against it, releases it and wakes you. "
                "Evidence text never clears waits. Waiting excludes only its dependent subtree; continue independent runnable Work."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "status": {"type": "string", "enum": list(tree.STATUSES)},
                    "title": {"type": "string"},
                    "note": {"type": "string"},
                    "progress": {"type": "string", "description": "Phase-preserving implementation, CI, review or cleanup progress; not approved scope."},
                    "ref": _NODE_SPEC["properties"]["ref"],
                    "waiting_on": _NODE_SPEC["properties"]["waiting_on"],
                    "priority": _ROOT_PRIORITY,
                    "operation_id": _PRIORITY_OPERATION_ID,
                    "evidence": {"type": "string"},
                    "expected_version": _EXPECTED_VERSION,
                },
                "required": ["id"],
                "anyOf": [{"required": [key]} for key in ('status', 'title', 'note', 'progress', 'ref', 'waiting_on', 'priority', 'evidence')],
            },
        },
        {
            "name": "work_node_remove",
            "description": "Remove an unnecessary queue item and its subtree without pretending it completed. Removed records are archived durably before deletion for recovery/audit. Clear explicit focus first. Use work_node_update priority=P1..P5 to reprioritize instead of deleting useful work.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "expected_version": _EXPECTED_VERSION},
                "required": ["id"],
            },
        },
        {
            "name": "work_select",
            "description": (
                "Make another goal active, retaining its own approval phase (new goals remain planning). "
                "Interrupting unfinished Work requires an explicit user request and user_requested=true, except that "
                "when every unfinished active step waits or is blocked, the queued goal holding the first runnable step "
                "may be selected without it. Queued goals become active by themselves, oldest first, when the active goal is done."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"root_id": {"type": "string"}, "user_requested": {"type": "boolean"},
                               "expected_version": _EXPECTED_VERSION},
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
            "name": "work_focus",
            "description": "On explicit user request, prioritize existing nodes (roots or subtrees) in node_ids order. Resolve exact IDs from Work status. Focus orders the work first but does not hold unrelated approved items: when no focused step can run, other approved items and discovery continue. Focus clears automatically once every focused step is done; node_ids=[] clears it now. Approval and ancestor waits/blocks remain; unrelated Work is never deleted or blocked.",
            "inputSchema": {"type": "object", "properties": {
                "node_ids": {"type": "array", "items": {"type": "string"}, "maxItems": tree.MAX_FOCUS_BATCH},
                "user_requested": {"type": "boolean"}, "expected_version": _EXPECTED_VERSION,
            }, "required": ["node_ids", "user_requested"]},
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
        {
            "name": WORK_MODE_SWITCH_TOOL,
            "description": (
                "Turn Ciel Work mode on or off. This is the same setting as ciel-runtime menu -> LLM options -> "
                "Work mode: it is stored for this workspace folder and applies to every Ciel session launched from "
                "it and to later launches. Plans are kept. Off stops Work prompts, edit gates and auto continue at "
                "their next check; while it is off the router lists only this tool, so it can be turned back on. A session launched "
                "while Work mode was off loads the Work hooks and instructions on its next launch."
            ),
            "inputSchema": {"type": "object", "properties": {
                "enabled": {"type": "boolean", "description": "true turns Work mode on, false turns it off."},
            }, "required": ["enabled"]},
        },
    ])
    from .work_parallel_mcp import schemas as parallel_schemas
    from .side_intake_mcp import schemas as side_schemas
    from .host_result_pages import schema as host_page_schema
    from .work_workflow_tools import schemas as workflow_schemas
    schemas.extend(parallel_schemas())
    schemas.extend(side_schemas())
    schemas.extend(workflow_schemas())
    schemas.append(host_page_schema())
    if not enabled:
        schemas = [schema for schema in schemas if schema["name"] in (WORK_MODE_SWITCH_TOOL, "work_live_settings", "work_side_settings", "work_side_intake", "work_side_drain", "work_side_discard_failed", "work_side_status", "work_side_claim", "work_side_understanding", "work_side_resolve", "work_side_fail", "work_side_fallback", "work_side_ack", "work_side_reply", "work_side_wake", "work_side_handoff", "work_side_renew", "work_side_retry_reply", "work_side_code_read", "work_side_code_search", "work_host_page")]
    for schema in schemas:
        inputs = schema["inputSchema"]
        inputs.setdefault("properties", {})["session_id"] = {
            "type": "string",
            "pattern": "^claude:[A-Za-z0-9_-]{1,200}$",
            "description": "Claude mod supplies the exact conversation identity. Codex identity comes from client metadata; omit this argument.",
        }
    return schemas


def _host_verified(args: dict[str, Any], config: dict[str, Any]) -> bool:
    from .work_parallel_mcp import host_context
    try:
        host_context(args, config)
    except (ValueError, OSError):
        return False
    return True


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
    save_config: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if name == WORK_MODE_SWITCH_TOOL:
        return respond(request_id, *_switch_work_mode(args.get("enabled"), load_config, save_config))
    config = load_config()
    from . import host_result_pages
    if name == host_result_pages.TOOL:
        from .work_parallel_mcp import host_context
        try:
            host_context(args, config)
            identity = args.get("session_id")
            if not isinstance(identity, str) or not identity.startswith("claude:"):
                raise tree.WorkPlanError("conversation identity is unavailable")
            return respond(request_id, json.dumps(
                host_result_pages.page(state_dir, identity, args.get("id"), args.get("part")), ensure_ascii=False))
        except (ValueError, OSError) as error:
            return respond(request_id, str(error), True)
    if isinstance(args.get("_host"), dict) and isinstance(args.get("session_id"), str) and _host_verified(args, config):
        # Only the authenticated mod gets pages; it reassembles and checks the digest.
        deliver, page_identity = respond, args["session_id"]

        def respond(request_id: Any, text: Any, is_error: bool = False) -> dict[str, Any]:  # noqa: F811
            if not is_error and isinstance(text, str):
                try:
                    text = host_result_pages.bound(state_dir, page_identity, text)
                except (ValueError, OSError) as error:
                    return deliver(request_id, "host result paging failed: " + type(error).__name__, True)
            return deliver(request_id, text, is_error)
    if name == "work_live_settings":
        from .work_parallel_mcp import host_context
        from .work_live_settings import settings
        try:
            host, _ = host_context(args, config)
            if host.get("agent_id") is not None:
                raise tree.WorkPlanError("coordinator required")
            return respond(request_id, json.dumps(settings(config, args, save_config), ensure_ascii=False))
        except (ValueError, OSError) as error:
            return respond(request_id, str(error), True)
    if name in ("work_side_code_read", "work_side_code_search"):
        # The side actor calls these natively: the engine skips the ciel-work
        # tool.call hook for agents that plugin spawned (re-entry), so no host
        # token or conversation identity is attached (owned run 2026-11-09).
        # They read only the router's launch workspace, through every path guard.
        if (config.get("work_side_intake") or {}).get("enabled") is not True:
            return respond(request_id, "side processing is disabled", True)
        from .side_code_read import read_code, search_code
        try:
            value = (read_code(args.get("path"), offset=args.get("offset", 1), limit=args.get("limit", 200))
                     if name == "work_side_code_read" else search_code(args.get("pattern"), glob=args.get("glob", "*")))
        except (tree.WorkPlanError, ValueError, OSError) as error:
            return respond(request_id, str(error), True)
        return respond(request_id, json.dumps(value, ensure_ascii=False))
    if not work_mode_enabled(config) and name not in ('work_side_settings','work_side_intake', 'work_side_drain', 'work_side_discard_failed', 'work_side_status', 'work_side_claim', 'work_side_understanding', 'work_side_resolve', 'work_side_fail', 'work_side_fallback', 'work_side_ack', 'work_side_reply', 'work_side_wake', 'work_side_handoff', 'work_side_review', 'work_side_renew', 'work_side_retry_reply', 'work_side_code_read', 'work_side_code_search'):
        if name == "work_status" and args.get("format") == "json":
            return respond(request_id, json.dumps(tree.snapshot(tree.empty_document(), enabled=False)))
        return respond(request_id, "Ciel Work mode is off (prelaunch menu: LLM options -> Work mode, "
                                   "or work_mode_set with enabled=true).", True)
    approval_ask = work_plan_approval_ask(config)
    try:
        if name == "work_parallel_report":
            from .work_parallel_mcp import report, ledger
            # The capability resolves the exact lease and scope. A plugin-spawned
            # fork may skip ciel-work's own tool hook, so no session_id is required.
            global_store = ledger()
            lease = global_store.read()["leases"].get(args.get("lease_id"))
            if not lease:
                raise tree.WorkPlanError("invalid or revoked worker capability")
            scoped = session_work_plan_store(state_dir, lease["scope"], log=log)
            return respond(request_id, report(args, scoped, config))
        if isinstance(metadata, dict) and "sessionId" in metadata:
            native_id = metadata["sessionId"]
            if not isinstance(native_id, str) or not native_id:
                raise tree.WorkPlanError("Codex conversation metadata is invalid")
            identity = "codex:" + native_id
        else:
            identity = args.get("session_id")
            if not isinstance(identity, str) or not identity.startswith("claude:"):
                raise tree.WorkPlanError("conversation identity is unavailable; no shared Work fallback is allowed")
        if name == "work_priority_command":
            from .work_parallel_mcp import host_context, ledger
            from .work_priority_service import mutate_existing_priority
            import hashlib
            import secrets
            host, _ = host_context(args, config)
            if host.get("internal") is not True or host.get("agent_id") is not None:
                raise tree.WorkPlanError("verified coordinator priority host required")
            command = args.get("command")
            payload = args.get("payload")
            if command not in ("work_plan_set", "work_node_update") or not isinstance(payload, dict):
                raise tree.WorkPlanError("invalid priority command")
            target = payload.get("priority")
            root_id = str((payload.get("root_id") if command == "work_plan_set" else payload.get("id")) or "")
            if target not in ("P0", "", *tree.PRIORITIES):
                raise tree.WorkPlanError("priority must be one of P0, P1, P2, P3, P4, P5")
            new_root_p0 = command == "work_plan_set" and target == "P0" and not root_id
            operation_id = payload.get("operation_id") or secrets.token_hex(16)
            if not isinstance(operation_id, str) or len(operation_id) != 32 or any(c not in "0123456789abcdef" for c in operation_id):
                raise tree.WorkPlanError("operation_id must be lowercase hex32")
            scoped = store or session_work_plan_store(state_dir, identity, log=log)
            expected = _expected_version(payload)
            if expected is None:
                expected = scoped.read()["version"]
            if new_root_p0:
                # A new P0 goal: an ordinary P1 create, then existing-root P0
                # activation, through the one application path REST, web and ctl use.
                from .work_application import WorkApplication
                fields = {key: payload[key] for key in ("goal", "note", "children", "ref", "waiting_on")
                          if key in payload}
                done = WorkApplication(scoped).command(
                    "plan_set", {**fields, "priority": "P0", "expected_version": expected}, operation_id,
                    origin={"source": "mcp", "auth": "host"}, scope=identity, parallel_store=ledger())
                return respond(request_id, json.dumps({
                    "root_id": done["result"]["root_id"], "canonical_priority": done["result"]["canonical_priority"],
                    "effective_priority": done["result"]["effective_priority"], "version": done["version"],
                    "priority_operation_ref": done["priority_operation_ref"], "replayed": done["replayed"],
                    "created": True, "dispatch": "immediate"}, ensure_ascii=False))
            clean = {key: value for key, value in payload.items()
                     if key not in ("priority", "operation_id", "expected_version")}
            manifest = hashlib.sha256(json.dumps(
                {"command": command, "payload": clean}, sort_keys=True, ensure_ascii=False,
                separators=(",", ":")).encode("utf-8")).hexdigest()
            if command == "work_plan_set":
                spec = {"title": clean.get("goal"), "note": clean.get("note", ""),
                        "children": clean.get("children") or []}
                spec.update({key: clean[key] for key in ("ref", "waiting_on") if key in clean})
                def mutate(document):
                    return tree.set_plan(document, spec, root_id)
                def satisfied(document):
                    import copy
                    probe = copy.deepcopy(document)
                    before = copy.deepcopy(probe)
                    tree.set_plan(probe, spec, root_id)
                    tree.update_node(probe, root_id, priority="P1" if target == "P0" else target)
                    return probe == before
            else:
                mutable = {"status", "title", "note", "progress", "evidence", "ref", "waiting_on"}
                supplied = {key: clean[key] for key in mutable if key in clean and clean[key] is not None}
                def mutate(document):
                    return tree.update_node(document, root_id, **supplied)
                def satisfied(document):
                    node = next((row for row in document.get("nodes", []) if row.get("id") == root_id), None)
                    return (node is not None and all(node.get(key, "") == value for key, value in supplied.items())
                            and node.get("priority", "") == ("P1" if target == "P0" else target))
            result = mutate_existing_priority(
                scoped, ledger(), identity, root_id, target,
                expected_board_version=expected, external_operation_id=operation_id,
                manifest_digest=manifest, mutate=mutate, satisfied=satisfied, set_by="user")
            ref = hashlib.sha256(("ciel.priority-ref.v1\0" + operation_id).encode()).hexdigest()[:16]
            root = result["root"]
            return respond(request_id, json.dumps({
                "root_id": root["id"], "canonical_priority": result["canonical_priority"],
                "effective_priority": result["effective_priority"], "version": result["version"],
                "priority_operation_ref": ref, "replayed": result["replayed"],
                "dispatch": "immediate"}, ensure_ascii=False))
        if name not in ('work_side_status', 'work_side_settings', 'work_side_intake', 'work_side_drain'):
            scoped = session_work_plan_store(state_dir, identity, log=log)
            store = store or scoped
        if name in ('work_side_claim', 'work_side_understanding', 'work_side_resolve', 'work_side_fail', 'work_side_fallback', 'work_side_ack', 'work_side_reply', 'work_side_wake', 'work_side_handoff', 'work_side_review', 'work_side_renew', 'work_side_retry_reply', 'work_side_code_read', 'work_side_code_search'):
            from .side_intake_mcp import run as side_run
            return respond(request_id, json.dumps(side_run(name, args, config, state_dir, identity, store), ensure_ascii=False))
        if name == "work_side_settings":
            from .work_parallel_mcp import host_context
            host, _ = host_context(args, config)
            if host.get("agent_id") is not None:
                raise tree.WorkPlanError("coordinator required")
            settings = dict(config.get("work_side_intake") or {})
            if "enabled" in args:
                if type(args["enabled"]) is not bool or save_config is None:
                    raise tree.WorkPlanError("boolean enabled and writable configuration required")
                settings.update(enabled=args["enabled"], type="btw_with_mcp")
                save_config({**config, "work_side_intake": settings})
            from .work_setting_values import injection_mode
            stored = settings.get("type")
            return respond(request_id, json.dumps({"enabled": settings.get("enabled") is True,
                                                  "type": stored if isinstance(stored, str) and stored else "btw_with_mcp",
                                                  "sse_injection_mode": injection_mode({"work_side_intake": settings})}))
        if name == 'work_side_status':
            from .work_parallel_mcp import host_context
            from .side_intake_inbox import SideIntakeInbox
            host, _ = host_context(args, config)
            if host.get('agent_id') is not None:
                raise tree.WorkPlanError('side status requires coordinator host')
            import time
            board = (store or session_work_plan_store(state_dir, identity, log=log)).read_snapshot()
            now = time.time()
            from .side_wake import redact_wake
            main_handoffs = []
            for key, row in board.get('side_handoffs', {}).items():
                if row.get('state') != 'requested':
                    continue
                wake = redact_wake(row.get('wake'))
                main_handoffs.append({'event_id': key, 'item_id': row.get('item_id'), 'kind': row.get('kind', ''),
                    'summary': row.get('summary', ''), 'question': row.get('question', ''),
                    'wake_state': wake.get('state', 'pending'), 'wake_phase': wake.get('phase'),
                    'wake_revision': wake.get('revision', 0), 'wake_attempt_ref': wake.get('attempt_ref'),
                    'delivery_uncertain': wake['delivery_uncertain'],
                    'needs_ack_retry': wake.get('needs_ack_retry') is True,
                    'wake_age_seconds': max(0, int(now - float(wake.get('requested_at') or row.get('requested_at') or now)))})
            inbox = SideIntakeInbox(state_dir, identity)
            # Delivery state of this session's undecided parallel reviews, the
            # same bounded view as REST /side (Side pane re-append release).
            try:
                from .review_delivery import ReviewDeliveryStore, public_view
                from .work_parallel_mcp import finalization
                journal = finalization()
                leases = [key for key, row in (journal.read().get('reviews') or {}).items()
                          if isinstance(row, dict) and row.get('scope') == identity and row.get('decision') is None]
                review_delivery = public_view(ReviewDeliveryStore.beside(journal), leases, identity)
            except (OSError, ValueError, tree.WorkPlanError):
                review_delivery = {'available': False, 'rows': [], 'truncated': False}
            from .side_forward import offered as forwards_offered
            from .side_ack_outbox import pending as acks_owed
            return respond(request_id, json.dumps({'enabled': config.get('work_side_intake', {}).get('enabled') is True,
                'review_delivery': review_delivery,
                # Receipt ACKs still owed; status keeps them moving while every
                # side actor slot is busy.
                'pending_acks': acks_owed(state_dir, identity),
                'side_inbox_receipts': inbox.summaries(),
                # Also offered through claim. Status keeps failure delivery moving
                # while every side actor slot is occupied.
                'main_forwards': forwards_offered(inbox),
                # Events whose completed side turn returned an invalid decision:
                # never retried (a reply may already have been sent), so count them.
                'quarantined_count': len(inbox.quarantined_keys()),
                'main_handoffs': main_handoffs}, ensure_ascii=False))
        if name == 'work_side_discard_failed':
            # A person's operator action (/work-mode discard-failed, menu, web,
            # REST, ctl): only the verified coordinator host, never a model or a
            # worker. Works whether side intake is on or off.
            from .work_parallel_mcp import host_context
            from .side_intake_processing import discard_failed
            host, _ = host_context(args, config)
            if host.get('agent_id') is not None or not (args.get('_host') or {}).get('token'):
                raise tree.WorkPlanError('side discard requires the verified Ciel Work host')
            return respond(request_id, json.dumps(discard_failed(state_dir, identity, category=args.get('category')),
                                                  ensure_ascii=False))
        if name == 'work_side_drain':
            from .work_parallel_mcp import host_context
            from .side_intake_service import drain
            host, _ = host_context(args, config)
            if host.get('agent_id') is not None:
                raise tree.WorkPlanError('side drain requires coordinator host')
            # A drain recovers durable side mail for AI classification. It must
            # never turn status questions into blanket Work roots.
            return respond(request_id, json.dumps(drain(state_dir, identity, admit=False, limit=args.get('limit', 10)), ensure_ascii=False))
        if name == "work_side_gate_check":
            from .work_parallel_mcp import host_context
            from .work_gate_check import gates, minutes
            host, _ = host_context(args, config)
            if host.get("agent_id") is not None:
                raise tree.WorkPlanError("gate check requires coordinator host")
            interval = minutes(config)
            side_on = (config.get("work_side_intake") or {}).get("enabled") is True
            status = {"interval_minutes": interval, "queued": False, "side_intake": side_on}
            if isinstance(args.get("approvals_read"), list):
                # Approval workflow 4.4.1, one batch per tick (2026-11-18): the
                # request messages the host read for this tick's approvals_due
                # come back with the tick's own call. Host code decides each
                # (work_workflow_approval.verify); no side fork is involved.
                from .work_workflow_tools import workflow_event as apply_workflow_checks
                applied = apply_workflow_checks({"check": True, "checks": args["approvals_read"]},
                                                store or session_work_plan_store(state_dir, identity, log=log),
                                                state_dir, identity, config, host)
                status["approval_results"] = applied["results"]
                if args.get("threads_read") is None:
                    # This tick's gate part was answered by its first call.
                    return respond(request_id, json.dumps({**status, "reason": "approvals_checked"},
                                                          ensure_ascii=False))
            if interval == 0:
                return respond(request_id, json.dumps({**status, "reason": "off"}))
            # Stage 3 (2026-11-17): the check also runs while Work forks run and
            # with side intake off. KOBE hotfix (2026-11-18): main's busy turn no
            # longer defers it either; it backs up approvals lost on the network.
            status["main_idle"] = args.get("idle") is True
            from .work_gate_check import effective_priority_for
            priority = effective_priority_for(identity)
            from .channel_message_repository import exclusive_file_lock
            from .side_intake_service import receive
            from .work_gate_check import host_report
            from hashlib import sha256
            import time
            record = state_dir / "sessions" / sha256(identity.encode()).hexdigest()[:24] / "gate-check.json"
            record.parent.mkdir(parents=True, exist_ok=True)
            with exclusive_file_lock(record):
                try:
                    last = json.loads(record.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    last = None
                last = last if isinstance(last, dict) else None
                if args.get("host_report") is not None:
                    # The host's own round (re-pump, diagnosis, notice): a claim
                    # of the one notice per changed diagnosis, or its record.
                    try:
                        updated, answer = host_report(config, last, args.get("host_report"), side_on=side_on,
                                                      forks_running=args.get("forks_running"))
                    except ValueError as error:
                        raise tree.WorkPlanError(str(error)) from None
                    if updated is not None:
                        record.write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")
                    return respond(request_id, json.dumps({**status, **answer}, ensure_ascii=False))
                round_at = float(((last or {}).get("host_round") or {}).get("at") or 0)
                status["host_due"] = time.time() - round_at >= interval * 60
                doc = (store or session_work_plan_store(state_dir, identity, log=log)).read()
                if doc.get("workflows") and args.get("threads_read") is None and args.get("approvals_read") is None:
                    # Only a tick's first call (its second carries the reads).
                    # Approval requests the host re-reads from Walkie once per
                    # interval (SSE may drop an approval), in the gate check's
                    # own order (effective_priority_for: P0, P1, then the rest);
                    # and delegated planning forks to start.
                    from .work_workflow import approvals_due
                    from .work_workflow_tools import approval_request_read, check_state, delegations_due
                    if time.time() - float((last or {}).get("approvals_at") or 0) >= interval * 60:
                        effective = {node["id"]: priority(node) for node in doc.get("nodes", [])
                                     if node.get("parent_id") is None}
                        rows = approvals_due(doc, effective)[:32]
                        board_store = store or session_work_plan_store(state_dir, identity, log=log)
                        rows = [{**row, **check_state(board_store, row)} for row in rows]
                        status["approvals_due"] = [{**row, "request_read": approval_request_read(config, row)}
                                                   for row in rows]
                        if rows:
                            last = {**(last or {}), "approvals_at": time.time()}
                            record.write_text(json.dumps(last, ensure_ascii=False), encoding="utf-8")
                    status["workflow_delegations"] = delegations_due(
                        store or session_work_plan_store(state_dir, identity, log=log), state_dir, config,
                        now=time.time())
                if not side_on:
                    # No side actor checks the gates; the host does what it can
                    # itself (re-pump, diagnosis, notice) and records it.
                    return respond(request_id, json.dumps({**status, "reason": "side_intake_off", "host_only": True,
                                                           "gates": [row["id"] for row in gates(doc, priority)]}))
                # One round at a time: a new round waits until every event of the
                # last one has finished, so a slow side queue never piles up.
                from .side_intake_inbox import SideIntakeInbox
                inbox = SideIntakeInbox(state_dir, identity)
                previous = [rid for rid in ((last or {}).get("receipt_ids") or [(last or {}).get("receipt_id")])
                            if isinstance(rid, str) and len(rid) == 64]
                # The public stage: processed, duplicate and quarantined rows are
                # finished; received, claimed (processing) and backed-off (failed)
                # rows are still in this round.
                checked_at = time.time()
                open_rows = [rid for rid in previous
                             if (row := inbox._load(rid)) is not None
                             and SideIntakeInbox._stage(SideIntakeInbox._stage_inputs(row), checked_at)
                             in ("received", "processing", "failed")]
                if open_rows:
                    return respond(request_id, json.dumps({**status, "reason": "previous_round_pending",
                                                           "pending": len(open_rows)}))
                from .work_gate_check import tick_event, tick_rows
                tick = tick_rows(config, doc, last, priority=priority, turn_end=args.get("turn_end") is True)
                if tick is None:
                    return respond(request_id, json.dumps({**status, "reason": "not_due" if gates(doc, priority) else "no_gates"}))
                rows, current, stamp = tick
                # Andy 2026-11-18 (user design): one batch per tick, latest state
                # only. Gates bound to a Walkie thread are checked against the
                # messages after that thread's cursor, read once per thread by
                # the host (thread_reads); a thread with nothing new drops its
                # gates from this tick, and no gate left means no side event.
                threads_by_gate, newest, reads_count = None, {}, 0
                if args.get("thread_reads") is True:
                    from .work_gate_threads import advance as advance_threads, clean_reads, threads_for
                    planned = threads_for(state_dir, identity, rows)
                    if planned and args.get("threads_read") is None:
                        return respond(request_id, json.dumps({**status, "reason": "read_threads", "read_threads": [
                            {key: plan[key] for key in ("key", "server", "conversation_id", "root_id", "after", "gates")}
                            for plan in planned.values()]}, ensure_ascii=False))
                    if planned:
                        reads = clean_reads(args.get("threads_read"), planned)
                        reads_count = len(reads)
                        threads_by_gate, quiet = {}, set()
                        for key, plan in planned.items():
                            read = reads.get(key)
                            if read is None or read.get("error"):
                                continue  # Unread: checked as a gate without a thread.
                            if not read["messages"]:
                                quiet.update(plan["gates"])
                                continue
                            for gate in plan["gates"]:
                                threads_by_gate[gate] = {"key": key, "root_id": plan["root_id"], "after": plan["after"],
                                                         "messages": read["messages"], "more": read["more"]}
                        newest = {key: read["newest"] for key, read in reads.items() if read.get("newest")}
                        rows = [row for row in rows if row["id"] not in quiet]
                        if not rows:
                            advance_threads(state_dir, identity, newest)
                            record.write_text(json.dumps({**(last or {}), "at": stamp, "digest": current, "receipt_ids": []},
                                                         ensure_ascii=False), encoding="utf-8")
                            return respond(request_id, json.dumps({**status, "reason": "nothing_new",
                                                                   "threads_read": reads_count}))
                event = tick_event(config, rows, current, stamp, threads_by_gate)
                receipts = [receive(state_dir, identity, part["text"], part["event_key"], admit=False,
                                    route={"source_kind": "gate_check"}).get("receipt_id")
                            for part in event["events"]]
                record.write_text(json.dumps({**(last or {}), "at": event["at"], "digest": event["digest"],
                                              "receipt_ids": receipts}, ensure_ascii=False), encoding="utf-8")
                if newest:
                    from .work_gate_threads import advance as advance_threads
                    advance_threads(state_dir, identity, newest)
            return respond(request_id, json.dumps({**status, "queued": True, "receipt_id": receipts[0],
                                                  "receipt_ids": receipts, "events": len(receipts),
                                                  "gates": event["gates"], "threads_read": reads_count},
                                                 ensure_ascii=False))
        from .work_workflow_tools import NAMES as WORKFLOW_TOOLS
        if name in WORKFLOW_TOOLS:
            # Approval workflow (spec 8): main's tools, the fork packet and the
            # host-only event tool share one dispatch.
            from .work_workflow_tools import dispatch as workflow_dispatch
            host = None
            verified = _host_verified(args, config)
            if verified:
                from .work_parallel_mcp import host_context
                host, _ = host_context(args, config)
            return respond(request_id, json.dumps(workflow_dispatch(
                name, args, store or session_work_plan_store(state_dir, identity, log=log), state_dir, identity,
                config, host, host_verified=verified), ensure_ascii=False))
        if name == "work_side_intake":
            from .work_parallel_mcp import host_context
            from .side_intake_service import receive
            host, _ = host_context(args, config)
            if host.get("agent_id") is not None:
                raise tree.WorkPlanError("side intake requires coordinator host")
            return respond(request_id, json.dumps(receive(state_dir, identity, args.get("text"), args.get("event_key"), admit=False), ensure_ascii=False))
        if name.startswith("work_parallel_") or name == "work_discovery_pickup":
            from .work_parallel_mcp import run
            if name == "work_parallel_status" and _host_verified(args, config):
                # Free slots held by leases whose owner process is gone before
                # this host read reports capacity (throttled; never fails it).
                from .work_parallel_orphan_sweep import sweep_while_running
                try:
                    sweep_while_running(log or (lambda *_: None))
                except Exception:  # noqa: BLE001 - the sweep is advisory to a status read
                    pass
            output = run(name, args, store, identity, config, save_config, log=log)
            if name in ("work_parallel_run", "work_parallel_event") and isinstance(output, str)                     and "worker_token" in output:
                # Approval workflow (spec 4.5): a fork of a workflow goal gets
                # the spec, its step, linked QA, rules and the report format.
                from .work_workflow_tools import attach_packet
                output = attach_packet(output, store, state_dir)
            return respond(request_id, output)
        try:
            output = _run(name, args, store, approval_ask, auto_continue=work_auto_continue_enabled(config), retention=retention_status(config), config=config, log=log)
        except ValueError as error:
            # Priority/P0 services use the parallel domain's ValueError base;
            # model-facing Work tools expose the same bounded error contract.
            raise tree.WorkPlanError(str(error)) from None
        if name == 'work_status' and args.get('format') == 'json' and not args.get('root_id'):
            from .side_intake_inbox import SideIntakeInbox
            state = json.loads(output)
            receipts = SideIntakeInbox(state_dir, identity).summaries()
            # Ordinary Work status can reach the main model. Full originals and
            # side conversations belong only in the host-only side-status pane.
            state['side_inbox_receipts'] = [
                {key: row[key] for key in ('receipt_id', 'stage', 'item_id', 'external_ack')}
                for row in receipts]
            if state.get('side_reviews'):
                # Main must know whether the person actually received each answer.
                from .side_review import with_delivery
                state['side_reviews'] = with_delivery(state['side_reviews'], state_dir, identity)
            output = json.dumps(state, ensure_ascii=False)
        return respond(request_id, output)
    except (tree.WorkPlanError, ValueError) as error:
        return respond(request_id, str(error), True)


def _switch_work_mode(enabled: Any, load_config: Callable[[], dict[str, Any]],
                      save_config: Callable[[dict[str, Any]], None] | None) -> tuple[str, bool]:
    """Write the menu's workspace Work mode setting; plans are never touched."""

    if type(enabled) is not bool:
        return "enabled must be true or false.", True
    if save_config is None:
        return "Work mode cannot be changed through this router.", True
    config = load_config()
    state = "on" if enabled else "off"
    if work_mode_enabled(config) == enabled:
        return f"Ciel Work mode is already {state} for this workspace; nothing changed.", False
    config[WORK_MODE_KEY] = enabled
    save_config(config)
    if enabled:
        return ("Ciel Work mode is now on for this workspace (ciel-runtime menu -> LLM options -> Work mode). "
                "Sessions launched with Work mode on resume Work at their next check; a session launched while it "
                "was off loads the Work tools, hooks and instructions on its next launch."), False
    return ("Ciel Work mode is now off for this workspace (ciel-runtime menu -> LLM options -> Work mode): every "
            "Ciel session launched from this folder and later launches. Plans are kept. Work prompts, edit gates and "
            "auto continue stop at their next check. Turn it back on with work_mode_set(enabled=true)."), False


def _run(name: str, args: dict[str, Any], store: WorkPlanStore, approval_ask: bool, *, auto_continue: bool = True, retention: dict[str, Any] | None = None, config: dict[str, Any] | None = None, log: Callable[[str, str], Any] | None = None) -> str:
    expected = _expected_version(args)
    if name == "work_status":
        doc = store.read_snapshot()
        if args.get("root_id") is not None:
            root_id = str(args["root_id"])
            root = next((n for n in doc["nodes"] if n["id"] == root_id and n["parent_id"] is None), None)
            if root is None:
                raise tree.WorkPlanError(f"{root_id} is not a goal")
            ids = {root_id}
            for _ in range(tree.MAX_DEPTH):
                ids.update(n["id"] for n in doc["nodes"] if n["parent_id"] in ids)
            if args.get("format") == "json":
                return json.dumps({"enabled": True, "version": doc["version"], "root_id": root_id,
                    "is_active": doc["active_root"] == root_id, "phase": tree.root_phase(doc, root_id),
                    "approval": "ask" if approval_ask else "auto",
                    "focus": tree.snapshot(doc)["focus"],
                    "nodes": [n for n in doc["nodes"] if n["id"] in ids]}, ensure_ascii=False)
            scoped = dict(doc, nodes=[n for n in doc["nodes"] if n["id"] in ids], active_root=root_id,
                          phase=tree.root_phase(doc, root_id))
            return tree.render(scoped, max_lines=104)
        if args.get("format") == "json":
            state = tree.snapshot(doc, approval_ask=approval_ask)
            state["auto_continue"] = auto_continue
            # Model-visible: the nested wake goes through the shared redactor.
            from .side_wake import redact_handoff
            state['side_handoffs'] = [redact_handoff(key, row) for key, row in doc.get('side_handoffs', {}).items()
                                      if row.get('state') == 'requested']
            from .side_review import pending as pending_reviews
            state['side_reviews'] = [{key: row.get(key) for key in ('receipt_id', 'kind', 'action', 'item_id', 'summary', 'reply', 'source')}
                                     for row in pending_reviews(doc)]
            state['side_intake_enabled'] = (config or {}).get('work_side_intake', {}).get('enabled') is True
            state['side_intake_receipts'] = [
                {'item_id': receipt['item_id'], 'recap': receipt['recap'],
                 'stage': 'runtime_queued', 'execution_approved': False}
                for receipt in list(doc.get('side_intake_receipts', {}).values())[-10:]
            ]
            if log is not None:
                log("INFO", f"work_status_decision version={doc['version']} auto_continue={auto_continue} "
                    f"actionable={state['continuation']['actionable']} "
                    f"next={(state['continuation']['next'] or {}).get('id')} "
                    f"excluded_leaves={state['wait_diagnostics']['excluded_leaves']}")
            from .work_discovery import discovery_hint
            state["discovery"] = discovery_hint(config or {}, state)
            from .work_parallel_mcp import ledger, project
            state["parallel"] = project(ledger(), "")
            state["parallel"].pop("leases", None)
            state["retention"] = retention or retention_status({})
            if args.get("detail") is True:
                active = {doc["active_root"]} if doc.get("active_root") else set()
                for _ in range(tree.MAX_DEPTH):
                    active.update(node["id"] for node in doc["nodes"] if node["parent_id"] in active)
                state["nodes"] = [node for node in doc["nodes"] if node["id"] in active]
            return json.dumps(state, ensure_ascii=False)
        return tree.render(doc, max_lines=104)
    if name == "work_plan_set":
        spec = {"title": args.get("goal"), "note": args.get("note", ""), "children": args.get("children") or []}
        spec.update({key: args[key] for key in ("ref", "waiting_on", "priority") if key in args})
        root_id = args.get("root_id")
        if args.get("priority") == "P0":
            raise tree.WorkPlanError("P0 priority is applied by the authenticated Work host")
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
        if not parent and "priority" in args:
            spec["priority"] = args["priority"]
        doc, node = store.transaction(
            lambda d: tree.add_subtree(d, str(parent) if parent else None, spec,
                                       within_scope=args.get("within_scope") is True), expected_version=expected
        )
        if not parent and doc.get("active_root") != node:
            return _after(doc, f"Added goal {node}, queued behind the active goal {doc['active_root']}; "
                               "it becomes active when that goal is done, or earlier with work_select.")
        return _after(doc, f"Added {node}.")
    if name == "work_node_update":
        node_id = str(args.get("id") or "")
        mutable = {'status', 'title', 'note', 'progress', 'evidence', 'ref', 'waiting_on', 'priority'}
        if not any(key in args and args[key] is not None for key in mutable):
            raise tree.WorkPlanError(
                'NO CHANGE: work_node_update requires at least one mutation field. '
                'id and expected_version only read nothing and change nothing. '
                'To clear a verified wait, supply waiting_on="" explicitly; '
                'do not repeat this empty call. Use work_status to inspect, or continue the next runnable item.')

        # Re-sending the identical mutation after a lost response is safe. When
        # it already matches current state, accept a future/stale expected
        # version without writing; a genuinely new mutation still uses strict CAS.
        current = store.read()
        existing = next((node for node in current['nodes'] if node['id'] == node_id), None)
        if existing is None:
            raise tree.WorkPlanError(f'unknown Work item: {node_id}')
        supplied = {key: args[key] for key in mutable if key in args and args[key] is not None}
        if args.get("priority") == "P0":
            if existing.get("parent_id") is not None:
                raise tree.WorkPlanError("priority belongs to a goal, not a detail item")
            raise tree.WorkPlanError("P0 existing-root priority is applied by the authenticated Work host")
        replayed = all(existing.get(key, '') == value for key, value in supplied.items())
        if replayed and expected != current['version']:
            doc, node, before, version_before = current, existing, current.get('active_root'), current['version']
        else:
            def update(d: dict[str, Any]) -> tuple[dict[str, Any], str | None, int]:
                before = d.get("active_root")
                return tree.update_node(
                    d, node_id, status=args.get("status"), title=args.get("title"),
                    note=args.get("note"), progress=args.get("progress"), evidence=args.get("evidence"),
                    ref=args.get("ref"), waiting_on=args.get("waiting_on"),
                    priority=args.get("priority"),
                ), before, d.get("version", 0)

            doc, (node, before, version_before) = store.transaction(update, expected_version=expected)
        if doc.get("version", 0) == version_before:
            message = (f"NO CHANGE: {node['id']} already had these values; version {version_before} is unchanged. "
                       "Do not repeat this call.")
        else:
            message = f"Updated {node['id']} ({node['status']})."
        wait = node.get('waiting_on', '')
        if wait:
            message += (f" WAITING: waiting_on={json.dumps(wait, ensure_ascii=False)}. "
                        'This item is still excluded from execution. Evidence/notes do NOT clear a wait. '
                        'To clear this exact wait after verifying its prerequisite, explicitly send '
                        f'work_node_update(id={json.dumps(node_id)}, waiting_on=""). '
                        'Ancestor waits/blocks remain independent; do not clear approval or user holds without authorization.')
        elif 'waiting_on' in args:
            message += ' waiting_on is empty on this node; ancestor waits/blocks may still exclude it.'
        else:
            message += ' waiting_on was not supplied; existing wait state was not changed.'
        resume = tree.continuation(doc)
        if resume.get('next'):
            message += f" Next runnable item: {resume['next']['id']} (goal {resume['next']['root_id']})."
        if before and doc.get("active_root") != before:
            message += (f" Goal {before} is done; the next queued goal {doc['active_root']} is now active in the "
                        f"{doc['phase']} phase. Review its plan; call work_execute if it is planning.")
        elif doc.get("active_root") and tree.progress(doc)[1] and tree.next_leaf(doc) is None:
            message += " Every step of the active goal is done; Work returned to the planning phase for the next goal."
        return _after(doc, message)
    if name == "work_node_remove":
        node_id = str(args.get("id") or "")
        doc, removed = store.transaction(lambda d: tree.remove_node(d, node_id), expected_version=expected)
        return _after(doc, f"Removed {removed} node(s).")
    if name == "work_select":
        root_id = str(args.get("root_id") or "")
        doc, _ = store.transaction(lambda d: tree.select_root(d, root_id,
            user_requested=args.get("user_requested") is True), expected_version=expected)
        return _after(doc, f"Goal {root_id} is active; Work is in the {doc['phase']} phase.")
    if name == "work_reorder":
        doc, _ = store.transaction(lambda d: tree.reorder_root(
            d, str(args.get("root_id") or ""), args.get("position"),
            user_requested=args.get("user_requested") is True), expected_version=expected)
        return _after(doc, "Goal priority updated.")
    if name == "work_focus":
        doc, _ = store.transaction(lambda d: tree.set_focus(d, args.get("node_ids"),
            user_requested=args.get("user_requested") is True), expected_version=expected)
        return _after(doc, "Work focus updated; focused steps run first and other approved items continue when none can run." if doc.get("focus_ids") else "Work focus cleared; normal priority resumed.")
    if name == "work_urgent":
        spec = {"title": args.get("goal"), "note": args.get("note", ""),
                "ref": args.get("ref", ""), "children": args.get("children") or []}
        doc, root = store.transaction(lambda d: tree.add_urgent(
            d, spec, user_requested=args.get("user_requested") is True), expected_version=expected)
        return _after(doc, f"Urgent goal {root} is active; all other goals are held until it finishes. Review its plan and call work_execute.")
    if name == "work_execute":
        return _execute(store, approval_ask, args.get("user_confirmed") is True, expected_version=expected)
    if name == "work_replan":
        doc, _ = store.transaction(lambda d: tree.set_phase(d, tree.PHASE_PLANNING), expected_version=expected)
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
        tree.set_phase(doc, tree.PHASE_EXECUTING)
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
    # Versions advance only when a change commits; state the real one so a
    # follow-up mutation never has to guess it.
    return f"{message}\nVersion: {doc.get('version', 0)} (expected_version for the next change)\n\n{tree.render(doc)}"


def _expected_version(args: dict[str, Any]) -> int | None:
    value = args.get("expected_version")
    if value is None or value == "":
        return None
    if type(value) is not int or value < 0:
        raise tree.WorkPlanError("expected_version must be a nonnegative integer")
    return value
