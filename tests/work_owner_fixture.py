"""Test fixture: the routed main session whose router is bound to one conversation.

A rework or stranded recovery is reserved only for a proven live owner
(work_parallel_mcp._proven_owner): in a real session the router's
compaction-scope.json names the conversation and its launcher process. Backend
suites that exercise rework model that session: the scope names their
conversation and the launcher is this test process, which is alive.
"""
import json
import os
from pathlib import Path
from unittest.mock import patch


def bind_router(case, conversation, root=None):
    """Patch ROUTER_INSTANCE_DIR to a temp dir whose scope names ``conversation``; returns the dir."""
    instance = Path(root or case.tmp) / "router-instance"
    instance.mkdir(parents=True, exist_ok=True)
    patcher = patch("ciel_runtime_support.runtime_paths.ROUTER_INSTANCE_DIR", instance)
    patcher.start()
    case.addCleanup(patcher.stop)
    runtime, session = conversation.split(":", 1)
    (instance / "compaction-scope.json").write_text(json.dumps({
        "runtime": runtime, "session_id": session, "launcher_pid": os.getpid(), "epoch": "fixture"}),
        encoding="utf-8")
    return instance


def attach_hosts(store, conversation, *nonces):
    """R2 B2: attach each named Work host process to this test launcher (os.getpid()).

    Every nonce shares this launcher, so each is an earlier claude.exe of the
    others: the one-child-at-a-time restart model. Returns the nonces.
    """
    from ciel_runtime_support.work_parallel_attachments import WorkParallelAttachments
    side = WorkParallelAttachments.beside(store.path)
    for nonce in nonces:
        side.attach(conversation, os.getpid(), nonce)
    return nonces
