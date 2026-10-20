"""Call an INSTALLED ciel_runtime.py's real ImportSession entry point with synthetic transcripts.

Usage (caller sets the environment):
  USERPROFILE=HOME=<temp home> CODEX_HOME=<temp home>/codex CIEL_RUNTIME_CONFIG_DIR=<scratch>
  CIEL_RUNTIME_TEST_ISOLATED=1  py -3.11 -I -B installed_root_controller.py --root <install root>

Imports ciel_runtime from --root only and calls maybe_handle_import_session_request (the function
claude_router and openai_responses_router call). Codex bodies go through the installed
ciel_runtime.openai_responses_to_anthropic_messages first, as the Responses router does.
The HTTP handler is an in-memory capture object; no socket, router or live state is used.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", required=True)
ap.add_argument("--label", default="")
args = ap.parse_args()
root = Path(args.root).resolve()
sys.path.insert(0, str(root))
home = Path(os.environ["USERPROFILE"]).resolve()
assert "ciel-import-home" in str(home), home  # refuse to run against a real profile
assert Path.home().resolve() == home

import ciel_runtime  # noqa: E402

assert Path(ciel_runtime.__file__).resolve() == root / "ciel_runtime.py", ciel_runtime.__file__
assert ciel_runtime.HOME.resolve() == home, ciel_runtime.HOME


class CaptureHandler:
    def __init__(self) -> None:
        self.wfile = io.BytesIO()
        self.status = None
        self.headers: dict = {}
        self.response_headers: list = []
        self.close_connection = False
        self.request_version = "HTTP/1.1"
        self.command = "POST"
        self.path = "/v1/messages"

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, key, value):
        self.response_headers.append((key, value))

    def end_headers(self):
        pass

    def log_message(self, *a):
        pass


codex_dir = Path(os.environ["CODEX_HOME"]) / "sessions" / "2026" / "10" / "17"
claude_dir = home / ".claude" / "projects" / "synthetic-project"
codex_dir.mkdir(parents=True, exist_ok=True)
claude_dir.mkdir(parents=True, exist_ok=True)
codex_file = codex_dir / "rollout synthetic codex.jsonl"
claude_file = claude_dir / "synthetic claude.jsonl"
codex_file.write_text(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
    {"type": "input_text", "text": "SYNTHETIC-CODEX-TASK-7f3a"}]}}) + "\n", encoding="utf-8")
claude_file.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "text", "text": "SYNTHETIC-CLAUDE-ANSWER-91c2"}]}}) + "\n", encoding="utf-8")

body_text = ciel_runtime.IMPORT_SESSION_SLASH_COMMAND.split("---", 2)[2].lstrip("\n")


def rendered(source: str, path: str) -> str:
    arguments = source + (f' "{path}"' if path else "")
    return body_text.replace("$ARGUMENTS", arguments).replace("$1", source).replace("$2", path)


def reply_text(raw: bytes) -> str:
    payload = json.loads(raw.decode("utf-8"))
    for item in payload.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return item["text"]
    for item in payload.get("output") or []:
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                return part["text"]
    return ""


cases = [
    ("claude-client <- Codex explicit path", "claude", "Codex", str(codex_file), "SYNTHETIC-CODEX-TASK-7f3a"),
    ("codex-client <- Claude explicit path", "codex", "Claude", str(claude_file), "SYNTHETIC-CLAUDE-ANSWER-91c2"),
    ("claude-client <- Codex latest", "claude", "Codex", "", "SYNTHETIC-CODEX-TASK-7f3a"),
    ("codex-client <- Claude latest", "codex", "Claude", "", "SYNTHETIC-CLAUDE-ANSWER-91c2"),
]
results = []
for name, client, source, path, expected in cases:
    handler = CaptureHandler()
    text = rendered(source, path)
    if client == "claude":
        body = {"model": "synthetic-model", "stream": False, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "<system-reminder>synthetic reminder</system-reminder>"},
            {"type": "text", "text": text}]}]}
        handled = ciel_runtime.maybe_handle_import_session_request(handler, body, client_runtime="claude")
    else:
        source_body = {"model": "synthetic-model", "stream": False, "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}]}
        converted = ciel_runtime.openai_responses_to_anthropic_messages(source_body, "synthetic-model")
        handled = ciel_runtime.maybe_handle_import_session_request(
            handler, converted, client_runtime="codex", response_format="openai", source_body=source_body)
    raw = handler.wfile.getvalue()
    reply = reply_text(raw) if raw else ""
    first = reply.splitlines()[0] if reply else ""
    ok = bool(handled) and handler.status == 200 and "ImportSession completed" in first and expected in reply
    results.append({"case": name, "handled": bool(handled), "http_status": handler.status, "ok": ok,
                    "reply_first_line": first, "expected_marker_present": expected in reply})

shortcuts = root / "ciel_runtime_support" / "request_shortcuts.py"
summary = {
    "label": args.label,
    "root": str(root),
    "ciel_runtime_file": str(Path(ciel_runtime.__file__).resolve()),
    "version": ciel_runtime.VERSION,
    "python": sys.version.split()[0],
    "request_shortcuts_lf_sha256": hashlib.sha256(shortcuts.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
    "results": results,
    "all_ok": all(r["ok"] for r in results),
}
print(json.dumps(summary, indent=2))
raise SystemExit(0 if summary["all_ok"] else 1)
