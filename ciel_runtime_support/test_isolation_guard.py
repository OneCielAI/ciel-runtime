"""Fail-closed guards for destructive cleanup under an un-isolated test runner.

The supported test entrypoint sets ``CIEL_RUNTIME_TEST_ISOLATED`` before the
runtime is imported.  A bare ``python -m unittest discover -s tests`` instead
binds the developer's real profile, and the router-startup path then
terminates the pid file's router and the live client that owns the running
session -- observed 2026-09-18 as the CLI exiting mid-sweep.  Cleanup
therefore fails closed whenever the process looks like such a runner.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


_TRUTHY = {"1", "true", "yes", "on"}


def test_state_isolated(environ: Mapping[str, str] | None = None) -> bool:
    """Report whether the supported entrypoint marked this process isolated."""

    source = os.environ if environ is None else environ
    return str(source.get("CIEL_RUNTIME_TEST_ISOLATED") or "").strip().lower() in _TRUTHY


def loose_test_entrypoint(
    *,
    environ: Mapping[str, str] | None = None,
    modules: Mapping[str, Any] | None = None,
    argv: Sequence[str] | None = None,
) -> bool:
    """Report whether this process itself is a unittest/pytest test run.

    Unlike ``unisolated_test_process`` this deliberately ignores a merely loaded
    ``unittest`` module: a production router must never be mistaken for a test
    and moved to a temporary profile. Only the process entrypoint counts.
    """

    source = os.environ if environ is None else environ
    if source.get("PYTEST_CURRENT_TEST"):
        return True
    loaded = sys.modules if modules is None else modules
    if "pytest" in loaded or "_pytest" in loaded:
        return True
    main = loaded.get("__main__")
    spec = getattr(main, "__spec__", None)
    if getattr(spec, "name", "") in {"unittest.__main__", "pytest.__main__"}:
        return True
    if Path(str(getattr(main, "__file__", "") or "")).name.startswith("test_"):
        return True
    arguments = sys.argv if argv is None else argv
    return test_runner_arguments([str(argument) for argument in arguments[1:]])


def path_within(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> bool:
    """Case-normalized containment check for an existing or future path."""

    child = os.path.normcase(os.path.abspath(os.fspath(path)))
    parent = os.path.normcase(os.path.abspath(os.fspath(root)))
    try:
        return os.path.commonpath([child, parent]) == parent and child != parent
    except ValueError:
        return False


def test_runner_arguments(arguments: Sequence[str]) -> bool:
    """Report whether command arguments are a loose test-runner invocation.

    ``python -m unittest discover -s tests`` passes no ``test_*`` argument, so
    the discovery shape has to be recognized too.  Keep this narrow: an
    ordinary launch may legitimately carry a directory or a prompt that
    mentions tests.
    """

    if any(Path(argument).name.startswith("test_") for argument in arguments):
        return True
    if arguments and arguments[0] in {"discover", "unittest"}:
        return True
    return any(
        argument.replace("\\", "/").startswith(("tests/", "tests."))
        for argument in arguments
    )


def unisolated_test_process(
    *,
    environ: Mapping[str, str] | None = None,
    modules: Mapping[str, Any] | None = None,
    argv: Sequence[str] | None = None,
) -> bool:
    """Return True when a test runner could touch the user's live state."""

    source = os.environ if environ is None else environ
    if test_state_isolated(source):
        return False
    if source.get("PYTEST_CURRENT_TEST"):
        return True
    loaded = sys.modules if modules is None else modules
    if "unittest" in loaded or "pytest" in loaded:
        return True
    arguments = sys.argv if argv is None else argv
    return test_runner_arguments([str(argument) for argument in arguments[1:]])


def terminate_tree(
    pid: int,
    label: str,
    *,
    terminate: Callable[..., bool],
    unisolated: Callable[[], bool],
    log: Callable[[str, str], Any],
    quiet: bool = False,
) -> bool:
    """Terminate one pid, refusing foreign pids while a test runner is loose."""

    if unisolated() and pid not in {os.getpid(), os.getppid()}:
        log(
            "WARN",
            f"process_tree_terminate_skipped_unisolated_test label={label!r} pid={pid}",
        )
        return False
    return terminate(pid, label, quiet=quiet)


def terminate_clients(
    reason: str,
    active_clients: list[int] | None,
    *,
    terminate: Callable[..., bool],
    unisolated: Callable[[], bool],
    log: Callable[[str, str], Any],
    quiet: bool = True,
) -> bool:
    """Terminate the registered clients, refusing to while a test runner is loose."""

    if unisolated():
        log("WARN", f"router_client_termination_skipped_unisolated_test reason={reason}")
        return False
    return terminate(reason, active_clients, quiet=quiet)


__all__ = [
    "loose_test_entrypoint",
    "path_within",
    "terminate_clients",
    "terminate_tree",
    "test_runner_arguments",
    "test_state_isolated",
    "unisolated_test_process",
]
