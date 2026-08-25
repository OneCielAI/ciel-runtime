#!/usr/bin/env bash
set -euo pipefail

config_path="/home/mia/.zcode/cli/config.json"
ciel_config_path="/home/mia/.config/ciel-runtime/config.json"
backup_path="$(mktemp /tmp/zcode-start-plan-config.XXXXXX.json)"
modified_path="$(mktemp /tmp/zcode-start-plan-modified.XXXXXX.json)"
original_sha256="$(sha256sum "$config_path" | awk '{print $1}')"

restore_config() {
  cp -- "$backup_path" "$config_path"
  restored_sha256="$(sha256sum "$config_path" | awk '{print $1}')"
  rm -f -- "$backup_path" "$modified_path"
  printf 'original_sha256=%s\nrestored_sha256=%s\n' \
    "$original_sha256" "$restored_sha256"
  test "$original_sha256" = "$restored_sha256"
}
trap restore_config EXIT

if pgrep -af '/zcode( |$)|vendor/zcode.cjs' | grep -vF "$$"; then
  printf 'Refusing to replace the ZCode config while ZCode is running.\n' >&2
  exit 2
fi

cp -- "$config_path" "$backup_path"
jq --slurpfile ciel "$ciel_config_path" \
  '.provider["builtin:zai-start-plan"] = {
     "kind": "openai-compatible",
     "name": "Z.AI Start Plan",
     "options": {
       "apiKey": $ciel[0].providers["zai-start-plan"].api_key,
       "apiKeyRequired": true,
       "baseURL": "https://zcode.z.ai/api/v1/zcode-plan"
     },
     "headers": {},
     "models": {
       "glm-5.3": {"name": "GLM-5.3"}
     }
   } |
   .model.main = "builtin:zai-start-plan/glm-5.3" |
   .model.lite = "builtin:zai-start-plan/glm-5.3"' \
  "$backup_path" >"$modified_path"
cp -- "$modified_path" "$config_path"

timeout 90 /home/mia/.npm-global/bin/zcode \
  --prompt 'Reply exactly OK' \
  --json \
  --verbose \
  --cwd /tmp
