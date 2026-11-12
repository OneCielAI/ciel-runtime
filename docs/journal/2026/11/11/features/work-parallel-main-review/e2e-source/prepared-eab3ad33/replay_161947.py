"""Offline replay of the eab3ad33 acceptance analyzers on run 161947 evidence.
Extracts only the analyzer functions from the runner (ast) and runs them on
preserved files. Launches nothing and writes nothing."""
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

runner = Path(sys.argv[1])
run_dir = Path(sys.argv[2])
base = Path(sys.argv[3])
result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
tree = ast.parse(runner.read_text(encoding="utf-8"))
wanted = {"attempt_ref", "iso_epoch", "review_attempts", "raw_prompt_submits",
          "stop_during_pending_prompt", "_strings", "presentations", "wake_messages"}
consts = {"SUBMIT_MARK", "DEBUG_LEASE", "TEXT_LEASE", "WAKE_TEXT", "STOP_BLOCK", "REVIEW_SUBMIT_TEXT"}
body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in wanted)
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in consts for t in n.targets))]
module = ast.Module(body=body, type_ignores=[])


class Gate:
    wake_calls = []  # 161947 predates the passive review_wake observer


claude = Path(result["preserved_claude_config"])
workspace = Path(result["preserved_workspace"])
transcript = claude / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(workspace)) / (result["session"] + ".jsonl")


def transcript_rows():
    rows = []
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


ns = {"hashlib": hashlib, "json": json, "re": re, "BASE": base, "gate": Gate,
      "wake_observations": result["wake_observations"], "transcript_rows": transcript_rows}
exec(compile(module, str(runner), "exec"), ns)
final = json.loads((run_dir / "07-final-state.json").read_text(encoding="utf-8"))
fin = final.get("finalization") or final.get("state", {}).get("finalization") or {}
reviews = fin.get("reviews") or {}
raw = ns["raw_prompt_submits"](reviews)
shown = ns["presentations"]()
terminal = {"queued", "appended", "uncertain"}
stuck = sorted(k for k, m in result["wake_observations"].items()
               if any(x[0] == "submitting" for x in m) and not any(x[0] in terminal for x in m))
print("reviews in final state:", len(reviews))
for call in raw["calls"]:
    print("raw submit", call["process"], call["at"], call["lease"], call["attribution"], call["identity"])
print("non-review submits:", len(raw["non_review_submits"]))
print("duplicates:", raw["duplicates"], "unattributed:", len(raw["unattributed"]))
print("stuck_submitting:", stuck)
print("stop_during_pending_prompt:", ns["stop_during_pending_prompt"](raw, shown))
print("presentations:", json.dumps(shown))
print("no_same_identity_raw_submit_twice:", not raw["duplicates"] and not raw["unattributed"])
print("no_wake_left_submitting:", not stuck)
print("wake_delivery_terminal:", all(any(x[0] in terminal for x in result["wake_observations"].get(k, []))
                                      for k in reviews))
