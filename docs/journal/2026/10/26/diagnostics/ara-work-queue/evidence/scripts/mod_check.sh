#!/usr/bin/env bash
# Validate and test the ciel-work mod in a scrubbed environment (temp HOME/config; no inherited Ciel/Claude/provider vars).
set -u
MOD=/c/Users/djlov/ciel-runtime-wt-work/ciel_runtime_support/claude_mods/ciel-work
OUT=${1:?output file}
T=$(mktemp -d)
mkdir -p "$T/home" "$T/appdata" "$T/localappdata" "$T/claude" "$T/tmp"
CLAUDE_BIN=$(command -v claude)
run() {
  env -i PATH="$PATH" SystemRoot="${SYSTEMROOT:-C:\\Windows}" COMSPEC="${COMSPEC:-C:\\Windows\\system32\\cmd.exe}" \
    HOME="$T/home" USERPROFILE="$(cygpath -w "$T/home")" APPDATA="$(cygpath -w "$T/appdata")" \
    LOCALAPPDATA="$(cygpath -w "$T/localappdata")" CLAUDE_CONFIG_DIR="$(cygpath -w "$T/claude")" \
    TEMP="$(cygpath -w "$T/tmp")" TMP="$(cygpath -w "$T/tmp")" DISABLE_AUTOUPDATER=1 \
    "$CLAUDE_BIN" "$@"
}
{
  echo "claude: $(run --version 2>&1)"
  echo "== plugin validate --strict"
  run plugin validate --strict "$(cygpath -w "$MOD")" 2>&1; echo "exit=$?"
  echo "== plugin test"
  run plugin test "$(cygpath -w "$MOD")" 2>&1; echo "exit=$?"
} > "$OUT"
rm -rf "$T"
