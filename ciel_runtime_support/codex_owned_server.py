"""Launch-scoped containment for the remote app-server (never port-based killing)."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any, Callable


class OwnedServerError(RuntimeError):
    pass


class OwnedServer:
    def __init__(self, process: Any, terminate_tree: Callable[[int], Any], *, grouped: bool) -> None:
        self.process = process
        self.terminate_tree = terminate_tree
        self.grouped = grouped

    @classmethod
    def launch(cls, cmd: list[str], *, popen: Callable[..., Any], terminate_tree: Callable[[int], Any], **kwargs: Any) -> OwnedServer:
        grouped = os.name == "posix"
        if grouped:
            # The sentinel survives a dead Node shim, pinning this session's
            # group ID until cleanup. No user configuration is imported.
            cmd = [sys.executable, "-I", str(Path(__file__).resolve()), *cmd]
            kwargs.update(start_new_session=True, stdin=subprocess.PIPE)
        return cls(popen(cmd, **kwargs), terminate_tree, grouped=grouped)

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    def poll(self) -> int | None:
        return self.process.poll()

    def cleanup(self) -> None:
        errors: list[str] = []
        try:
            if self.process.poll() is not None:
                # Windows taskkill /T cannot recover descendants of a dead
                # shim. Nor is an already-reaped POSIX group a safe identity.
                raise OwnedServerError("launch owner exited; descendant ownership unresolved")
            if self.grouped:
                if os.getpgid(self.pid) != self.pid:
                    raise OwnedServerError("launch process group identity changed")
                os.killpg(self.pid, signal.SIGKILL)
            elif self.terminate_tree(self.pid) is False:
                raise OwnedServerError("owned process tree termination failed")
        except Exception as exc:
            errors.append(str(exc))
        try:
            self.process.wait(timeout=10)
        except Exception as exc:
            errors.append(f"launch owner reap failed: {exc}")
        finally:
            if self.grouped and self.process.stdin is not None:
                try:
                    self.process.stdin.close()
                except OSError as exc:
                    errors.append(f"sentinel pipe close failed: {exc}")
        if errors:
            raise OwnedServerError("; ".join(errors))


def authenticate_server(listen_url: str, token: str, version: str) -> None:
    from ciel_runtime_support.codex_app_server import CodexAppServerClient
    from ciel_runtime_support.codex_app_server_websocket import CodexAppServerWebSocketProcess

    client = CodexAppServerClient(CodexAppServerWebSocketProcess.connect(listen_url, bearer_token=token))
    try:
        client.initialize(client_name="ciel-runtime-readiness", client_title=None, client_version=version)
    finally:
        client.close()


def _sentinel() -> int:
    # stdin EOF also tears down the group if the launcher itself disappears.
    # The sentinel never exits merely because its shim exited.
    subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)
    sys.stdin.buffer.read()
    os.killpg(os.getpgrp(), signal.SIGKILL)
    return 1


if __name__ == "__main__":
    raise SystemExit(_sentinel())
