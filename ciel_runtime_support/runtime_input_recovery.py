"""Explicit offline quarantine. No automatic replay or live-state repair."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable
import uuid
from .channel_message_repository import exclusive_file_lock
from .workspace_router_selection import _process_identity, port_is_free, workspace_digest, workspace_identity

from .runtime_input_recovery_marker import (
    RecoveryRefused, recovery_marker, recovery_generation,
)

_LIMIT = 64 * 1024 * 1024
_ID = re.compile(rb'"(?:request_id|id)"\s*:\s*"?([0-9]{1,100})(?![0-9])')

def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

def _durable(path: Path, data: bytes, *, replace=False):
    target = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp") if replace else path
    with target.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if replace:
        os.replace(target, path)
    if os.name != "nt":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

def _snapshot(path: Path):
    opened = False
    try:
        with path.open("rb") as stream:
            opened = True
            before = os.fstat(stream.fileno())
            if before.st_size > _LIMIT:
                raise RecoveryRefused("history exceeds recovery byte budget")
            raw = stream.read(_LIMIT + 1)
            after = os.fstat(stream.fileno())
        current = path.stat()
    except FileNotFoundError:
        if opened:
            raise RecoveryRefused("history disappeared during preview")
        return {"exists": False, "size": 0, "sha256": hashlib.sha256(b"").hexdigest(),
                "max_known_id": 0, "identity": None}, b""
    def sig(s):
        return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]
    if len(raw) > _LIMIT or sig(before) != sig(after) or sig(after) != sig(current):
        raise RecoveryRefused("history changed during preview")
    maximum = max((int(match[1]) for match in _ID.finditer(raw)), default=0)
    return {"exists": True, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "max_known_id": maximum, "identity": sig(current)}, raw

@contextmanager
def registry_maintenance_lock(registry: Path):
    """Same exclusive-create lock as port selection; never steal a lock."""
    lock = registry.with_suffix(registry.suffix + ".lock")
    fd = None
    try:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RecoveryRefused("router selection/maintenance already active") from exc
        os.write(fd, (str(os.getpid()) + chr(10)).encode("ascii"))
        os.fsync(fd)
        yield
    finally:
        if fd is not None:
            os.close(fd)
            lock.unlink()

def runtime_process_inventory():
    """Conservative machine-wide runtime/CLI inventory, including orphan clients.

    Requires an inspectable process inventory; missing optional psutil is an
    explicit refusal, never proof that unregistered child processes are gone.
    """
    try:
        import psutil
    except ImportError as exc:
        raise RecoveryRefused("offline process inventory requires psutil") from exc
    matches = []
    candidates = {"python", "python3", "pythonw", "node", "bun", "claude", "codex", "muse", "ciel-runtime", "ciel_runtime", "pwsh", "powershell", "cmd", "bash", "sh", "kimi", "grok", "zcode", "agy"}
    clients = {"claude", "codex", "muse", "ciel-runtime", "ciel_runtime", "kimi", "grok", "zcode", "agy"}
    try:
        for process in psutil.process_iter(["pid", "name"]):
            if process.pid == os.getpid():
                continue
            name = str(process.info.get("name") or "").lower().removesuffix(".exe")
            if name not in candidates and not name.startswith("python3."):
                continue
            try:
                command = " ".join(process.cmdline()).lower()
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, OSError) as exc:
                raise RecoveryRefused("runtime candidate process cannot be inspected") from exc
            if name in clients or not command or any(term in command for term in (
                    "ciel-runtime", "ciel_runtime", "@anthropic-ai/claude", "@openai/codex")):
                matches.append({"pid": process.pid, "name": name})
    except (psutil.AccessDenied, OSError) as exc:
        raise RecoveryRefused("offline process inventory unavailable") from exc
    return matches


class OfflineHistoryRecovery:
    def __init__(self, state_dir: Path, registry_path: Path, workspace: Path, *,
                 process_identity: Callable[[int], str | None] = _process_identity,
                 port_available: Callable[[int], bool] = port_is_free,
                 process_inventory: Callable[[], list] = runtime_process_inventory):
        self.state_dir = Path(state_dir).resolve()
        self.registry = Path(registry_path).resolve()
        self.workspace = workspace_identity(workspace)
        self.process_identity = process_identity
        self.port_available = port_available
        self.process_inventory = process_inventory
        self.queue = self.state_dir / "runtime-inputs.jsonl"
        self.status = self.state_dir / "runtime-input-status.jsonl"

    def _offline(self):
        try:
            records = json.loads(self.registry.read_text(encoding="utf-8"))["workspaces"]
            if not isinstance(records, dict):
                raise ValueError()
            owners, instances, ports = [], set(), set()
            for record in records.values():
                if not isinstance(record, dict):
                    raise ValueError()
                if workspace_identity(record.get("workspace")) != self.workspace:
                    continue
                port = int(record["port"])
                if not 1 <= port <= 65535:
                    raise ValueError()
                ports.add(port)
                instances.add(self.registry.parent / "router-instances" /
                              f"{port}-{workspace_digest(self.workspace)}")
                if "launch_lease" in record:
                    owners.append(("launcher", int(record["launch_lease"]["pid"])))
                if "owner_pid" in record:
                    owners.append(("owner", int(record["owner_pid"])))
            if not instances:
                raise RecoveryRefused("workspace has no verified registry record")
            root = self.registry.parent / "router-instances"
            if root.exists():
                instances.update(root.glob(f"*-{workspace_digest(self.workspace)}"))
            for instance in sorted(instances):
                if not instance.is_dir():
                    raise RecoveryRefused("router instance inventory missing or unknown")
                pid = instance / "router.pid"
                if pid.exists():
                    owners.append(("router", int(pid.read_text(encoding="ascii").strip())))
                clients = instance / "router-clients"
                if clients.exists():
                    for path in clients.iterdir():
                        if path.suffix != ".json":
                            raise RecoveryRefused("unknown client registry entry")
                        client = json.loads(path.read_text(encoding="utf-8"))
                        owners.append(("client", int(client["pid"])))
                        for key in ("launcher_pid", "owner_pid", "parent_pid"):
                            if key in client:
                                owners.append((key, int(client[key])))
            observations = []
            for kind, pid in owners:
                if pid <= 0:
                    raise RecoveryRefused("invalid recorded process owner")
                identity = self.process_identity(pid)
                observations.append({"kind": kind, "pid": pid,
                    "state": "dead" if identity == "" else "live" if identity else "unknown"})
            if not owners or any(row["state"] != "dead" for row in observations):
                raise RecoveryRefused("all routers, clients and launch owners must be verified stopped")
            if self.process_inventory():
                raise RecoveryRefused("runtime/CLI processes remain; offline quarantine requires full stop")
            if any(not self.port_available(port) for port in ports):
                raise RecoveryRefused("recorded router endpoint is still occupied")
            return {"verified_stopped": True, "owners": observations}
        except RecoveryRefused:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise RecoveryRefused("process inventory unavailable or malformed") from exc

    def preview(self):
        """Read-only byte identities and known counts; never normalize corruption."""
        files, latest, invalid = {}, {}, 0
        queue_ids, invalid_queue = set(), 0
        for path in (self.queue, self.status, self.queue.with_suffix(".jsonl.1")):
            facts, raw = _snapshot(path)
            files[path.name] = facts
            if path == self.queue:
                for line in raw.splitlines(keepends=True):
                    try:
                        row = json.loads(line)
                        rid = row["id"]
                        if not line.endswith(bytes([10])) or type(rid) is not int or rid < 1:
                            raise ValueError()
                        queue_ids.add(rid)
                    except (ValueError, TypeError, KeyError, UnicodeError):
                        invalid_queue += 1
            if path == self.status:
                for line in raw.splitlines(keepends=True):
                    try:
                        from .runtime_input_status import _decode_history_row
                        row = _decode_history_row(line)
                        rid = row["request_id"]
                        if not line.endswith(bytes([10])) or not str(rid).isascii() or not str(rid).isdigit() or int(rid) < 1:
                            raise ValueError()
                        if row["status"] not in {"queued", "submitted", "replied", "failed", "skipped", "interrupted_uncertain"}:
                            raise ValueError()
                        latest[int(rid)] = row["status"]
                    except (ValueError, TypeError, KeyError, UnicodeError):
                        invalid += 1
        marker_facts, _ = _snapshot(recovery_marker(self.queue))
        previous = recovery_generation(self.queue)
        maximum = max(f["max_known_id"] for f in files.values())
        if previous:
            maximum = max(maximum, previous["id_floor"] - 1)
        result = {"version": 1, "workspace": self.workspace, "state_dir": str(self.state_dir),
                  "registry": str(self.registry), "files": files, "marker": marker_facts,
                  "max_known_id": maximum, "id_floor": maximum + 1,
                  "known_pending": {s: sum(v == s for v in latest.values()) for s in ("queued", "submitted")},
                  "counts_complete": invalid == 0 and invalid_queue == 0,
                  "invalid_status_rows": invalid, "invalid_queue_rows": invalid_queue,
                  "known_queue_rows": len(queue_ids),
                  "known_queue_without_status": len(queue_ids - latest.keys()),
                  "effect": "quarantine all old queue/status bytes; no replay; new inputs only"}
        result["confirmation_token"] = hashlib.sha256(_canonical(result)).hexdigest()
        try:
            result["offline"] = self._offline()
        except RecoveryRefused as exc:
            result["offline"] = {"verified_stopped": False, "reason": str(exc)}
        return result

    def confirm(self, token: str, *, quarantine_old_inputs: bool = False):
        if quarantine_old_inputs is not True:
            raise RecoveryRefused("explicit data-changing quarantine confirmation required")
        if not isinstance(token, str) or len(token) != 64:
            raise RecoveryRefused("preview confirmation token required")
        with registry_maintenance_lock(self.registry), exclusive_file_lock(self.queue), exclusive_file_lock(self.status):
            offline = self._offline()
            current = self.preview()
            if current["confirmation_token"] != token:
                raise RecoveryRefused("preview is stale; inspect both histories again")
            generation = uuid.uuid4().hex
            archive = self.state_dir / "runtime-input-quarantine" / generation
            archive.mkdir(parents=True, exist_ok=False)
            for name, facts in current["files"].items():
                observed, raw = _snapshot(self.state_dir / name)
                if observed != facts:
                    raise RecoveryRefused("history changed before backup")
                if facts["exists"]:
                    _durable(archive / name, raw)
            old_marker, old_marker_bytes = _snapshot(recovery_marker(self.queue))
            if old_marker != current["marker"]:
                raise RecoveryRefused("generation changed before backup")
            if old_marker["exists"]:
                _durable(archive / "previous-generation.json", old_marker_bytes)
            manifest = {"version": 1, "phase": "prepared", "generation": generation,
                        "id_floor": current["id_floor"], "archive": str(archive),
                        "preview": current, "offline": offline}
            # Marker is fail-closed before any original is replaced.
            _durable(recovery_marker(self.queue), _canonical(manifest), replace=True)
            _durable(archive / "manifest.json", _canonical(manifest))
            return self._finalize(manifest)

    def _finalize(self, manifest):
        archive = Path(manifest["archive"])
        for name, facts in manifest["preview"]["files"].items():
            path = self.state_dir / name
            if facts["exists"]:
                backup, _ = _snapshot(archive / name)
                if backup["sha256"] != facts["sha256"] or backup["size"] != facts["size"] or not backup["exists"]:
                    raise RecoveryRefused("quarantine backup verification failed")
            observed, _ = _snapshot(path)
            if observed["exists"] and observed["sha256"] not in {facts["sha256"], hashlib.sha256(b"").hexdigest()}:
                raise RecoveryRefused("history changed during prepared recovery")
        for name in manifest["preview"]["files"]:
            _durable(self.state_dir / name, b"", replace=True)
        finalized = {**manifest, "phase": "finalized"}
        _durable(archive / "finalized.json", _canonical(finalized), replace=True)
        _durable(recovery_marker(self.queue), _canonical(finalized), replace=True)
        return {"ok": True, "generation": manifest["generation"], "id_floor": manifest["id_floor"],
                "archive": str(archive), "replayed_old_inputs": 0}

    def resume_prepared(self, token: str, *, quarantine_old_inputs: bool = False):
        """Explicit offline retry of the same transaction, never rollback/replay."""
        if quarantine_old_inputs is not True:
            raise RecoveryRefused("explicit data-changing quarantine confirmation required")
        with registry_maintenance_lock(self.registry), exclusive_file_lock(self.queue), exclusive_file_lock(self.status):
            self._offline()
            try:
                manifest = json.loads(recovery_marker(self.queue).read_text(encoding="utf-8"))
                if manifest["phase"] != "prepared" or manifest["preview"]["confirmation_token"] != token:
                    raise ValueError()
                archive = Path(manifest["archive"])
                if archive.parent != self.state_dir / "runtime-input-quarantine":
                    raise ValueError()
                archived_manifest = archive / "manifest.json"
                if archived_manifest.exists():
                    if json.loads(archived_manifest.read_text(encoding="utf-8")) != manifest:
                        raise ValueError()
                else:
                    _durable(archived_manifest, _canonical(manifest))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise RecoveryRefused("prepared recovery identity mismatch") from exc
            return self._finalize(manifest)
