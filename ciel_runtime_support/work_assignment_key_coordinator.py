"""Shared assignment-key creation fence for P0 and released-wait authority."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from .durable_file_lock import exclusive_file_lock
from .work_p0_assignment_key import P0AssignmentKeyStore, P0AssignmentKeyUnavailable

CREATION_LOCK_TARGET = "work-assignment-key.creation"
P0_SIDECAR_NAME = "work-parallel-p0.json"
WAIT_SIDECAR_NAME = "work-parallel-wait-boost-v1.json"
CAPABILITY_DOMAIN = "ciel.wait-boost.assignment-capability.v1"
CAPABILITY_DIGEST_DOMAIN = "ciel.wait-boost.capability.v1"
MAX_TEXT = 200
MAX_TIME = 1e15


class DependencyReader(Protocol):
    @property
    def lock_target(self) -> Path: ...
    def has_dependency_locked(self) -> bool: ...


@dataclass(frozen=True)
class DependencySnapshot:
    p0: bool
    wait: bool

    @property
    def any(self) -> bool:
        return self.p0 or self.wait


@dataclass(frozen=True)
class AssignmentKeyResult:
    state: str
    reason: str | None
    key: bytes | None = field(default=None, repr=False)
    dependencies: DependencySnapshot = field(
        default_factory=lambda: DependencySnapshot(False, False)
    )

    def public(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "dependencies": {
                "p0": self.dependencies.p0,
                "wait": self.dependencies.wait,
            },
        }


class AssignmentKeyCoordinatorError(ValueError):
    pass


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _frame(data: bytes) -> bytes:
    return len(data).to_bytes(8, "big") + data


def _digest(domain: str, value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_frame(domain.encode("utf-8")) + _frame(_canonical(value))).hexdigest()


def _text(value: Any) -> bool:
    return type(value) is str and 0 < len(value) <= MAX_TEXT and "\0" not in value


def validate_assignment_manifest(manifest: Mapping[str, Any]) -> None:
    exact = {
        "scope", "root_id", "root_created_at", "item_id", "attempt_id",
        "lease_id", "assignment_generation", "dispatch_name", "purpose",
    }
    if type(manifest) is not dict or set(manifest) != exact:
        raise AssignmentKeyCoordinatorError("invalid assignment manifest fields")
    if not all(_text(manifest[name]) for name in (
        "scope", "root_id", "item_id", "lease_id", "dispatch_name"
    )):
        raise AssignmentKeyCoordinatorError("invalid assignment manifest text")
    root_time = manifest["root_created_at"]
    if (
        type(root_time) not in (int, float)
        or isinstance(root_time, bool)
        or not math.isfinite(root_time)
        or not 0 <= root_time <= MAX_TIME
    ):
        raise AssignmentKeyCoordinatorError("invalid assignment root time")
    attempt = manifest["attempt_id"]
    if (
        type(attempt) is not str
        or len(attempt) != 32
        or any(char not in "0123456789abcdef" for char in attempt)
    ):
        raise AssignmentKeyCoordinatorError("invalid assignment attempt id")
    generation = manifest["assignment_generation"]
    if type(generation) is not int or not 1 <= generation <= 2**53 - 1:
        raise AssignmentKeyCoordinatorError("invalid assignment generation")
    if manifest["dispatch_name"] != "work-wait-boost-" + attempt:
        raise AssignmentKeyCoordinatorError("invalid assignment dispatch name")
    if manifest["purpose"] != "worker_report":
        raise AssignmentKeyCoordinatorError("invalid assignment purpose")


def assignment_frame(manifest: Mapping[str, Any]) -> bytes:
    validate_assignment_manifest(manifest)
    return _frame(CAPABILITY_DOMAIN.encode("utf-8")) + _frame(_canonical(manifest))


def derive_wait_capability(key: bytes, manifest: Mapping[str, Any]) -> str:
    if type(key) is not bytes or len(key) != 32:
        raise AssignmentKeyCoordinatorError("invalid assignment key bytes")
    return hmac.new(key, assignment_frame(manifest), hashlib.sha256).hexdigest()


def capability_derivation_digest(manifest: Mapping[str, Any]) -> str:
    validate_assignment_manifest(manifest)
    return _digest(CAPABILITY_DIGEST_DOMAIN, manifest)


P0_OPEN_INTENT_STATES = frozenset((
    "intent", "reserving", "reserved", "workspace_preparing",
    "workspace_attached", "spawning", "bound", "recovery_required",
))
P0_OPEN_SEND_STATES = frozenset(("prepared", "slot_acquired", "uncertain"))


def p0_dependencies(document: Mapping[str, Any]) -> bool:
    return (
        any(row.get("state") in P0_OPEN_INTENT_STATES for row in document["intents"].values())
        or any(row.get("state") in P0_OPEN_SEND_STATES for row in document["sends"].values())
    )


def wait_dependencies(document: Mapping[str, Any]) -> bool:
    return (
        any(row.get("state") == "consuming" for row in document["rows"].values())
        or any(
            row.get("state") not in ("done", "rolled_back")
            for name in ("bind_sagas", "workspaces", "cleanups")
            for row in document[name].values()
        )
    )


class P0StoreDependencyReader:
    def __init__(self, store: Any):
        self.store = store
        self.lock_target = Path(store.path)

    def has_dependency_locked(self) -> bool:
        return p0_dependencies(self.store.read_snapshot())


class WaitStoreDependencyReader:
    def __init__(self, store: Any):
        self.store = store
        self.lock_target = Path(store.path)

    def has_dependency_locked(self) -> bool:
        document, _ = self.store._read_unlocked()
        return wait_dependencies(document)


class AssignmentKeyCoordinator:
    def __init__(
        self,
        ledger_path: Path,
        *,
        p0_reader: DependencyReader,
        wait_reader: DependencyReader,
        key_store: P0AssignmentKeyStore | None = None,
    ):
        self.ledger_path = Path(ledger_path).resolve()
        self.creation_target = self.ledger_path.with_name(CREATION_LOCK_TARGET)
        self.p0_reader = p0_reader
        self.wait_reader = wait_reader
        self.key_store = key_store or P0AssignmentKeyStore.beside_ledger(self.ledger_path)
        expected = (
            self.ledger_path.with_name(P0_SIDECAR_NAME).resolve(),
            self.ledger_path.with_name(WAIT_SIDECAR_NAME).resolve(),
        )
        actual = (
            Path(p0_reader.lock_target).resolve(),
            Path(wait_reader.lock_target).resolve(),
        )
        if actual != expected:
            raise AssignmentKeyCoordinatorError("dependency reader lock target mismatch")
        expected_key = P0AssignmentKeyStore.beside_ledger(self.ledger_path).path.resolve()
        if Path(self.key_store.path).resolve() != expected_key:
            raise AssignmentKeyCoordinatorError("assignment key path mismatch")

    @property
    def physical_creation_lock_path(self) -> Path:
        return self.creation_target.with_name(self.creation_target.name + ".lock")

    def _snapshot_locked(self) -> DependencySnapshot:
        return DependencySnapshot(
            p0=bool(self.p0_reader.has_dependency_locked()),
            wait=bool(self.wait_reader.has_dependency_locked()),
        )

    def acquire_for_dependency(self) -> AssignmentKeyResult:
        """Create/read the key while holding the complete creation lock order."""
        with ExitStack() as stack:
            stack.enter_context(exclusive_file_lock(self.creation_target))
            stack.enter_context(exclusive_file_lock(self.p0_reader.lock_target))
            stack.enter_context(exclusive_file_lock(self.wait_reader.lock_target))
            dependencies = self._snapshot_locked()
            stack.enter_context(exclusive_file_lock(self.key_store.path))
            try:
                key = self.key_store.read()
            except P0AssignmentKeyUnavailable as error:
                if error.reason != "missing" or dependencies.any:
                    return AssignmentKeyResult(
                        "recovery_required", error.reason, None, dependencies
                    )
                key = self.key_store.ensure_before_first_intent_locked()
            return AssignmentKeyResult("ready", None, key, dependencies)

    def create_p0_intent(
        self, store: Any, facts: Mapping[str, Any], *, expected_version: int, now=None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically fence key creation and one validated P0 intent CAS."""
        if Path(store.path).resolve() != Path(self.p0_reader.lock_target).resolve():
            raise AssignmentKeyCoordinatorError("P0 creator store mismatch")
        with ExitStack() as stack:
            stack.enter_context(exclusive_file_lock(self.creation_target))
            stack.enter_context(store._lock)
            stack.enter_context(exclusive_file_lock(self.p0_reader.lock_target))
            stack.enter_context(exclusive_file_lock(self.wait_reader.lock_target))
            dependencies = self._snapshot_locked()
            stack.enter_context(exclusive_file_lock(self.key_store.path))
            try:
                self.key_store.read()
            except P0AssignmentKeyUnavailable as error:
                if error.reason != "missing" or dependencies.any:
                    raise AssignmentKeyCoordinatorError(
                        "shared assignment key recovery required (" + error.reason + ")"
                    ) from error
                self.key_store.ensure_before_first_intent_locked()
            mutation = store._create_intent_mutation(dict(facts), now=now)
            state, row = store._change_locked(mutation, expected_version)
            if not p0_dependencies(state):
                raise AssignmentKeyCoordinatorError("P0 intent dependency missing after commit")
            return state, row

    def create_wait_dependency(
        self,
        store: Any,
        expected_version: int,
        mutate: Any,
        *,
        target_key: str,
        attempt_id: str,
        replay=None,
        now=None,
    ) -> tuple[dict[str, Any], Any]:
        """Fence one exact boosted-to-consuming transition into wait authority."""
        if Path(store.path).resolve() != Path(self.wait_reader.lock_target).resolve():
            raise AssignmentKeyCoordinatorError("wait creator store mismatch")
        if not callable(mutate):
            raise AssignmentKeyCoordinatorError("wait creator mutation required")
        if type(target_key) is not str or type(attempt_id) is not str:
            raise AssignmentKeyCoordinatorError("wait creator target required")
        with ExitStack() as stack:
            stack.enter_context(exclusive_file_lock(self.creation_target))
            stack.enter_context(exclusive_file_lock(self.p0_reader.lock_target))
            stack.enter_context(exclusive_file_lock(self.wait_reader.lock_target))
            before, _ = store._read_unlocked()
            if before["version"] != expected_version:
                raise AssignmentKeyCoordinatorError("wait state changed; reread")
            prior_target = before["rows"].get(target_key)
            if prior_target is None or prior_target.get("state") != "boosted":
                raise AssignmentKeyCoordinatorError("wait creator target is not boosted")
            dependencies = self._snapshot_locked()
            stack.enter_context(exclusive_file_lock(self.key_store.path))
            try:
                self.key_store.read()
            except P0AssignmentKeyUnavailable as error:
                if error.reason != "missing" or dependencies.any:
                    raise AssignmentKeyCoordinatorError(
                        "shared assignment key recovery required (" + error.reason + ")"
                    ) from error
                self.key_store.ensure_before_first_intent_locked()
            def exact_mutation(document):
                result = mutate(document)
                target = document["rows"].get(target_key)
                if (
                    target is None
                    or target.get("state") != "consuming"
                    or target.get("attempt_id") != attempt_id
                    or target.get("scope") != prior_target.get("scope")
                    or target.get("item_id") != prior_target.get("item_id")
                    or target.get("root_id") != prior_target.get("root_id")
                    or target.get("root_created_at") != prior_target.get("root_created_at")
                    or target.get("release_receipt") != prior_target.get("release_receipt")
                ):
                    raise AssignmentKeyCoordinatorError(
                        "wait mutation did not create exact target authority"
                    )
                return result

            return store._change_locked(
                expected_version, exact_mutation, replay=replay, now=now
            )

    def status(self) -> AssignmentKeyResult:
        """Read-only snapshot; never creates/hardens or returns key bytes."""
        with ExitStack() as stack:
            stack.enter_context(exclusive_file_lock(self.p0_reader.lock_target))
            stack.enter_context(exclusive_file_lock(self.wait_reader.lock_target))
            dependencies = self._snapshot_locked()
            if not dependencies.any:
                return AssignmentKeyResult("not_required", None, None, dependencies)
            stack.enter_context(exclusive_file_lock(self.key_store.path))
            try:
                self.key_store.read()
            except P0AssignmentKeyUnavailable as error:
                return AssignmentKeyResult(
                    "recovery_required", error.reason, None, dependencies
                )
            return AssignmentKeyResult("ready", None, None, dependencies)


def is_assignment_key_secret(path: str | Path) -> bool:
    return Path(path).name == "work-p0-assignment.key"
