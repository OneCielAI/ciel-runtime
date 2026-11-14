"""ciel-runtimectl work: Work/SSE read views and operator actions.

Every view and action runs the web operator's own code. ctl builds the same
WorkHttpController the router mounts (work_http_runtime.build_work_http) in
this process and calls ``get``/``post`` on the same REST paths, bound to the
conversation named by ``--session``. Validation, version pins, idempotency and
audit therefore come from one implementation; ctl adds only the explicit
session, the ``--confirm`` gate the web UI shows as a danger confirmation, and
one-line errors.

Why in-process and not loopback REST: a router serves only the conversation
in its own compaction-scope.json (WorkHttpController._identity), so REST cannot
reach a stranded review of a closed conversation, and needs a live router.
Measured in docs/journal/2026/11/13/features/ctl-work-sse-parity/design.md.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable

IDENTITY = re.compile(r"(?:claude|codex):[A-Za-z0-9_-]{1,200}\Z")
SOURCE = "ciel-runtimectl"
COMMANDS = "/ca/work/api/v1/commands/"
# Exit codes: 0 done, 1 refused or failed, 3 needs --confirm (nothing changed).
EXIT_REFUSED, EXIT_CONFIRM = 1, 3

READS = ("sessions", "reviews", "launches", "capacity", "receipts", "receipt", "handoffs", "notices",
         "review-delivery", "audit", "orphan-preview")
WRITES = ("review-approve", "review-rework", "launch-resolve", "legacy-accept", "orphan-release",
          "wake-retry", "reappend-retry", "review-reappend-retry", "notice-retry", "reply-retry",
          "side-review-ok", "side-review-corrected", "handoff-ack", "side-drain",
          "operation-reconcile", "operation-release", "execute")
ACTIONS = READS + WRITES

# The exact danger texts the web console shows before the same commands.
CONFIRM_TEXT = {
    "review-approve": "Approve and finalize {title}? The board item is finalized with this result.",
    "launch-resolve": ("Request resolution of the stuck rework launch for {title}? This does not stop or start "
                       "anything by itself: the Work host first checks its native agent list. If the fork is "
                       "listed, the request is refused (agent present) and the fork is bound; if it is absent, a "
                       "new rework fork may start."),
    "legacy-accept": ("Accept the legacy reviewed result for {title}? The board item is finalized with the result "
                      "its earlier reviewer fork accepted."),
    "orphan-release": "Release lease {lease} for {title}? Its owner process is gone.",
    "orphan-release-legacy": ("Release lease {lease} for {title}? It has no owner record (older build); release "
                              "it only after confirming that session is shut down."),
    "wake-retry": ("Main may already have received this side event. Retrying may deliver it to main twice. "
                   "Continue?"),
    "reappend-retry": ("Main may already have this side event re-appended. Retrying may append it to the "
                       "conversation twice. Continue?"),
    "review-reappend-retry": ("Main may already have this review re-appended. Retrying may append it to the "
                              "conversation twice. Continue?"),
    "notice-retry": "Main may already have this released-wait notice. Retrying can show it to main twice. Continue?",
    "reply-retry": ("The last send outcome is unknown. Resending may post a duplicate reply to the source. "
                    "Continue?"),
    # The console's own text, plus the plan it acts on (the console shows it on screen).
    "execute": ("Start execution for the active plan? Active plan {root} “{title}”, phase {phase}, approval "
                "{approval}."),
    "operation-reconcile": ("Reconcile “{command}” ({op})? This checks the actual store to see whether the command "
                            "took effect. Nothing is re-run automatically. If the store cannot show it, the outcome "
                            "stays unknown."),
    "operation-release": ("Release “{command}” ({op}) as unverified? The store could not show whether it took "
                          "effect: the effect may already have happened; retrying may duplicate it. Releasing only "
                          "frees the key so a new attempt can be made; nothing is re-run automatically."),
}


class CtlError(Exception):
    def __init__(self, message: str, *, code: str = "invalid_request", exit_code: int = EXIT_REFUSED,
                 payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.exit_code, self.payload = code, exit_code, payload or {}


class _Request:
    """The handler shape WorkHttpController reads: path, headers, captured reply."""

    def __init__(self, path: str, headers: dict[str, str] | None = None) -> None:
        self.path = path
        self.headers = dict(headers or {})
        self.status: int | None = None
        self.payload: dict[str, Any] | None = None


def _write_json(handler: _Request, payload: dict[str, Any], status: int = 200) -> None:
    handler.status, handler.payload = status, payload


def state_dir() -> Path:
    """The Work state directory of the workspace ctl runs in, the one its router uses.

    There is deliberately no other-workspace option: the operator path also
    reads this workspace's configuration (Work mode, approval, side settings),
    so a conversation is operated only from its own workspace.
    """
    from . import runtime_paths
    return Path(runtime_paths.WORKSPACE_STATE_DIR)


def workspace_label() -> str:
    from . import runtime_paths
    return str(runtime_paths.ROUTER_WORKSPACE)


def operator_origin() -> dict[str, Any]:
    """The trusted in-process origin the controller records with each receipt."""
    import getpass
    import os
    try:
        user = getpass.getuser()
    except (OSError, KeyError, ImportError):
        user = "unknown"
    user = "".join(char for char in str(user) if ord(char) >= 32)[:64] or "unknown"
    return {"source": "ctl", "os_user": user, "pid": os.getpid()}


class WorkCtl:
    def __init__(self, load_config: Callable[[], dict[str, Any]], save_config: Callable[[dict[str, Any]], Any],
                 root: Path) -> None:
        self.load_config, self.save_config, self.root = load_config, save_config, Path(root)

    # -- the router's own controller, bound to one explicit conversation -----
    def controller(self, session: str):
        from .work_http_controller import WorkHttpController, WorkHttpServices
        from .work_parallel_mcp import ledger, project

        def runtime_status(identity):
            return {"session_id": identity, "runtime": str(identity).split(":", 1)[0], "source": SOURCE}

        return WorkHttpController(WorkHttpServices(
            self.root, self.load_config, lambda: session, runtime_status, _write_json, SOURCE,
            lambda _identity: [], lambda identity, board: project(ledger(), identity, board),
            self.save_config, ledger, operator_origin=operator_origin()))

    def require_session(self, session: str | None) -> str:
        if not session:
            raise CtlError("--session claude:<uuid> is required (list candidates with: work sessions)",
                           code="session_required")
        if IDENTITY.fullmatch(session) is None:
            raise CtlError("--session must be exact claude:<id> or codex:<id>", code="invalid_session")
        # Refused before anything goes through the controller (its reads and
        # every audit receipt can create files): the conversation must already
        # have a Work board or a side inbox in THIS workspace. A device-ledger
        # lease alone does not count; it may belong to another workspace.
        digest = hashlib.sha256(session.encode()).hexdigest()
        if not any((self.root / area / digest).is_dir() for area in ("work-sessions", "side-inboxes")):
            raise CtlError("no Work state for " + session + " in workspace " + workspace_label()
                           + " (run ciel-runtimectl in the folder the session was launched from; see: work sessions)",
                           code="unknown_session")
        return session

    def get(self, session: str, path: str, **query: str) -> dict[str, Any]:
        query = {"session_id": session, **{key: value for key, value in query.items() if value not in (None, "")}}
        request = _Request(path + "?" + urllib.parse.urlencode(query))
        if not self.controller(session).get(request, path):
            raise CtlError("this build's Work REST controller has no " + path, code="unsupported")
        return self._result(request)

    def post(self, session: str, command: str, body: dict[str, Any], operation_id: str | None) -> dict[str, Any]:
        key = operation_id or "ctl-" + uuid.uuid4().hex
        # The audit source comes from the trusted operator_origin of this
        # in-process controller; no header is sent or relied on.
        request = _Request(COMMANDS + command, {"Idempotency-Key": key})
        if not self.controller(session).post(request, COMMANDS + command, {"session_id": session, **body}):
            raise CtlError("this build's Work REST controller has no command " + command, code="unsupported")
        # The exact body is echoed: an idempotent retry repeats --operation-id
        # with these same pins (the REST digest covers the whole request).
        try:
            return {**self._result(request), "request": {"command": command, **body}}
        except CtlError as error:
            error.payload = {**error.payload, "request": {"command": command, **body}}
            raise

    @staticmethod
    def _result(request: _Request) -> dict[str, Any]:
        payload = request.payload if isinstance(request.payload, dict) else {}
        if request.status is None:
            raise CtlError("the Work controller did not answer", code="no_response")
        if request.status >= 400 or payload.get("ok") is False:
            raise CtlError(str(payload.get("message") or payload.get("error") or "refused"),
                           code=str(payload.get("error") or "refused"), payload=payload)
        return payload

    # -- conversation discovery (identity, activity and counts only) ---------
    def _ledger_scopes(self) -> set[str]:
        scopes: set[str] = set()
        try:
            from .work_parallel_mcp import finalization, ledger
            store = ledger()
            scopes |= {lease.get("scope") for lease in store.read().get("leases", {}).values()}
            scopes |= {row.get("scope") for row in (finalization(store).read().get("reviews") or {}).values()
                       if isinstance(row, dict)}
        except (OSError, ValueError):
            pass
        return {scope for scope in scopes if isinstance(scope, str) and IDENTITY.fullmatch(scope)}

    def _candidates(self) -> set[str]:
        """Identities this workspace may hold, from metadata only (never content)."""
        from . import runtime_paths
        found = set(self._ledger_scopes())
        for scope in Path(runtime_paths.CONFIG_DIR).glob("router-instances/*/compaction-scope.json"):
            try:
                value = json.loads(scope.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(value, dict) and value.get("runtime") and value.get("session_id"):
                found.add(str(value["runtime"]) + ":" + str(value["session_id"]))
        for marker in (self.root / "work-sessions").glob("*/work-lineage.json"):
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(value, dict):
                found |= {value.get(key) for key in ("session", "canonical") if isinstance(value.get(key), str)}
        # Claude Code names each transcript of this workspace by its session id.
        import os
        claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        folder = re.sub(r"[^A-Za-z0-9]", "-", str(Path(os.getcwd()).resolve()))
        for transcript in (claude_home / "projects" / folder).glob("*.jsonl"):
            found.add("claude:" + transcript.stem)
        return {identity for identity in found if isinstance(identity, str) and IDENTITY.fullmatch(identity)}

    def sessions(self) -> list[dict[str, Any]]:
        digests: dict[str, dict[str, Any]] = {}
        for area in ("work-sessions", "side-inboxes"):
            base = self.root / area
            if not base.is_dir():
                continue
            for folder in base.iterdir():
                if re.fullmatch(r"[0-9a-f]{64}", folder.name) and folder.is_dir():
                    row = digests.setdefault(folder.name, {"areas": [], "last_activity": 0.0})
                    row["areas"].append(area)
                    for item in folder.iterdir():
                        try:
                            row["last_activity"] = max(row["last_activity"], item.stat().st_mtime)
                        except OSError:
                            pass
        names = {hashlib.sha256(identity.encode()).hexdigest(): identity for identity in self._candidates()}
        pending = self._pending_reviews()
        rows = []
        for digest, row in digests.items():
            identity = names.get(digest)
            entry = {"session": identity, "digest": digest[:16], "last_activity": row["last_activity"] or None,
                     "work_state": "work-sessions" in row["areas"], "side_inbox": "side-inboxes" in row["areas"]}
            if identity is not None:
                entry.update(self._counts(identity, "work-sessions" in row["areas"], "side-inboxes" in row["areas"]))
                entry["pending_reviews"] = pending.get(identity, 0)
            rows.append(entry)
        return sorted(rows, key=lambda item: item["last_activity"] or 0, reverse=True)

    def _pending_reviews(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        try:
            from .work_parallel_mcp import finalization, ledger
            for row in (finalization(ledger()).read().get("reviews") or {}).values():
                if isinstance(row, dict) and row.get("decision") is None and isinstance(row.get("scope"), str):
                    counts[row["scope"]] = counts.get(row["scope"], 0) + 1
        except (OSError, ValueError):
            pass
        return counts

    def _counts(self, identity: str, work: bool, side: bool) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if work:
            try:
                from .work_plan_store import session_work_plan_store
                doc = session_work_plan_store(self.root, identity).read_snapshot()
                result.update(work_version=doc["version"], nodes=len(doc["nodes"]),
                              unfinished=sum(1 for node in doc["nodes"] if node.get("status") != "done"))
            except (OSError, ValueError):
                result["work_state"] = "unreadable"
        if side:
            try:
                from .side_intake_inbox import SideIntakeInbox
                page = SideIntakeInbox(self.root, identity).receipt_page(limit=1)
                result.update(receipts=page["total"], receipt_counts=page["counts"])
            except (OSError, ValueError):
                result["side_inbox"] = "unreadable"
        return result

    # -- operational summary for work status ---------------------------------
    def operational(self, session: str | None) -> dict[str, Any]:
        device: dict[str, Any]
        try:
            from .work_parallel import active_by_priority
            from .work_parallel_mcp import ledger
            d = ledger().read()
            limits = d["policy"]["priority_limits"]
            active = active_by_priority(d["leases"])
            device = {"status": "ok", "ledger_version": d["version"], "device_capacity": sum(limits.values()),
                      "active_slots": sum(active.values()), "active_by_priority": active,
                      "paused": d["policy"]["paused"], "enabled": d["policy"]["enabled"]}
        except (OSError, ValueError):
            device = {"status": "unreadable"}
        # The workspace is the current directory, exactly as the router of a
        # session launched here resolves it; a subdirectory is another workspace.
        result: dict[str, Any] = {"workspace": workspace_label(), "device": device, "session": None}
        # Router fork watch (read-only, the file the router persists): counts,
        # last notice time and key only; every persisted session without --session.
        from . import runtime_paths
        from .fork_turn_watch import status_rows, status_view
        result["fork_watch"] = status_rows(Path(runtime_paths.CONFIG_DIR))
        if session:
            session = self.require_session(session)
            result["fork_watch"] = status_view(Path(runtime_paths.CONFIG_DIR), session)
            parallel = self.get(session, "/ca/work/api/v1/parallel")
            side = self.get(session, "/ca/work/api/v1/side", limit="1")
            reviews = parallel.get("parallel_reviews") or []
            outbox: dict[str, int] = {}
            for row in side.get("reviews") or []:
                outbox[str(row.get("delivery"))] = outbox.get(str(row.get("delivery")), 0) + 1
            delivery = side.get("review_delivery") or {}
            result["session"] = {
                "session": session, "review_version": parallel.get("review_version"),
                "pending_reviews": sum(1 for row in reviews if row.get("decision") is None),
                "stranded": sum(1 for row in reviews if isinstance(row.get("stranded"), dict)),
                "superseded": len(parallel.get("parallel_reviews_ended") or []),
                "legacy_reviews": len(parallel.get("parallel_legacy_reviews") or []),
                "stuck_launches": sum(1 for row in reviews if (row.get("launch") or {}).get("stuck") is True),
                "uncertain_wakes": sum(1 for row in side.get("handoffs") or [] if row.get("delivery_uncertain")),
                "uncertain_reappends": sum(1 for row in side.get("handoffs") or []
                                           if (row.get("wake") or {}).get("reappend_uncertain")),
                "uncertain_notices": self._uncertain_notices(session),
                "reply_outbox": outbox,
                "review_delivery": {"available": delivery.get("available"),
                                    "states": _count(delivery.get("rows") or [], "state")},
                "receipts": side.get("total"), "receipt_counts": side.get("counts"),
                "quarantined": side.get("quarantined_count"),
                # G5, display only: the server's object as is; its message is
                # the one warning line (non-null whenever the gate is not loaded).
                "fork_gate": parallel.get("fork_gate"), "gate_presence_version": parallel.get("gate_presence_version")}
            result["warnings"] = fork_gate_warnings(parallel)
        return result

    def _uncertain_notices(self, session: str) -> int:
        return sum(1 for row in self.all_receipts(session) if row.get("notice_delivery_uncertain") is True)

    def all_receipts(self, session: str, limit: int = 2000) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = ""
        while len(rows) < limit:
            page = self.get(session, "/ca/work/api/v1/side", limit="100", cursor=cursor)
            rows += page.get("receipts") or []
            cursor = page.get("next_cursor") or ""
            if not page.get("has_more") or not cursor:
                break
        return rows

    # -- views ---------------------------------------------------------------
    def view(self, action: str, session: str, target: str | None) -> dict[str, Any]:
        if action in ("reviews", "launches", "capacity"):
            parallel = self.get(session, "/ca/work/api/v1/parallel")
            if action == "launches":
                # Rework/recovery launch state per reviewed lease (the web card's launch line).
                return {"session_id": parallel.get("session_id"), "review_version": parallel.get("review_version"),
                        "launches": [{"lease_id": row.get("lease_id"), "item_id": row.get("item_id"),
                                      "item_title": row.get("item_title"), "decision": row.get("decision"),
                                      "state": row.get("state"), **({"stranded": row["stranded"]}
                                                                   if row.get("stranded") else {}),
                                      "launch": row["launch"]}
                                     for row in parallel.get("parallel_reviews") or [] if row.get("launch")]}
            if action == "capacity":
                return {key: parallel.get(key) for key in ("session_id", "version", "policy", "device", "runtime",
                                                           "limitations", "fork_gate", "gate_presence_version")
                        if key in parallel} | {
                    "forks": {group: len(rows) for group, rows in (parallel.get("forks") or {}).items()
                              if isinstance(rows, list)},
                    "warnings": fork_gate_warnings(parallel)}
            return {key: parallel.get(key) for key in (
                "session_id", "version", "review_version", "reviews_truncated", "parallel_reviews",
                "stranded_version", "parallel_reviews_ended", "parallel_legacy_reviews", "parallel_failures",
                "failures_version")
                if key in parallel}
        if action == "receipt":
            if not target:
                raise CtlError("work receipt RECEIPT_ID --session S")
            return self.get(session, "/ca/work/api/v1/side/receipt", receipt_id=self.receipt_id(session, target))
        if action == "audit":
            return self.get(session, "/ca/work/api/v1/audit")
        if action == "orphan-preview":
            return self.post(session, "parallel-orphans-preview", {}, None)
        side = self.get(session, "/ca/work/api/v1/side", limit="100")
        if action == "receipts":
            return {key: side.get(key) for key in ("session_id", "enabled", "receipts", "total", "counts",
                                                   "counts_complete", "quarantined_count", "next_cursor", "has_more")}
        if action == "handoffs":
            return {"session_id": side.get("session_id"), "version": side.get("version"),
                    "handoffs": side.get("handoffs") or []}
        if action == "notices":
            return {"session_id": side.get("session_id"),
                    "notices": [row for row in self.all_receipts(session) if "notice_delivery_uncertain" in row]}
        return {"session_id": side.get("session_id"), "version": side.get("version"),
                "reviews": side.get("reviews") or [], "review_delivery": side.get("review_delivery")}

    # -- target resolution against the same views the web console shows ------
    @staticmethod
    def _one(rows: list[dict[str, Any]], field: str, target: str, what: str) -> dict[str, Any]:
        if not target:
            raise CtlError(what + " id required")
        exact = [row for row in rows if row.get(field) == target]
        matches = exact or ([row for row in rows if str(row.get(field) or "").startswith(target)]
                            if len(target) >= 8 else [])
        if len(matches) > 1:
            raise CtlError("ambiguous " + what + " " + target + "; give more characters", code="unknown_target")
        if matches:
            return matches[0]
        if len(target) >= 16:
            # Not listed now (already decided, or a replay of a committed
            # operation id): send the exact id and let the REST validator and
            # its idempotent replay decide, exactly as a repeated web click.
            return {field: target}
        raise CtlError("no " + what + " " + target, code="unknown_target")

    def receipt_id(self, session: str, target: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", target or ""):
            return target
        return self._one(self.all_receipts(session), "receipt_id", target, "side receipt")["receipt_id"]

    def act(self, action: str, session: str, args: Any) -> dict[str, Any]:
        target, value = args.key, args.value
        confirm, op = args.confirm is True, args.operation_id

        def gate(kind: str, preview: dict[str, Any], **fields: Any) -> None:
            if not confirm:
                raise CtlError(CONFIRM_TEXT[kind].format(**fields) + " Re-run with --confirm.",
                               code="confirmation_required", exit_code=EXIT_CONFIRM, payload={"would": preview})

        if action in ("review-approve", "review-rework", "launch-resolve"):
            view = self.get(session, "/ca/work/api/v1/parallel")
            row = self._one(view.get("parallel_reviews") or [], "lease_id", target, "pending review")
            expected = (args.expected_finalization_version if args.expected_finalization_version is not None
                        else view.get("review_version"))
            title = "“" + str(row.get("item_title") or row.get("item_id") or row["lease_id"]) + "”"
            if action == "launch-resolve":
                launch = row.get("launch") or {}
                body = {"lease_id": row["lease_id"], "launch_ref": launch.get("launch_ref"), "confirmed": True}
                if row.get("review_journal") == "stranded":
                    # A recovery launch is pinned by the stranded-v1 journal version.
                    body["expected_stranded_version"] = (args.expected_version if args.expected_version is not None
                                                         else view.get("stranded_version"))
                else:
                    body["expected_finalization_version"] = expected
                if launch.get("launch_ref"):
                    # Without a shown launch reference there is nothing to
                    # confirm; the REST validator explains the refusal.
                    gate(action, body, title=title)
                return self.post(session, "parallel-launch-resolve", body, op)
            decision = "approve" if action == "review-approve" else "rework"
            body = {"lease_id": row["lease_id"], "decision": decision, "evidence": value or "",
                    "expected_finalization_version": expected}
            # The R1 semantic pin: the review_pin of the row ctl rendered (or one
            # given from an earlier read). With it, unrelated review/wake writes do
            # not refuse the decision; without it the version rule applies.
            pin = getattr(args, "expected_review_pin", None) or row.get("review_pin")
            if pin:
                body["expected_review_pin"] = pin
            if decision == "approve":
                gate(action, body, title=title)
            return self.post(session, "parallel-item-review", body, op)
        if action == "legacy-accept":
            view = self.get(session, "/ca/work/api/v1/parallel")
            row = self._one(view.get("parallel_legacy_reviews") or [], "lease_id", target, "legacy review")
            body = {"lease_id": row["lease_id"], "confirmed": True,
                    "expected_version": args.expected_version if args.expected_version is not None
                    else view.get("version", 0)}
            gate(action, body, title="“" + str(row.get("item_title") or row.get("item_id") or row["lease_id"]) + "”")
            return self.post(session, "parallel-legacy-accept", body, op)
        if action == "orphan-release":
            preview = self.post(session, "parallel-orphans-preview", {}, None)["result"]
            digest = args.preview_digest or preview["digest"]
            row = self._one(preview.get("leases") or [], "lease_id", target, "slot lease")
            legacy = row.get("owner_state") == "legacy_no_owner"
            body = {"preview_digest": digest, "lease_id": row["lease_id"], "expected_version": preview["version"],
                    **({"legacy_confirmed": True} if legacy else {})}
            gate("orphan-release-legacy" if legacy else action, {**body, "owner_state": row.get("owner_state")},
                 lease=row["lease_id"][:10], title=str(row.get("item_id") or "an unlisted item"))
            return self.post(session, "parallel-orphans-apply", body, op)
        if action in ("wake-retry", "reappend-retry", "handoff-ack"):
            view = self.get(session, "/ca/work/api/v1/side", limit="1")
            row = self._one(view.get("handoffs") or [], "event_id", target, "side handoff")
            wake = row.get("wake") or {}
            if action == "handoff-ack":
                body = {"event_id": row["event_id"], "answer": value or "", "activate": args.activate is True,
                        "clear_wait": list(args.clear_wait or []),
                        "expected_version": args.expected_version if args.expected_version is not None
                        else view.get("version")}
                return self.post(session, "side-handoff", body, op)
            if action == "wake-retry":
                body = {"event_id": row["event_id"], "attempt_ref": args.attempt_ref or wake.get("attempt_ref"),
                        "revision": args.revision if args.revision is not None else wake.get("revision"),
                        "confirmed": True}
            else:
                body = {"event_id": row["event_id"], "reappend_ref": args.attempt_ref or wake.get("reappend_ref"),
                        "revision": args.revision if args.revision is not None else wake.get("revision"),
                        "confirmed": True}
            gate(action, body)
            return self.post(session, "side-wake-retry" if action == "wake-retry" else "side-reappend-retry",
                             body, op)
        if action == "review-reappend-retry":
            view = self.get(session, "/ca/work/api/v1/side", limit="1")
            row = self._one((view.get("review_delivery") or {}).get("rows") or [], "lease_id", target,
                            "review delivery")
            body = {"lease_id": row["lease_id"], "reappend_ref": args.attempt_ref or row.get("reappend_ref"),
                    "confirmed": True}
            gate(action, body)
            return self.post(session, "review-reappend-retry", body, op)
        if action in ("notice-retry", "reply-retry"):
            # Pinned like the web console: the exact attempt_ref and revision of
            # the receipt detail just read (or --attempt-ref/--revision from an
            # earlier read). A retry or new attempt in between is refused.
            receipt = self.receipt_id(session, target)
            detail = self.get(session, "/ca/work/api/v1/side/receipt", receipt_id=receipt).get("receipt") or {}
            prefix = "notice" if action == "notice-retry" else "reply"
            attempt = args.attempt_ref or detail.get(prefix + "_attempt_ref")
            revision = args.revision if args.revision is not None else detail.get(prefix + "_revision")
            if action == "notice-retry":
                body = {"receipt_id": receipt, "attempt_ref": attempt, "revision": revision, "confirmed": True}
                gate(action, body)
                return self.post(session, "side-notice-retry", body, op)
            state = value or detail.get("delivery")
            body = {"receipt_id": receipt, "expected_state": state, "revision": revision,
                    **({"attempt_ref": attempt} if attempt else {})}
            if state in ("uncertain", "submitting"):
                gate(action, body)
                body["confirmed"] = True
            return self.post(session, "side-reply-retry", body, op)
        if action in ("side-review-ok", "side-review-corrected"):
            view = self.get(session, "/ca/work/api/v1/side", limit="1")
            row = self._one(view.get("reviews") or [], "receipt_id", target, "side review")
            body = {"receipt_ids": [row["receipt_id"]], "verdict": "ok" if action == "side-review-ok" else "corrected",
                    "note": value or "", "expected_version": args.expected_version if args.expected_version is not None
                    else view.get("version")}
            return self.post(session, "side-review", body, op)
        if action == "side-drain":
            limit = int(value) if value and value.isdigit() else 10
            return self.post(session, "side-drain", {"limit": limit}, op)
        if action == "execute":
            # The web Execute button: the board command `execute` with the
            # person's confirmation, pinned to the plan version just shown.
            state = self.get(session, "/ca/work/api/v1/state")
            root = state.get("active_root") or {}
            body = {"expected_version": (args.expected_version if args.expected_version is not None
                                         else state.get("version", 0)),
                    "user_confirmed": True}
            gate(action, {**body, "root_id": root.get("id"), "title": root.get("title"), "phase": state.get("phase"),
                          "approval": state.get("approval")},
                 root=root.get("id") or "(none)", title=root.get("title") or "no active plan",
                 phase=state.get("phase"), approval=state.get("approval"))
            return self.post(session, "execute", body, op)
        if action in ("operation-reconcile", "operation-release"):
            # The web audit tab's two controls on an outcome-unknown receipt.
            # The positional id names that receipt; the command itself is not a
            # receipt, so no Idempotency-Key matters (--operation-id unused).
            events = self.get(session, "/ca/work/api/v1/audit").get("events") or []
            row = self._one([event for event in events if isinstance(event, dict)], "operation_id", target,
                            "operation receipt")
            fields = {"command": str(row.get("command") or "command"), "op": str(row["operation_id"])[:12]}
            body: dict[str, Any] = {"operation_id": row["operation_id"], "confirmed": True}
            if action == "operation-release":
                body["expected_receipt_version"] = (args.expected_version if args.expected_version is not None
                                                    else row.get("receipt_version"))
            gate(action, {**body, "outcome": row.get("outcome"), "last_reconcile": row.get("last_reconcile")},
                 **fields)
            return self.post(session, action, body, None)
        raise CtlError("unknown Work action: " + action)


def fork_gate_warnings(parallel: dict[str, Any]) -> list[str]:
    """The G5 line: the server's fork_gate message, verbatim, whenever it is non-null."""
    gate = parallel.get("fork_gate")
    message = gate.get("message") if isinstance(gate, dict) else None
    return [message] if isinstance(message, str) and message else []


def _count(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict):
            counts[str(row.get(field))] = counts.get(str(row.get(field)), 0) + 1
    return counts


def text_lines(action: str, payload: dict[str, Any]) -> list[str]:
    """Readable text: one line per row; ids are shown in full so they can be pasted."""
    if action == "sessions":
        return [f"{row.get('session') or 'unresolved ' + row['digest']}  last={_when(row.get('last_activity'))}"
                + "".join(f"  {key}={row[key]}" for key in ("nodes", "unfinished", "pending_reviews", "receipts")
                          if key in row) for row in payload.get("sessions", [])] or ["no Work conversations"]
    lines = []
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if result.get("state") == "awaiting_capacity":
        # A durable decision that waits (owner_offline, capacity, paused...):
        # recorded and queued, not a failure (exit 0).
        retry = "the conversation's own Work host" if result.get("retry") == "host_pump" else result.get("retry")
        lines.append("queued: " + str(result.get("reason") or "awaiting capacity")
                     + (f" (retried by {retry})" if retry else ""))
    # G5 warning first, once (work capacity).
    lines += ["warning: " + line for line in payload.get("warnings") or [] if isinstance(line, str)]
    for key, value in payload.items():
        if key == "warnings":
            continue
        if isinstance(value, list):
            lines.append(f"{key}: {len(value)}")
            for row in value:
                lines.append("  " + json.dumps(row, ensure_ascii=False, sort_keys=True))
                # B2: a failure row's server message (owner_unknown, owner_live),
                # verbatim and once, under its row; ctl adds no wording.
                message = row.get("message") if key == "parallel_failures" and isinstance(row, dict) else None
                if isinstance(message, str) and message:
                    lines.append("    " + message)
        elif isinstance(value, dict):
            lines.append(f"{key}: " + json.dumps(value, ensure_ascii=False, sort_keys=True))
        elif value is not None:
            lines.append(f"{key}: {value}")
    return lines


def _when(stamp: Any) -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp)) if isinstance(stamp, (int, float)) else "-"
