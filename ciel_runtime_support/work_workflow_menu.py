"""Approval workflow in the Work settings menu (spec 9): status, operations, prompts.

The menu runs the same code as ciel-runtimectl and the web console: a WorkCtl
in-process controller bound to each conversation of this workspace, calling
the REST operator commands (workflow-resolve, workflow-prompt-set/reset).
Two menu rows: ``work_workflow_actions`` (approval wait status, plus one choice
per available operation on each goal) and ``work_workflow_prompts`` (one
choice per stage prompt; Edit replaces it, ``reset:<name>`` restores the
default). Their values are observations, not stored settings.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from .work_workflow import MENU_KEYS as KEYS

ACTIONS_KEY, PROMPTS_KEY = KEYS


def _ctl(load: Callable[[], dict[str, Any]], save: Callable[[dict[str, Any]], Any] | None):
    from .work_ctl import WorkCtl, state_dir
    return WorkCtl(load, save or (lambda _config: None), state_dir())


def goals(load, save=None) -> list[dict[str, Any]]:
    ctl = _ctl(load, save)
    rows = []
    for session in ctl.sessions():
        identity = session.get("session")
        if not identity or not session.get("work_state"):
            continue
        try:
            view = ctl.get(identity, "/ca/work/api/v1/workflow")
        except Exception:
            continue
        for goal in view.get("goals") or []:
            rows.append({**goal, "session": identity})
    return rows


def summary(load, save=None) -> str:
    try:
        rows = goals(load, save)
    except Exception:
        return "unavailable"
    if not rows:
        return "no workflow goals"
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    waiting = [row["root_id"] for row in rows if row["state"] == "awaiting_approval"]
    return (", ".join(f"{state} {count}" for state, count in sorted(counts.items()))
            + (" · awaiting approval: " + ", ".join(waiting[:8]) if waiting else ""))


def action_choices(load, save=None) -> list[tuple[str, str]]:
    out = []
    for row in goals(load, save):
        available = []
        if row["state"] == "awaiting_approval":
            available.append("resend")
        if row["state"] == "escalated":
            available += ["escalate_main", "escalate_model"]
        if row["state"] not in ("completed", "cancelled"):
            available.append("cancel")
        for action in available:
            label = f"{row['root_id']} {row['title'][:40]} [{row['state']}] → {action}"
            out.append((label, json.dumps({"session": row["session"], "root_id": row["root_id"], "action": action})))
    return out


def prompt_choices(load, save=None) -> list[tuple[str, str]]:
    from .work_ctl import state_dir
    from .work_workflow_prompts import listing
    out = []
    for row in listing(state_dir())["prompts"]:
        mark = "default" if row["using_default"] else "edited"
        out.append((f"{row['name']} ({mark})", row["name"]))
        if not row["using_default"]:
            out.append((f"{row['name']} → reset to default", "reset:" + row["name"]))
    return out


def apply(load, save, key: str, value: str) -> dict[str, Any]:
    """One menu operation through the ctl controller (same code as REST and the web)."""
    ctl = _ctl(load, save)
    if key == ACTIONS_KEY:
        try:
            chosen = json.loads(value)
        except ValueError:
            raise ValueError("choose an operation from the workflow list") from None
        action, _, model = str(chosen.get("action") or "").partition("=")
        body = {"root_id": chosen.get("root_id"), "action": action}
        if action == "escalate_model":
            model = model or str(chosen.get("model") or "")
            if not model:
                raise ValueError("escalate_model needs a model: enter escalate_model=<model id> with Other")
            body["model"] = model
        return ctl.post(ctl.require_session(chosen.get("session")), "workflow-resolve", body, None)
    if key == PROMPTS_KEY:
        name, _, text = str(value).partition("=")
        reset = name.startswith("reset:")
        name = name[len("reset:"):] if reset else name
        sessions = [row["session"] for row in ctl.sessions() if row.get("session") and row.get("work_state")]
        if not sessions:
            raise ValueError("no Work conversation in this workspace to record the change with")
        session = ctl.require_session(sessions[0])
        rows = ctl.get(session, "/ca/work/api/v1/workflow/prompts").get("prompts") or []
        row = next((item for item in rows if item["name"] == name), None)
        if row is None:
            raise ValueError("unknown workflow prompt " + name)
        if reset:
            return ctl.post(session, "workflow-prompt-reset", {"name": name, "expected_fingerprint": row["fingerprint"]},
                            None)
        if not text.strip():
            raise ValueError("enter <name>=<new prompt text> with Edit")
        return ctl.post(session, "workflow-prompt-set", {"name": name, "value": text,
                                                         "expected_fingerprint": row["fingerprint"]}, None)
    raise ValueError("unknown workflow menu key")


__all__ = ["ACTIONS_KEY", "KEYS", "PROMPTS_KEY", "action_choices", "apply", "goals", "prompt_choices", "summary"]
