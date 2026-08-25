#!/usr/bin/env bash
set -euo pipefail

readonly runtime="/home/mia/Applications/ZCode-3.9.1-extracted/squashfs-root/resources/glm/zcode.cjs"
readonly preload="/tmp/capture-official-zcode-oauth.cjs"
capture_path="$(mktemp /tmp/zcode-oauth-safe.XXXXXX.jsonl)"
readonly capture_path

cleanup() {
  printf '%s\n' 'SAFE_OAUTH_TRACE'
  if [[ -s "$capture_path" ]]; then
    cat "$capture_path"
  fi
  rm -f -- "$capture_path"
}
trap cleanup EXIT INT TERM

chmod 600 "$capture_path"
test -f "$runtime"
test -f "$preload"
printf 'SAFE_CAPTURE=%s\n' "$capture_path"

ZCODE_OAUTH_CAPTURE_PATH="$capture_path" \
NODE_OPTIONS="--require=$preload" \
  timeout 360 node "$runtime" login --no-browser --json --verbose
