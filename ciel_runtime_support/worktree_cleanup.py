"""Conservative owned-worktree cleanup with remote push or exact user approval."""
from __future__ import annotations
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=30, check=False)
    if done.returncode:
        raise ValueError("Git cleanup verification failed: " + done.stderr.strip()[:400])
    return done.stdout.strip()


def cleanup_worktree(repo: Path, target: Path, *, owned_root: Path,
                     expected_head: str, active: bool, approved_target: str | None = None,
                     remote: str = "origin", remote_ref: str = "") -> dict:
    """Approval binds to exact resolved path; never force-remove dirty worktrees.

    Call only with the coordinator's ownership registry and host lifecycle facts.
    No branch is deleted. Network errors retain the target unless explicitly approved.
    """
    raw_target = Path(target).absolute()
    for part in (raw_target, *raw_target.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("symlink/junction cleanup target retained")
    repo, target, owned_root = Path(repo).resolve(), raw_target.resolve(), Path(owned_root).resolve()
    if active or target == repo or target == owned_root or not target.is_relative_to(owned_root):
        raise ValueError("worktree is active or outside its owned cleanup root")
    # Resolve each existing parent; reject junction/symlink escapes and targets not registered.
    listed = _git(repo, "worktree", "list", "--porcelain")
    paths = [Path(line[9:]).resolve() for line in listed.splitlines() if line.startswith("worktree ")]
    if target not in paths:
        raise ValueError("target is not a registered owned worktree")
    head = _git(target, "rev-parse", "HEAD")
    if head != expected_head:
        raise ValueError("worktree HEAD changed; review cleanup again")
    if _git(target, "status", "--porcelain", "--untracked-files=all", "--ignored"):
        raise ValueError("dirty worktree retained, including untracked or ignored files")
    approved = approved_target is not None and Path(approved_target).resolve() == target
    pushed = False
    remote_sha = ""
    if remote_ref and not remote_ref.startswith("-") and not remote.startswith("-"):
        try:
            rows = _git(repo, "ls-remote", "--exit-code", remote, remote_ref).splitlines()
            remote_sha = next((line.split()[0] for line in rows if len(line.split()) == 2
                               and line.split()[1] == remote_ref), "")
            pushed = remote_sha == head
            # Ancestor proof is safe only when the advertised object exists locally.
            if remote_sha and not pushed:
                check = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", head,
                                        remote_sha], capture_output=True, timeout=30, check=False)
                pushed = check.returncode == 0
        except (ValueError, subprocess.TimeoutExpired):
            if not approved:
                raise ValueError("worktree deletion needs verified remote push or explicit user approval for this path; remote push could not be verified")
    if not pushed and not approved:
        raise ValueError("worktree deletion needs verified remote push or explicit user approval for this path")
    # Recheck after network access before mutation; no --force and no git clean.
    if _git(target, "rev-parse", "HEAD") != head or _git(target, "status", "--porcelain", "--untracked-files=all", "--ignored"):
        raise ValueError("worktree changed during cleanup verification")
    _git(repo, "worktree", "remove", str(target))
    remaining = _git(repo, "worktree", "list", "--porcelain")
    if target.exists() or any(Path(line[9:]).resolve() == target for line in remaining.splitlines()
                              if line.startswith("worktree ")):
        raise ValueError("cleanup pending: path or worktree registration still exists")
    return {"status": "removed", "path": str(target), "head": head,
            "pushed": pushed, "remote_ref": remote_ref, "remote_sha": remote_sha,
            "explicit_user_approval": approved, "path_absent": True, "registration_absent": True}
