"""Measure the installed Codex CLI's own retry contract against an owned loopback fixture.

The installed CLI runs with a private HOME/USERPROFILE/CODEX_HOME/cwd,
``--ignore-user-config``, ``--dangerously-bypass-approvals-and-sandbox`` and a
provider block supplied as ``-c`` overrides (the same shape the runtime emits)
whose only base URL is ``http://127.0.0.1:<port>/v1``.  Every inherited
``CIEL_RUNTIME_*``, ``CODEX_*``, ``OPENAI_*``, ``ANTHROPIC_*``, ``CLAUDE*``,
proxy and credential-shaped environment variable is removed first; the only
credential the CLI can see is ``CODEX_FIXTURE_API_KEY=fixture-key``.

The provider data path contacts only the loopback fixture.  The CLI's plugin
and MCP startup is independent of the provider block: observed attempts are
captured in ``cli.stderr_tail`` and never contribute to fixture hit counts.

The fixture implements ``POST /v1/responses`` and returns either the exact
CielAiRouter screenshot rejection body (HTTP 503 or HTTP 200 + ``event:
error``), a native ``response.failed`` event, a partial output followed by the
screenshot error, or a valid ``response.created`` / ``response.output_item.done``
/ ``response.completed`` stream.  The fixture counts real HTTP hits, which is
the only client retry signal observed.  Nothing outside the loopback socket is
contacted; only fixture counters, request metadata digests, CLI output and the
last-message file are stored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Exact body captured from the CielAiRouter pre-dispatch rejection in the
# investigation screenshot.
SCREENSHOT_EVENT = (
    b'event: error\n'
    b'data: {"type":"error","error":{"type":"overloaded_error","message":'
    b'"service_unavailable: Service temporarily unavailable: '
    b'all targets were skipped by pre-dispatch filters"}}\n\n'
)

RESPONSE_ID = "resp_fixture_1"
MESSAGE_ID = "msg_fixture_1"


def _sse(events: list[dict]) -> bytes:
    chunks = []
    for event in events:
        chunks.append(
            f"event: {event['type']}\ndata: "
            f"{json.dumps(event, separators=(',', ':'))}\n\n"
        )
    return "".join(chunks).encode()


def _created(response_id: str = RESPONSE_ID) -> dict:
    return {"type": "response.created", "response": {"id": response_id}}


def _message_item(text: str) -> dict:
    return {
        "type": "response.output_item.done",
        "item": {
            "type": "message",
            "role": "assistant",
            "id": MESSAGE_ID,
            "content": [{"type": "output_text", "text": text}],
        },
    }


def _completed(response_id: str = RESPONSE_ID) -> dict:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "usage": {
                "input_tokens": 1,
                "input_tokens_details": None,
                "output_tokens": 1,
                "output_tokens_details": None,
                "total_tokens": 2,
            },
        },
    }


def success_stream(text: str = "ok") -> bytes:
    return _sse([_created(), _message_item(text), _completed()])


def partial_then_screenshot_error(text: str = "partial answer") -> bytes:
    return _sse([_created("resp_fixture_2"), _message_item(text)]) + SCREENSHOT_EVENT


def native_failed_stream() -> bytes:
    return _sse([{
        "type": "response.failed",
        "response": {
            "id": "resp_fixture_failed",
            "error": {"code": "server_error", "message": "fixture stream failure"},
        },
    }])


# verdict -> (status, body, content_type)
VERDICTS = {
    "fail503": (503, SCREENSHOT_EVENT, "text/event-stream; charset=utf-8"),
    "success": (200, success_stream(), "text/event-stream; charset=utf-8"),
    "screenshot_error": (200, SCREENSHOT_EVENT, "text/event-stream; charset=utf-8"),
    "partial_then_error": (200, partial_then_screenshot_error(),
                           "text/event-stream; charset=utf-8"),
    "native_failed": (200, native_failed_stream(), "text/event-stream; charset=utf-8"),
}


class Fixture:
    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.hits: list[dict] = []
        self.lock = threading.Lock()

    def next_verdict(self) -> tuple[int, str]:
        with self.lock:
            index = len(self.hits)
            verdict = (
                self.script[index] if index < len(self.script) else self.script[-1]
            )
            return index, verdict

    def record(self, entry: dict) -> None:
        with self.lock:
            self.hits.append(entry)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return json.loads(json.dumps(self.hits))

    def handler(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def _read_body(self) -> bytes:
                length = int(self.headers.get("Content-Length", "0") or 0)
                return self.rfile.read(length) if length else b""

            def _send(self, status: int, ctype: str, body: bytes, hit: int) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("x-fixture-hit", str(hit))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                self.close_connection = True

            def do_GET(self):
                # No listing/auth traffic is expected; record it if it happens.
                entry = {
                    "index": -1, "method": "GET", "path": self.path,
                    "verdict": "unexpected_get",
                }
                fixture.record(entry)
                self._send(404, "application/json",
                           b'{"error":{"message":"fixture: GET not served"}}', -1)

            def do_POST(self):
                raw = self._read_body()
                index, verdict = fixture.next_verdict()
                status, body, ctype = VERDICTS[verdict]
                parsed: dict = {}
                try:
                    candidate = json.loads(raw or b"{}")
                    if isinstance(candidate, dict):
                        parsed = candidate
                except ValueError:
                    pass
                inputs = parsed.get("input")
                entry = {
                    "index": index,
                    "method": "POST",
                    "path": self.path,
                    "verdict": verdict,
                    "status": status,
                    "content_type": self.headers.get("Content-Type", ""),
                    "accept": self.headers.get("Accept", ""),
                    "authorization_present": bool(self.headers.get("Authorization")),
                    "user_agent": self.headers.get("User-Agent", ""),
                    "request_bytes": len(raw),
                    "request_sha256": hashlib.sha256(raw).hexdigest(),
                    "request_model": parsed.get("model"),
                    "request_stream": parsed.get("stream"),
                    "request_store": parsed.get("store"),
                    "request_input_items": len(inputs) if isinstance(inputs, list) else None,
                    "response_bytes": len(body),
                }
                fixture.record(entry)
                if verdict == "partial_then_error":
                    # Deliver real output first, then the error, so an
                    # after-output replay decision is observable.
                    created_part = _sse([_created("resp_fixture_2"),
                                         _message_item("partial answer")])
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("x-fixture-hit", str(index))
                    self.end_headers()
                    self.wfile.write(created_part)
                    self.wfile.flush()
                    time.sleep(0.05)
                    self.wfile.write(SCREENSHOT_EVENT)
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self._send(status, ctype, body, index)

        return Handler


def _vendor_binary() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if os.name == "nt" and appdata:
        candidate = (
            Path(appdata) / "npm" / "node_modules" / "@openai" / "codex"
            / "node_modules" / "@openai" / "codex-win32-x64" / "vendor"
            / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
        )
        if candidate.exists():
            return candidate
    return None


def _version_of(path: str) -> dict:
    try:
        completed = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
        return {"path": path, "version": (completed.stdout or "").strip(),
                "returncode": completed.returncode}
    except Exception as exc:  # noqa: BLE001 - report the constraint verbatim
        return {"path": path, "version": None, "error": str(exc)}


def _cmd_version_of(path: str) -> dict:
    try:
        completed = subprocess.run(
            f'"{path}" --version', shell=True,
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        )
        return {"path": path, "version": (completed.stdout or "").strip(),
                "returncode": completed.returncode}
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "version": None, "error": str(exc)}


def resolve_codex(binary: str) -> tuple[str, dict]:
    which_codex = shutil.which("codex")
    which_cmd = shutil.which("codex.cmd")
    identity: dict = {
        "shutil_which_codex": which_codex,
        "shutil_which_codex_cmd": which_cmd,
    }
    if which_cmd:
        identity["launcher"] = _cmd_version_of(which_cmd)
    other = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
    if other.exists():
        identity["other_install"] = _version_of(str(other))

    if binary:
        chosen = binary
    else:
        vendor = _vendor_binary()
        chosen = str(vendor) if vendor else (which_codex or "codex")
    identity["binary_used"] = chosen
    identity["binary"] = _version_of(chosen)
    if Path(chosen).exists():
        identity["binary_sha256"] = hashlib.sha256(Path(chosen).read_bytes()).hexdigest()
    return chosen, identity


def isolated_env(home: Path, codex_home: Path) -> dict:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith(("CIEL_RUNTIME_", "CODEX_", "OPENAI_", "ANTHROPIC_", "CLAUDE")):
            continue
        if any(word in upper for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")):
            continue
        if upper in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            continue
        env[key] = value
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        CODEX_HOME=str(codex_home),
        CODEX_FIXTURE_API_KEY="fixture-key",
        NO_COLOR="1",
        RUST_LOG="warn",
    )
    return env


def config_overrides(port: int, model: str,
                     request_max_retries: int, stream_max_retries: int) -> dict:
    """The same ``-c`` provider block shape the runtime emits."""
    return {
        "model": f'"{model}"',
        "model_provider": '"fixture"',
        "approval_policy": '"never"',
        "sandbox_mode": '"read-only"',
        "check_for_update_on_startup": "false",
        "model_providers.fixture.name": '"Loopback fixture"',
        "model_providers.fixture.base_url": f'"http://127.0.0.1:{port}/v1"',
        "model_providers.fixture.wire_api": '"responses"',
        "model_providers.fixture.env_key": '"CODEX_FIXTURE_API_KEY"',
        "model_providers.fixture.request_max_retries": str(request_max_retries),
        "model_providers.fixture.stream_max_retries": str(stream_max_retries),
    }


def config_args(settings: dict) -> list[str]:
    args: list[str] = []
    for key, value in settings.items():
        args.extend(["-c", f"{key}={value}"])
    return args


def _create_kill_job():
    """Windows job object that kills every descendant when its handle closes."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_ulonglong),
                ("PerJobUserTimeLimit", ctypes.c_ulonglong),
                ("LimitFlags", ctypes.c_ulong),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_ulong),
                ("Affinity", ctypes.c_void_p),
                ("PriorityClass", ctypes.c_ulong),
                ("SchedulingClass", ctypes.c_ulong),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            ctypes.c_void_p(handle), 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        return handle
    except Exception:  # noqa: BLE001 - cleanup hardening must not mask the case
        return None


def _assign_job(job, proc) -> bool:
    if job is None or os.name != "nt":
        return False
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        return bool(kernel32.AssignProcessToJobObject(
            ctypes.c_void_p(job), ctypes.c_void_p(int(proc._handle))  # noqa: SLF001
        ))
    except Exception:  # noqa: BLE001
        return False


def _close_job(job) -> None:
    if job is None or os.name != "nt":
        return
    try:
        import ctypes
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
            ctypes.c_void_p(job)
        )
    except Exception:  # noqa: BLE001
        pass


def kill_own_tree(pid: int, job=None) -> dict:
    result: dict = {}
    if os.name == "nt":
        if job is not None:
            # Closing the job terminates every process the child created,
            # including descendants that outlive a parent that already exited.
            _close_job(job)
            result["job_closed"] = True
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, timeout=30,
        )
        result.update(taskkill=completed.returncode,
                      taskkill_out=(completed.stdout or "").strip()[:200])
        return result
    try:
        os.killpg(pid, 9)
        result["killpg"] = 0
    except Exception as exc:  # noqa: BLE001
        result["killpg_error"] = str(exc)[:200]
    return result


def parse_jsonl(stdout: str) -> dict:
    types: dict[str, int] = {}
    errors: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = str(event.get("type") or event.get("msg", {}).get("type") or "")
        if kind:
            types[kind] = types.get(kind, 0) + 1
        if "error" in kind or kind in ("turn.failed", "error"):
            errors.append(line[:400])
    return {"event_types": types, "error_events": errors[-5:]}


def run_case(codex: str, model: str, case: dict, timeout: float) -> dict:
    fixture = Fixture(case["script"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.handler())
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    port = server.server_port
    result: dict = {
        "name": case["name"],
        "script": case["script"],
        "config": {
            "request_max_retries": case["request_max_retries"],
            "stream_max_retries": case["stream_max_retries"],
        },
        "expected_hits": case.get("expected_hits"),
        "endpoint": f"http://127.0.0.1:{port}/v1/responses",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="codex-native-retry-") as root:
            base = Path(root)
            home, codex_home, cwd = base / "home", base / "codex", base / "cwd"
            for path in (home, codex_home, cwd):
                path.mkdir(parents=True, exist_ok=True)
            settings = config_overrides(
                port, model,
                case["request_max_retries"], case["stream_max_retries"],
            )
            result["config_overrides"] = settings
            env = isolated_env(home, codex_home)
            result["env_scrubbed"] = {
                "removed_prefixes": ["CIEL_RUNTIME_", "CODEX_", "OPENAI_",
                                     "ANTHROPIC_", "CLAUDE"],
                "removed_proxy_vars": ["HTTP_PROXY", "HTTPS_PROXY",
                                       "ALL_PROXY", "NO_PROXY"],
                "fixture_key_env": "CODEX_FIXTURE_API_KEY",
            }
            cmd = [
                codex, "exec",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--ephemeral",
                "--dangerously-bypass-approvals-and-sandbox",
                *config_args(settings),
                "-C", str(cwd),
                "-m", model,
                "-o", str(cwd / "last-message.txt"),
                "--json",
                "Reply with the word ok.",
            ]
            result["command"] = cmd
            started = time.monotonic()
            job = _create_kill_job()
            proc = subprocess.Popen(
                cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace",
            )
            result["job_assigned"] = _assign_job(job, proc)
            timed_out = False
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                result["poll_before_cleanup"] = proc.poll()
                result["cleanup"] = kill_own_tree(proc.pid, job)
                job = None  # already closed
                try:
                    stdout, stderr = proc.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    stdout, stderr = "", ""
            finally:
                _close_job(job)
            elapsed = round(time.monotonic() - started, 2)
            last_message = cwd / "last-message.txt"
            result["cli"] = {
                "returncode": proc.returncode,
                "timed_out": timed_out,
                "elapsed_seconds": elapsed,
                "stdout_tail": (stdout or "").strip()[-3000:],
                "stderr_tail": (stderr or "").strip()[-3000:],
                "jsonl": parse_jsonl(stdout or ""),
                "last_message": (
                    last_message.read_text(encoding="utf-8", errors="replace")[:1000]
                    if last_message.exists() else None
                ),
            }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        result["listener_closed"] = not thread.is_alive()
        result["observed_hits"] = len(fixture.hits)
        result["hits"] = fixture.snapshot()
        result["retried"] = result["observed_hits"] > 1
        if result["expected_hits"] is not None:
            result["expected_hit_match"] = (
                result["observed_hits"] == result["expected_hits"]
            )
    return result


CASES = [
    {
        "name": "request_503_then_success",
        "request_max_retries": 3, "stream_max_retries": 0,
        "script": ["fail503", "success"], "expected_hits": 2,
    },
    {
        "name": "request_503_all_fail_limit3",
        "request_max_retries": 3, "stream_max_retries": 0,
        "script": ["fail503"], "expected_hits": 4,
    },
    {
        "name": "request_503_limit0",
        "request_max_retries": 0, "stream_max_retries": 0,
        "script": ["fail503"], "expected_hits": 1,
    },
    {
        "name": "screenshot_error_before_output_stream0",
        "request_max_retries": 3, "stream_max_retries": 0,
        "script": ["screenshot_error", "success"], "expected_hits": 1,
    },
    {
        "name": "screenshot_error_before_output_stream2",
        "request_max_retries": 3, "stream_max_retries": 2,
        "script": ["screenshot_error", "success"], "expected_hits": None,
    },
    {
        "name": "screenshot_error_after_output_stream0",
        "request_max_retries": 3, "stream_max_retries": 0,
        "script": ["partial_then_error", "success"], "expected_hits": 1,
    },
    {
        "name": "screenshot_error_after_output_stream2",
        "request_max_retries": 3, "stream_max_retries": 2,
        "script": ["partial_then_error", "success"], "expected_hits": None,
    },
    {
        "name": "native_response_failed_before_output_stream2",
        "request_max_retries": 3, "stream_max_retries": 2,
        "script": ["native_failed", "success"], "expected_hits": None,
    },
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="")
    parser.add_argument("--binary", default="")
    parser.add_argument("--model", default="fixture-model")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--only", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    codex, identity = resolve_codex(args.binary)
    selected = CASES
    if args.only:
        wanted = {name.strip() for name in args.only.split(",") if name.strip()}
        selected = [case for case in CASES if case["name"] in wanted]
        if not selected:
            raise SystemExit(f"no cases matched --only {args.only!r}")

    payload: dict = {
        "label": args.label,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "codex_identity": identity,
        "model": args.model,
        "isolation": {
            "home": "private temp HOME/USERPROFILE",
            "codex_home": "private temp CODEX_HOME (no config.toml written)",
            "cwd": "private temp cwd",
            "user_config": "--ignore-user-config; provider block via -c overrides",
            "stdin": "DEVNULL",
            "sandbox": "--dangerously-bypass-approvals-and-sandbox (fixture returns text only)",
        },
        "cases": [],
    }
    for case in selected:
        result = run_case(codex, args.model, case, args.timeout)
        payload["cases"].append(result)
        print(json.dumps({
            "name": result["name"],
            "expected_hits": result.get("expected_hits"),
            "observed_hits": result["observed_hits"],
            "retried": result["retried"],
            "returncode": result["cli"]["returncode"],
            "timed_out": result["cli"]["timed_out"],
            "last_message": result["cli"]["last_message"],
        }, ensure_ascii=False), flush=True)

    out = Path(args.out) if args.out else HERE / (
        "native-codex" + (f"-{args.label}" if args.label else "") + ".json"
    )
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(out)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
