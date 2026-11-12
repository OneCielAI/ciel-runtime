"""Check the installed runner's installed-path guard (references_installed) and
marker non-overlap for a given installed path, using the runner's own functions
(ast). Nothing launches. Usage: <runner> <installed path>."""
import ast
import json
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text(encoding="utf-8")
inst = Path(sys.argv[2])
mod = ast.parse(src)
body = [n for n in mod.body if (isinstance(n, ast.FunctionDef) and n.name in ("_path_text", "references_installed"))
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "INSTALLED_TEXT" for t in n.targets))]
ns = {"INSTALLED": inst}
exec(compile(ast.Module(body=body, type_ignores=[]), "runner", "exec"), ns)
r, p = ns["references_installed"], ns["_path_text"]
back = str(inst)
fwd_upper = back.replace("\\", "/").replace(inst.name, inst.name.upper()).replace("Users", "USERS")
other = str(inst.with_name("ciel-runtime-8e08b97bf29878a9cf7302c5e945c0307675aa06"))
snap = r"C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\.local\owned-main-review-snapshot-20261112-120000"
base = r"C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\.local\owned-main-review-20261112-120000"
scratch = r"C:\Users\djlov\AppData\Local\Temp\ciel-owned-main-review-abcd"
checks = {
    "live_session_backslash": r('py -3 "' + back + '\\ciel_runtime.py" cli'),
    "live_session_forward_slash_upper": r("python " + fwd_upper + "/ciel_runtime.py"),
    "owned_snapshot_not_matched": not r('py -3 "' + snap + '\\ciel_runtime.py" cli'),
    "other_install_not_matched": not r('py -3 "' + other + '\\ciel_runtime.py"'),
    "empty_not_matched": not r(None) and not r(""),
    "markers_do_not_overlap": all(p(m) not in ns["INSTALLED_TEXT"] and ns["INSTALLED_TEXT"] not in p(m)
                                  for m in (snap, base, scratch)),
}
print("installed", back)
print(json.dumps(checks, indent=1))
print("all", all(checks.values()))
sys.exit(0 if all(checks.values()) else 1)
