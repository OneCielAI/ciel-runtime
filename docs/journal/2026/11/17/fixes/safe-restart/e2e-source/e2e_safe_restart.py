"""E2E: a safe restart — no folder-trust stall, worktree snapshot + takeover, held inputs picked up.

Usage: python e2e_safe_restart.py <source tree> <out dir>

Isolated: everything under the system temp folder (the test isolation guard accepts only
that), a fresh git repository that is also the launched process's home folder (Celly's
shape: "This is your home folder, so Yes lasts for this session only"), a scratch
CIEL_RUNTIME_CONFIG_DIR, unique router port, CIEL_RUNTIME_TEST_ISOLATED=1, every
inherited CIEL_RUNTIME_/ANTHROPIC_/CLAUDE_/CODEX_/OPENAI_ variable cleared, and a stub
OpenAI-compatible upstream. Live sessions are never touched. Claude Code is the
machine's installed claude.exe, launched by the real `ciel_runtime.py cli`.

Steps:
  1 first launch: the trust dialog is answered once, as the person would ("Yes").
  2 turn 1; then a tool turn: the stub asks Claude Code's Bash tool to create a linked
    worktree, edit a tracked file, add an untracked file and cd into the worktree.
  3 a slow turn; restart_session (MCP) is requested while it runs; three channel inputs
    are sent while the restart waits for the turn.
  4 after the relaunch: was the trust dialog shown again? the restart notice text; the
    three held inputs reach the model after the relaunch, in order, once each; the
    snapshot ref and manifest; worktree files, index and branch unchanged; session id.
  5 turn 3 (same conversation); then `/restart-session` is typed into the prompt and
    must restart again with source=slash.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SOURCE = Path(sys.argv[1]).resolve()
RUN = Path(sys.argv[2]).resolve()
RUN.mkdir(parents=True, exist_ok=False)
SLOW = float(os.environ.get("E2E_SLOW_SECONDS") or 45)
NOTICE = "계속-세션재시작됨"
SCRATCH = Path(tempfile.mkdtemp(prefix="ciel-e2e-safe-restart-"))
CFG, WS, SHOTS = SCRATCH / "cfg", SCRATCH / "home-repo", RUN / "shots"
for folder in (CFG, WS, SHOTS):
    folder.mkdir(parents=True, exist_ok=True)
TITLE = "CIELE2E-SAFE-RESTART-" + RUN.name
(RUN / "scratch.txt").write_text(str(SCRATCH), encoding="utf-8")  # local cleanup aid; never committed
LOG = open(RUN / "driver.log", "w", encoding="utf-8")
HELD = ["E2E-HELD-1", "E2E-HELD-2", "E2E-HELD-3"]


def log(message: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {message}".replace(str(SCRATCH), "<scratch>")
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(WS), *args], capture_output=True, text=True, check=True).stdout.strip()


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


# ---- the home-folder repository ----
git("init", "-q", "-b", "main")
git("config", "user.name", "e2e")
git("config", "user.email", "e2e@example.invalid")
(WS / "README.md").write_text("safe restart e2e\n", encoding="utf-8")
git("add", "README.md")
git("commit", "-q", "-m", "base")
(WS / ".claude").mkdir()
# As on the machine's own profile: ciel-runtime launches Claude Code in bypass mode,
# whose one-time acceptance lives in user settings.
(WS / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash"], "deny": []},
                                                          "skipDangerousModePermissionPrompt": True}),
                                               encoding="utf-8")
(WS / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True, "theme": "dark", "projects": {}}),
                                 encoding="utf-8")
WT_REL = ".claude/worktrees/feat"
WT = WS / ".claude" / "worktrees" / "feat"
SETUP_COMMAND = (f"git worktree add -q -b feat {WT_REL} && cd {WT_REL} && printf 'fork edit\\n' >> README.md "
                 "&& printf 'untracked work\\n' > new.txt && pwd")

# ---- stub upstream (OpenAI-compatible chat completions) ----
REQS: list[dict] = []


def _text(message: dict) -> str:
    content = message.get("content")
    return content if isinstance(content, str) else " ".join(
        str(part.get("text", "")) for part in (content or []) if isinstance(part, dict))


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, value, code=200):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "stub-model", "object": "model", "owned_by": "stub",
                                                "max_model_len": 128000}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        messages = body.get("messages") or []
        users = [_text(m) for m in messages if m.get("role") == "user"]
        last = users[-1] if users else ""
        # Tool results: OpenAI role "tool", or Anthropic tool_result blocks in a user message.
        tool_results = [m for m in messages if m.get("role") == "tool"] + [
            block for m in messages if m.get("role") == "user" and isinstance(m.get("content"), list)
            for block in m["content"] if isinstance(block, dict) and block.get("type") == "tool_result"]
        n = len(REQS) + 1
        # Markers are matched on the full text: the hook context in front of a
        # prompt is longer than any stored excerpt (run after-3 missed them).
        words = ("E2E-TURN-1", "E2E-WT-SETUP", "E2E-SLOW-2", "E2E-TURN-3", NOTICE, *HELD)
        record = {"n": n, "at": time.time(), "path": self.path, "last": " ".join(last.split())[-1500:],
                  "users": len(users), "tool_results": len(tool_results),
                  "markers": [w for w in words if w in last],
                  "positions": {w: last.find(w) for w in words if w in last},
                  "user_counts": {w: sum(w in u for u in users) for w in words},
                  "history": [w for w in words if any(w in u for u in users)]}
        notice_text = next((u for u in reversed(users) if NOTICE in u), "")
        if notice_text:
            record["notice"] = " ".join(notice_text[notice_text.index(NOTICE):].split())[:2000]
        REQS.append(record)
        setup = "E2E-WT-SETUP" in last and not tool_results
        if "E2E-SLOW-2" in last and not any("E2E-SLOW-2" in u for u in users[:-1]):
            record["slow_started_at"] = time.time()
            time.sleep(SLOW)
        reply = f"STUB-REPLY-{n}"
        arguments = {"command": SETUP_COMMAND, "description": "create the e2e worktree"}
        if self.path.split("?")[0].endswith("/v1/messages"):
            self._anthropic(body, n, reply, arguments if setup else None)
        else:
            self._chat(body, n, reply, arguments if setup else None)
        record["replied_at"] = time.time()
        record["tool_call"] = setup

    def _anthropic(self, body, n, reply, tool_input):
        message = {"id": f"msg_{n}", "type": "message", "role": "assistant", "model": body.get("model") or "stub-model",
                   "content": [], "stop_reason": None, "stop_sequence": None,
                   "usage": {"input_tokens": 10, "output_tokens": 2}}
        if tool_input:
            block = {"type": "tool_use", "id": f"toolu_setup_{n}", "name": "Bash", "input": {}}
            events = [{"type": "content_block_start", "index": 0, "content_block": block},
                      {"type": "content_block_delta", "index": 0,
                       "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool_input)}},
                      {"type": "content_block_stop", "index": 0}]
            stop = "tool_use"
            final = [{**block, "input": tool_input}]
        else:
            events = [{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                      {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply}},
                      {"type": "content_block_stop", "index": 0}]
            stop = "end_turn"
            final = [{"type": "text", "text": reply}]
        if not body.get("stream"):
            self._json({**message, "content": final, "stop_reason": stop})
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in ([{"type": "message_start", "message": message}] + events
                      + [{"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                          "usage": {"output_tokens": 2}}, {"type": "message_stop"}]):
            self.wfile.write(("event: " + event["type"] + chr(10) + "data: " + json.dumps(event)
                              + chr(10) + chr(10)).encode())
            self.wfile.flush()

    def _chat(self, body, n, reply, tool_input):
        tool_call = ({"id": "call_setup", "type": "function",
                      "function": {"name": "Bash", "arguments": json.dumps(tool_input)}} if tool_input else None)
        base = {"id": f"c{n}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub-model"}
        if tool_call:
            chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                          {"index": 0, **tool_call}]}, "finish_reason": None}]},
                      {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
        else:
            chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply},
                                            "finish_reason": None}]},
                      {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        chunks[-1]["usage"] = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(("data: " + json.dumps(chunk) + chr(10) + chr(10)).encode())
                self.wfile.flush()
            self.wfile.write(("data: [DONE]" + chr(10) + chr(10)).encode())
            self.wfile.flush()
            return
        message = {"role": "assistant", "content": None if tool_call else reply}
        if tool_call:
            message["tool_calls"] = [tool_call]
        self._json({"id": f"c{n}", "object": "chat.completion", "created": int(time.time()), "model": "stub-model",
                    "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_call else "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})


stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=stub.serve_forever, daemon=True).start()
ROUTER = free_port()
(CFG / "config.json").write_text(json.dumps({
    "current_provider": "vllm", "language": "en",
    "providers": {"vllm": {"base_url": f"http://127.0.0.1:{stub.server_address[1]}", "current_model": "stub-model",
                           "api_key": "dummy", "context_window": 128000, "max_output_tokens": 4096}},
}, indent=2), encoding="utf-8")
(CFG / "log-level").write_text("INFO")
clear = "".join(f'for /f "delims==" %%v in (\'set {prefix} 2^>nul\') do set "%%v="\n'
                for prefix in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
script = RUN / "launch.cmd"
script.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    f'set "USERPROFILE={WS}"\nset "HOME={WS}"\n'
    # The isolation guard accepts a config folder only under the launched
    # process's temp root; a new Windows Terminal window may not inherit it.
    f'set "TEMP={tempfile.gettempdir()}"\nset "TMP={tempfile.gettempdir()}"\n'
    f'cd /d "{WS}"\npython "{SOURCE / "ciel_runtime.py"}" cli --ca-runtime claude --ca-no-update-check '
    "--ca-no-self-update-check\necho LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
log(f"source={SOURCE} stub={stub.server_address[1]} router={ROUTER} scratch={SCRATCH}")
(RUN / "answer-trust").write_text("1")   # the person answers the first launch's dialog
subprocess.Popen(["wt.exe", "-w", "new", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(script)])


def http(method: str, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"content-type": "application/json",
                                              "accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001
        return {"raw": f"{type(error).__name__}: {error}"}
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw[:500]}


def shot(label: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_title.ps1"),
                           TITLE, str(out)], capture_output=True, text=True)
    log(f"shot {label}: {(done.stdout.strip() or done.stderr.strip())[:160]}")


def router_lines(*needles: str) -> list[str]:
    rows = []
    for path in CFG.rglob("*.log*"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rows += [line[-400:] for line in text.splitlines() if any(needle in line for needle in needles)]
    return rows


def wait(predicate, seconds: float, label: str) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if predicate():
                log(f"wait {label}: True")
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    log(f"wait {label}: False")
    return False


def notify(text: str) -> None:
    reply = http("POST", "/ca/chat/notify", {"message": text, "channel": "e2e", "sender_id": "e2e-driver"})
    log(f"notify {text!r} -> ok={reply.get('ok')}")


def launcher_pid() -> int:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*' -and "
                          "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
                          + str(script) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }"],
                         capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


def transcripts() -> list[str]:
    return sorted(path.name for path in (WS / ".claude" / "projects").rglob("*.jsonl")
                  if "subagents" not in path.parts)


def tree_digest(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file() and ".git" not in path.parts}


def manifest() -> dict:
    for path in CFG.rglob("restart-takeover.json"):
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def trust_seen() -> list[dict]:
    path = RUN / "trust-seen.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def restarts() -> list[str]:
    return router_lines("claude_session_restart exit_code")


def line_epoch(line: str) -> float:
    """Local timestamp at the start of a router log line, as epoch seconds (1 s resolution)."""
    import re
    match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", line)
    return time.mktime(time.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")) if match else 0.0


result: dict = {"source": str(SOURCE)}
pid = 0


def seen(word: str) -> list[dict]:
    """Requests whose newest user message carries the marker."""
    return [q for q in REQS if word in q.get("markers", [])]


try:
    wait(lambda: "raw" not in http("GET", "/health", timeout=2), 120, "router up")
    wait(lambda: bool(launcher_pid()), 90, "launcher pid")
    pid = launcher_pid()
    watcher = subprocess.Popen([sys.executable, str(HERE / "e2e_console.py"), str(pid), str(RUN)],
                               creationflags=0x00000008)
    wait(lambda: bool(trust_seen()), 90, "first-launch trust dialog seen")
    # Isolation installs the slash commands into the scratch config folder
    # (runtime_paths.CLI_ASSET_HOME); this Claude Code reads the scratch home.
    # The product's own installed file is copied there for the /restart-session step.
    installed = next(CFG.rglob("restart-session.md"), None)
    result["slash_asset_installed"] = installed is not None
    if installed is not None:
        (WS / ".claude" / "commands").mkdir(parents=True, exist_ok=True)
        shutil.copy2(installed, WS / ".claude" / "commands" / "restart-session.md")
    time.sleep(10)
    shot("started")
    notify("E2E-TURN-1 hello")
    wait(lambda: bool(seen("E2E-TURN-1")), 120, "turn 1 at the stub")
    time.sleep(3)
    notify("E2E-WT-SETUP please run the setup")
    wait(lambda: WT.is_dir() and (WT / "new.txt").exists(), 120, "worktree created by the session's Bash tool")
    wait(lambda: any(q["tool_results"] for q in REQS), 60, "tool result back at the stub")
    time.sleep(4)
    before = {"transcripts": transcripts(), "worktree": tree_digest(WT),
              "index": subprocess.run(["git", "-C", str(WT), "diff", "--cached", "--name-status"], capture_output=True,
                                      text=True).stdout,
              "branches": git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")}
    result["before"] = {"transcripts": before["transcripts"], "worktree_files": sorted(before["worktree"])}
    log("before: " + json.dumps(result["before"], ensure_ascii=False))
    notify("E2E-SLOW-2 take your time")
    wait(lambda: bool(seen("E2E-SLOW-2")), 120, "slow turn at the stub")
    time.sleep(2)
    requested_at = time.time()
    reply = http("POST", "/ca/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": "restart_session", "arguments": {"reason": "e2e safe restart"}}})
    log("restart_session -> " + json.dumps(reply, ensure_ascii=False)[:300])
    time.sleep(3)
    held_sent = {}
    for text in HELD:
        held_sent[text] = time.time()
        notify(text + " sent while the restart waits")
        time.sleep(2)
    shot("restart-waiting")
    wait(lambda: len(restarts()) >= 1, SLOW + 180, "restart 1 performed")
    restart_line = restarts()[-1] if restarts() else ""
    # The launcher logs this line after the old child exited, before the relaunch.
    relaunched_at = line_epoch(restart_line) - 1.0 if restart_line else float("inf")
    wait(lambda: bool(seen(NOTICE)), 180, "restart notice reaches the model")
    wait(lambda: all(seen(text) for text in HELD), 120, "held inputs reach the model")
    time.sleep(5)
    shot("after-restart-1")
    notice = (seen(NOTICE) or [{}])[0]
    slow = (seen("E2E-SLOW-2") or [{}])[0]
    first_delivery = {text: (seen(text) or [{}])[0].get("at") for text in HELD}
    first_request = {text: (seen(text) or [{}])[0].get("n") for text in HELD}
    # Order across requests, then within one message (one request may carry several).
    held_order = sorted(HELD, key=lambda t: ((seen(t) or [{}])[0].get("n") or 0,
                                             (seen(t) or [{}])[0].get("positions", {}).get(t, -1)))
    data = manifest()
    session_row = next((row for row in data.get("worktrees", []) if Path(row["path"]).resolve() == WT.resolve()), {})
    snapshot_files = {}
    if session_row.get("snapshot_ref"):
        names = git("ls-tree", "-r", "--name-only", session_row["snapshot_ref"]).splitlines()
        snapshot_files = {name: git("show", f"{session_row['snapshot_ref']}:{name}") for name in names}
    after = {"transcripts": transcripts(), "worktree": tree_digest(WT),
             "index": subprocess.run(["git", "-C", str(WT), "diff", "--cached", "--name-status"], capture_output=True,
                                     text=True).stdout,
             "branches": git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")}
    notify("E2E-TURN-3 after the restart")
    wait(lambda: bool(seen("E2E-TURN-3")), 120, "turn 3 at the stub")
    turn3 = (seen("E2E-TURN-3") or [{}])[0]
    time.sleep(3)
    (RUN / "type.txt").write_text("/restart-session e2e slash", encoding="utf-8")
    wait(lambda: len(restarts()) >= 2, 180, "restart 2 (slash) performed")
    wait(lambda: len(seen(NOTICE)) >= 2, 180, "second restart notice")
    time.sleep(4)
    shot("after-restart-2")
    restart2 = restarts()[-1] if len(restarts()) >= 2 else ""
    trust = trust_seen()
    final_counts = REQS[-1].get("user_counts", {}) if REQS else {}
    notice_text = notice.get("notice", "")
    result.update({
        "claude_version": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
        "restart_line": restart_line, "restart2_line": restart2,
        "trust_dialogs": trust,
        "notice_text": notice_text,
        "held_sent_at": held_sent, "held_first_delivery_at": first_delivery, "held_first_request": first_request,
        "held_order": held_order, "notice_request": notice.get("n"),
        "held_user_message_counts_in_final_request": {text: final_counts.get(text) for text in HELD},
        "slow_started_at": slow.get("slow_started_at"), "slow_replied_at": slow.get("replied_at"),
        "restart_requested_at": requested_at, "old_child_exit_logged_at": relaunched_at + 1.0,
        "manifest": {key: data.get(key) for key in ("session_cwd", "session_worktree", "repository")},
        "manifest_rows": [{k: row.get(k) for k in ("path", "branch", "modified", "untracked", "snapshot_ref", "error",
                                                    "skipped")} for row in data.get("worktrees", [])],
        "snapshot_files": snapshot_files,
        "transcripts_before": before["transcripts"], "transcripts_after": after["transcripts"],
        "turn3_history": turn3.get("history"),
        "hold_log": router_lines("runtime_session_restart_hold_started", "restart_hold_since",
                                 "claude_restart_folder_trusted", "restart_takeover_snapshot")[-10:],
    })
    checks = {
        "trust_dialog_only_on_first_launch": len(trust) == 1 and trust[0]["answered"],
        "restart_1_performed": bool(restart_line),
        # The request came while the slow turn ran, and the CLI was stopped only after it replied.
        "restart_waited_for_the_turn": bool(slow.get("replied_at")) and requested_at < slow["replied_at"]
            and relaunched_at + 1.0 >= int(slow["replied_at"]),
        # Every held input was sent before the old CLI stopped, so the hold had to keep it.
        "held_inputs_sent_during_the_wait": all(requested_at < at < relaunched_at for at in held_sent.values()),
        "held_inputs_delivered_after_relaunch_once_in_order": all(first_request.values())
            and all(final_counts.get(text) == 1 for text in HELD)
            and held_order == HELD
            and all((first_delivery[t] or 0) >= relaunched_at for t in HELD),
        # The notice says held inputs come before it.
        "held_inputs_precede_the_notice": all(first_request.values()) and bool(notice.get("n"))
            and max(first_request.values()) <= notice["n"],
        "held_inputs_not_given_to_the_old_session": all(
            not any(t in q.get("markers", []) and q["at"] < relaunched_at for q in REQS) for t in HELD),
        "notice_names_the_worktree": "재시작 전 작업 위치" in notice_text and "worktrees" in notice_text
            and "branch feat" in notice_text,
        "snapshot_holds_the_work": snapshot_files.get("README.md", "").endswith("fork edit")
            and snapshot_files.get("new.txt") == "untracked work",
        "worktree_index_branches_unchanged": before["worktree"] == after["worktree"]
            and before["index"] == after["index"] and before["branches"] == after["branches"],
        "same_conversation": bool(turn3.get("history")) and "E2E-TURN-1" in turn3.get("history", []),
        "slash_restart_performed": "source=slash" in restart2,
    }
    result["session_id_kept"] = before["transcripts"] == after["transcripts"]
    result["checks"] = checks
    result["passed"] = all(checks.values())
finally:
    (RUN / "stop").write_text("1")
    time.sleep(1)
    (RUN / "stub-requests.json").write_text(json.dumps(REQS, indent=1, ensure_ascii=False), encoding="utf-8")
    for path in CFG.rglob("router.log"):
        shutil.copy2(path, RUN / ("router-" + path.parent.name + ".log"))
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*" + str(script)
                    + "*' } | % { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True)
    # Every process whose command line names this run's scratch folder is owned by it.
    owned = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process | ? { $_.CommandLine -like '*" + str(SCRATCH)
                            + "*' -and $_.ProcessId -ne $PID } | % { $_.ProcessId; Stop-Process -Id $_.ProcessId -Force }"],
                           capture_output=True, text=True).stdout.split()
    time.sleep(2)
    survivors = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "@(Get-CimInstance Win32_Process | ? { $_.CommandLine -like '*" + str(SCRATCH)
                                + "*' -and $_.ProcessId -ne $PID }).Count"], capture_output=True, text=True).stdout.strip()
    result["owned_killed_at_end"] = len(owned)
    result["owned_survivors"] = int(survivors or 0)
    text = json.dumps(result, indent=1, ensure_ascii=False).replace(str(SCRATCH), "<scratch>").replace(
        str(Path.home()), "<home>")
    (RUN / "result.json").write_text(text, encoding="utf-8")
    log("result: " + json.dumps({"passed": result.get("passed"), "checks": result.get("checks"),
                                 "session_id_kept": result.get("session_id_kept")}, ensure_ascii=False))
