"""Tool schema registry and input repair service."""

from __future__ import annotations

import json
import re
from typing import Any, Callable


def _noop_log(level: str, message: str) -> None:
    del level, message


def tool_schema_in_body(body: dict[str, Any], tool_name: str) -> dict[str, Any] | None:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if isinstance(tool, dict) and str(tool.get("name") or "") == tool_name:
            schema = tool.get("input_schema")
            return schema if isinstance(schema, dict) else None
    return None
TASK_UPDATE_STATUSES = {"pending", "in_progress", "completed", "deleted"}
TASK_UPDATE_STATUS_ALIASES = {
    "active": "in_progress",
    "assigned": "in_progress",
    "current": "in_progress",
    "doing": "in_progress",
    "inprogress": "in_progress",
    "in_progress": "in_progress",
    "in-progress": "in_progress",
    "in progress": "in_progress",
    "ongoing": "in_progress",
    "processing": "in_progress",
    "running": "in_progress",
    "started": "in_progress",
    "working": "in_progress",
    "complete": "completed",
    "completed": "completed",
    "done": "completed",
    "finished": "completed",
    "resolved": "completed",
    "success": "completed",
    "closed": "completed",
    "open": "pending",
    "pending": "pending",
    "queued": "pending",
    "todo": "pending",
    "to_do": "pending",
    "to-do": "pending",
    "waiting": "pending",
    "cancel": "deleted",
    "cancelled": "deleted",
    "canceled": "deleted",
    "delete": "deleted",
    "deleted": "deleted",
    "remove": "deleted",
    "removed": "deleted",
}

_TOOL_SCHEMA_REGISTRY: dict[str, dict[str, Any]] = {}

_BUILTIN_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "Bash": {
        "required": ["command"],
        "properties": {
            "command": {"type": "string"},
            "description": {"type": "string"},
            "timeout": {"type": "integer"},
            "run_in_background": {"type": "boolean"},
        },
    },
    "Read": {
        "required": ["file_path"],
        "properties": {
            "file_path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "Write": {
        "required": ["file_path", "content"],
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
        },
    },
    "Edit": {
        "required": ["file_path", "old_string", "new_string"],
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
    },
    "Glob": {
        "required": ["pattern"],
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
        },
    },
    "Grep": {
        "required": ["pattern"],
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "output_mode": {"type": "string"},
        },
    },
    "TaskList": {
        "required": [],
        "properties": {},
    },
    "TaskUpdate": {
        "required": ["taskId"],
        "properties": {
            "taskId": {"type": "string"},
            "subject": {"type": "string"},
            "description": {"type": "string"},
            "activeForm": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]},
            "owner": {"type": "string"},
            "addBlocks": {"type": "array"},
            "addBlockedBy": {"type": "array"},
            "metadata": {"type": "object"},
        },
    },
    "TaskCreate": {
        "required": ["subject", "description"],
        "properties": {
            "subject": {"type": "string"},
            "description": {"type": "string"},
        },
    },
    "TaskGet": {
        "required": ["taskId"],
        "properties": {
            "taskId": {"type": "string"},
        },
    },
    "TaskStop": {
        "required": ["task_id"],
        "properties": {
            "task_id": {"type": "string"},
        },
    },
    "CronCreate": {
        "required": ["cron", "prompt"],
        "properties": {
            "cron": {"type": "string"},
            "prompt": {"type": "string"},
            "recurring": {"type": "boolean"},
            "durable": {"type": "boolean"},
        },
    },
    "CronDelete": {
        "required": ["id"],
        "properties": {
            "id": {"type": "string"},
        },
    },
    "CronList": {
        "required": [],
        "properties": {},
    },
    "advisor": {
        "required": ["question"],
        "properties": {
            "question": {"type": "string"},
        },
    },
}


def _update_tool_schema_registry(tools: Any) -> None:
    """Cache tool schemas from incoming Anthropic requests."""
    if not isinstance(tools, list):
        return
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not name:
            continue
        _TOOL_SCHEMA_REGISTRY[name] = tool.get("input_schema") or {}


def _lookup_tool_schema(tool_name: str) -> dict[str, Any] | None:
    """Look up a tool schema by name, checking registry then builtins."""
    if tool_name in _TOOL_SCHEMA_REGISTRY:
        return _TOOL_SCHEMA_REGISTRY[tool_name]
    if tool_name in _BUILTIN_TOOL_SCHEMAS:
        return _BUILTIN_TOOL_SCHEMAS[tool_name]
    return None


def _fuzzy_match_tool_name(name: str) -> str | None:
    """Fuzzy match a tool name against known schemas (case-insensitive, prefix)."""
    low = name.lower()
    candidates = list(_TOOL_SCHEMA_REGISTRY.keys()) + list(_BUILTIN_TOOL_SCHEMAS.keys())
    # Exact match first
    for c in candidates:
        if c == name:
            return c
    # Case-insensitive
    for c in candidates:
        if c.lower() == low:
            return c
    # Prefix/substring match
    for c in candidates:
        if low in c.lower() or c.lower() in low:
            return c
    return None


def _coerce_value(value: Any, expected_type: str | None) -> Any:
    """Coerce a value to the expected JSON schema type."""
    if expected_type is None:
        return value
    if isinstance(value, bool) and expected_type == "boolean":
        return value
    if isinstance(value, (int, float)) and expected_type == "integer":
        return int(value)
    if isinstance(value, (int, float)) and expected_type == "number":
        return float(value)
    if isinstance(value, str) and expected_type == "string":
        return value
    if expected_type == "array":
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
            return [text]
        if value is None:
            return []
        return value
    if expected_type == "object":
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return value
    # Coerce string -> integer
    if isinstance(value, str) and expected_type in ("integer", "number"):
        try:
            return int(value) if expected_type == "integer" else float(value)
        except Exception:
            pass
    # Coerce string -> boolean
    if isinstance(value, str) and expected_type == "boolean":
        low = value.lower()
        if low in ("true", "yes", "on", "1"):
            return True
        if low in ("false", "no", "off", "0"):
            return False
    # Coerce int/float -> string
    if isinstance(value, (int, float)) and expected_type == "string":
        return str(value)
    # Coerce anything -> string as last resort
    if expected_type == "string" and value is not None:
        return str(value)
    return value


def normalize_task_update_status(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = re.sub(r"[\s\-]+", "_", text.lower())
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    if normalized in TASK_UPDATE_STATUSES:
        return normalized
    return TASK_UPDATE_STATUS_ALIASES.get(text.lower()) or TASK_UPDATE_STATUS_ALIASES.get(normalized)


def _default_for_missing_required(tool_name: str, field: str) -> Any:
    """Return a safe default for known required fields."""
    defaults: dict[str, dict[str, Any]] = {
        "Bash": {"command": "true", "timeout": 30000, "description": "", "run_in_background": False},
        "Read": {"offset": 0, "limit": 0},
        "Edit": {"replace_all": False},
        "Glob": {"path": "."},
        "Grep": {"output_mode": "content"},
        "TaskCreate": {"description": ""},
        "TaskStop": {},
    }
    return defaults.get(tool_name, {}).get(field)


def _is_empty_value(value: Any) -> bool:
    """Check if a value is effectively empty and should be defaulted."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _move_first_present(fixed: dict[str, Any], target: str, aliases: tuple[str, ...]) -> None:
    """Move the first non-empty alias value to the target field."""
    if target in fixed and not _is_empty_value(fixed.get(target)):
        return
    for alias in aliases:
        value = fixed.get(alias)
        if not _is_empty_value(value):
            fixed[target] = value
            break


def _validate_and_fix_tool_input(
    tool_name: str,
    input_dict: dict[str, Any],
    source_body: dict[str, Any] | None = None,
    log: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """
    Validate tool_use input against schema and fix common errors:
      - fuzzy-match tool name
      - coerce types to match schema
      - add defaults for missing required fields
      - keep unknown fields (Claude Code may accept extra fields)
    """
    schema = tool_schema_in_body(source_body, tool_name) if isinstance(source_body, dict) else None
    if schema is None:
        schema = _lookup_tool_schema(tool_name)
    matched_name = tool_name
    if schema is None:
        matched = _fuzzy_match_tool_name(tool_name)
        if matched:
            matched_name = matched
            schema = _lookup_tool_schema(matched)

    if schema is None:
        # No schema known: just ensure it's a dict and return
        return input_dict if isinstance(input_dict, dict) else {}

    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fixed: dict[str, Any] = {}

    for key, raw_value in input_dict.items():
        prop_schema = properties.get(key)
        if prop_schema is None:
            # Unknown field: keep it rather than dropping it.
            # Claude Code may accept fields not in our static registry.
            fixed[key] = raw_value
            continue
        expected_type = prop_schema.get("type") if isinstance(prop_schema, dict) else None
        fixed[key] = _coerce_value(raw_value, expected_type)

    if matched_name == "TaskUpdate":
        if "taskId" not in fixed or _is_empty_value(fixed.get("taskId")):
            for alias in ("task_id", "id"):
                value = fixed.get(alias)
                if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip():
                    fixed["taskId"] = str(value)
                    break
        if "status" in fixed:
            status = normalize_task_update_status(fixed.get("status"))
            if status:
                fixed["status"] = status
        for alias in ("task_id", "id"):
            fixed.pop(alias, None)

    if matched_name == "CronCreate":
        _move_first_present(
            fixed,
            "cron",
            ("schedule", "cronExpression", "cron_expression", "expression", "interval", "time"),
        )
        _move_first_present(
            fixed,
            "prompt",
            ("message", "task", "instruction", "instructions", "command", "query"),
        )
        for key in ("cron", "prompt", "recurring", "durable"):
            prop_schema = properties.get(key)
            expected_type = prop_schema.get("type") if isinstance(prop_schema, dict) else None
            if key in fixed:
                fixed[key] = _coerce_value(fixed[key], expected_type)
        for alias in (
            "schedule",
            "cronExpression",
            "cron_expression",
            "expression",
            "interval",
            "time",
            "message",
            "task",
            "instruction",
            "instructions",
            "command",
            "query",
        ):
            fixed.pop(alias, None)

    if matched_name == "CronDelete":
        _move_first_present(fixed, "id", ("taskId", "task_id", "jobId", "job_id", "cronId", "cron_id"))
        if "id" in fixed:
            fixed["id"] = _coerce_value(fixed["id"], "string")
        for alias in ("taskId", "task_id", "jobId", "job_id", "cronId", "cron_id"):
            fixed.pop(alias, None)

    if matched_name == "CronList":
        fixed = {}

    # Fill in missing or empty required fields with defaults
    injected: list[str] = []
    for req in required:
        if req not in fixed or _is_empty_value(fixed.get(req)):
            default = _default_for_missing_required(matched_name, req)
            if default is not None:
                fixed[req] = default
            elif matched_name == "TaskUpdate" and req == "taskId":
                continue
            elif req not in fixed:
                # No known default: inject empty value matching expected type
                prop_schema = properties.get(req)
                expected_type = prop_schema.get("type") if isinstance(prop_schema, dict) else None
                if expected_type == "string":
                    fixed[req] = ""
                elif expected_type == "integer":
                    fixed[req] = 0
                elif expected_type == "number":
                    fixed[req] = 0.0
                elif expected_type == "boolean":
                    fixed[req] = False
                elif expected_type == "array":
                    fixed[req] = []
                elif expected_type == "object":
                    fixed[req] = {}
                else:
                    fixed[req] = ""
            injected.append(req)

    if injected:
        (log or _noop_log)("WARN", f"tool_guard: {matched_name}: injected missing required fields: {', '.join(injected)}")

    return fixed


def _missing_required_tool_fields(tool_name: str, input_dict: dict[str, Any], source_body: dict[str, Any] | None = None) -> list[str]:
    schema = tool_schema_in_body(source_body, tool_name) if isinstance(source_body, dict) else None
    if schema is None:
        schema = _lookup_tool_schema(tool_name)
    if not isinstance(schema, dict):
        return []
    required = schema.get("required") or []
    if not isinstance(required, list):
        return []
    missing: list[str] = []
    for field in required:
        if not isinstance(field, str):
            continue
        if field not in input_dict or _is_empty_value(input_dict.get(field)):
            missing.append(field)
    return missing

__all__ = [
    "_fuzzy_match_tool_name",
    "_lookup_tool_schema",
    "_missing_required_tool_fields",
    "_update_tool_schema_registry",
    "_validate_and_fix_tool_input",
    "normalize_task_update_status",
]
