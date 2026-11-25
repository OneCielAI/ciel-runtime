"""Durable worktree ownership. Lifecycle APIs require trusted host observations."""
from __future__ import annotations
import hashlib
import hmac
import json
import math
import os
import stat
from contextlib import ExitStack
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
from .channel_message_repository import exclusive_file_lock
from .worktree_cleanup import cleanup_worktree

class WorkspaceError(ValueError):
    """``clean`` is True only when the failing call changed nothing on disk.

    ``prepare`` sets it: a failure before its ownership manifest is first saved
    leaves no file, branch or worktree, so the caller may release the
    reservation. Any later failure keeps whatever was written (retained).
    """

    def __init__(self, *args, clean=False):
        super().__init__(*args)
        self.clean = clean


# Git's own refusal when a repository is owned by another account; the router
# runs Work git commands as its own account (a sandbox account on Celly).
DUBIOUS_OWNERSHIP = "detected dubious ownership"


def trust_arguments(trust):
    """Command-scope ``safe.directory`` entries for exact repository roots.

    Git honors ``safe.directory`` only from protected configuration, which
    includes the command scope (``git -c``); each value must name the exact
    worktree root git discovers, so a parent folder or subdirectory matches
    nothing. A wildcard is refused: it would trust every repository.
    """
    arguments = []
    for path in trust:
        value = str(path)
        if not value or "*" in value or chr(0) in value or not Path(value).is_absolute():
            raise WorkspaceError("Work git trust names exact absolute repository roots only")
        arguments += ["-c", "safe.directory=" + value]
    return arguments


_OWNED_WORKTREE = re.compile(r"work-parallel-[0-9a-f]{24}\Z")


def trusted_roots(path):
    """``(path,)`` when git may trust it for Work, else ``()``.

    Trusted are exactly the folder this router was launched in, which the
    person chose to run Ciel Runtime in, and a Work worktree directly under its
    ``.claude/worktrees`` (``work-parallel-<24 hex>``, created by this code).
    Any other folder gets git's own ownership check, as before. A sandbox
    account runs the router in a repository another account owns (Celly,
    2026-11-17: "detected dubious ownership"), so without this every Work
    worktree preparation failed there.
    """
    from . import runtime_paths
    from .workspace_router_selection import workspace_identity
    launch = workspace_identity(runtime_paths.ROUTER_WORKSPACE)
    if not launch or not str(path):
        return ()
    candidate = Path(path)
    if workspace_identity(candidate) == launch:
        return (candidate,)
    if (_OWNED_WORKTREE.fullmatch(candidate.name) is not None
            and workspace_identity(candidate.parent) == workspace_identity(Path(launch) / ".claude" / "worktrees")):
        return (candidate,)
    return ()


_MAX_CHANGED_FILES = 100
_MAX_PATH_LENGTH = 2000
_MAX_DIFF_STAT = 16_384
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
CONTENT_BASELINE_PRODUCER = "ciel-runtime/work-content-baseline-v1"
MAX_CONTENT_ENTRIES = 20_000
MAX_CONTENT_TOTAL_BYTES = 1_073_741_824
MAX_CONTENT_SINGLE_FILE_BYTES = 268_435_456
CONTENT_CAPTURE_DEADLINE_SECONDS = 30
_CONTENT_CHUNK = 1_048_576
_CONTENT_SUFFIX = ".content-baseline-v1.json"
_CONTENT_INTENT_SUFFIX = ".content-baseline-v1.intent"
_CONTENT_SKIPPED_SUFFIX = ".content-baseline-v1.skipped"

_GIT_TIMEOUT_SECONDS = 30.0
_GIT_NONINTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GIT_GCM_INTERACTIVE": "never",
}


def _kill_process_tree(process):
    """Kill the whole subprocess tree so grandchildren cannot hold pipes open."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    else:
        try:
            os.killpg(process.pid, 9)
        except OSError:
            process.kill()
    try:
        process.kill()
    except OSError:
        pass


def _git_popen_env():
    return {**os.environ, **_GIT_NONINTERACTIVE_ENV}


def _run_git_subprocess(cmd, *, timeout, env):
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "stdin": subprocess.DEVNULL,
              "text": True, "encoding": "utf-8", "errors": "replace", "env": env}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    process = subprocess.Popen(cmd, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Betty 2026-11-25: on Windows, git.exe (cmd wrapper) spawns mingw64 git
        # and credential-manager; killing only the wrapper left grandchildren
        # holding stdout/stderr open so communicate() blocked forever and kept
        # the unbound reservation's slot pinned. Kill the whole tree before draining.
        _kill_process_tree(process)
        try:
            process.communicate(timeout=5)
        except Exception:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        raise
    return subprocess.CompletedProcess(cmd, process.returncode, stdout or "", stderr or "")


def _git(repo, *args, strip=True, trust=None):
    """One Work git command run in ``repo``; ``trust`` defaults to trusted_roots(repo)."""
    started = time.monotonic()
    trust = trusted_roots(repo) if trust is None else trust
    cmd = ["git", *trust_arguments(trust), "-C", str(repo), *args]
    try:
        r = _run_git_subprocess(cmd, timeout=_GIT_TIMEOUT_SECONDS, env=_git_popen_env())
    except subprocess.TimeoutExpired as exc:
        # Seen only inside the router (owned run 2026-11-09: worktree add, 30 s);
        # the same command outside it took 0.06 s. Name the step for the log.
        raise WorkspaceError("git " + " ".join(str(a) for a in args[:2]) + " timed out after "
                             + format(time.monotonic() - started, ".0f") + " s") from exc
    if r.returncode:
        raise WorkspaceError("Git verification failed: " + r.stderr.strip()[:400])
    # Column-positioned output (status rows start with their two status
    # characters, the first of which may be a space) must be read unstripped.
    return r.stdout.strip() if strip else r.stdout

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
        path.parent.mkdir(parents=True, exist_ok=True)
        with temp.open("x", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _sync_parent_directory(path):
    """Best-effort directory durability; Windows commonly refuses directory fsync."""
    try:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return "synced"
    except OSError:
        return "unsupported"


def _save_content_record(path, record):
    _save(path, record)
    return _sync_parent_directory(path)


def content_baseline_paths(repo, lease_id, *, config_dir=None):
    """Paths keyed beside the exact replacement manifest; beta ignores them."""
    repo, key, manifest = _identity(repo, lease_id, config_dir)
    return {"manifest": manifest,
            "intent": manifest.with_name(key + _CONTENT_INTENT_SUFFIX),
            "sidecar": manifest.with_name(key + _CONTENT_SUFFIX),
            "skipped": manifest.with_name(key + _CONTENT_SKIPPED_SUFFIX),
            "replacement_key": key, "repo": repo}


# A worktree too large or too slow to fingerprint within the bounds. The
# baseline only attributes drift before a rework binds (changed_before_bind);
# Celly 2026-11-19: 20,001 entries refused n1108's rework on every pump forever.
_CAPACITY_ERRORS = ("content baseline entry bound exceeded", "content baseline deadline exceeded",
                    "content baseline single file byte bound exceeded",
                    "content baseline total byte bound exceeded")


def _capacity_error(error):
    return str(error).startswith(_CAPACITY_ERRORS)

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

def _complete_interrupted(r):
    """The ready record of this manifest's own creation, when only its last save was lost.

    Andy 2026-11-20 (n1174 recovery 8b365b32): ``worktree add`` had created and
    registered the worktree on the manifest's own branch, but the manifest stayed
    ``preparing``, so every retry was refused and the recovery held its slot for
    a day. Only an unbound fresh creation whose worktree verifies exactly (root,
    Git dirs, branch, registration) and still sits at its initial HEAD is
    completed. Anything else stays retained.
    """
    if (r["status"] != "preparing" or r["git_dir"] is not None or r["host_id"] is not None or r["idle"]
            or any(key in r for key in ("ownership_lease_id", "generation", "prior_lease_id", "handoff", "removal"))):
        return None
    try:
        target = _plain(r["path"])
        if not target.is_dir():
            return None
        candidate = {**r, "git_dir": _git(target, "rev-parse", "--absolute-git-dir"), "status": "ready"}
        verified = _verify(candidate)
    except WorkspaceError:
        return None
    return candidate if verified["head"] == r["initial_head"] == r["head"] else None


def prepare(repo, lease_id, *, config_dir=None):
    """Create once or verify durable binding. Never adopt an existing path.
    Persist returned binding on lease before dispatch; path is native worker cwd.
    Incomplete creation stays retained and blocks retries. config_dir is a test
    override for CONFIG_DIR. Preparation starts non-idle, including after crash.
    """
    try:
        repo, key, manifest = _identity(repo, lease_id, config_dir)
    except WorkspaceError as error:
        raise WorkspaceError(*error.args, clean=True) from error
    with exclusive_file_lock(manifest):
        if manifest.exists():
            r = _load(manifest, repo, lease_id, key)
            completed = _complete_interrupted(r)
            if completed is not None:
                _save(manifest, completed)
                return dict(completed)
            return _verify(r)
        target = repo / ".claude" / "worktrees" / ("work-parallel-" + key[:24])
        try:
            # Nothing below writes until the manifest is saved: a failure here
            # leaves no file, branch or worktree behind.
            _plain(target)
            if target.exists():
                raise WorkspaceError("existing path has no ownership manifest; retained")
            head = _git(repo, "rev-parse", "HEAD")
            common_dir = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        except WorkspaceError as error:
            raise WorkspaceError(*error.args, clean=True) from error
        r = dict(schema=1, repo=str(repo), path=str(target), head=head, initial_head=head,
                 branch="work-parallel/" + key, lease_id=lease_id, status="preparing",
                 common_dir=common_dir, git_dir=None, host_id=None, idle=False, idle_proof=None)
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

def _status_names(raw, *, legacy=False):
    """Bounded names and row count from NUL-separated ``status --porcelain=v1``.

    A row is ``XY name``; a rename or copy row is followed by its source path.
    ``legacy`` reproduces the parse of builds up to beta 57dbfb3, which read the
    stripped output (a leading space of the first row was lost, so its name lost
    its first character) and recognized a second path only for the exact pairs
    below. It exists for the digest body alone: see ``_snapshot``.
    """
    names, rows, index, total = [], (raw.split(chr(0)) if raw else []), 0, 0
    while index < len(rows):
        row = rows[index]
        index += 1
        if not row:
            continue
        total += 1
        name = row[3:] if len(row) >= 4 else ""
        paired = (row[:2] in ("R ", " R", "C ", " C") if legacy
                  else "R" in row[:2] or "C" in row[:2])
        if paired and index < len(rows):
            source = rows[index]
            index += 1
            name = source + " -> " + name
        if len(names) < _MAX_CHANGED_FILES:
            names.append(name[:_MAX_PATH_LENGTH])
    return names, total


def _deadline(started):
    if time.monotonic() - started > CONTENT_CAPTURE_DEADLINE_SECONDS:
        raise WorkspaceError("content baseline deadline exceeded: "
                             + str(CONTENT_CAPTURE_DEADLINE_SECONDS) + "s")


def _git_binary(repo, *args, started, trust=None):
    _deadline(started)
    remaining = max(.001, CONTENT_CAPTURE_DEADLINE_SECONDS - (time.monotonic() - started))
    trust = trusted_roots(repo) if trust is None else trust
    try:
        result = subprocess.run(["git", *trust_arguments(trust), "-C", str(repo), *args], capture_output=True,
                                timeout=remaining, check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError("content baseline deadline exceeded: "
                             + str(CONTENT_CAPTURE_DEADLINE_SECONDS) + "s") from exc
    if result.returncode:
        raise WorkspaceError("Git content fingerprint failed")
    return result.stdout


def _enumerate_content_paths(target, started):
    """Return raw path bytes + exact index bytes, stopping at the entry bound."""
    index = _git_binary(target, "ls-files", "--stage", "-z", started=started)
    modes, paths = {}, set()
    for row in index.split(b"\0"):
        if not row:
            continue
        try:
            prefix, raw_path = row.split(b"\t", 1)
            mode = prefix.split(b" ", 1)[0]
        except ValueError as exc:
            raise WorkspaceError("Git content fingerprint failed") from exc
        modes[raw_path] = mode
        paths.add(raw_path)
    for args in (("ls-files", "--others", "--exclude-standard", "-z"),
                 ("ls-files", "--others", "--ignored", "--exclude-standard", "-z")):
        _deadline(started)
        remaining = max(.001, CONTENT_CAPTURE_DEADLINE_SECONDS - (time.monotonic() - started))
        # stderr is never read (the error text names no Git output), so it is
        # not piped: an unread pipe could fill and stall the child.
        process = subprocess.Popen(["git", *trust_arguments(trusted_roots(target)), "-C", str(target), *args],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        # read() blocks while the child writes nothing, so the between-chunk
        # check alone cannot end a silent child. The watchdog kills this exact
        # child at the remaining deadline; the blocked read then sees EOF.
        expired = threading.Event()
        def _expire(child=process, flag=expired):
            flag.set()
            if child.poll() is None:
                try:
                    child.kill()
                except OSError:
                    pass
        watchdog = threading.Timer(remaining, _expire)
        watchdog.daemon = True
        watchdog.start()
        pending = b""
        try:
            while True:
                _deadline(started)
                chunk = process.stdout.read(_CONTENT_CHUNK)
                if not chunk:
                    break
                pending += chunk
                parts = pending.split(b"\0")
                pending = parts.pop()
                for raw_path in parts:
                    if raw_path and raw_path not in (b".git", b".claude/worktrees") \
                            and not raw_path.startswith((b".git/", b".claude/worktrees/")):
                        paths.add(raw_path)
                        if len(paths) > MAX_CONTENT_ENTRIES:
                            process.kill()
                            process.wait()
                            raise WorkspaceError("content baseline entry bound exceeded: "
                                                 + str(len(paths)) + "/" + str(MAX_CONTENT_ENTRIES))
            if expired.is_set():
                raise WorkspaceError("content baseline deadline exceeded: "
                                     + str(CONTENT_CAPTURE_DEADLINE_SECONDS) + "s")
            try:
                process.wait(timeout=max(.001, CONTENT_CAPTURE_DEADLINE_SECONDS
                                         - (time.monotonic() - started)))
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise WorkspaceError("content baseline deadline exceeded: "
                                     + str(CONTENT_CAPTURE_DEADLINE_SECONDS) + "s") from exc
            if expired.is_set():
                raise WorkspaceError("content baseline deadline exceeded: "
                                     + str(CONTENT_CAPTURE_DEADLINE_SECONDS) + "s")
            if process.returncode:
                raise WorkspaceError("Git content fingerprint failed")
        finally:
            watchdog.cancel()
            watchdog.join()
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
    paths = {raw for raw in paths if raw not in (b".git", b".claude/worktrees")
             and not raw.startswith((b".git/", b".claude/worktrees/"))}
    if len(paths) > MAX_CONTENT_ENTRIES:
        raise WorkspaceError("content baseline entry bound exceeded: "
                             + str(len(paths)) + "/" + str(MAX_CONTENT_ENTRIES))
    return index, modes, sorted(paths)


def _is_unsupported_reparse(row, path):
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attrs = getattr(row, "st_file_attributes", 0)
    return bool(attrs & flag) and not path.is_symlink()


def _content_fingerprint(record):
    """Hash exact index/worktree content without walking Git administration."""
    started = time.monotonic()
    target = Path(record["path"])
    head = _git_binary(target, "rev-parse", "HEAD", started=started).strip()
    index, modes, raw_paths = _enumerate_content_paths(target, started)
    rows, total = [], 0
    for raw_path in raw_paths:
        _deadline(started)
        path = target / os.fsdecode(raw_path)
        mode = modes.get(raw_path)
        if mode == b"160000":
            if not path.exists():
                payload = b"absent"
            else:
                # A submodule is neither the launch folder nor a Work worktree.
                sub_head = _git_binary(path, "rev-parse", "HEAD", started=started, trust=()).strip()
                sub_dirty = _git_binary(path, "status", "--porcelain=v1", "-z",
                                        "--untracked-files=all", started=started, trust=())
                payload = b"head\0" + sub_head + b"\0dirty\0" + sub_dirty
            rows.append((raw_path, b"S", payload, None))
            total += len(payload)
            continue
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            rows.append((raw_path, b"D", b"", None))
            continue
        if _is_unsupported_reparse(before, path):
            raise WorkspaceError("unsupported worktree reparse type")
        if stat.S_ISLNK(before.st_mode):
            payload = os.fsencode(os.readlink(path))
            rows.append((raw_path, b"L", payload, before))
            total += len(payload)
        elif stat.S_ISREG(before.st_mode):
            if before.st_size > MAX_CONTENT_SINGLE_FILE_BYTES:
                raise WorkspaceError("content baseline single file byte bound exceeded: "
                                     + str(before.st_size) + "/" + str(MAX_CONTENT_SINGLE_FILE_BYTES))
            total += before.st_size
            rows.append((raw_path, b"F", None, before))
        elif stat.S_ISDIR(before.st_mode):
            rows.append((raw_path, b"G", b"", before))
        else:
            raise WorkspaceError("unsupported worktree path type")
        if total > MAX_CONTENT_TOTAL_BYTES:
            raise WorkspaceError("content baseline total byte bound exceeded: "
                                 + str(total) + "/" + str(MAX_CONTENT_TOTAL_BYTES))
    digest = hashlib.sha256()
    digest.update(b"ciel-work-content-baseline-v1\0")
    for value in (head, index):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    digest.update(len(rows).to_bytes(8, "big"))
    bytes_read = 0
    for raw_path, kind, payload, before in rows:
        _deadline(started)
        digest.update(len(raw_path).to_bytes(8, "big"))
        digest.update(raw_path)
        digest.update(kind)
        if kind == b"F":
            digest.update(before.st_size.to_bytes(8, "big"))
            path = target / os.fsdecode(raw_path)
            with path.open("rb") as stream:
                while True:
                    _deadline(started)
                    chunk = stream.read(_CONTENT_CHUNK)
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                    digest.update(chunk)
            after = os.lstat(path)
            keys = ("st_mode", "st_size", "st_mtime_ns", "st_ino")
            if any(getattr(before, key, None) != getattr(after, key, None) for key in keys):
                raise WorkspaceError("content baseline changed during capture")
        else:
            payload = payload or b""
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
            bytes_read += len(payload)
    return {"algorithm": "sha256", "digest": digest.hexdigest(),
            "index_digest": hashlib.sha256(index).hexdigest(), "head": head.decode("ascii"),
            "entries": len(rows), "bytes_read": bytes_read}


def _snapshot(record):
    """Return bounded Git metadata only. Never read or return file contents.

    Returns ``(snapshot, digest_body)``. The snapshot is what a person or main is
    shown. The digest body is the same snapshot with the names and count as
    builds up to beta 57dbfb3 parsed them. Stored digests are compared by
    whichever build decides a review or rebinds a workspace, on a device those
    builds share, so the digest of one worktree must not depend on the build.
    """
    target = Path(record["path"])
    raw = _git(target, "-c", "core.quotePath=false", "status", "--porcelain=v1",
               "-z", "--untracked-files=all", "--ignored", strip=False)
    names, total = _status_names(raw)
    stat = _git(target, "-c", "core.quotePath=false", "diff", "--stat", "HEAD", "--", ":(top)")
    snapshot = {"path": record["path"], "branch": record["branch"],
                "head": _git(target, "rev-parse", "HEAD"), "dirty": bool(raw),
                "changed_files": names, "changed_files_total": total,
                "changed_files_truncated": total > len(names),
                "diff_stat": stat[:_MAX_DIFF_STAT],
                "diff_stat_truncated": len(stat) > _MAX_DIFF_STAT}
    old_names, old_total = _status_names(raw.strip(), legacy=True)
    return snapshot, {**snapshot, "changed_files": old_names, "changed_files_total": old_total,
                      "changed_files_truncated": old_total > len(old_names)}


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
        snapshot, digest_body = _snapshot(record)
        return {**snapshot, "lease_id": lease_id, "generation": generation,
                "receipt_digest": _receipt_digest(digest_body, lease_id, generation)}


def review_observe(repo, lease_id, *, generation, artifact, completion_receipt,
                   host_id, idle_proof, config_dir=None):
    """Fresh bounded snapshot proving a completed worker is idle and unchanged."""
    if (not all(_text(value) for value in (generation, artifact, completion_receipt,
                                           host_id, idle_proof))
            or not _SHA256.fullmatch(artifact) or not _SHA256.fullmatch(completion_receipt)):
        raise WorkspaceError("invalid completion review identity")
    repo, key, manifest = _identity(repo, lease_id, config_dir)
    with exclusive_file_lock(manifest):
        record = _owned(_verify(_load(manifest, repo, lease_id, key)))
        if (record["host_id"] != host_id or record["idle"] is not True
                or record["idle_proof"] != idle_proof):
            raise WorkspaceError("positive matching idle workspace proof required")
        snapshot, digest_body = _snapshot(record)
        body = {"snapshot": digest_body, "lease_id": lease_id, "generation": generation,
                "artifact": artifact, "completion_receipt": completion_receipt}
        snapshot_digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                                    ensure_ascii=False).encode("utf-8")).hexdigest()
        return {"repo": str(repo), "path": snapshot["path"], "branch": snapshot["branch"],
                "head": snapshot["head"], "dirty": snapshot["dirty"],
                "changed_files": snapshot["changed_files"],
                "changed_files_total": snapshot["changed_files_total"], "diff_stat": snapshot["diff_stat"],
                "snapshot_digest": snapshot_digest,
                # handoff_rebind authenticates the exact current workspace using
                # its established lease/generation receipt digest.
                "receipt_digest": _receipt_digest(digest_body, lease_id, generation)}


def _baseline_identity(repo, old, prior_lease_id, replacement_lease_id,
                       prior_generation, replacement_generation, replacement_key):
    owner = old.get("ownership_lease_id", prior_lease_id)
    return {"schema": 1, "producer": CONTENT_BASELINE_PRODUCER, "repo": str(repo),
            "ownership_lease_id": owner, "ownership_key": _lease_key(repo, owner),
            "replacement_key": replacement_key, "prior_lease_id": prior_lease_id,
            "replacement_lease_id": replacement_lease_id,
            "prior_generation": prior_generation,
            "replacement_generation": replacement_generation}


_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def _valid_baseline_values(row):
    """Exact types and bounds; bool is never an int, NaN/inf never a time."""
    def bounded(value, maximum):
        return type(value) is int and 0 <= value <= maximum
    captured = row["captured_at"]
    return (row["algorithm"] == "sha256"
            and type(row["digest"]) is str and _SHA256.fullmatch(row["digest"]) is not None
            and type(row["index_digest"]) is str and _SHA256.fullmatch(row["index_digest"]) is not None
            and type(row["head"]) is str and _OBJECT_ID.fullmatch(row["head"]) is not None
            and bounded(row["entries"], MAX_CONTENT_ENTRIES)
            and bounded(row["bytes_read"], MAX_CONTENT_TOTAL_BYTES)
            and type(captured) in (int, float) and math.isfinite(captured) and captured > 0)


def _load_content_record(path, identity, *, require_digest):
    try:
        raw = path.read_bytes()
        if len(raw) > 8192:
            raise ValueError("oversized")
        row = json.loads(raw)
        required = set(identity) | ({"algorithm", "digest", "head", "index_digest",
                                    "entries", "bytes_read", "captured_at"} if require_digest else set())
        if type(row) is not dict or set(row) != required or any(row[key] != value
                for key, value in identity.items()):
            raise ValueError("identity")
        if require_digest and not _valid_baseline_values(row):
            raise ValueError("digest")
        return row
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise WorkspaceError("content baseline incomplete; retained") from exc


def _write_handoff_baseline(repo, old, prior_lease_id, replacement_lease_id,
                            prior_generation, replacement_generation, paths):
    identity = _baseline_identity(repo, old, prior_lease_id, replacement_lease_id,
                                  prior_generation, replacement_generation,
                                  paths["replacement_key"])
    if paths["skipped"].exists():
        return None
    if paths["intent"].exists():
        _load_content_record(paths["intent"], identity, require_digest=False)
        if paths["sidecar"].exists():
            return _load_content_record(paths["sidecar"], identity, require_digest=True)
    else:
        _save_content_record(paths["intent"], identity)
    try:
        try:
            fingerprint = _content_fingerprint(old)
        except WorkspaceError as exc:
            if not _capacity_error(exc):
                raise
            # Not capturable within the bounds: record that, drop the intent, and
            # let the handoff go on; drift is then reported as unknown.
            _save_content_record(paths["skipped"], {**identity, "reason": str(exc)[:200]})
            paths["intent"].unlink(missing_ok=True)
            return None
        row = {**identity, **fingerprint, "captured_at": time.time()}
        _save_content_record(paths["sidecar"], row)
        return row
    except WorkspaceError:
        raise
    except Exception as exc:
        raise WorkspaceError("content baseline persistence failed; retained") from exc


def handoff_drift(repo, replacement_lease_id, *, config_dir=None):
    """Compare the immutable retained-worktree handoff baseline, without mutation."""
    repo, key, manifest = _identity(repo, replacement_lease_id, config_dir)
    paths = content_baseline_paths(repo, replacement_lease_id, config_dir=config_dir)
    with exclusive_file_lock(manifest):
        record = _load(manifest, repo, replacement_lease_id, key)
        handoff = record.get("handoff")
        if handoff is None:
            # First generation has no retained-handoff attribution. Workspace
            # ownership is still verified by the later bind; Item 2 converges a
            # failure there into a visible stranded result.
            return {"changed_before_bind": "not_applicable"}
        try:
            record = _verify(record)
        except WorkspaceError as exc:
            # A valid intended baseline exists below; a broken retained worktree
            # is unknown drift, not a false clean result and not an incomplete
            # baseline. Bind may commit, then Item 2 records the stranded state.
            verification_error = str(exc)[:200]
        else:
            verification_error = None
        identity = _baseline_identity(repo, record, record["prior_lease_id"], replacement_lease_id,
                                      handoff["prior_generation"], handoff["replacement_generation"], key)
        if paths["skipped"].exists():
            return {"changed_before_bind": "unknown", "reason": "content baseline not captured: worktree exceeds its bounds"}
        if not paths["intent"].exists():
            return {"changed_before_bind": "unknown", "reason": "old_build_not_recorded"}
        _load_content_record(paths["intent"], identity, require_digest=False)
        baseline = _load_content_record(paths["sidecar"], identity, require_digest=True)
        if verification_error is not None:
            return {"changed_before_bind": "unknown", "reason": verification_error,
                    "baseline_digest": baseline["digest"]}
        try:
            current = _content_fingerprint(record)
        except WorkspaceError as exc:
            return {"changed_before_bind": "unknown", "reason": str(exc)[:200],
                    "baseline_digest": baseline["digest"]}
        return {"changed_before_bind": current["digest"] != baseline["digest"],
                "baseline_digest": baseline["digest"], "current_digest": current["digest"]}


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
        paths = content_baseline_paths(repo, replacement_lease_id, config_dir=config_dir)
        if old["status"] == "rebound":
            if prior_handoff.get("replacement_lease_id") == replacement_lease_id and new_manifest.exists():
                current = _verify(_load(new_manifest, repo, replacement_lease_id, new_key))
                if (current["path"] == old["path"]
                        and prior_handoff.get("receipt_digest") == receipt_digest
                        and prior_handoff.get("prior_generation") == prior_generation
                        and prior_handoff.get("replacement_generation") == replacement_generation):
                    # A new-build intent must always retain its valid sidecar.
                    identity = _baseline_identity(repo, current, prior_lease_id,
                                                  replacement_lease_id, prior_generation,
                                                  replacement_generation, new_key)
                    if paths["intent"].exists():
                        _load_content_record(paths["intent"], identity, require_digest=False)
                        _load_content_record(paths["sidecar"], identity, require_digest=True)
                    return current
            raise WorkspaceError("workspace already rebound; retained")
        old = _verify(old)
        snapshot, digest_body = _snapshot(old)
        expected = {"path": expected_path, "branch": expected_branch,
                    "head": expected_head, "dirty": expected_dirty}
        if (not hmac.compare_digest(_receipt_digest(digest_body, prior_lease_id,
                                                     prior_generation), receipt_digest)
                or any(snapshot[k] != v for k, v in expected.items())):
            raise WorkspaceError("stale or mismatched salvage receipt; retained")
        if new_manifest.exists():
            current = _verify(_load(new_manifest, repo, replacement_lease_id, new_key))
            handoff = current.get("handoff") or {}
            if (current["path"] == old["path"] and handoff.get("receipt_digest") == receipt_digest
                    and handoff.get("prior_generation") == prior_generation
                    and handoff.get("replacement_generation") == replacement_generation):
                # Complete the old tombstone after a crash between manifest saves.
                identity = _baseline_identity(repo, current, prior_lease_id,
                                              replacement_lease_id, prior_generation,
                                              replacement_generation, new_key)
                if not paths["skipped"].exists():
                    _load_content_record(paths["intent"], identity, require_digest=False)
                    _load_content_record(paths["sidecar"], identity, require_digest=True)
                old.update(status="rebound", handoff=current["handoff"])
                _save(old_manifest, old)
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
        # Persist the original handoff baseline before either manifest save.
        _write_handoff_baseline(repo, old, prior_lease_id, replacement_lease_id,
                                prior_generation, replacement_generation, paths)
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

    An idle observation also completes a workspace bind that never landed. The
    bind event commits its ledger half before this workspace half, and once the
    lease has a host nothing sends that bind again; a single failure there (a
    Git timeout, a worktree later repaired) would otherwise refuse every idle
    write. The caller passes the ledger's host, so the first host written here
    is the one that bind would have written. A different bound host is refused.
    """
    return _activity(repo, lease_id, host_id, idle, proof, config_dir, binding=idle)

def _activity(repo, lease_id, host_id, idle, proof, config_dir, binding=False):
    if not _text(host_id) or type(idle) is not bool or (idle and not _text(proof)):
        raise WorkspaceError("host identity and idle evidence required")
    repo, key, manifest = _identity(repo, lease_id, config_dir)
    with exclusive_file_lock(manifest):
        r = _owned(_verify(_load(manifest, repo, lease_id, key)))
        if r["host_id"] != host_id and not (binding and r["host_id"] is None):
            raise WorkspaceError("workspace host binding mismatch")
        if binding and not idle and r["host_id"] == host_id:
            # An identical bind retry. It writes nothing: writing "not idle" here
            # would undo an idle observation made since the first bind, and the
            # ledger would still hold that observation's proof.
            return dict(r)
        r.update(host_id=host_id, idle=idle, idle_proof=proof if idle else None)
        _save(manifest, r)
        return dict(r)

_MAX_CLEANUP_GENERATIONS = 128


def _content_cleanup_keys(repo, record, key, config_dir):
    """Exact replacement keys of one physical worktree, from trusted manifests only.

    Walks ``prior_lease_id`` back through the ownership manifests. The baseline
    files themselves are never parsed, so a corrupt one cannot hide its key, and
    a file of another ownership is never named. Prior manifests are rebound
    tombstones and are read without their locks: taking them while this lock is
    held could deadlock against a concurrent handoff replay.
    """
    owner = record.get("ownership_lease_id", record["lease_id"])
    keys = []
    while record.get("handoff") is not None and len(keys) < _MAX_CLEANUP_GENERATIONS:
        if record.get("ownership_lease_id", record["lease_id"]) != owner:
            break
        keys.append(key)
        prior = record.get("prior_lease_id")
        if not _text(prior):
            break
        try:
            _, key, path = _identity(repo, prior, config_dir)
            record = _load(path, repo, prior, key)
        except WorkspaceError:
            break
    return keys


def _cleanup_content_baselines(repo, record, key, manifest, config_dir):
    pending = False
    for replacement_key in _content_cleanup_keys(repo, record, key, config_dir):
        for suffix in (_CONTENT_INTENT_SUFFIX, _CONTENT_SUFFIX, _CONTENT_SKIPPED_SUFFIX):
            try:
                manifest.with_name(replacement_key + suffix).unlink(missing_ok=True)
            except OSError:
                pending = True
    _sync_parent_directory(manifest)
    return pending


def remove(repo, lease_id, *, host_id, active, idle_proof, expected_head,
           approved_target=None, remote="origin", remote_ref="", config_dir=None):
    """Exact ownership + idle lease + reviewed HEAD + pushed OR user approval.
    active/idle_proof come from bound lease; approved_target must be explicit user
    approval for this exact path. Dirty/ignored block. Branch/tombstone retained.
    """
    repo, key, manifest = _identity(repo, lease_id, config_dir)
    with exclusive_file_lock(manifest):
        loaded = _load(manifest, repo, lease_id, key)
        if loaded["status"] == "removed":
            pending = _cleanup_content_baselines(repo, loaded, key, manifest, config_dir)
            return {**loaded["removal"], "lease_id": lease_id, "repo": str(repo),
                    "host_id": host_id, "content_baseline_cleanup_pending": pending}
        r = _owned(_verify(loaded))
        if (active is not False or not r["idle"] or r["host_id"] != host_id
                or not _text(idle_proof) or idle_proof != r["idle_proof"]):
            raise WorkspaceError("verified matching host idle/lease activity required; retained")
        if r["head"] != expected_head:
            raise WorkspaceError("workspace HEAD changed; review cleanup again")
        try:
            result = cleanup_worktree(repo, Path(r["path"]), owned_root=repo / ".claude" / "worktrees",
                                      expected_head=expected_head, active=False,
                                      approved_target=approved_target, remote=remote, remote_ref=remote_ref,
                                      trust=trusted_roots(repo) + trusted_roots(Path(r["path"])))
        except ValueError as exc:
            raise WorkspaceError(str(exc)) from exc
        r.update(status="removed", removal=result)
        _save(manifest, r)
        pending = _cleanup_content_baselines(repo, r, key, manifest, config_dir)
        return {**result, "lease_id": lease_id, "repo": str(repo), "host_id": host_id,
                "content_baseline_cleanup_pending": pending}
