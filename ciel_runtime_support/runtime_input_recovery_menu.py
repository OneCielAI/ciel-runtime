"""Standalone preview/confirm panel; parent menu supplies explicit state paths.

This controller imports no runtime_paths and therefore creates no launch lease.
A running parent menu can preview, but confirmation refuses its live launcher.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .runtime_input_recovery import OfflineHistoryRecovery, RecoveryRefused

CONFIRM_PHRASE = "QUARANTINE OLD INPUTS"

def run_recovery_panel(recovery: OfflineHistoryRecovery, *, read=input, write=print):
    preview = recovery.preview()
    write(json.dumps(preview, indent=2))
    write("Original queue and status will be archived byte-for-byte. No old inputs will replay.")
    if not preview["offline"]["verified_stopped"]:
        write("Recovery refused: all router/client/launcher owners must first be verified stopped.")
        return {"ok": False, "reason": "workspace_not_offline"}
    write("Type QUARANTINE OLD INPUTS to explicitly confirm this preview; anything else cancels.")
    if read().strip() != CONFIRM_PHRASE:
        return {"ok": False, "reason": "cancelled"}
    result = recovery.confirm(preview["confirmation_token"], quarantine_old_inputs=True)
    write(json.dumps(result, indent=2))
    return result

def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit offline runtime input history quarantine")
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--preview", action="store_true")
    action.add_argument("--confirm", metavar="PREVIEW_TOKEN")
    action.add_argument("--resume-prepared", metavar="PREVIEW_TOKEN")
    parser.add_argument("--quarantine-old-inputs", action="store_true")
    args = parser.parse_args(argv)
    recovery = OfflineHistoryRecovery(args.state_dir, args.registry, args.workspace)
    try:
        if args.confirm:
            result = recovery.confirm(args.confirm, quarantine_old_inputs=args.quarantine_old_inputs)
        elif args.resume_prepared:
            result = recovery.resume_prepared(args.resume_prepared, quarantine_old_inputs=args.quarantine_old_inputs)
        elif args.preview:
            result = recovery.preview()
        else:
            result = run_recovery_panel(recovery)
    except (RecoveryRefused, OSError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())


def with_recovery_rows(rows, values):
    rows, values = list(rows), list(values)
    index = values.index("back") if "back" in values else len(values)
    rows.insert(index, "Preview offline input-history quarantine")
    values.insert(index, "preview_history_quarantine")
    return rows, values


def preview_menu_messages():
    # Only called inside an already-initialized launcher; never in standalone
    # offline recovery, which must not acquire its own launch lease.
    from .runtime_paths import WORKSPACE_STATE_DIR, WORKSPACE_ROUTER_REGISTRY_PATH, ROUTER_WORKSPACE
    recovery = OfflineHistoryRecovery(WORKSPACE_STATE_DIR, WORKSPACE_ROUTER_REGISTRY_PATH, Path(ROUTER_WORKSPACE))
    try:
        preview = recovery.preview()
        files = preview["files"]
        messages = ["Read-only offline quarantine preview (no data changed)"]
        for name in ("runtime-inputs.jsonl", "runtime-input-status.jsonl"):
            facts = files[name]
            messages.append("{}: {} bytes sha256={} max-known-id={}".format(name, facts["size"], facts["sha256"], facts["max_known_id"]))
        messages.append("Known pending: {}; counts complete={}".format(preview["known_pending"], preview["counts_complete"]))
        messages.append("All router/client/launcher owners must stop before recovery; this menu never quarantines live state.")
        messages.append("Offline utility: python -m ciel_runtime_support.runtime_input_recovery_menu --state-dir <state> --registry <registry> --workspace <workspace> --preview")
        messages.append(f"State: {WORKSPACE_STATE_DIR}; registry: {WORKSPACE_ROUTER_REGISTRY_PATH}; workspace: {ROUTER_WORKSPACE}")
        return messages
    except (OSError, RecoveryRefused) as exc:
        return ["Quarantine preview unavailable: " + str(exc)]
