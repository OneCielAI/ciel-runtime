"""Drive an INSTALLED copy's ImportSession controller with synthetic transcripts.

Usage: py -3.11 -I -B installed_import_session.py --root <install root> [--label NAME]

Imports ciel_runtime_support only from --root (isolated mode, no script dir on
sys.path, no bytecode writes). Builds the same wiring ciel_runtime.py uses:
IMPORT_SESSION_REQUEST_MARKERS, request_shortcuts.has_marker/import_session_args
with ConversationTurnPolicy.latest_user_text, ImportSessionRepository/Service and
ImportSessionHttpController. Codex requests go through the installed
protocols.openai_responses_to_anthropic_messages converter first, as the
Responses router does. Transcripts are synthetic files in a temporary home.
No router, network, user home, live config or real session is touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--root", required=True)
parser.add_argument("--label", default="")
args = parser.parse_args()
root = Path(args.root).resolve()
for key in tuple(os.environ):
    if key.startswith("CIEL_RUNTIME_"):
        os.environ.pop(key)
sys.path.insert(0, str(root))

import ciel_runtime_support  # noqa: E402
from ciel_runtime_support import request_shortcuts, session_import, slash_command_assets  # noqa: E402
from ciel_runtime_support.protocols import openai_responses_to_anthropic_messages  # noqa: E402
from ciel_runtime_support.protocols.conversation_turn_policy import (  # noqa: E402
    ConversationTurnPolicy,
    ConversationTurnPorts,
)

loaded_from = Path(ciel_runtime_support.__file__).resolve().parent
assert loaded_from == root / "ciel_runtime_support", loaded_from


def unused(*_a, **_k):
    raise AssertionError("port not expected")


policy = ConversationTurnPolicy(ConversationTurnPorts(unused, unused, unused, lambda *_: None, unused, unused, unused))
markers = slash_command_assets.IMPORT_SESSION_REQUEST_MARKERS
services = request_shortcuts.ShortcutTextServices(latest_user_text=policy.latest_user_text)
template = slash_command_assets.IMPORT_SESSION_SLASH_COMMAND
body_text = template.split("---", 2)[2].lstrip("\n")


def rendered(source: str, path: str) -> str:
    arguments = source + (f' "{path}"' if path else "")
    return body_text.replace("$ARGUMENTS", arguments).replace("$1", source).replace("$2", path)


def claude_body(text: str) -> dict:
    return {"model": "synthetic-model", "stream": False, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "<system-reminder>synthetic reminder</system-reminder>"},
        {"type": "text", "text": "<command-message>ImportSession is running</command-message>\n<command-name>/ImportSession</command-name>"},
        {"type": "text", "text": text},
    ]}]}


def codex_body(text: str) -> dict:
    return {"model": "synthetic-model", "stream": False, "input": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
    ]}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


results = []
with tempfile.TemporaryDirectory(prefix="ciel-import-installed-") as tmp:
    home = Path(tmp) / "home"
    codex_home = home / "codex"
    codex_dir = codex_home / "sessions" / "2026" / "10" / "17"
    claude_dir = home / ".claude" / "projects" / "synthetic-project"
    codex_dir.mkdir(parents=True)
    claude_dir.mkdir(parents=True)
    codex_file = codex_dir / "rollout synthetic codex.jsonl"
    claude_file = claude_dir / "synthetic claude.jsonl"
    codex_file.write_text(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "SYNTHETIC-CODEX-TASK-7f3a"}]}}) + "\n", encoding="utf-8")
    claude_file.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "SYNTHETIC-CLAUDE-ANSWER-91c2"}]}}) + "\n", encoding="utf-8")

    repository = session_import.ImportSessionRepository(
        home, {"CODEX_HOME": str(codex_home)}, session_import.ImportSessionLimits(512 * 1024, 240000))

    def parse(body):
        return request_shortcuts.import_session_args(body, markers, services, posix=os.name != "nt")

    service = session_import.ImportSessionService(repository, parse)
    cases = [
        ("claude-client <- Codex explicit path", "claude", "Codex", str(codex_file), "SYNTHETIC-CODEX-TASK-7f3a"),
        ("codex-client <- Claude explicit path", "codex", "Claude", str(claude_file), "SYNTHETIC-CLAUDE-ANSWER-91c2"),
        ("claude-client <- Codex latest", "claude", "Codex", "", "SYNTHETIC-CODEX-TASK-7f3a"),
        ("codex-client <- Claude latest", "codex", "Claude", "", "SYNTHETIC-CLAUDE-ANSWER-91c2"),
    ]
    for name, client, source, path, expected in cases:
        writes: list = []
        controller = session_import.ImportSessionHttpController(session_import.ImportSessionHttpPorts(
            is_request=lambda b: request_shortcuts.has_marker(b, markers, services),
            response_text=service.response_text,
            load_config=lambda: {}, current_alias=lambda _c: "synthetic-model",
            current_provider=lambda _c: ("synthetic-provider", {}), estimate_tokens=lambda _x: 1,
            write_openai=lambda h, message, **k: writes.append(("openai", message["content"][0]["text"])),
            write_anthropic=lambda h, model, text, stream: writes.append(("anthropic", text)),
            publish_event=lambda **k: None))
        text = rendered(source, path)
        if client == "claude":
            handled = controller.handle(object(), claude_body(text), client_runtime="claude")
        else:
            source_body = codex_body(text)
            converted = openai_responses_to_anthropic_messages(source_body, "synthetic-model")
            handled = controller.handle(object(), converted, client_runtime="codex",
                                        response_format="openai", source_body=source_body)
        wire, reply = writes[0] if writes else ("none", "")
        ok = bool(handled) and "ImportSession completed" in reply.splitlines()[0] and expected in reply
        results.append({"case": name, "handled": bool(handled), "wire": wire, "ok": ok,
                        "reply_first_line": reply.splitlines()[0] if reply else "",
                        "expected_marker_present": expected in reply})

summary = {
    "label": args.label,
    "root": str(root),
    "loaded_from": str(loaded_from),
    "python": sys.version.split()[0],
    "request_shortcuts_lf_sha256": sha(loaded_from / "request_shortcuts.py"),
    "results": results,
    "all_ok": all(r["ok"] for r in results),
}
print(json.dumps(summary, indent=2))
raise SystemExit(0 if summary["all_ok"] else 1)
