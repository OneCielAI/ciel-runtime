"""Work texts a model or the person reads, as data (user 2026-11-19).

Every text here is a managed prompt: the default lives in this file, an
override is stored by work_workflow_prompts (REST, web, ctl, menu) and applies
without a restart. Keys starting with ``mod_`` are used by the ciel-work mod:
``mod_texts_js`` generates its defaults file from this one source, and the mod
reads stored overrides from the router every gate tick.
"""
from __future__ import annotations

import json

TEXTS: dict[str, str] = {
    # Shown when the Work status cannot be read.
    'mod_status_unavailable': (
        "Work status unavailable: router connection or snapshot could not be verified."),
    # Refusal of a direct edit while the plan is not approved.
    'mod_planning': (
        "Work mode is planning: direct file edits wait until the plan is approved. Build it with work_plan_se"
        "t, then call work_execute. (/work-mode help)"),
    # One line for the person at session start.
    'mod_notice': (
        "Ciel Work mode is on: plan and execute by item; new independent requests queue without pausing appro"
        "ved Work. Items show above the prompt · /work-mode help · turn off: ciel-runtime menu → LLM options "
        "→ Work mode, or the agent (work_mode_set)"),
    # Main's Work mode instruction, added to every main request.
    'mod_instructions': (
        "Ciel Work mode is hybrid per-item planning and execution, not native Plan Mode. Before edits or stat"
        "e-changing commands for an unapproved goal, use work_plan_set: goal -> phases -> tasks -> concrete v"
        "erifiable steps. Triage every new user request first: an independent request becomes its own goal (r"
        "oot-level item), its title starting with its item number where possible (\"1. ...\", \"2. ...\"); a requ"
        "est that changes or extends an existing item goes under that item as a child (work_node_add with par"
        "ent_id). A new goal queues behind the unfinished active goal and becomes active when it is done; kee"
        "p the active goal going. Read-only exploration is allowed while planning. Call work_execute when rea"
        "dy; auto approval is default and configured ask approval uses the Work dialog. Never supply user_con"
        "firmed yourself. During execution mark the current leaf in_progress with work_node_update, then done"
        " only with verification evidence. Add/split nodes using work_node_add with within_scope=true only fo"
        "r refinements wholly within already approved scope; otherwise leave it false and review/approve the "
        "changed scope. Planning queued goals must not pause approved active work. Use work_replan when the a"
        "pproach changes. Use work_status(root_id=...) for exact goal details only when needed, and expected_"
        "version for mutations. Automatic context excludes queued details, but raw user messages remain in na"
        "tive history; this is not history isolation. Bash and PowerShell permission decisions belong to the "
        "native host, not the Work board. Work does not inspect shell side effects or override native permiss"
        "ions; keep commands within the approved scope. These workflow rules include subagents: do not use a "
        "shell or delegate changes to evade required planning or approval. Set ref for a linked id, and waiti"
        "ng_on for what an item waits on (empty text clears). When the user asks to prioritize specific exist"
        "ing items, resolve the exact IDs from Work status and call work_focus with ordered node_ids and user"
        "_requested=true. Focus orders the work first but does not hold unrelated approved items: when no foc"
        "used step can run, keep working other approved items and discovery. Focus clears automatically once "
        "every focused step is done; node_ids=[] clears it now. Do not duplicate goals or mark unrelated item"
        "s blocked. Use work_reorder to change root order: queue-only by default; changing the active slot ne"
        "eds an explicit user request and user_requested=true, except that when every active step waits or is"
        " blocked, work_select may move to the queued goal holding the next runnable step. On an explicit urg"
        "ent/ad-hoc request use work_urgent with user_requested=true to hold other goals, break down the requ"
        "est and execute; held goals resume afterwards."),
    # Auto continue could not read the Work status.
    'mod_stop_unavailable': (
        "Ciel Work auto continue could not read the Work status, so unfinished items were not checked. /work-"
        "mode status"),
    # The plan waits for approval.
    'mod_stop_approval': (
        "Ciel Work stopped: the plan is ready and waits for your approval before execution. Ask the model to "
        "execute it (you approve in the Work dialog) or request changes. /work-mode tree"),
    # Wake prompt after a workflow host message.
    'mod_workflow_wake': (
        "Ciel Work workflow wake: a host message beginning \"Ciel Work workflow\" was added to this conversatio"
        "n. Read it and do what it asks. This wake holds none of it and grants no approval."),
    # The review call signature quoted in review notices.
    'mod_review_call': (
        "work_parallel_item_review(lease_id, decision, evidence, expected_finalization_version, expected_revi"
        "ew_pin)"),
    # Parallel execution is disabled because the fork gate mod is not loaded.
    'mod_fork_gate_missing': (
        "Work fork gate not loaded: parallel execution disabled"),
    # A launch attempt whose owner is unknown.
    'mod_owner_unknown': (
        "Owner of this launch attempt is unknown; resolve it from the Work pane."),
    # A launch attempt owned by another live host.
    'mod_owner_live': (
        "Another live Work host owns this launch attempt; waiting for that host."),
}


MOD_PREFIX = "mod_"
MOD_TEXTS_FILE = "texts.generated.js"


def mod_defaults() -> dict[str, str]:
    return {name: text for name, text in TEXTS.items() if name.startswith(MOD_PREFIX)}


def mod_texts_js() -> str:
    """The ciel-work mod's defaults module, generated from TEXTS."""
    body = json.dumps(mod_defaults(), ensure_ascii=False, indent=1, sort_keys=True)
    return (
        "// GENERATED from ciel_runtime_support/work_texts.py (mod_texts_js). Do not edit:\n"
        "// change the text there and run python -m ciel_runtime_support.work_texts.\n"
        "export const TEXTS = " + body + "\n"
        "let overrides = {}\n"
        "// Stored overrides from the router (work_side_texts); they apply without a restart.\n"
        "export function setTextOverrides(map) { overrides = map && typeof map === 'object' ? map : {} }\n"
        "export function T(name, vars) {\n"
        "  let text = typeof overrides[name] === 'string' && overrides[name].trim() ? overrides[name] : TEXTS[name]\n"
        "  if (typeof text !== 'string') return String(name)\n"
        "  if (vars) for (const key of Object.keys(vars)) text = text.split('{' + key + '}').join(String(vars[key]))\n"
        "  return text\n"
        "}\n")


def write_mod_texts() -> str:
    from pathlib import Path
    path = Path(__file__).resolve().parent / "claude_mods" / "ciel-work" / "hooks" / MOD_TEXTS_FILE
    path.write_bytes(mod_texts_js().encode("utf-8"))
    return str(path)


if __name__ == "__main__":
    print(write_mod_texts())


__all__ = ["MOD_PREFIX", "MOD_TEXTS_FILE", "TEXTS", "mod_defaults", "mod_texts_js", "write_mod_texts"]
