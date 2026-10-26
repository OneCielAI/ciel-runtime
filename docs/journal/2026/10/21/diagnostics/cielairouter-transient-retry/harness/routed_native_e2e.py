"""Installed native CLIs through actual RouterHandler production dispatch.

Only starts owned ThreadingHTTPServers; no run_cli/serve lifecycle, schedulers,
receivers, backup or lifetime watchdog. Prompt bodies stay in memory. Evidence
contains only fixture replies, protocol metadata, counts and digests.
"""
import json, os, subprocess, sys, tempfile, threading, time, hashlib
from pathlib import Path
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

HERE = Path(__file__).resolve().parent
# Resolve executable homes before the imported harness replaces HOME.
CLAUDE = Path.home() / ".local/bin/claude.exe"
CODEX = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
SECOND_CODEX = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/OpenAI/Codex/bin/codex.exe"
sys.path.insert(0, str(HERE))
# This module scrubs environment and creates private HOME/config/cwd/TEMP before
# importing ciel_runtime. No native CLI session/config is inherited.
import protocol_acceptance as pa
import native_codex_retry as ncr
import direct_claude_retry as direct
import ciel_runtime
# Isolated diagnostics must opt in to retry WARN lines (default is ERROR).
Path(ciel_runtime.LOG_LEVEL_PATH).write_text("WARN", encoding="utf-8")
ciel_runtime.log_level_repository().reset_cache()

INGRESS = []
LOCK = threading.Lock()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Observed(ciel_runtime.RouterHandler):
    def do_POST(self):
        # Do not read, rewrite or replace rfile; actual handler admission/parsing
        # remains production. Record only its inbound path and final status.
        record = {"path": self.path.split("?")[0], "method": "POST"}
        with LOCK:
            INGRESS.append(record)
        try:
            super().do_POST()
        finally:
            record["status"] = getattr(self, "_ciel_runtime_response_status", None)


class RecordingFixture(pa.Fixture):
    def __init__(self, script, default):
        self.sent_statuses = []
        self.auxiliary_http = []
        super().__init__(script, default)

    def _handler(self):
        base = super()._handler()
        fixture = self

        class Recorded(base):
            def send_response(self, code, message=None):
                if self.command == "POST" and self.path.split("?")[0] in {"/v1/messages", "/v1/responses"}:
                    fixture.sent_statuses.append(int(code))
                else:
                    fixture.auxiliary_http.append({"method": self.command, "path": self.path.split("?")[0], "status": int(code)})
                super().send_response(code, message)

        return Recorded


def write_cfg(base, model):
    cfg = {"current_provider": "cielairouter", "external_event_receivers": {},
           "remote_bridge": {"enabled": False}, "web_backend": {"enabled": False},
           "remote_memory": {"enabled": False}, "remote_instructions": {"enabled": False},
           "transcript_events": {"enabled": False}, "tool_call_events": {"enabled": False},
           "providers": {"cielairouter": {
               "base_url": base, "api_key": "fixture-key", "current_model": model,
               "gateway_selection": "off", "gateway_retries": 3,
               "responses_stream_truncation_retries": 0}}}
    Path(ciel_runtime.CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(ciel_runtime.CONFIG_PATH).write_text(json.dumps(cfg), encoding="utf-8")
    ciel_runtime.invalidate_config_cache()
    provider, pcfg = ciel_runtime.get_current_provider(ciel_runtime.load_config())
    assert provider == "cielairouter" and pcfg["base_url"] == base
    assert pcfg["gateway_retries"] == 3 and pcfg["gateway_selection"] == "off"


def router():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Observed)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    return server, thread


def run_cli(protocol, binary, port, root):
    home, config, cwd = root/"home", root/"cli", root/"cwd"
    for p in (home, config, cwd):
        p.mkdir(parents=True, exist_ok=True)
    env = ncr.isolated_env(home, config)
    last = cwd/"last-message.txt"
    if protocol == "claude":
        controls = {"CLAUDE_CODE_MAX_RETRIES": "0", "CLAUDE_CODE_RETRY_WATCHDOG": "0",
                    "CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES": "0",
                    "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK": "1"}
        env.update(controls)
        env.update(CLAUDE_CONFIG_DIR=str(config), ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}",
                   ANTHROPIC_API_KEY="fixture-key")
        cmd = [str(binary), "--bare", "-p", "Reply with the word ok.",
               "--model", "claude-sonnet-5-5", "--max-turns", "1", "--tools", "", "--no-session-persistence"]
    else:
        controls = ncr.config_overrides(port, "gpt-5.5", 0, 0)
        controls.update({"features.plugins": "false", "features.remote_plugin": "false",
                         "features.apps": "false", "features.shell_tool": "false",
                         "features.unified_exec": "false", "features.goals": "false",
                         "features.view_image": "false", "features.shell_snapshot": "false",
                         "features.skill_search": "false", "features.multi_agent": "false",
                         "features.tool_suggest": "false", "features.unbounded_connection_retries": "false",
                         "web_search": '"disabled"'})
        cmd = [str(binary), "exec", "--ignore-user-config", "--skip-git-repo-check", "--ephemeral",
               "--sandbox", "read-only", *ncr.config_args(controls), "-C", str(cwd), "-m", "gpt-5.5",
               "-o", str(last), "--json", "Reply with the word ok."]
    started = time.monotonic()
    job = ncr._create_kill_job()
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    assigned = ncr._assign_job(job, proc)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        timed_out = True
        if assigned:
            ncr._close_job(job)
            job = None
        proc.kill()
        stdout, stderr = proc.communicate(timeout=15)
    finally:
        ncr._close_job(job)
    successes, tool_items = [], 0
    events = ncr.parse_jsonl(stdout)
    events.pop("error_events", None)
    if protocol == "claude":
        successes = ["ok"] if stdout.strip() == "ok" else []
    else:
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get("item", {})
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                successes.append(item.get("text"))
            if item.get("type") in {"command_execution", "file_change", "mcp_tool_call", "web_search"}:
                tool_items += 1
    return {"returncode": proc.returncode, "timed_out": timed_out,
            "elapsed_seconds": round(time.monotonic()-started, 3), "job_assigned": assigned,
            "retry_controls": controls, "success_outputs": successes,
            "success_output_count": len(successes), "tool_execution_item_count": tool_items,
            "stdout_sha256": sha(stdout.encode()), "stdout_bytes": len(stdout.encode()),
            "stderr_sha256": sha(stderr.encode()), "stderr_bytes": len(stderr.encode()),
            "external_plugin_warning": "chatgpt.com" in stderr,
            "event_types": events["event_types"],
            "last_message_is_ok": last.exists() and last.read_text(encoding="utf-8").strip() == "ok"}


def case(protocol, binary, persistent):
    SSE = "text/event-stream"
    failure = (503, pa.SCREENSHOT, SSE)
    success = (200, direct._stream_body() if protocol == "claude" else ncr.success_stream(), SSE)
    fixture = RecordingFixture([], failure) if persistent else RecordingFixture([failure], success)
    log_path = Path(ciel_runtime.LOG_PATH)
    start_log = log_path.stat().st_size if log_path.exists() else 0
    INGRESS.clear()
    server = thread = None
    try:
        write_cfg(fixture.base, "claude-sonnet-5-5" if protocol == "claude" else "gpt-5.5")
        server, thread = router()
        with tempfile.TemporaryDirectory(prefix="routed-native-cli-", dir=pa.ISOLATION["tmp"]) as root:
            cli = run_cli(protocol, binary, server.server_address[1], Path(root))
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join(5)
        fixture_closed = fixture.stop()
    hits = []
    for index, hit in enumerate(fixture.hits):
        body = json.loads(hit["body"])
        hits.append({"sequence": index + 1, "path": hit["path"],
                     "status": fixture.sent_statuses[index],
                     "request_bytes": len(hit["body"]), "request_sha256": sha(hit["body"]),
                     "request_model": body.get("model"), "request_stream": body.get("stream"),
                     "authorization_present": bool(hit["authorization"])})
    logs = []
    if log_path.exists():
        with log_path.open("rb") as stream:
            stream.seek(start_log)
            logs = [line for line in stream.read().decode("utf-8", errors="replace").splitlines()
                    if "cielairouter_transient_retry " in line or "cielairouter_transient_stopped " in line]
    path = "/v1/messages" if protocol == "claude" else "/v1/responses"
    checks = {"one_cli_http_request": len(INGRESS) == 1 and INGRESS[0]["path"] == path,
              "downstream_status": len(INGRESS) == 1 and INGRESS[0]["status"] == (503 if persistent else 200),
              "exact_upstream_hits": len(hits) == (4 if persistent else 2),
              "exact_upstream_status_sequence": fixture.sent_statuses == ([503]*4 if persistent else [503, 200]),
              "same_upstream_endpoint": all(hit["path"].split("?")[0] == path for hit in hits),
              "same_upstream_body": len({hit["request_sha256"] for hit in hits}) == 1,
              "one_retry_operation_id": len({line.split("operation_id=")[1].split()[0] for line in logs if "operation_id=" in line}) == 1,
              "correct_final_retry_budget": not persistent or any("attempts=3 opens=4" in line for line in logs),
              "correct_cli_exit": cli["returncode"] == (1 if persistent else 0) and not cli["timed_out"],
              "exact_cli_success_count": cli["success_outputs"] == ([] if persistent else ["ok"]),
              "no_cli_tool_execution": cli["tool_execution_item_count"] == 0,
              "no_external_plugin_warning": not cli["external_plugin_warning"],
              "retry_log_count": len(logs) == (4 if persistent else 1),
              "owned_listeners_closed": fixture_closed and not thread.is_alive()}
    return {"name": protocol + ("_persistent503" if persistent else "_503_then_success"),
            "binary": str(binary), "passed": all(checks.values()), "checks": checks,
            "ingress": list(INGRESS), "upstream_hits": hits, "upstream_auxiliary_http": fixture.auxiliary_http, "cli": cli, "retry_logs": logs}


def main():
    binaries = [("claude", CLAUDE), ("codex", CODEX), ("codex", SECOND_CODEX)]
    identities = []
    for protocol, binary in binaries:
        version = subprocess.run([str(binary), "--version"], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=30)
        identities.append({"protocol": protocol, "path": str(binary), "version": version.stdout.strip(),
                           "sha256": sha(binary.read_bytes())})
    source_paths = ["ciel_runtime.py", "ciel_runtime_support/router_http.py",
                    "ciel_runtime_support/transient_retry.py", "ciel_runtime_support/upstream_retry.py",
                    "ciel_runtime_support/provider_responses_passthrough.py", "ciel_runtime_support/claude_router.py"]
    before = {path: sha((pa.REPO/path).read_bytes()) for path in source_paths}
    results = []
    for protocol, binary in binaries:
        for persistent in (False, True):
            result = case(protocol, binary, persistent)
            results.append(result)
            print(json.dumps({"name": result["name"], "passed": result["passed"],
                              "cli_requests": len(result["ingress"]),
                              "upstream_hits": len(result["upstream_hits"]),
                              "upstream_statuses": [hit["status"] for hit in result["upstream_hits"]],
                              "successes": result["cli"]["success_output_count"],
                              "returncode": result["cli"]["returncode"]}), flush=True)
    after = {path: sha((pa.REPO/path).read_bytes()) for path in source_paths}
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "cli_identities": identities,
               "production_dispatch": "RouterHandler -> build_router_http_services -> RouterHttpHandler.do_POST -> _dispatch_with_failover -> route_runtime_post",
               "lifecycle": "Only ThreadingHTTPServer. No run_cli/serve/scheduler/receiver/backup/lifetime-watchdog start.",
               "isolation": pa.ISOLATION, "sources_before": before, "sources_after": after,
               "sources_stable_during_run": before == after, "cases": results,
               "fixture_error_sha256": sha(pa.SCREENSHOT),
               "limitations": ["Owned loopback fixtures, not live provider infrastructure.",
                               "Codex still advertises request_user_input/apply_patch; fixture emits no tool call and zero execution items were observed.",
                               "No process-wide network capture; provider HTTP sequence is recorded at owned ingress/upstream sockets.",
                               "Upstream statuses observed at fixture send_response; raw request bodies and native transcripts are not persisted."],
               "all_passed": before == after and all(result["passed"] for result in results)}
    out = HERE/"routed-native-e2e.json"
    out.write_text(json.dumps(payload, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"evidence": str(out), "all_passed": payload["all_passed"]}), flush=True)
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
