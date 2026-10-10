"""Create, verify and restore session snapshots on backup targets."""

from __future__ import annotations

import hashlib
import json
import os
import secrets as random_source
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from ciel_runtime_support.process_control import pid_is_running
from ciel_runtime_support.session_backup_collect import (
    CollectOptions,
    CollectRoots,
    SessionCollector,
    build_manifest,
    workspace_label,
)
from ciel_runtime_support.session_backup_remap import remap_after_restore, remapped_entry_path
from ciel_runtime_support.session_backup_secrets import SecretBundle, merge_secret_fields, open_sealed, seal
from ciel_runtime_support.session_backup_store import (
    BackupTarget,
    ChunkWriter,
    blob_key,
    encode_manifest,
    manifest_key,
    read_chunks,
)
from ciel_runtime_support.workspace_router_selection import workspace_identity

ROOT_NAMES = ("cwd", "claude", "home", "codex", "ciel", "ciel_ws")


def new_snapshot_id(clock: Callable[[], float] = time.time) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(clock())) + "-" + random_source.token_hex(3)


@dataclass
class CreateResult:
    manifest: dict[str, Any]
    stats: dict[str, int]
    targets: list[str]


def create_snapshot(
    roots: CollectRoots,
    targets: list[BackupTarget],
    options: CollectOptions,
    *,
    key: bytes | None,
    versions: Mapping[str, str],
    host: str,
    user: str,
) -> CreateResult:
    if not targets:
        raise ValueError("no backup target")
    writer = ChunkWriter(targets)
    collection = SessionCollector(roots, writer, options).collect()
    secrets_ref: dict[str, Any] | None = None
    if key is not None and collection.secrets:
        sealed = seal(collection.secrets.to_bytes(), key)
        secrets_ref = {"included": True, "names": collection.secrets.names(), "chunks": writer.write_stream([sealed])}
    workspace = workspace_label(roots.cwd, host, user)
    manifest = build_manifest(
        new_snapshot_id(), workspace, roots, collection, options, secrets_ref=secrets_ref, versions=versions
    )
    manifest["stats"] = writer.stats.as_dict()
    data = encode_manifest(manifest)
    for target in targets:
        # Every chunk is on the target before the manifest that names them.
        flush = getattr(target, "flush", None)
        if flush is not None:
            flush()
        target.put(manifest_key(workspace, manifest["id"]), data)
        close = getattr(target, "close", None)
        if close is not None:
            close()
    return CreateResult(manifest, writer.stats.as_dict(), [target.name for target in targets])


def verify_snapshot(target: BackupTarget, manifest: Mapping[str, Any]) -> list[str]:
    """Problems found (missing or corrupt chunks); empty when the snapshot is whole."""

    problems: list[str] = []
    checked: dict[str, bool] = {}
    digests = [digest for entry in manifest.get("entries") or [] for digest in entry.get("chunks") or []]
    digests += list((manifest.get("secrets") or {}).get("chunks") or [])
    for digest in digests:
        if digest in checked:
            continue
        try:
            chunk = zlib.decompress(target.get(blob_key(digest)))
            checked[digest] = hashlib.sha256(chunk).hexdigest() == digest
        except Exception as error:  # noqa: BLE001 - every failure is a finding
            checked[digest] = False
            problems.append(f"chunk {digest[:12]}: {type(error).__name__}")
            continue
        if not checked[digest]:
            problems.append(f"chunk {digest[:12]}: checksum mismatch")
    return problems


def load_secrets(target: BackupTarget, manifest: Mapping[str, Any], key: bytes | None) -> SecretBundle | None:
    ref = manifest.get("secrets") or {}
    if not ref.get("included") or key is None:
        return None
    sealed = b"".join(read_chunks(target, list(ref.get("chunks") or [])))
    return SecretBundle.from_bytes(open_sealed(sealed, key))


@dataclass
class RestorePlan:
    writes: list[tuple[dict[str, Any], Path]] = field(default_factory=list)
    secret_skipped: list[str] = field(default_factory=list)


def destination_roots(manifest: Mapping[str, Any], overrides: Mapping[str, str | Path]) -> dict[str, Path]:
    original = manifest.get("roots") or {}
    return {name: Path(overrides.get(name) or original.get(name) or "") for name in ROOT_NAMES}


def plan_restore(manifest: Mapping[str, Any], dest: Mapping[str, Path], bundle: SecretBundle | None, *, include_files: bool) -> RestorePlan:
    plan = RestorePlan()
    for entry in manifest.get("entries") or []:
        root = entry["root"]
        if root == "cwd" and not include_files:
            continue
        relative = remapped_entry_path(entry, str((manifest.get("roots") or {}).get("cwd") or ""), str(dest["cwd"]))
        target_path = Path(dest[root]).joinpath(*relative.split("/"))
        if entry["kind"] == "secret-file" and (bundle is None or f"{root}/{entry['path']}" not in bundle.files):
            plan.secret_skipped.append(f"{root}/{entry['path']}")
            continue
        plan.writes.append((entry, target_path))
    return plan


def _atomic_write(path: Path, data: bytes, mtime: float | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".restore-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    if mtime:
        try:
            os.utime(path, (mtime, mtime))
        except OSError:
            pass


def entry_bytes(target: BackupTarget, entry: Mapping[str, Any], bundle: SecretBundle | None) -> bytes:
    key = f"{entry['root']}/{entry['path']}"
    if entry["kind"] == "secret-file":
        assert bundle is not None
        return bundle.files[key]
    data = b"".join(read_chunks(target, list(entry.get("chunks") or [])))
    if entry["kind"] == "json-public" and bundle is not None and key in bundle.fields:
        merged = merge_secret_fields(json.loads(data.decode("utf-8")), bundle.fields[key])
        data = json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
    return data


def live_session_pid(ciel_config_dir: Path, cwd: Path) -> int:
    """The Ciel launcher pid still running for this cwd, else 0."""

    try:
        state = json.loads((Path(ciel_config_dir) / "launch-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    wanted = workspace_identity(cwd)
    for key, value in (state.get("by_cwd") or {}).items():
        if workspace_identity(key) == wanted and isinstance(value, dict):
            pid = int(value.get("pid") or 0)
            if pid and pid != os.getpid() and pid_is_running(pid):
                return pid
    return 0


def restore_snapshot(
    target: BackupTarget,
    manifest: Mapping[str, Any],
    dest: Mapping[str, Path],
    *,
    key: bytes | None,
    include_files: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    bundle = load_secrets(target, manifest, key)
    plan = plan_restore(manifest, dest, bundle, include_files=include_files)
    written = 0
    remapped: dict[str, Any] = {}
    if not dry_run:
        for entry, path in plan.writes:
            _atomic_write(path, entry_bytes(target, entry, bundle), entry.get("mtime"))
            written += 1
        remapped = remap_after_restore(manifest, dest)
    return {
        "remapped": remapped,
        "files": len(plan.writes),
        "written": written,
        "secrets_restored": bundle is not None,
        "secret_skipped": plan.secret_skipped,
        "paths": [str(path) for _, path in plan.writes],
    }


def prune_snapshots(target: BackupTarget, workspace: str, *, keep_last: int, keep_daily: int, dry_run: bool = False) -> dict[str, Any]:
    """Keep the newest ``keep_last`` snapshots plus the newest one of each of the last ``keep_daily`` days.

    Chunks are deleted only when a removed snapshot was their last user, so chunks
    shared with kept snapshots (of any workspace on this target) stay.
    """

    from ciel_runtime_support.session_backup_store import decode_manifest, list_snapshots

    ids = [snapshot_id for _, snapshot_id in list_snapshots(target, workspace)]
    keep = set(ids[-keep_last:]) if keep_last > 0 else set()
    days: dict[str, str] = {}
    for snapshot_id in ids:
        days[snapshot_id[:8]] = snapshot_id
    keep |= set(list(days.values())[-keep_daily:]) if keep_daily > 0 else set()
    remove = [snapshot_id for snapshot_id in ids if snapshot_id not in keep]

    def digests(manifest: Mapping[str, Any]) -> set[str]:
        found = {digest for entry in manifest.get("entries") or [] for digest in entry.get("chunks") or []}
        return found | set((manifest.get("secrets") or {}).get("chunks") or [])

    removed_digests: set[str] = set()
    for snapshot_id in remove:
        removed_digests |= digests(decode_manifest(target.get(manifest_key(workspace, snapshot_id))))
    still_used: set[str] = set()
    for other_workspace, snapshot_id in list_snapshots(target):
        if other_workspace == workspace and snapshot_id in remove:
            continue
        try:
            still_used |= digests(decode_manifest(target.get(manifest_key(other_workspace, snapshot_id))))
        except Exception:  # noqa: BLE001 - an unreadable manifest keeps everything it might use
            return {"removed": [], "chunks_deleted": 0, "error": f"unreadable manifest {snapshot_id}; nothing pruned"}
    orphaned = sorted(removed_digests - still_used)
    if not dry_run:
        for snapshot_id in remove:
            target.delete(manifest_key(workspace, snapshot_id))
        for digest in orphaned:
            target.delete(blob_key(digest))
    return {"removed": remove, "kept": len(ids) - len(remove), "chunks_deleted": len(orphaned)}


def resume_hint(manifest: Mapping[str, Any], dest: Mapping[str, Path]) -> list[str]:
    sessions = manifest.get("sessions") or {}
    cwd = dest.get("cwd") or Path((manifest.get("roots") or {}).get("cwd") or ".")
    lines = []
    if "claude" in sessions:
        lines.append(f'cd "{cwd}" && ciel-runtime --continue   (Claude session {sessions["claude"].get("latest")})')
    if "codex" in sessions:
        lines.append(f'cd "{cwd}" && ciel-runtime codex resume {sessions["codex"].get("latest")}')
    return lines


__all__ = [
    "CreateResult",
    "RestorePlan",
    "create_snapshot",
    "destination_roots",
    "entry_bytes",
    "live_session_pid",
    "load_secrets",
    "new_snapshot_id",
    "plan_restore",
    "prune_snapshots",
    "restore_snapshot",
    "resume_hint",
    "verify_snapshot",
]
