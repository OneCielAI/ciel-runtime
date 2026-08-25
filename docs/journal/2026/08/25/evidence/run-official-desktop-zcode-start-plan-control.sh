#!/usr/bin/env bash
set -euo pipefail

readonly config_path="/home/mia/.zcode/cli/config.json"
readonly desktop_config_path="/home/mia/.zcode/v2/config.json"
readonly desktop_runtime="/home/mia/Applications/ZCode-3.9.1-extracted/squashfs-root/resources/glm/zcode.cjs"
readonly credential_probe="/tmp/probe-official-zcode-start-plan-auth.cjs"
readonly ciel_python_root="/home/mia/.local/share/ciel-runtime"
readonly public_captcha_origin="http://100.95.132.58:42124"
readonly request_capture_script="/tmp/capture-official-zcode-model-request.cjs"
backup_path="$(mktemp /tmp/zcode-desktop-start-plan-backup.XXXXXX.json)"
modified_path="$(mktemp /tmp/zcode-desktop-start-plan-config.XXXXXX.json)"
captcha_path="$(mktemp /tmp/zcode-desktop-start-plan-captcha.XXXXXX.json)"
jwt_path="$(mktemp /tmp/zcode-desktop-start-plan-jwt.XXXXXX)"
request_capture_path="$(mktemp /tmp/zcode-desktop-start-plan-request.XXXXXX.jsonl)"
readonly backup_path modified_path captcha_path jwt_path request_capture_path
readonly original_sha256="$(sha256sum "$config_path" | awk '{print $1}')"

restore_config() {
  install -m 600 "$backup_path" "$config_path"
  restored_sha256="$(sha256sum "$config_path" | awk '{print $1}')"
  if [[ -s "$request_capture_path" ]]; then
    printf '%s\n' 'REQUEST_CAPTURE'
    cat "$request_capture_path"
  fi
  rm -f -- "$backup_path" "$modified_path" "$captcha_path" "$jwt_path" \
    "$request_capture_path"
  printf 'RESTORE original_sha256=%s restored_sha256=%s\n' \
    "$original_sha256" "$restored_sha256"
  test "$original_sha256" = "$restored_sha256"
}
trap restore_config EXIT INT TERM

active_zcode="$({ ps -u "$(id -u)" -o pid=,comm=,args= || true; } \
  | awk '$2 == "node" && $0 ~ /\/zcode\.cjs/ { print }')"
if [[ -n "$active_zcode" ]]; then
  printf 'ERROR an existing ZCode runtime is active; refusing to replace its config\n' >&2
  printf '%s\n' "$active_zcode" >&2
  exit 71
fi

test -f "$desktop_runtime"
test -f "$desktop_config_path"
test -f "$request_capture_script"
test -f "$credential_probe"
install -m 600 "$config_path" "$backup_path"

ZCODE_JWT_OUTPUT_PATH="$jwt_path" \
  node "$credential_probe" "$desktop_runtime"
chmod 600 "$jwt_path"
printf 'START_PLAN_CREDENTIAL sha256_12=%s\n' \
  "$(tr -d '\r\n' <"$jwt_path" | sha256sum | cut -c1-12)"

PYTHONPATH="$ciel_python_root" \
CAPTCHA_OUTPUT_PATH="$captcha_path" \
CAPTCHA_PUBLIC_ORIGIN="$public_captcha_origin" \
python3 - <<'PY'
import json
import os

from ciel_runtime_support.zai_start_plan_captcha import ZaiStartPlanCaptchaBroker


def log(_level: str, message: str) -> None:
    print(message, flush=True)


headers = ZaiStartPlanCaptchaBroker(log=log).headers(
    {
        "zcode_app_version": "3.9.1",
        "zai_captcha_bind_host": "0.0.0.0",
        "zai_captcha_port": 42124,
        "zai_captcha_public_base_url": os.environ["CAPTCHA_PUBLIC_ORIGIN"],
        "zai_captcha_timeout_seconds": 300,
    }
)
output_path = os.environ["CAPTCHA_OUTPUT_PATH"]
with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(headers, handle, separators=(",", ":"))
os.chmod(output_path, 0o600)
print("CAPTCHA_CAPTURED", flush=True)
PY

jq --rawfile jwt "$jwt_path" --slurpfile captcha "$captcha_path" \
  '.provider["builtin:zai-start-plan"] = {
     "kind": "anthropic",
     "name": "Z.AI Start Plan official Desktop control",
     "options": {
       "apiKey": ($jwt | gsub("[\\r\\n]+$"; "")),
       "apiKeyRequired": true,
       "baseURL": "https://zcode.z.ai/api/v1/zcode-plan/anthropic"
     },
     "headers": $captcha[0],
     "models": {
       "GLM-5.3": {"name": "GLM-5.3"}
     }
   } |
   .model.main = "builtin:zai-start-plan/GLM-5.3" |
   .model.lite = "builtin:zai-start-plan/GLM-5.3"' \
  "$backup_path" >"$modified_path"
install -m 600 "$modified_path" "$config_path"

ZCODE_MODEL_RETRY_MAX_RETRIES=0 \
ZCODE_REQUEST_CAPTURE_PATH="$request_capture_path" \
NODE_OPTIONS="--require=$request_capture_script" \
  timeout 120 node "$desktop_runtime" \
  --prompt 'Reply exactly NATIVE_DESKTOP_START_PLAN_OK' \
  --json \
  --verbose \
  --cwd /tmp
