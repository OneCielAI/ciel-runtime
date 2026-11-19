"""Approval workflow hooks the parallel ledger code calls (work_parallel_mcp).

Kept apart from work_workflow_tools so the support import graph stays acyclic:
work_parallel_mcp imports this module, and work_workflow_tools imports both.
It holds the session-folder bodies (spec 7), the completion-report check and
record (spec 4.6), main's per-QA review record (spec 4.7) and the escalation
model override the fork gate applies.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from . import work_plan_tree as tree
from . import work_workflow as wf

MAX_EVIDENCE_BYTES = 20 * 1024 * 1024
MAX_EVIDENCE_TOTAL = 100 * 1024 * 1024


def snapshot(store: Any) -> dict[str, Any]:
    """The board document; some callers pass a store wrapper without read_snapshot."""
    reader = getattr(store, "read_snapshot", None)
    return reader() if callable(reader) else store.read()


# ---------------------------------------------------------------- session folder

def folder(store: Any, root_id: str) -> Path:
    safe = "".join(ch for ch in str(root_id) if ch.isalnum() or ch in "-_")[:64] or "root"
    return Path(store.path).parent / "workflow" / safe


def save_body(store: Any, root_id: str, kind: str, body: Any) -> str:
    """Write one immutable body (proposal, report, review) addressed by its digest."""
    # Addressed by the canonical digest the board record carries (wf.digest).
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, indent=1)
    value = wf.digest(body)
    path = folder(store, root_id) / f"{kind}-{value[:24]}.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    return value


def load_body(store: Any, root_id: str, kind: str, value: str) -> Any:
    try:
        return json.loads((folder(store, root_id) / f"{kind}-{value[:24]}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def model_overrides(board: Mapping[str, Any] | None, leases: Mapping[str, Any], config: Mapping[str, Any]) -> dict:
    """lease id -> {alias, upstream} for steps an escalation moved to another model."""
    if not board or not board.get("workflows"):
        return {}
    chosen = {}
    for row in board["workflows"].values():
        for item_id, step in (row.get("steps") or {}).items():
            if step.get("model_override"):
                chosen[item_id] = step["model_override"]
    out = {}
    for lease_id, lease in leases.items():
        model = chosen.get((lease or {}).get("item_id"))
        if not model:
            continue
        try:
            from .work_fork_models import _provider, request_names
            provider, _ = _provider(dict(config))
            alias, upstream = request_names(provider, model)
        except Exception:
            alias = upstream = model
        out[lease_id] = {"alias": alias, "upstream": upstream, "id": model}
    return out


# ---------------------------------------------------------------- report and review hooks

def _workspace(lease: Mapping[str, Any]) -> Path | None:
    workspace = lease.get("workspace")
    path = workspace.get("path") if isinstance(workspace, dict) else None
    if isinstance(path, str) and path and Path(path).is_absolute():
        return Path(path)
    return None


def _resolve(path: str, base: Path | None) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        if base is None:
            raise tree.WorkPlanError(f"evidence path {path} must be absolute (the fork has no workspace)")
        candidate = base / candidate
    resolved = candidate.resolve()
    if base is not None:
        try:
            resolved.relative_to(base.resolve())
        except ValueError:
            raise tree.WorkPlanError(f"evidence file {path} is outside the fork workspace") from None
    return resolved


def report_check(board_store: Any, lease: Mapping[str, Any], args: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate a workflow completion before the ledger accepts it (spec 4.6). None: not a workflow goal."""
    document = snapshot(board_store)
    if not document.get("workflows"):
        return None
    by_id = {node["id"]: node for node in document["nodes"]}
    node = by_id.get(lease["item_id"])
    if node is None:
        return None
    root = node
    while root.get("parent_id"):
        root = by_id[root["parent_id"]]
    record = wf.record(document, root["id"])
    if record is None:
        return None
    try:
        report = wf.validate_report(record, wf.step_for_item(document, root["id"], lease["item_id"]), args.get("detail"))
    except wf.WorkflowError as error:
        raise tree.WorkPlanError("workflow report refused: " + str(error)) from None
    base = _workspace(lease)
    total = 0
    files = []
    for item in report["evidence_files"]:
        path = _resolve(item["path"], base)
        if not path.is_file():
            raise tree.WorkPlanError(f"workflow report refused: evidence file {item['path']} does not exist")
        size = path.stat().st_size
        total += size
        if size > MAX_EVIDENCE_BYTES or total > MAX_EVIDENCE_TOTAL:
            raise tree.WorkPlanError("workflow report refused: evidence exceeds the size limit")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != item["sha256"]:
            raise tree.WorkPlanError(f"workflow report refused: sha256 of {item['path']} does not match")
        files.append((item, path))
    value = wf.digest(report)
    results = report["qa_results"]
    # The coordination receipt keeps its fixed completion shape; the full
    # report is the session-folder body this digest names.
    receipt = {"summary": report["summary"][:4000],
               "coverage": [{"item_id": lease["item_id"], "verified": all(r["status"] == "pass" for r in results),
                             "evidence_digest": value}],
               "verification": "; ".join(f"{r['id']} {r['status']}" for r in results)[:4000] or "no QA"}
    return {"root_id": root["id"], "item_id": lease["item_id"], "report": report, "files": files,
            "digest": value, "receipt_detail": receipt}


def report_record(board_store: Any, checked: Mapping[str, Any], lease: Mapping[str, Any],
                  usage: Mapping[str, Any] | None = None) -> None:
    """After the ledger accepted the completion: keep the evidence and move the goal to review."""
    root_id = checked["root_id"]
    target = folder(board_store, root_id) / "evidence" / str(lease["id"])[:64]
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for item, path in checked["files"]:
        name = item["sha256"][:16] + "-" + "".join(ch for ch in path.name if ch.isalnum() or ch in "._-")[:80]
        destination = target / name
        if not destination.exists():
            shutil.copyfile(path, destination)
        copied.append({**item, "stored": str(destination.relative_to(folder(board_store, root_id)))})
    body = {**checked["report"], "evidence_files": copied, "lease_id": lease["id"], "item_id": checked["item_id"]}
    value = save_body(board_store, root_id, "report", body)
    fork = {"lease_id": lease["id"], "priority": lease.get("priority"), **dict(usage or {})}
    board_store.transaction(lambda doc: wf.report_accepted(doc, root_id, checked["item_id"], lease["id"], body, value,
                                                           fork=fork))


def review_target(board_store: Any, lease: Mapping[str, Any]) -> str | None:
    document = snapshot(board_store)
    if not document.get("workflows"):
        return None
    by_id = {node["id"]: node for node in document["nodes"]}
    node = by_id.get(lease["item_id"])
    while node is not None and node.get("parent_id"):
        node = by_id.get(node["parent_id"])
    if node is None or wf.record(document, node["id"]) is None:
        return None
    step = ((wf.record(document, node["id"]) or {}).get("steps") or {}).get(lease["item_id"])
    return node["id"] if step else None


def review_apply(board_store: Any, root_id: str, lease: Mapping[str, Any], decision: str, args: Mapping[str, Any],
                 *, max_rework: int, reviewer: Mapping[str, Any] | None = None, dry_run: bool = False) -> dict:
    """Validate (dry_run) or record main's per-QA review; returns the recorded entry."""
    def mutate(doc):
        try:
            _, entry = wf.review(doc, root_id, lease["item_id"], lease["id"], decision, args, max_rework=max_rework,
                                 reviewer=reviewer)
        except wf.WorkflowError as error:
            raise tree.WorkPlanError("workflow review refused: " + str(error)) from None
        if entry["decision"] == "approve":
            wf.complete_if_done(doc, root_id)
        return entry
    if dry_run:
        document = json.loads(json.dumps(snapshot(board_store)))
        return mutate(document)
    _, entry = board_store.transaction(mutate)
    save_body(board_store, root_id, "review", entry)
    return entry


def step_approved_after_board(board_store: Any, root_id: str) -> None:
    """A step leaf finished on the board after its approve: complete the goal when nothing is left."""
    try:
        board_store.transaction(lambda doc: wf.complete_if_done(doc, root_id))
    except Exception:
        pass


__all__ = ["folder", "load_body", "model_overrides", "report_check", "report_record", "review_apply",
           "review_target", "save_body", "snapshot", "step_approved_after_board"]
