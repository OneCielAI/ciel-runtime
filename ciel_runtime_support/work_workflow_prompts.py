"""Managed per-stage workflow prompts (spec 9.1).

Each stage's instruction is data, not code: a default here, an optional
override stored in ``<state_dir>/workflow-prompts.json``. The web dashboard,
REST, ciel-runtimectl and the menu list, compare, edit and reset them through
this one module. A change applies from the next request that renders the
prompt; every workflow transition records the digest of the prompt it used.
Writes are compare-and-set against the fingerprint the editor read, so two
editors never overwrite each other silently. Safety rules (approval
verification, the execution gate, required artifacts) live in code and do not
weaken when a prompt is edited.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

FILE = "workflow-prompts.json"
MAX_CHARS = 20000
MAX_HISTORY = 100

DEFAULTS: dict[str, str] = {
    "side_intake": (
        "Workflow intake: an external request that needs implementation becomes one goal (action create). "
        "Restate it in your own words (understanding) so the requester can confirm you understood. Do not "
        "start, plan or approve any work: main writes the technical proposal and the requester approves it."),
    "side_answer": (
        "Answer-only requests are not registered on the board. Answer directly when you can. A simple "
        "question only main can answer goes to main at once (action answer with relay to main); main's "
        "answer returns through the handoff path to the original thread."),
    "side_approval": (
        "Approval events (message.approved / message.rejected) are decided by host code, not by you. Never "
        "treat a text reply such as 'go ahead' as approval. When you tell a requester how to approve, say: "
        "press the approval button on the proposal message."),
    "main_proposal": (
        "Write the technical proposal for this goal and submit it with work_proposal_submit. Required: "
        "request_restated, analysis (current behaviour, cause or design basis, files and evidence you read), "
        "alternatives (what you weighed and why you chose), ui_impact {changes, detail}, risks (impact, how to "
        "revert), spec (scope, what must not change, interfaces, ordered work), steps[] (id S-n, title, input, "
        "procedure, done_when, qa ids) and qa[] (id QA-n, claim, method command|e2e|screenshot|log|manual, how, "
        "expected, evidence, must_fail_before). If the UI changes, add benchmarks[] (product, screen, source, "
        "lesson, decision) and mockups[] (file, state, description) for default, empty, error and mobile "
        "states. Write the spec so a weaker fork model can follow it exactly."),
    "main_approval_request": (
        "Send the approval request yourself with the Walkie tools, in the original thread: a summary of the "
        "proposal, the mockups and benchmark summary when the UI changes, the full proposal as an attachment "
        "or link, the confirmation code {code} in the message body, and approval buttons (approvers = the "
        "requester first, per the Walkie rules). Then register the sent message with work_approval_requested "
        "(conversation_id, message_id, thread_root_id, approvers, proposal_version, mockups_attached). Do not "
        "take a text reply as approval; work starts only after the host verifies message.approved."),
    "proposal_fork": (
        "You are a planning fork running on main's model. Write the detailed technical proposal for the goal "
        "below and submit it with work_proposal_submit; follow the main_proposal rules exactly. Do not edit "
        "files or start work."),
    "worker_execute": (
        "Execute only the assigned step of the approved proposal (digest {digest}). Follow the spec and the "
        "step's procedure; do not change what the spec says must not change. Prove every linked QA with the "
        "evidence it names. Save screenshots, logs and outputs under .work-evidence/{lease}/ in your workspace."),
    "worker_report": (
        "Report completion with work_parallel_report(event=completion) and detail {summary (done and not "
        "done), changes (files, commits, artifact digests), qa_results[] (id, status pass|fail|not_run, "
        "command, exit_code, actual, evidence_files), evidence_files[] (path, kind screenshot|log|output, "
        "sha256), deviations, open_issues}. Every linked QA must appear. Report fail or not_run honestly; a "
        "missing QA, a missing file or a wrong hash is refused."),
    "main_review": (
        "Review this step yourself: open the evidence files and the changes; a fork's 'pass' is not evidence. "
        "Call work_parallel_item_review with qa_verdicts[] (id, verdict pass|fail, note). approve only when "
        "every QA passes and evidence_checked=true. Otherwise rework with {failed_qa[], observed, fix, "
        "evidence_required}."),
    "review_fork": (
        "You are a review fork running on main's model, separate from the fork that implemented this step. "
        "Review it by the main_review rules and submit the decision with work_parallel_item_review."),
    "rework": (
        "Rework requested for QA {failed_qa}. Observed: {observed}. Fix: {fix}. Resubmit with the evidence: "
        "{evidence_required}. Continue in the same workspace and report again with the full report format."),
}


def _path(state_dir: Path) -> Path:
    return Path(state_dir) / FILE


def _read(state_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads(_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"prompts": {}, "history": []}
    if not isinstance(value, dict) or not isinstance(value.get("prompts"), dict):
        return {"prompts": {}, "history": []}
    value["prompts"] = {k: v for k, v in value["prompts"].items() if k in DEFAULTS and isinstance(v, str)}
    value["history"] = [row for row in value.get("history") or [] if isinstance(row, dict)][-MAX_HISTORY:]
    return value


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def effective(state_dir: Path | None, name: str) -> str:
    if name not in DEFAULTS:
        raise KeyError(name)
    stored = _read(state_dir)["prompts"].get(name) if state_dir is not None else None
    return stored if stored is not None else DEFAULTS[name]


def render(state_dir: Path | None, name: str, **values: Any) -> tuple[str, str]:
    """(text, digest) of a prompt with {placeholders} filled; unknown ones stay literal."""
    text = effective(state_dir, name)
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text, fingerprint(text)


def listing(state_dir: Path) -> dict[str, Any]:
    data = _read(state_dir)
    rows = []
    for name, default in DEFAULTS.items():
        current = data["prompts"].get(name, default)
        rows.append({"name": name, "value": current, "default": default, "using_default": name not in data["prompts"],
                     "fingerprint": fingerprint(current), "default_fingerprint": fingerprint(default)})
    return {"prompts": rows, "history": data["history"][-30:]}


class PromptConflict(ValueError):
    """The prompt changed after the editor read it; nothing was saved."""


def _write(state_dir: Path, name: str, value: str | None, expected: Any, source: str) -> dict[str, Any]:
    from .channel_message_repository import exclusive_file_lock
    if name not in DEFAULTS:
        raise ValueError("unknown workflow prompt: " + str(name))
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("expected_fingerprint (the prompt's fingerprint you read) is required")
    path = _path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        data = _read(state_dir)
        before = data["prompts"].get(name, DEFAULTS[name])
        if fingerprint(before) != expected:
            raise PromptConflict(f"prompt {name} changed since it was read; reload and apply again")
        if value is None:
            data["prompts"].pop(name, None)
            after = DEFAULTS[name]
        else:
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_CHARS:
                raise ValueError(f"prompt text must be 1-{MAX_CHARS} characters")
            data["prompts"][name] = value
            after = value
        data["history"] = (data["history"] + [{"name": name, "action": "reset" if value is None else "set",
                                               "before": fingerprint(before), "after": fingerprint(after),
                                               "source": str(source)[:20], "at": time.time()}])[-MAX_HISTORY:]
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(path)
    return {"name": name, "fingerprint": fingerprint(after), "using_default": value is None}


def set_prompt(state_dir: Path, name: str, value: str, expected: Any, *, source: str = "rest") -> dict[str, Any]:
    return _write(state_dir, name, value, expected, source)


def reset_prompt(state_dir: Path, name: str, expected: Any, *, source: str = "rest") -> dict[str, Any]:
    return _write(state_dir, name, None, expected, source)


__all__ = ["DEFAULTS", "FILE", "PromptConflict", "effective", "fingerprint", "listing", "render", "reset_prompt",
           "set_prompt"]
