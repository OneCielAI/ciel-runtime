"""Durable worktree ownership. Lifecycle APIs require trusted host observations."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from .channel_message_repository import exclusive_file_lock
from .worktree_cleanup import cleanup_worktree

class WorkspaceError(ValueError):
    pass

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

def _identity(repo, lease_id, config_dir):
    if not _text(lease_id) or len(lease_id) > 200:
        raise WorkspaceError("invalid lease identity")
    repo = _plain(repo)
    if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise WorkspaceError("repo must be exact worktree root")
    key = hashlib.sha256((os.path.normcase(str(repo)) + chr(0) + lease_id).encode()).hexdigest()
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
        if (type(r) is not dict or set(r) - set("schema repo path head initial_head branch lease_id status common_dir git_dir host_id idle idle_proof removal".split())
                or not set("schema repo path head initial_head branch lease_id status common_dir git_dir host_id idle idle_proof".split()) <= set(r)
                or not all(_text(r[k]) for k in ("head", "initial_head", "common_dir"))
                or (r["status"] == "ready" and not _text(r["git_dir"]))
                or r["schema"] != 1 or r["repo"] != str(repo) or r["lease_id"] != lease_id
                or r["path"] != str(repo / ".claude" / "worktrees" / ("work-parallel-" + key[:24]))
                or r["branch"] != "work-parallel/" + key
                or r["status"] not in ("preparing", "ready", "removed")
                or type(r["idle"]) is not bool
                or (r["host_id"] is not None and not _text(r["host_id"]))
                or (r["idle"] and (not _text(r["idle_proof"]) or not _text(r["host_id"])))):
            raise ValueError("ownership mismatch")
        return r
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WorkspaceError("missing/corrupt ownership manifest; retained") from exc

def _verify(r):
    if r["status"] != "ready":
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
        r = _verify(_load(manifest, repo, lease_id, key))
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
        r = _verify(_load(manifest, repo, lease_id, key))
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
