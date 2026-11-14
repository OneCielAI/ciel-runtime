"""Product adapter for authenticated wait-boost host events."""
from __future__ import annotations

from .work_parallel import WorkParallelError
from .work_wait_host_api import WaitHostApi


def production_wait_factory(store, board_store, identity, host):
    """Build the backend from the device ledger and its sibling stores."""
    from pathlib import Path

    from .work_parallel_receipts import coordination_store
    from .work_parallel_wait_boost import WaitBoostStore
    from .work_wait_orchestrator import WaitOrchestrator

    sidecar = WaitBoostStore(Path(store.path).with_name("work-parallel-wait-boost-v1.json"))
    orchestrator = WaitOrchestrator(
        sidecar=sidecar, ledger=store, coordination=coordination_store(store),
    )
    # Cancelling a stale boost needs the accepted pin-terminal edge; until the
    # host provides one, stale rows are only skipped, never selected.
    return orchestrator, lambda row, reason: None


_FACTORY = production_wait_factory


def configure_wait_factory(factory):
    """Test seam; ``None`` restores the production backend."""
    global _FACTORY
    _FACTORY = factory if factory is not None else production_wait_factory


def dispatch_wait_event(store, board_store, identity, host, args):
    if type(args) is not dict or type(args.get("wait_args")) is not dict:
        raise WorkParallelError("wait boost arguments required")
    orchestrator, cancel_stale = _FACTORY(store, board_store, identity, host)
    api = WaitHostApi(orchestrator, cancel_stale=cancel_stale)
    event = args.get("event")
    mapping = {
        "wait_boost_reserve": "reserve",
        "wait_boost_workspace_bind": "workspace_bind",
        "wait_boost_reacquire_slot": "reacquire_slot",
        "wait_boost_reacquire_result": "reacquire_result",
        "wait_boost_cleanup": "cleanup",
    }
    normalized = mapping.get(event)
    if normalized is None:
        raise WorkParallelError("unknown wait boost host event")
    request = {
        "event": normalized,
        "args": args.get("wait_args"),
        "_host": {"internal": True, "scope": identity},
    }
    return api.dispatch(request)


def project_wait_for_status(value):
    from .work_wait_surfaces import project_wait_status
    return project_wait_status(value)
