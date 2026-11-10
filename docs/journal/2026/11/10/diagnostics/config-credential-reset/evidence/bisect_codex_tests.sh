#!/bin/bash
set -u
ROOT=$PWD
UNSET=$(env | grep -o '^CIEL_RUNTIME_[A-Z_0-9]*' | sed 's/^/-u /' | tr '\n' ' ')
i=0
while IFS= read -r name || [ -n "$name" ]; do
  d=$(printf '.local/item2/codex-one/%02d-%s' "$i" "$name")
  env $UNSET APPDATA="$ROOT/$d/AppData/Roaming" LOCALAPPDATA="$ROOT/$d/AppData/Local" USERPROFILE="$ROOT/$d/Home" HOME="$ROOT/$d/Home" PYTHONPATH="$ROOT" CIEL_RUNTIME_TEST_ISOLATED=1 python tests/test_codex_runtime.py "CodexRuntimeTests.$name" > "$d/output.txt" 2>&1
  python - "$d" "$name" <<'PY'
import sys,pathlib
r=pathlib.Path(sys.argv[1]); b=(r/'before.json').read_bytes(); a=(r/'AppData/Roaming/ciel-runtime/config.json').read_bytes()
if a!=b: print('CHANGED',sys.argv[2],len(b),'->',len(a))
PY
  i=$((i+1))
done < .local/item2/codex-tests.txt
