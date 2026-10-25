"""Build e2e_plan_exit.py from the verified e2e_semantic_retry.py (fix ollama-semantic-retry).

python make_plan_harness.py <e2e_semantic_retry.py> <e2e_plan_exit.py>

Only the launch target, the fake Ollama main-turn reply, the console agent and
the recorded facts change; isolation, teardown and evidence capture are reused.
"""
import sys
from pathlib import Path

s = Path(sys.argv[1]).read_text(encoding="utf-8")
DOC = '''"""Isolated actual-runtime + actual-Claude-CLI E2E: ExitPlanMode in plan mode without EnterPlanMode history.

python -I e2e_plan_exit.py <repo-root> <run-name>

The console agent enters plan mode with Shift+Tab (no EnterPlanMode call, the same
request shape as after a native compaction removed it) and types ONE prompt. The
fake Ollama answers the marked main turn with an ExitPlanMode tool call on POST 1
and with a text marker on later POSTs. <repo-root> selects the runtime under test
(the current nightly or the fix). Evidence goes to run-<run-name>/.

Isolation is the same as e2e_semantic_retry.py (fix ollama-semantic-retry):
- fresh temporary HOME/APPDATA/LOCALAPPDATA/TEMP, runtime config dir,
  CLAUDE_CONFIG_DIR, CODEX_HOME and workspace;
- new loopback ports;
- every inherited CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI* cleared;
- an egress sentinel proxy;
- teardown limited to the owned process tree.
"""
'''
s = DOC + s[s.index("from __future__ import annotations"):]
REPLACEMENTS = [
    ("REPO = HERE.parents[7]", "REPO = Path(sys.argv[1]).resolve()"),
    ('CASE = sys.argv[1]\nRETRIES = int(sys.argv[2]) if len(sys.argv) > 2 else 1\n'
     'assert CASE in ("recover", "exhaust"), CASE\nRUN = HERE / f"run-{CASE}-r{RETRIES}"',
     'CASE = sys.argv[2]\nRETRIES = 1\nRUN = HERE / f"run-{CASE}"'),
    ('prefix=f"ciel-osr-e2e-{CASE}-r{RETRIES}-"', 'prefix=f"ciel-plan-e2e-{CASE}-"'),
    ('PROMPT_MARKER = f"OSR-E2E-{CASE.upper()}-{UID}"\nANSWER_MARKER = f"OSR-ANSWER-{UID}"\n'
     'PROMPT = f"{PROMPT_MARKER} reply with one short line"',
     'PROMPT_MARKER = f"PLAN-E2E-{CASE.upper()}-{UID}"\nANSWER_MARKER = f"PLAN-ANSWER-{UID}"\n'
     'PLAN_MARKER = f"PLAN-BODY-{UID}"\nPROMPT = f"{PROMPT_MARKER} finish the plan and exit plan mode"'),
    ('def chunk(content: str = "", thinking: str = "", done: bool = False, eval_count: int = 0) -> dict:\n'
     '    message = {"role": "assistant", "content": content}\n',
     'def chunk(content: str = "", thinking: str = "", done: bool = False, eval_count: int = 0, tool_calls=None) -> dict:\n'
     '    message = {"role": "assistant", "content": content}\n'
     '    if tool_calls:\n        message["tool_calls"] = tool_calls\n'),
    ('TITLE = f"CIELE2E-OSR-{CASE}-r{RETRIES}-{UID}"', 'TITLE = f"CIELE2E-PLAN-{CASE}-{UID}"'),
    ('str(HERE / "e2e_console_agent.py")', 'str(HERE / "e2e_plan_agent.py")'),
    ("import os\nimport re\n", "import os\n"),
]
for old, new in REPLACEMENTS:
    assert s.count(old) == 1, old[:80]
    s = s.replace(old, new)

start = s.index("def main_turn_chunks(attempt: int)")
end = s.index("class FakeOllama")
s = s[:start] + '''def main_turn_chunks(attempt: int) -> tuple[str, list[dict]]:
    if attempt == 1:
        plan = f"# Plan {PLAN_MARKER}\\n\\n1. Edit the file.\\n2. Verify."
        call = {"function": {"name": "ExitPlanMode", "arguments": {"plan": plan}}}
        return "exit_plan_mode", [chunk(thinking="The plan is complete."), chunk(tool_calls=[call]),
                                  chunk(done=True, eval_count=12)]
    return "answer", [chunk(content=ANSWER_MARKER), chunk(content=" after ExitPlanMode."), chunk(done=True, eval_count=9)]


''' + s[end:]

start = s.index('    wait(lambda: len(main_posts()) >= 1, 60, "first main-turn POST at fake Ollama")')
end = s.index('    result["main_posts_at_response"] = posts_at_response')
s = s[:start] + '''    wait(lambda: len(main_posts()) >= 1, 60, "first main-turn POST at fake Ollama")
    wait(lambda: any(m in screen() for m in ("Exit plan mode?", "Claude wants to exit plan mode", "Would you like to proceed", "empty end_turn",
                                             "reasoning without a final answer", ANSWER_MARKER)), 120,
         "plan approval prompt, runtime notice or answer on Claude screen")
    time.sleep(3)
    shot("response")
    posts_at_response = len(main_posts())
    log(f"main-turn POSTs at response: {posts_at_response}; quiet window 20 s, nothing typed")
    time.sleep(20)
    shot("after-quiet")
''' + s[end:]

s = s[:s.index("# ---------------------------------------------------------------- facts")] + '''# ---------------------------------------------------------------- facts
posts = main_posts()
transcript = transcript_facts()
for name in ("router.log", "router-sse-trace.jsonl", "router-last-sse.json", "router-activity.json"):
    if (CFG / name).exists():
        shutil.copy(CFG / name, RUN / name)
plan_lines = router_log_lines("ExitPlanMode", "EnterPlanMode", "ollama_empty_stream", "ollama_reasoning_only_stream",
                              "ollama_semantic", "ollama_stream_request")
entries = []
for path in sorted((CCFG / "projects").rglob("*.jsonl")) if (CCFG / "projects").exists() else []:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            pass


def assistant_tool_uses(name: str) -> list[dict]:
    return [b for e in entries if e.get("type") == "assistant" for b in (e.get("message") or {}).get("content") or []
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == name]


exit_calls = assistant_tool_uses("ExitPlanMode")
final = screen()
result.update({
    "fake_ollama_requests": list(REQUESTS),
    "main_post_count": len(posts),
    "egress_sentinel_requests": EGRESS,
    "router_plan_lines": plan_lines,
    "transcript_exit_plan_mode_calls": len(exit_calls),
    "transcript_plan_marker_in_exit_call": any(PLAN_MARKER in json.dumps(b, ensure_ascii=False) for b in exit_calls),
    "transcript_enter_plan_mode_calls": len(assistant_tool_uses("EnterPlanMode")),
    "transcript_permission_modes": [e.get("permissionMode") for e in entries if e.get("type") == "user" and e.get("permissionMode")],
    "transcript_plan_attachments": [(e.get("attachment") or {}).get("type") for e in entries
                                    if e.get("type") == "attachment" and "plan" in str((e.get("attachment") or {}).get("type"))],
    "transcript_texts": [b["text"] for b in transcript["assistant_blocks"] if b["type"] == "text" and b["text"]],
    "final_screen": final,
    "screen_shows_plan_approval": any(m in final for m in ("Exit plan mode?", "Claude wants to exit plan mode", "Would you like to proceed")),
    "screen_shows_runtime_empty_notice": "empty end_turn" in final or "reasoning without a final answer" in final,
    "router_dropped_exit_plan_mode": any("dropped ExitPlanMode" in line for line in plan_lines),
    "no_model_api_egress_attempt": not any(any(h in e["target"] for h in MODEL_API_HOSTS) for e in EGRESS),
    "owned_processes_stopped": not result.get("owned_still_alive"),
    "live_router_pids_observed_and_unchanged": all(
        result["live_health_before"][p].get("pid")
        and result["live_health_before"][p].get("pid") == result["live_health_after"][p].get("pid")
        for p in result["live_health_before"]),
})
(RUN / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
log("facts: " + json.dumps({k: result[k] for k in (
    "main_post_count", "transcript_exit_plan_mode_calls", "transcript_enter_plan_mode_calls", "screen_shows_plan_approval",
    "screen_shows_runtime_empty_notice", "router_dropped_exit_plan_mode", "owned_processes_stopped",
    "live_router_pids_observed_and_unchanged")}))
LOG.close()
'''
Path(sys.argv[2]).write_text(s, encoding="utf-8", newline="\n")
