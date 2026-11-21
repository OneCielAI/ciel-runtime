"""Release orphaned Work fork leases when a Claude session starts.

A lease holds a device fork slot until it is released. A session that crashed,
was closed or was relaunched leaves its leases holding slots, and nothing else
frees them: new forks then wait on capacity indefinitely (measured 2026-11-15,
9 of 8 device slots held by leases whose owners were gone, 0 forks running).

Every Claude launch runs this once before the CLI starts. It releases exactly
the orphans the operator preview would release on its own evidence:

- ``dead``: the owner process recorded at reservation is positively absent
  (``_process_identity`` returned ``""``), the same proof web and ctl use;
- ``legacy_no_owner``: a lease an older build wrote without an owner record,
  only when no running router serves that lease's conversation.

Live and unknown owners are kept, and so is any lease the ledger refuses to
release (a submitted result waiting for its review). A failure never blocks
the launch; it is only counted.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .work_parallel import WorkParallelError

PROOF_DEAD = "startup orphan sweep: owner process identity absent"
PROOF_LEGACY = "startup orphan sweep: no running router serves this conversation"
PROOF_STALE = "orphan sweep: reservation from a build before dispatch names never bound a worker"
# Reservations record an owner since 34966b6 (2026-11-12) and a dispatch name
# since 4eebfdc (2026-11-09). An unbound reservation with neither was written by
# an older build, so no running launch can still bind it; it only blocks a
# device slot, even when its conversation is live again (Walkie 2026-11-16:
# n176 P3 and n212 P2 held the only P3 slot and one of two P2 slots). A lease's
# created_at is its item's creation time, not the reservation time, so age is
# not used.
# While a router runs, the sweep repeats at most this often (it was launch-only:
# a lease whose owner died after the launch held its slot until the next one).
SWEEP_INTERVAL_SECONDS = 60
_last_sweep = {"at": 0.0}
LEGACY_OBSERVATION = {"owner_state": "legacy_no_owner", "host_dead": True,
                      "scope_board_missing": True, "pid_absent": True}


def live_router_scopes(config_dir: Path, process_identity: Callable[[int], str | None]) -> set[str]:
    """Conversations served by a router process that is not positively dead.

    A router instance directory holds ``router.pid`` and the conversation it
    serves in ``compaction-scope.json``. An unreadable pid or an unknown
    process counts as live, so a doubtful scope keeps its leases.
    """
    scopes: set[str] = set()
    base = Path(config_dir) / "router-instances"
    try:
        instances = [path for path in base.iterdir() if path.is_dir()]
    except OSError:
        return scopes
    for instance in instances:
        try:
            scope = json.loads((instance / "compaction-scope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        runtime, session = scope.get("runtime"), scope.get("session_id")
        if runtime not in ("claude", "codex") or not isinstance(session, str) or not session:
            continue
        try:
            pid = int((instance / "router.pid").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            scopes.add(f"{runtime}:{session}")
            continue
        if process_identity(pid) != "":
            scopes.add(f"{runtime}:{session}")
    return scopes


def sweep(store: Any, live_scopes: set[str], preview: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    """Release every provable orphan; return what was released and kept."""
    released: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    tried: set[str] = set()
    for _ in range(256):
        current = preview(store)
        leases = store.read().get("leases", {})

        def stale(item):
            lease = leases.get(item["lease_id"]) or {}
            return (item["owner_state"] == "legacy_no_owner" and lease.get("state") == "reserved"
                    and lease.get("host_id") is None and not lease.get("dispatch_name"))
        row = next((item for item in current["leases"] if item["lease_id"] not in tried and (
            item["owner_state"] == "dead"
            or (item["owner_state"] == "legacy_no_owner" and (item["scope"] not in live_scopes or stale(item))))),
            None)
        if row is None:
            break
        tried.add(row["lease_id"])
        dead = row["owner_state"] == "dead"
        try:
            if not dead and row["scope"] in live_scopes:
                store.release_unbound(row["lease_id"], proof=PROOF_STALE, expected_version=current["version"])
            else:
                store.orphan_release(
                    row["lease_id"], proof=PROOF_DEAD if dead else PROOF_LEGACY,
                    observation={"owner_state": "dead", "observed_identity": ""} if dead else LEGACY_OBSERVATION,
                    expected_version=current["version"])
        except WorkParallelError as error:
            kept.append({"lease_id": row["lease_id"], "owner_state": row["owner_state"], "reason": str(error)[:200]})
            continue
        released.append({"lease_id": row["lease_id"], "scope": row["scope"], "priority": row["priority"],
                         "state": row["state"], "owner_state": row["owner_state"]})
    return {"released": released, "kept": kept}


def sweep_while_running(log: Callable[[str, str], Any], *, now: float | None = None) -> dict[str, int] | None:
    """The same sweep from a running router, at most once per SWEEP_INTERVAL_SECONDS."""
    stamp = time.time() if now is None else now
    if stamp - _last_sweep["at"] < SWEEP_INTERVAL_SECONDS:
        return None
    _last_sweep["at"] = stamp
    return sweep_at_launch(log)


def sweep_at_launch(log: Callable[[str, str], Any]) -> dict[str, int]:
    """The launch phase: never raises, logs one bounded line per release.

    After the lease sweep, the coordination runs and P0 intents of reservations
    that never launched are retired (work_parallel_reconcile): an unbound lease
    released here leaves exactly such halves behind.
    """
    try:
        from .runtime_paths import CONFIG_DIR
        from .work_parallel_mcp import _orphan_preview, ledger, process_identity
        store = ledger()
        result = sweep(store, live_router_scopes(Path(CONFIG_DIR), process_identity),
                       lambda current: _orphan_preview(current, include_dead_parked=True))
    except Exception as error:  # noqa: BLE001 - a sweep failure must not block the launch
        log("WARN", f"work_orphan_sweep_failed error={type(error).__name__}")
        return {"released": 0, "kept": 0, "failed": 1}
    try:
        from .work_parallel_reconcile import reconcile_quietly
        reconcile_quietly(store, log)
    except Exception as error:  # noqa: BLE001 - never blocks the launch or the status read
        log("WARN", f"work_reservation_reconcile_failed error={type(error).__name__}")
    for row in result["released"]:
        log("INFO", f"work_orphan_released lease={row['lease_id'][:12]} priority={row['priority']} "
                    f"state={row['state']} owner={row['owner_state']}")
    for row in result["kept"]:
        log("INFO", f"work_orphan_kept lease={row['lease_id'][:12]} owner={row['owner_state']} "
                    f"reason={json.dumps(row['reason'])}")
    return {"released": len(result["released"]), "kept": len(result["kept"]), "failed": 0}
