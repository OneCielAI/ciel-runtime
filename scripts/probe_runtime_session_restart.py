#!/usr/bin/env python3
"""Probe the CLI session restart path end to end with real child processes.

Run with test isolation so no live router instance is touched:

    CIEL_RUNTIME_TEST_ISOLATED=1 python scripts/probe_runtime_session_restart.py

The probe writes a restart request into a scratch instance directory, runs the
real CLI transport (the same dispatch service the launcher uses) around a child
process, and reports whether the transport terminated that child, claimed the
request, and relaunched it with the runtime's session-continue argument.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("CIEL_RUNTIME_TEST_ISOLATED", "1")

import ciel_runtime  # noqa: E402
from ciel_runtime_support.runtime_session_restart import (  # noqa: E402
    RuntimeSessionRestartService,
    RuntimeSessionRestartServicePorts,
    runtime_resume_command,
)

CHILD_SOURCE = (
    "import sys,time\n"
    "print('CHILD_UP ' + ' '.join(sys.argv[1:]), flush=True)\n"
    "time.sleep(120)\n"
)


def write_fake_cli(root: Path) -> Path:
    """A stand-in CLI whose argv mirrors a real launch (no bare -c flag)."""

    script = root / "fake_cli.py"
    script.write_text(CHILD_SOURCE, encoding="utf-8")
    return script


def build_service(instance_dir: Path, logs: list[str]) -> RuntimeSessionRestartService:
    return RuntimeSessionRestartService(
        RuntimeSessionRestartServicePorts(
            instance_dir=instance_dir,
            instances_root=instance_dir.parent,
            is_running=ciel_runtime.pid_is_running,
            log=lambda level, message: logs.append(f"{level} {message}"),
            workspace_digest=lambda path: "probe",
        )
    )


def run_transport(cmd: list[str], control, *, transport: str) -> tuple[int, float]:
    started = time.time()
    if transport == "dispatch":
        returncode = ciel_runtime.subprocess_call_with_channel_wake_proxy(
            cmd,
            dict(os.environ),
            inject_channel_messages=False,
            restart_poll=control.incoming,
            restart_state=control,
        )
    else:
        returncode = ciel_runtime.subprocess_call_with_child_pid_record(
            cmd, dict(os.environ), None,
            restart_poll=control.incoming,
            restart_state=control,
        )
    return returncode, time.time() - started


def probe(transport: str, reason: str) -> dict:
    logs: list[str] = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        instance = root / "9611-probe"
        clients = instance / "router-clients"
        clients.mkdir(parents=True)
        # This process plays the launcher that owns the CLI child, so the
        # router-side queue resolves it exactly like a live session.
        (clients / f"{os.getpid()}.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "router_port": 9611,
                    "workspace": str(root),
                }
            ),
            encoding="utf-8",
        )
        service = build_service(instance, logs)
        control = service.control()
        cmd = [sys.executable, str(write_fake_cli(root)), "--model", "probe"]
        result = service.queue(source="probe", reason=reason)
        request_file = service.request_path()

        returncode, elapsed = run_transport(cmd, control, transport=transport)
        claimed = control.request
        resumed = runtime_resume_command(cmd, "claude")
        # Report the relaunched argv without starting a second long-lived child.
        echo_argv = [
            sys.executable,
            "-c",
            "import sys; print('RELAUNCH_ARGS ' + ' '.join(sys.argv[1:]), flush=True)",
            *resumed,
        ]
        relaunch = run_transport(echo_argv, service.control(), transport=transport)
        result_payload = {
            "transport": transport,
            "queued": result.get("queued"),
            "request_file_exists": request_file.exists(),
            "claimed": claimed is not None,
            "claimed_source": getattr(claimed, "source", ""),
            "first_returncode": returncode,
            "first_elapsed_seconds": round(elapsed, 2),
            "resume_argument_added": resumed != cmd and "--continue" in resumed,
            "resumed_command_tail": resumed[-3:],
            "relaunch_returncode": relaunch[0],
            "logs": logs[-6:],
        }
        print(json.dumps(result_payload, indent=2, ensure_ascii=False), flush=True)
        return result_payload


def live_router_tools(port: int) -> list[str]:
    """Read-only: the tools a live router advertises at /ca/mcp."""

    import urllib.request

    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/ca/mcp",
        data=payload,
        headers={"Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    return [tool.get("name") for tool in body.get("result", {}).get("tools", [])]


def probe_router_mcp(port: int) -> dict:
    """Start an isolated router and drive restart_session over its MCP endpoint."""

    import subprocess
    import urllib.error
    import urllib.request

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        config_dir = root / "config"
        # Under CIEL_RUNTIME_TEST_ISOLATED the router instance directory *is*
        # the config directory, so its client registry lives one level down.
        clients = config_dir / "router-clients"
        clients.mkdir(parents=True)
        (clients / f"{os.getpid()}.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "router_port": port,
                    "workspace": str(root),
                }
            ),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "CIEL_RUNTIME_TEST_ISOLATED": "1",
            "CIEL_RUNTIME_CONFIG_DIR": str(config_dir),
            "CIEL_RUNTIME_ROUTER_PORT": str(port),
        }
        log_path = root / "router.log"
        with log_path.open("wb") as log_stream:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "ciel_runtime.py"), "serve"],
                env=env,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT),
            )
        try:
            health = f"http://127.0.0.1:{port}/health"
            deadline = time.time() + 60
            ready = False
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(health, timeout=3):
                        ready = True
                        break
                except Exception:
                    time.sleep(0.5)
            if not ready:
                return {
                    "ok": False,
                    "detail": "isolated router did not become healthy",
                    "log_tail": log_path.read_text(encoding="utf-8", errors="replace")[-800:],
                }
            tools = live_router_tools(port)
            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "restart_session",
                        "arguments": {"reason": "probe-mcp", "runtime": "claude"},
                    },
                }
            ).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/ca/mcp",
                data=payload,
                headers={"Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
            request_file = config_dir / "runtime-session-restart.json"
            queued = json.loads(request_file.read_text(encoding="utf-8")) if request_file.exists() else {}
            return {
                "ok": bool(queued),
                "tools": tools,
                "mcp_result": body.get("result", {}).get("content", [{}])[0].get("text", ""),
                "is_error": bool(body.get("result", {}).get("isError")),
                "queued_request": queued,
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> int:
    failures: list[str] = []
    for transport in ("dispatch", "direct"):
        result = probe(transport, reason=f"probe-{transport}")
        if not result["queued"]:
            failures.append(f"{transport}: request was not queued")
        if not result["claimed"]:
            failures.append(f"{transport}: request was not claimed")
        if result["request_file_exists"]:
            failures.append(f"{transport}: request file was not consumed")
        if result["first_elapsed_seconds"] > 60:
            failures.append(f"{transport}: child was not terminated by the restart")
        if not result["resume_argument_added"]:
            failures.append(f"{transport}: --continue was not added")
    mcp = probe_router_mcp(9599)
    print("mcp:", json.dumps(mcp, indent=2, ensure_ascii=False), flush=True)
    if not mcp.get("ok"):
        failures.append("mcp: restart_session did not queue a request")
    if "restart_session" not in (mcp.get("tools") or []):
        failures.append("mcp: restart_session is not advertised")
    for port in (9611, 9465):
        try:
            tools = live_router_tools(port)
        except Exception as exc:
            print(f"live router {port}: unreachable ({type(exc).__name__})", flush=True)
            continue
        print(f"live router {port} tools: {tools}", flush=True)
    print("RESULT:", "FAIL " + "; ".join(failures) if failures else "OK", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
