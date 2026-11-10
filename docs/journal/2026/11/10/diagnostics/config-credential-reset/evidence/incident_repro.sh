#!/bin/bash
# Re-run the incident command with the same inherited Ciel environment shape,
# but with every profile path redirected into a sandbox copy.
# usage: incident_repro.sh <code-tree> <out-dir>
set -u
TREE=$(cd "$1" && pwd -W)
OUT=$2
REAL="$APPDATA/ciel-runtime"
rm -rf "$OUT"
mkdir -p "$OUT/cfg/workspaces/bd5f642b3583" "$OUT/cfg/router-instances/9524-bd5f642b3583"
cp "$REAL/config.json" "$OUT/cfg/config.json"
cp "$REAL/workspaces/bd5f642b3583/config.json" "$OUT/cfg/workspaces/bd5f642b3583/config.json"
cp "$OUT/cfg/config.json" "$OUT/shared-before.json"
cp "$OUT/cfg/workspaces/bd5f642b3583/config.json" "$OUT/workspace-before.json"
CFG=$(cd "$OUT/cfg" && pwd -W)
# Inherited by the 17:29 subagent from the live Claude session (values from this
# session's environment), minus the redirected profile paths. TEST_ISOLATED is
# deliberately absent, as in the original command.
(
  cd "$TREE" &&
  env -u CIEL_RUNTIME_TEST_ISOLATED \
    CIEL_RUNTIME_CONFIG_DIR="$CFG" \
    CIEL_RUNTIME_STATE_DIR="$CFG\\router-instances\\9524-bd5f642b3583" \
    CIEL_RUNTIME_WORKSPACE_STATE_DIR="$CFG\\workspaces\\bd5f642b3583" \
    CIEL_RUNTIME_LAUNCH_CWD='C:\Users\djlov\ciel-runtime' \
    python -m unittest discover -s tests -p "test_*runtime.py"
) > "$OUT/output.txt" 2>&1
echo "rc=$?" >> "$OUT/output.txt"
python - "$OUT" <<'PY'
import json, sys, pathlib
r = pathlib.Path(sys.argv[1])
def creds(d):
    return sum(1 for v in d.get("providers", {}).values()
               if isinstance(v, dict) and (v.get("api_key") or v.get("api_keys")))
sb, sa = json.loads((r/"shared-before.json").read_text()), json.loads((r/"cfg/config.json").read_text())
wb, wa = json.loads((r/"workspace-before.json").read_text()), json.loads((r/"cfg/workspaces/bd5f642b3583/config.json").read_text())
summary = {
    "shared_provider_credentials_before": creds(sb), "shared_provider_credentials_after": creds(sa),
    "shared_unchanged": sb == sa,
    "workspace_provider_before": wb.get("current_provider"), "workspace_provider_after": wa.get("current_provider"),
    "workspace_work_mode_after": wa.get("work_mode"),
}
(r/"summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
PY
tail -4 "$OUT/output.txt"
