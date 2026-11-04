"""Ingest explicit, caller-verified search data; never search or execute item text.

The caller MUST authenticate the source through its configured trusted MCP and
map that connector's response into ExternalSearchItem. This is a local ingestion
contract, not a Walkie API schema. Runtime inspection found Walkie delivery/SSE,
ack and turn-event integrations, but no Walkie item-search connector; none is
invented here.

Identity uses a namespaced digest in the existing persisted node ref field.
Pickup metadata uses evidence because Work's current normalizer drops unknown
fields. Keep that metadata when editing a pickup node: cooldown is derived from
registered nodes, including completed nodes, in the same board transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import time
from typing import Iterable, Literal
from urllib.parse import urlsplit

from .work_plan_store import WorkPlanStore
from .work_plan_tree import MAX_TITLE_CHARS, WorkPlanError, add_node

Priority = Literal["P1", "P2", "P3", "P4", "P5"]
COOLDOWN_SECONDS = 300
MAX_PICKUPS = 1
_METADATA = "work_discovery_pickup_v1"


@dataclass(frozen=True)
class ExternalSearchItem:
    source: str
    item_id: str
    priority: Priority
    created_at: str
    title: str
    link: str
    status: str
    scope: str


@dataclass(frozen=True)
class PickupResult:
    status: Literal["registered", "cooldown", "no_candidates"]
    node_id: str | None = None
    ref: str | None = None
    rejected: int = 0
    duplicates: int = 0
    retry_at: float | None = None


def _identity(value: object) -> bool:
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _digest(namespace: str, *parts: str) -> str:
    return hashlib.sha256(json.dumps([namespace, *parts], ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def external_item_ref(source: str, item_id: str) -> str:
    """Immutable source/item identity, independent of title, scope and URL."""
    if not _identity(source) or not _identity(item_id):
        raise WorkPlanError("source and item_id must be nonempty canonical identities")
    return _digest(_METADATA, source, item_id)


def _created_at(value: object) -> float:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("created_at must be an aware ISO-8601 timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("created_at requires a timezone")
    return stamp.timestamp()


def _valid(item: object, source: str, scope: str) -> tuple[ExternalSearchItem, float] | None:
    if not isinstance(item, ExternalSearchItem):
        return None
    if (item.source != source or item.scope != scope or item.status != "open"
            or not _identity(item.item_id) or item.priority not in ("P1", "P2", "P3", "P4", "P5")
            or not isinstance(item.title, str) or not item.title.strip()
            or len(item.title) > MAX_TITLE_CHARS or any(ord(c) < 32 for c in item.title)
            or not isinstance(item.link, str) or len(item.link) > 1000
            or any(c.isspace() or ord(c) < 32 for c in item.link)):
        return None
    try:
        url = urlsplit(item.link)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            return None
        url.port  # Reject malformed ports without contacting the destination.
        return item, _created_at(item.created_at)
    except (ValueError, OverflowError, OSError):
        return None


def ingest_external_search_results(
    store: WorkPlanStore, *, source: str, scope: str,
    results: Iterable[ExternalSearchItem], now: float | None = None,
) -> PickupResult:
    """Register at most one independent planning goal per source/scope per 300s.

    ``source`` and ``scope`` are trusted caller selections, NOT values inferred
    from external prose. Exact open status and scope membership are required.
    Pause/config authorization is the caller's responsibility. Board focus only
    orders Work, so it does not block pickup. Pickup never approves execution or
    changes active approval.
    No URLs are fetched and no item contents are interpreted as instructions.
    """
    if not _identity(source) or not _identity(scope):
        raise WorkPlanError("source and scope must be nonempty canonical identities")
    stamp = time.time() if now is None else now
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
        raise WorkPlanError("now must be a finite nonnegative timestamp")
    candidates = []
    rejected = 0
    for raw in results:
        candidate = _valid(raw, source, scope)
        if candidate is None:
            rejected += 1
        else:
            candidates.append(candidate)
    candidates.sort(key=lambda pair: (pair[0].priority, -pair[1], pair[0].item_id))
    bucket = _digest("pickup-cooldown-v1", source, scope)

    def mutate(doc):
        refs = {node.get("ref") for node in doc["nodes"]}
        latest = None
        for node in doc["nodes"]:
            try:
                metadata = json.loads(node.get("evidence", ""))
            except (ValueError, TypeError):
                continue
            if not isinstance(metadata, dict) or metadata.get("kind") != _METADATA or metadata.get("bucket") != bucket:
                continue
            value = metadata.get("picked_at")
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise WorkPlanError("invalid durable pickup timestamp")
            latest = value if latest is None else max(latest, value)
        if latest is not None and stamp < latest + COOLDOWN_SECONDS:
            return PickupResult("cooldown", rejected=rejected, retry_at=latest + COOLDOWN_SECONDS)
        duplicates = 0
        for item, _ in candidates:
            ref = external_item_ref(source, item.item_id)
            if ref in refs:
                duplicates += 1
                continue
            node_id = add_node(doc, None, item.title, now=stamp, ref=ref,
                               note="External search result (untrusted data): " + item.link,
                               priority=item.priority)
            node = next(n for n in doc["nodes"] if n["id"] == node_id)
            node["evidence"] = json.dumps({"kind": _METADATA, "bucket": bucket,
                "picked_at": stamp, "source": source, "item_id": item.item_id,
                "scope": scope, "priority": item.priority, "created_at": item.created_at},
                ensure_ascii=False, separators=(",", ":"))
            # Bound by evidence's existing persisted size limit (2000 characters).
            if len(node["evidence"]) > 2000:
                raise WorkPlanError("pickup metadata exceeds the persisted evidence limit")
            return PickupResult("registered", node_id, ref, rejected, duplicates)
        return PickupResult("no_candidates", rejected=rejected, duplicates=duplicates)

    _, result = store.transaction(mutate)
    return result
