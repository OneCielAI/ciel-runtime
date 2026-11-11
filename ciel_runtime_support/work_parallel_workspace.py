"""Durable worktree ownership. Lifecycle APIs require trusted host observations."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from contextlib import ExitStack
from pathlib import Path
import re
import subprocess
import time
import uuid
from .channel_message_repository import exclusive_file_lock
from .worktree_cleanup import cleanup_worktree

class WorkspaceError(ValueError):
    pass

_MAX_CHANGED_FILES = 100
_MAX_PATH_LENGTH = 2000
_MAX_DIFF_STAT = 16_384
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

def _git(repo, *args):
    started = time.monotonic()
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           text=True, encoding="utf-8", timeout=30, check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        # Seen only inside the router (owned run 2026-11-09: worktree add, 30 s);
        # the same command outside it took 0.06 s. Name the step for the log.
        raise WorkspaceError("git " + " ".join(str(a) for a in args[:2]) + " timed out after "
                             + format(time.monotonic() - started, ".0f") + " s") from exc
    if r.returncode:
        raise WorkspaceError("Git verification failed: " + r.stderr.strip()[:400])
    return r.stdout.strip()

def _plain(path):
    path = Path(path).absolute()
    for p in (path, *path.parents):
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise WorkspaceError("symlink/junction retained")
    return path.resolve()

def _text(v):
    return isinstance(v, str) and 0 < len(v) <= 2000 and chr(0) not in v

def _lease_key(repo, lease_id):
    return hashlib.sha256((os.path.normcase(str(repo)) + chr(0) + lease_id).encode()).hexdigest()


def _identity(repo, lease_id, config_dir):
    if not _text(lease_id) or len(lease_id) > 200:
        raise WorkspaceError("invalid lease identity")
    repo = _plain(repo)
    if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise WorkspaceError("repo must be exact worktree root")
    key = _lease_key(repo, lease_id)
    if config_dir is None:
        from .runtime_paths import CONFIG_DIR
        config_dir = CONFIG_DIR
    return repo, key, Path(config_dir).resolve() / "work-parallel-workspaces" / (key + ".json")

def _save(path, record):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def _load(path, repo, lease_id, key):
    try:
        def unique(pairs):
            d = {}
            for k, v in pairs:
                if k in d:
                    raise ValueError("duplicate field")
                d[k] = v
            return d
        with path.open("rb") as f:
            raw = f.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized manifest")
        r = json.loads(raw, object_pairs_hook=unique)
        required = set("schema repo path head initial_head branch lease_id status common_dir git_dir host_id idle idle_proof".split())
        allowed = required | set("removal ownership_lease_id generation prior_lease_id handoff".split())
        ownership_lease_id = r.get("ownership_lease_id", lease_id) if type(r) is dict else None
        owner_key = _lease_key(repo, ownership_lease_id) if _text(ownership_lease_id) else ""
        handoff = r.get("handoff") if type(r) is dict else None
        if (type(r) is not dict or set(r) - allowed or not required <= set(r)
                or not all(_text(r[k]) for k in ("head", "initial_head", "common_dir"))
                or (r["status"] in ("ready", "rebound") and not _text(r["git_dir"]))
                or r["schema"] != 1 or r["repo"] != str(repo) or r["lease_id"] != lease_id
                or r["path"] != str(repo / ".claude" / "worktrees" / ("work-parallel-" + owner_key[:24]))
                or r["branch"] != "work-parallel/" + owner_key
                or r["status"] not in ("preparing", "ready", "removed", "rebound")
                or ("ownership_lease_id" in r and not _text(r["ownership_lease_id"]))
                or ("generation" in r and not _text(r["generation"]))
                or ("prior_lease_id" in r and not _text(r["prior_lease_id"]))
                or (handoff is not None and (type(handoff) is not dict
                    or set(handoff) != {"receipt_digest", "prior_generation", "replacement_lease_id", "replacement_generation"}
                    or not all(_text(handoff.get(k)) for k in handoff)))
                or type(r["idle"]) is not bool
                or (r["host_id"] is not None and not _text(r["host_id"]))
                or (r["idle"] and (not _text(r["idle_proof"]) or not _text(r["host_id"])))):
            raise ValueError("ownership mismatch")
        return r
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WorkspaceError("missing/corrupt ownership manifest; retained") from exc

def _verify(r):
    # ``rebound`` is a retained ownership tombstone for the prior generation;
    # the same Git worktree is still verified below and its replacement alias is
    # the only record allowed to bind a new host.
    if r["status"] not in ("ready", "rebound"):
        raise WorkspaceError("workspace preparation/removal incomplete; retained")
    repo, target = _plain(r["repo"]), _plain(r["path"])
    if not target.is_dir():
        raise WorkspaceError("owned path unavailable; retained")
    if Path(_git(target, "rev-parse", "--show-toplevel")).resolve() != target:
        raise WorkspaceError("owned worktree root changed; retained")
    common = _git(target, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if (common != r["common_dir"]
            or _git(target, "rev-parse", "--absolute-git-dir") != r["git_dir"]
            or common != _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
            or _git(target, "symbolic-ref", "--quiet", "HEAD") != "refs/heads/" + r["branch"]):
        raise WorkspaceError("Git ownership/branch changed; retained")
    paths = [Path(row[9:]).resolve() for row in _git(repo, "-c", "core.quotePath=false", "worktree", "list", "--porcelain").splitlines()
             if row.startswith("worktree ")]
    if target not in paths:
        raise WorkspaceError("owned registration missing; retained")
    return {**r, "head": _git(target, "rev-parse", "HEAD")}

def _owned(r):
    """Mutation/salvage gate: a rebound tombstone can be observed, never used."""
    if r["status"] != "ready":
        raise WorkspaceError("workspace ownership was handed off; retained")
    return r

def prepare(repo, lease_id, *, config_dir=None):
    """Create once or verify durable binding. Never adopt an existing path.
    Persist returned binding on lease before dispatch; path is native worker cwd.
    Incomplete creation stays retained and blocks retries. config_dir is a test
    override for CONFIG_DIR. Preparation starts non-idle, including after crash.
    """
    repo, key, manifest = _identity(repo, lease_id, config_dir)
    with exclusive_file_lock(manifest):
        if manifest.exists():
            return _verify(_load(manifest, repo, lease_id, key))
        target = repo / ".claude" / "worktrees" / ("work-parallel-" + key[:24])
        _plain(target)
        if target.exists():
            raise WorkspaceError("existing path has no ownership manifest; retained")
        head = _git(repo, "rev-parse", "HEAD")
        r = dict(schema=1, repo=str(repo), path=str(target), head=head, initial_head=head,
                 branch="work-parallel/" + key, lease_id=lease_id, status="preparing",
                 common_dir=_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"),
                 git_dir=None, host_id=None, idle=False, idle_proof=None)
        _save(manifest, r)
        _git(repo, "worktree", "add", "-b", r["branch"], str(target), head)
        r.update(git_dir=_git(target, "rev-parse", "--absolute-git-dir"), status="ready")
        _verify(r)
        _save(manifest, r)
        return dict(r)

def observe(repo, lease_id, *, config_dir=None):
    """Exact ownership and current HEAD, never idle inferred from time/PID."""
    repo, key, manifest = _identity(repo, lease_id, config_dir)
    with exclusive_file_lock(manifest):
        return _verify(_load(manifest, repo, lease_id, key))

def _snapshot(record):
    """Return bounded Git metadata only. Never read or return file contents."""
    target = Path(record["path"])
    raw = _git(target, "-c", "core.quotePath=false", "status", "--porcelain=v1",
               "-z", "--untracked-files=all", "--ignored")
    names, rows, index, total = [], (raw.split(chr(0)) if raw else []), 0, 0
    while index < len(rows):
        row = rows[index]
        index += 1
        if not row:
            continue
        total += 1
        name = row[3:] if len(row) >= 4 else ""
        if row[:2] in ("R ", " R", "C ", " C") and index < len(rows):
            source = rows[index]
            index += 1
            name = source + " -> " + name
        if len(names) < _MAX_CHANGED_FILES:
            names.append(name[:_MAX_PATH_LENGTH])
    stat = _git(target, "-c", "core.quotePath=false", "diff", "--stat", "HEAD", "--", ":(top)")
    snapshot = {"path": record["path"], "branch": record["branch"],
                "head": _git(target, "rev-parse", "HEAD"), "dirty": bool(rows),
                "changed_files": names, "changed_files_total": total,
                "changed_files_truncated": total > len(names),
                "diff_stat": stat[:_MAX_DIFF_STAT],
                "diff_stat_truncated": len(stat) > _MAX_DIFF_STAT}
    return snapshot


def _receipt_digest(snapshot, lease_id, generation):
    body = {"snapshot": snapshot, "lease_id": lease_id, "generation": generation}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def salvage_observe(repo, lease_id, *, generation, terminal, slot, config_dir=None):
    """Host-only ownership proof and bounded metadata for salvage."""
    if not _text(generation) or terminal is not True or slot is not False:
        raise WorkspaceError("terminal slot-free generation required for salvage")
    repo, key, manifest = _identity(repo, lease_id, config_dir)
    with exclusive_file_lock(manifest):
        record = _owned(_verify(_load(manifest, repo, lease_id, key)))
        snapshot = _snapshot(record)
        return {**snapshot, "lease_id": lease_id, "generation": generation,
                "receipt_digest": _receipt_digest(snapshot, lease_id, generation)}


def handoff_rebind(repo, prior_lease_id, replacement_lease_id, *,
                   prior_generation, replacement_generation, receipt_digest,
                   expected_path, expected_branch, expected_head, expected_dirty,
                   prior_terminal, prior_slot, config_dir=None):
    """Rebind a retained workspace after a fresh exact receipt observation."""
    if (prior_lease_id == replacement_lease_id or not all(_text(v) for v in
            (prior_generation, replacement_generation, receipt_digest,
             expected_path, expected_branch, expected_head))
            or not _SHA256.fullmatch(receipt_digest) or type(expected_dirty) is not bool
            or prior_terminal is not True or prior_slot is not False):
        raise WorkspaceError("invalid terminal replacement handoff")
    repo, old_key, old_manifest = _identity(repo, prior_lease_id, config_dir)
    repo2, new_key, new_manifest = _identity(repo, replacement_lease_id, config_dir)
    if repo2 != repo:
        raise WorkspaceError("replacement repo mismatch")
    with ExitStack() as stack:
        for lock in sorted((old_manifest, new_manifest), key=lambda p: os.path.normcase(str(p))):
            stack.enter_context(exclusive_file_lock(lock))
        old = _load(old_manifest, repo, prior_lease_id, old_key)
        prior_handoff = old.get("handoff") or {}
        if old["status"] == "rebound":
            if prior_handoff.get("replacement_lease_id") == replacement_lease_id and new_manifest.exists():
                current = _verify(_load(new_manifest, repo, replacement_lease_id, new_key))
                if (current["path"] == old["path"]
                        and prior_handoff.get("receipt_digest") == receipt_digest
                        and prior_handoff.get("prior_generation") == prior_generation
                        and prior_handoff.get("replacement_generation") == replacement_generation):
                    return current
            raise WorkspaceError("workspace already rebound; retained")
        old = _verify(old)
        snapshot = _snapshot(old)
        expected = {"path": expected_path, "branch": expected_branch,
                    "head": expected_head, "dirty": expected_dirty}
        if (not hmac.compare_digest(_receipt_digest(snapshot, prior_lease_id,
                                                     prior_generation), receipt_digest)
                or any(snapshot[k] != v for k, v in expected.items())):
            raise WorkspaceError("stale or mismatched salvage receipt; retained")
        if new_manifest.exists():
            current = _verify(_load(new_manifest, repo, replacement_lease_id, new_key))
            handoff = current.get("handoff") or {}
            if (current["path"] == old["path"] and handoff.get("receipt_digest") == receipt_digest
                    and handoff.get("prior_generation") == prior_generation
                    and handoff.get("replacement_generation") == replacement_generation):
                return current
            raise WorkspaceError("replacement already owns another workspace; retained")
        replacement = {**old, "lease_id": replacement_lease_id,
                       "ownership_lease_id": old.get("ownership_lease_id", prior_lease_id),
                       "generation": replacement_generation, "prior_lease_id": prior_lease_id,
                       "status": "ready", "host_id": None, "idle": False, "idle_proof": None,
                       "handoff": {"receipt_digest": receipt_digest,
                                   "prior_generation": prior_generation,
                                   "replacement_lease_id": replacement_lease_id,
                                   "replacement_generation": replacement_generation}}
        replacement.pop("removal", None)
        _save(new_manifest, replacement)
        old.update(status="rebound", handoff=replacement["handoff"])
        _save(old_manifest, old)
        return dict(replacement)


def bind(repo, lease_id, host_id, *, config_dir=None):
    """Bind authenticated native host once; identical retries accepted."""
    return _activity(repo, lease_id, host_id, False, None, config_dir, binding=True)

def set_activity(repo, lease_id, host_id, *, idle, proof=None, config_dir=None):
    """Trusted host only. Set idle=False BEFORE dispatch/resume.
    idle=True requires observed lifecycle evidence. Adapter authenticates facts;
    model-provided values must never be treated as authoritative observations.
    """
    return _activity(repo, lease_id, host_id, idle, proof, config_dir)

def _activity(repo, lease_id, host_id, idle, proof, config_dir, binding=False):
    if not _text(host_id) or type(idle) is not bool or (idle and not _text(proof)):
        raise WorkspaceError("host identity and idle evidence required")
    repo, key, manifest = _identity(repo, lease_id, config_dir)
    with exclusive_file_lock(manifest):
        r = _owned(_verify(_load(manifest, repo, lease_id, key)))
        if r["host_id"] != host_id and not (binding and r["host_id"] is None):
            raise WorkspaceError("workspace host binding mismatch")
        r.update(host_id=host_id, idle=idle, idle_proof=proof if idle else None)
        _save(manifest, r)
        return dict(r)

def remove(repo, lease_id, *, host_id, active, idle_proof, expected_head,
           approved_target=None, remote="origin", remote_ref="", config_dir=None):
    """Exact ownership + idle lease + reviewed HEAD + pushed OR user approval.
    active/idle_proof come from bound lease; approved_target must be explicit user
    approval for this exact path. Dirty/ignored block. Branch/tombstone retained.
    """
    repo, key, manifest = _identity(repo, lease_id, config_dir)
    with exclusive_file_lock(manifest):
        r = _owned(_verify(_load(manifest, repo, lease_id, key)))
        if (active is not False or not r["idle"] or r["host_id"] != host_id
                or not _text(idle_proof) or idle_proof != r["idle_proof"]):
            raise WorkspaceError("verified matching host idle/lease activity required; retained")
        if r["head"] != expected_head:
            raise WorkspaceError("workspace HEAD changed; review cleanup again")
        try:
            result = cleanup_worktree(repo, Path(r["path"]), owned_root=repo / ".claude" / "worktrees",
                                      expected_head=expected_head, active=False,
                                      approved_target=approved_target, remote=remote, remote_ref=remote_ref)
        except ValueError as exc:
            raise WorkspaceError(str(exc)) from exc
        r.update(status="removed", removal=result)
        _save(manifest, r)
        return {**result, "lease_id": lease_id, "repo": str(repo), "host_id": host_id}
