"""Codex child-process records, discovery, and shutdown lifecycle."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CodexProcessPorts:
    pid_running: Callable[[int], bool]
    command_line: Callable[[int], str]
    managed_process: Callable[[int, str | None], bool]
    terminate_tree: Callable[[int, str, bool], bool]
    process_rows: Callable[[], list[tuple[int, str, str]]]
    process_cwd: Callable[[int], Path | None]
    parent_info: Callable[[int], tuple[int, str] | None]
    log: Callable[[str, str], None]
    current_pid: Callable[[], int]
    parent_pid: Callable[[], int]


class CodexProcessRepository:
    def __init__(self, process_dir: Path, log: Callable[[str, str], None]) -> None:
        self.process_dir = process_dir
        self.log = log

    def write(
        self,
        path: Path | None,
        pid: int,
        command: list[str],
        cwd: Path | None = None,
    ) -> None:
        if path is None or pid <= 0:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "pid": int(pid),
                "owner_pid": os.getpid(),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cwd": str(cwd or Path.cwd()),
                "cmd": [str(item) for item in command],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except Exception as exc:
                self.log(
                    "WARN",
                    "codex_child_process_record_chmod_failed "
                    f"path={path} error={type(exc).__name__}: {exc}",
                )
            self.log("INFO", f"codex_child_process_registered pid={pid} path={path}")
        except Exception as exc:
            self.log(
                "WARN",
                f"codex_child_process_register_failed pid={pid} "
                f"error={type(exc).__name__}: {exc}",
            )

    def release(self, path: Path | None, pid: int | None = None) -> None:
        if path is None:
            return
        try:
            if pid is not None and path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if int(data.get("pid") or 0) != int(pid):
                    return
            path.unlink()
            self.log("INFO", f"codex_child_process_released path={path}")
        except FileNotFoundError:
            return
        except Exception as exc:
            self.log(
                "WARN",
                f"codex_child_process_release_failed path={path} "
                f"error={type(exc).__name__}: {exc}",
            )

    def records(self) -> list[tuple[Path, int]]:
        if not self.process_dir.exists():
            return []
        records: list[tuple[Path, int]] = []
        for path in sorted(self.process_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                records.append((path, int(data.get("pid") or 0)))
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                AttributeError,
                TypeError,
                ValueError,
            ) as exc:
                self.log(
                    "WARN",
                    f"codex_tracked_process_record_invalid path={path} "
                    f"error={type(exc).__name__}: {exc}",
                )
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as unlink_exc:
                    self.log(
                        "WARN",
                        f"codex_tracked_process_record_cleanup_failed path={path} "
                        f"error={type(unlink_exc).__name__}: {unlink_exc}",
                    )
        return records


def managed_process(
    pid: int,
    command: str | None,
    *,
    managed_environment: Callable[[int, str, str | None], bool],
    command_line: Callable[[int], str],
) -> bool:
    if managed_environment(pid, "CIEL_RUNTIME_CODEX_MANAGED", "1"):
        return True
    text = (command if command is not None else command_line(pid)).lower()
    return "codex" in text and ("--yolo" in text or "app-server" in text)


def terminate_recorded_child(
    process: Any,
    label: str,
    *,
    terminate_tree: Callable[[int, str, bool], bool],
    log: Callable[[str, str], None],
) -> None:
    try:
        pid = int(getattr(process, "pid", 0) or 0)
    except (TypeError, ValueError) as exc:
        log("WARN", f"codex_child_process_pid_invalid error={type(exc).__name__}: {exc}")
        return
    if pid <= 0:
        return
    try:
        if process.poll() is not None:
            return
    except Exception as exc:
        log(
            "WARN",
            f"codex_child_process_poll_failed pid={pid} error={type(exc).__name__}: {exc}",
        )
    terminate_tree(pid, label, True)
    try:
        process.wait(timeout=2)
    except Exception as wait_exc:
        log(
            "WARN",
            f"codex_child_process_wait_failed pid={pid} "
            f"error={type(wait_exc).__name__}: {wait_exc}",
        )
        try:
            process.kill()
        except Exception as kill_exc:
            log(
                "WARN",
                f"codex_child_process_kill_failed pid={pid} "
                f"error={type(kill_exc).__name__}: {kill_exc}",
            )
        try:
            process.wait(timeout=2)
        except Exception as final_wait_exc:
            log(
                "WARN",
                f"codex_child_process_final_wait_failed pid={pid} "
                f"error={type(final_wait_exc).__name__}: {final_wait_exc}",
            )


class CodexProcessLifecycle:
    def __init__(self, repository: CodexProcessRepository, ports: CodexProcessPorts) -> None:
        self.repository = repository
        self.ports = ports

    def terminate_tracked(self, reason: str, quiet: bool = True) -> bool:
        stopped = False
        for path, pid in self.repository.records():
            if pid <= 0 or not self.ports.pid_running(pid):
                self.repository.release(path, pid)
                continue
            command = self.ports.command_line(pid)
            if not self.ports.managed_process(pid, command):
                self.ports.log(
                    "WARN",
                    f"codex_tracked_process_skipped_unexpected_command pid={pid} path={path}",
                )
                self.repository.release(path, pid)
                continue
            stopped = self.ports.terminate_tree(pid, "previous Codex", quiet) or stopped
            self.repository.release(path, pid)
        self.ports.log(
            "INFO",
            f"codex_tracked_processes_terminated reason={reason} "
            f"stopped={str(stopped).lower()}",
        )
        return stopped

    def ancestor_pids(self, platform_name: str, limit: int = 12) -> set[int]:
        ancestors = {self.ports.current_pid(), self.ports.parent_pid()}
        if platform_name == "nt":
            return ancestors
        current = self.ports.current_pid()
        for _ in range(max(0, limit)):
            info = self.ports.parent_info(current)
            if info is None:
                break
            parent, _command = info
            if parent <= 0 or parent in ancestors:
                break
            ancestors.add(parent)
            current = parent
        return ancestors

    def untracked_pids(
        self,
        cwd: Path | None,
        *,
        platform_name: str,
        enabled: bool,
    ) -> list[int]:
        if platform_name == "nt" or not enabled:
            return []
        target_cwd = (cwd or Path.cwd()).resolve()
        protected = self.ancestor_pids(platform_name)
        matches = [
            pid
            for pid, stat, command in self.ports.process_rows()
            if pid not in protected
            and not stat.startswith("Z")
            and self.ports.managed_process(pid, command)
            and self.ports.process_cwd(pid) == target_cwd
        ]
        return sorted(set(matches))

    def terminate_untracked(
        self,
        reason: str,
        cwd: Path | None,
        quiet: bool,
        *,
        platform_name: str,
        enabled: bool,
    ) -> bool:
        pids = self.untracked_pids(cwd, platform_name=platform_name, enabled=enabled)
        if not pids:
            return False
        stopped = False
        for pid in pids:
            stopped = (
                self.ports.terminate_tree(pid, "previous untracked Codex", quiet)
                or stopped
            )
        self.ports.log(
            "WARN",
            f"codex_untracked_processes_terminated reason={reason} "
            f"pids={','.join(map(str, pids))} stopped={str(stopped).lower()}",
        )
        return stopped
