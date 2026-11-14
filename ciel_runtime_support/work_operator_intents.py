"""Authoritative operator-command intents that mixed Work board writers cannot evict.

``work-operator-intents-v1.json`` lives beside a conversation's ``work-plan.json``.
Builds through 57dbfb3 never name this file.  The Work board's
``rest_operations`` map remains a schema-compatible replay mirror, but this
sidecar is read first for operator commands, their audit rows and their
reconcile/release state.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Callable

from .durable_file_lock import exclusive_file_lock
from .durable_json_file import save
from .work_plan_tree import WorkPlanError, WorkStateUnavailable

PRODUCER = "ciel-runtime/work_operator_intents"
SCHEMA = 1
FILE_NAME = "work-operator-intents-v1.json"
MAX_PREPARED = 128
MAX_RESOLVED = 128
MAX_INTENTS = MAX_PREPARED + MAX_RESOLVED
MAX_BYTES = 512 * 1024
STATES = frozenset(("prepared", "committed", "refused", "released", "released_unverified"))
ROW_FIELDS = frozenset(("digest", "command", "source", "origin", "state", "created_at", "request",
                        "receipt_version", "last_reconcile", "resolution", "response"))


def intents_path(board_path: Any) -> Path:
    return Path(board_path).with_name(FILE_NAME)


def _empty() -> dict[str, Any]:
    return {"producer": PRODUCER, "schema": SCHEMA, "version": 0, "intents": {}}


def _valid_key(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 200
            and all(33 <= ord(char) <= 126 for char in value))


def _valid_row(row: Any) -> bool:
    return (type(row) is dict and set(row) <= ROW_FIELDS
            and isinstance(row.get("digest"), str) and len(row["digest"]) == 64
            and isinstance(row.get("command"), str) and bool(row["command"])
            and row.get("source") in ("web", "rest", "ctl")
            and isinstance(row.get("origin"), dict)
            and row.get("state") in STATES
            and type(row.get("created_at")) in (int, float) and math.isfinite(row["created_at"])
            and isinstance(row.get("request"), dict)
            and type(row.get("receipt_version")) is int and row["receipt_version"] >= 1
            and (row.get("last_reconcile") is None or isinstance(row.get("last_reconcile"), dict))
            and (row.get("resolution") is None or isinstance(row.get("resolution"), dict))
            and isinstance(row.get("response"), dict))


def _from_board(row: dict[str, Any]) -> dict[str, Any]:
    """The operator fields a schema-compatible board mirror may contribute."""
    copied = {key: copy.deepcopy(value) for key, value in row.items() if key in ROW_FIELDS}
    copied.setdefault("source", "rest")
    copied.setdefault("origin", {})
    copied.setdefault("request", {})
    copied.setdefault("receipt_version", 1)
    copied.setdefault("last_reconcile", None)
    copied.setdefault("response", {"ok": False, "state": copied.get("state")})
    return copied


class WorkOperatorIntents:
    def __init__(self, path: Any) -> None:
        self.path = Path(path)

    @classmethod
    def beside(cls, board_path: Any) -> "WorkOperatorIntents":
        return cls(intents_path(board_path))

    def read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return _empty()
        if len(raw) > MAX_BYTES:
            raise WorkStateUnavailable("operator intent record exceeds its limit; left unchanged")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise WorkStateUnavailable("operator intent record is unreadable; left unchanged") from error
        if (type(doc) is not dict or set(doc) != {"producer", "schema", "version", "intents"}
                or doc.get("producer") != PRODUCER or doc.get("schema") != SCHEMA
                or type(doc.get("version")) is not int or doc["version"] < 0
                or type(doc.get("intents")) is not dict or len(doc["intents"]) > MAX_INTENTS
                or not all(_valid_key(key) and _valid_row(row) for key, row in doc["intents"].items())
                or sum(row["state"] == "prepared" for row in doc["intents"].values()) > MAX_PREPARED):
            raise WorkStateUnavailable("operator intent record has another producer or schema; left unchanged")
        return doc

    def transaction(self, mutate: Callable[[dict[str, dict[str, Any]]], Any], *,
                    undo: list | None = None) -> Any:
        """Apply ``mutate`` under the record's lock and save it when it changed.

        With ``undo``, a saved write appends (bytes before or None, document
        written) so that ``restore`` can put back exactly what was there.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            try:
                previous = self.path.read_bytes()
            except FileNotFoundError:
                previous = None
            doc = self.read()
            before = copy.deepcopy(doc["intents"])
            result = mutate(doc["intents"])
            prepared = sum(row.get("state") == "prepared" for row in doc["intents"].values())
            if prepared > MAX_PREPARED:
                raise WorkPlanError("operator intent prepared bound exceeded; left unchanged")
            # Resolved intents keep the newest MAX_RESOLVED; prepared ones are never evicted.
            resolved = sorted((key for key, row in doc["intents"].items() if row.get("state") != "prepared"),
                              key=lambda key: (float(doc["intents"][key].get("created_at") or 0), key))
            for key in resolved[:max(0, len(resolved) - MAX_RESOLVED)]:
                del doc["intents"][key]
            if doc["intents"] != before:
                doc["version"] += 1
                save(self.path, doc)
                if undo is not None:
                    undo.append((previous, copy.deepcopy(doc)))
            return result

    def restore(self, previous: bytes | None, written: dict[str, Any]) -> bool:
        """Put back the bytes a refused command found, only if nothing wrote since.

        True when restored. Otherwise the caller removes its own intent with an
        ordinary write; another writer's change is never undone.
        """
        with exclusive_file_lock(self.path):
            if self.read() != written:
                return False
            if previous is None:
                self.path.unlink(missing_ok=True)
            else:
                save(self.path, json.loads(previous.decode("utf-8")))
            return True

    @staticmethod
    def merge_board(intents: dict[str, dict[str, Any]], receipts: Any) -> bool:
        """Merge pre-sidecar operator rows into ``intents``; True when anything changed.

        A board row is adopted only when the sidecar has no row for its key or
        the board row carries a newer receipt_version (a write that reached the
        board before this sidecar existed). Board commands carry no state and
        stay board-only.
        """
        changed = False
        for key, value in (receipts if isinstance(receipts, dict) else {}).items():
            if not _valid_key(key) or not isinstance(value, dict) or value.get("state") not in STATES:
                continue
            row = _from_board(value)
            if not _valid_row(row):
                continue
            prior = intents.get(key)
            if prior is None or row["receipt_version"] > prior["receipt_version"]:
                intents[key] = row
                changed = True
        return changed

    def view(self, receipts: Any) -> dict[str, dict[str, Any]]:
        """Read-only merged operator rows: the sidecar first, then pre-sidecar board rows."""
        intents = copy.deepcopy(self.read()["intents"])
        self.merge_board(intents, receipts)
        return intents

    def adopt_board(self, receipts: Any) -> dict[str, dict[str, Any]]:
        """Persist the merged view before a write decides on a key."""
        self.transaction(lambda intents: self.merge_board(intents, receipts))
        return self.read()["intents"]
