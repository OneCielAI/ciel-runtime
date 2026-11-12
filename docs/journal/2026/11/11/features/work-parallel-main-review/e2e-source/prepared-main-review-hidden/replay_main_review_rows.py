"""Replay the reworked-item Main review check on run 213818 (defect visible) and
on synthetic fixed-state texts. Read-only; launches nothing."""
import ast
import json
import re
import sys
from pathlib import Path

runner, run = Path(sys.argv[1]), Path(sys.argv[2])
tree = ast.parse(runner.read_text(encoding="utf-8"))
body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in ("terminal_main_review", "web_main_review"))
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "TERM_GROUP" for t in n.targets))]
ns = {"re": re}
exec(compile(ast.Module(body=body, type_ignores=[]), str(runner), "exec"), ns)
result = json.loads((run / "result.json").read_text(encoding="utf-8"))
item = result["items"]["Failing implementation then rework"]
term = ns["terminal_main_review"]((run / "07-finalized.txt").read_text(encoding="utf-8"), item)
web = {m: ns["web_main_review"](result["screenshots"]["final_web"][m]["text"], item) for m in ("desktop", "mobile")}
ok = lambda t, w: t.get("visible") is True and not t.get("item_rows") and all(
    x.get("visible") is True and not x.get("item_rows") for x in w.values())
print("213818 item", item)
print("213818 terminal", json.dumps(term, ensure_ascii=False))
print("213818 web", json.dumps(web, ensure_ascii=False))
print("213818 reworked_item_absent_from_final_main_review:", ok(term, web))

fixed_term = "\n".join(["  ⎿  ciel-work: Ciel Work parallel", "     FORKS: 0 confirmed", "     DEVICE: slots 0/6",
                        "     Policy: enabled · ledger v40", "     ENDED (5)",
                        "       n4 → work-auto-x · P1 worker · lease done", "     Settings: x"])
fixed_web = "Main review (0)\nA finished worker result waits here.\nNo worker result is awaiting main review.\n" \
            "Processing now · confirmed native forks (0)\nn4\nnative idle"
scrolled = "\n".join(["       Rework · n4 Failing", "     ENDED (5)", "       n4 → work-auto-x"])
checks = {
    "defect_213818_flagged": not ok(term, web) and len(term.get("item_rows") or []) == 1
        and all(len(x["item_rows"]) == 1 for x in web.values()),
    "fixed_state_passes": ok(ns["terminal_main_review"](fixed_term, item),
                             {"desktop": ns["web_main_review"](fixed_web, item)}),
    "ended_group_rows_not_counted": ns["terminal_main_review"](fixed_term, item)["item_rows"] == [],
    "scrolled_off_status_not_visible": ns["terminal_main_review"](scrolled, item)["visible"] is False,
    "missing_web_section_not_visible": ns["web_main_review"]("Processing now", item)["visible"] is False,
}
print(json.dumps(checks, indent=1))
sys.exit(0 if all(checks.values()) else 1)
