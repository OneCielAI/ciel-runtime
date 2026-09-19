#!/usr/bin/env python3
"""Drive a real `muse serve` host with the Ciel Runtime injection layer.

The probe opens an MSP session host (Muse Code lives in WSL on Windows), then
delivers one message per injection intent through :class:`MuseInjectionService`
and reports the receipts and the notifications the host emitted. It needs no
credentials: the session runs the `echo` provider, so nothing reaches a model.

    python scripts/probe_muse_msp_injection.py
    python scripts/probe_muse_msp_injection.py --host "muse"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ciel_runtime_support.muse_injection import (  # noqa: E402
    MuseInjectionOptions,
    MuseInjectionPorts,
    MuseInjectionService,
    serve_host_argv,
)
from ciel_runtime_support.muse_msp import (  # noqa: E402
    MuseCommandIds,
    MuseMspConnection,
    muse_command_id,
)


def default_host() -> list[str]:
    """Muse Code lives in WSL on Windows; `wsl -e muse` needs an absolute path."""

    if os.name != "nt":
        return ["muse"]
    resolved = subprocess.run(
        ["wsl", "-e", "sh", "-lc", "command -v muse"],
        capture_output=True,
        text=True,
        check=False,
    )
    path = str(resolved.stdout or "").strip().splitlines()
    if resolved.returncode == 0 and path and path[-1].startswith("/"):
        return ["wsl", "-e", path[-1]]
    return ["wsl", "-e", "muse"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="", help="Muse executable (may be 'wsl -e muse')")
    parser.add_argument("--workspace", default="", help="Workspace root for the session")
    args = parser.parse_args()
    host = args.host.split() if args.host else default_host()

    logs: list[str] = []
    log = lambda level, message: logs.append(f"{level} {message}")  # noqa: E731

    if args.workspace:
        workspace = args.workspace
    elif host and host[0] == "wsl":
        # The host validates the workspace as an absolute path it can see.
        workspace = "/tmp/muse-inject-probe"
        subprocess.run(["wsl", "-e", "mkdir", "-p", workspace], check=False)
    else:
        workspace = tempfile.mkdtemp(prefix="muse-inject-probe-")
    options = MuseInjectionOptions(
        transport="msp",
        sandbox=("disable_shell", "disable_write"),
        workspace=workspace,
    )
    argv = serve_host_argv(host, options)
    report: dict[str, object] = {"host_argv": argv, "workspace": workspace}

    connection = MuseMspConnection.start(
        argv, log=log, request_timeout_seconds=30.0
    )
    try:
        handshake = connection.initialize(client_title="Ciel Runtime injection probe")
        report["initialize"] = {
            "server": handshake.get("serverInfo"),
            "schema": handshake.get("schema"),
            "durability": handshake.get("sessionDurability"),
        }
        # No approvalMode here: the host refuses a mode that exceeds the one
        # its startup posture sealed (live: -32030 for allowAll).
        session = connection.session_start(
            command_id=muse_command_id(),
            workspace_root=workspace,
            provider_id="echo",
        )
        session_id = str(session["session"]["sessionId"])
        report["session_id"] = session_id
        report["session_provider"] = session["session"].get("providerId")

        service = MuseInjectionService(
            MuseInjectionPorts(
                log=log,
                command_ids=MuseCommandIds(),
                connection=lambda _options: connection,
            )
        )
        receipts = {}
        for intent in ("queue", "steer", "replace"):
            receipts[intent] = service.deliver(
                f"probe message ({intent})",
                MuseInjectionOptions(
                    transport="msp",
                    intent=intent,
                    display_text=f"probe preview ({intent})",
                ),
                session_id=session_id,
                command_key=f"probe:{intent}",
            )
        report["receipts"] = {
            intent: {
                key: value
                for key, value in receipt.items()
                if key in {"method", "status", "disposition", "started_new_turn", "turn_id"}
            }
            for intent, receipt in receipts.items()
        }
        redelivered = service.deliver(
            "probe message (steer)",
            MuseInjectionOptions(transport="msp", intent="steer"),
            session_id=session_id,
            command_key="probe:steer",
        )
        report["redelivery"] = {
            "command_id_rotated": redelivered["command_id"] != receipts["steer"]["command_id"],
            "command_reused": redelivered["command_reused"],
            "disposition": redelivered["disposition"],
        }

        notifications = [
            {"method": item.method, "session": item.params.get("session", {}).get("sessionId")}
            if item.method == "session/started"
            else {"method": item.method, "turnId": item.params.get("turnId")}
            for item in connection.drain_notifications()
        ]
        report["notifications"] = notifications
        report["notification_methods"] = sorted(
            {str(entry["method"]) for entry in notifications}
        )
    finally:
        report["host_returncode"] = connection.close()
        report["host_log_tail"] = logs[-5:]

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
