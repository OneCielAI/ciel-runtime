#!/usr/bin/env bash
set -euo pipefail

readonly config_path="/home/mia/.zcode/cli/config.json"
readonly ciel_config_path="/home/mia/.config/ciel-runtime/config.json"
readonly zcode_bin="/home/mia/.npm-global/bin/zcode"
readonly ciel_python_root="/home/mia/.npm-global/lib/node_modules/@oneciel-ai/ciel-runtime"
readonly public_captcha_origin="http://100.95.132.58:42122"
backup_path="$(mktemp /tmp/zcode-start-plan-control-backup.XXXXXX.json)"
modified_path="$(mktemp /tmp/zcode-start-plan-control-config.XXXXXX.json)"
captcha_path="$(mktemp /tmp/zcode-start-plan-control-captcha.XXXXXX.json)"
readonly backup_path modified_path captcha_path
readonly original_sha256="$(sha256sum "$config_path" | awk '{print $1}')"

restore_config() {
  install -m 600 "$backup_path" "$config_path"
  restored_sha256="$(sha256sum "$config_path" | awk '{print $1}')"
  rm -f -- "$backup_path" "$modified_path" "$captcha_path"
  printf 'RESTORE original_sha256=%s restored_sha256=%s\n' \
    "$original_sha256" "$restored_sha256"
  test "$original_sha256" = "$restored_sha256"
}
trap restore_config EXIT INT TERM

if pgrep -u "$(id -u)" -fa '/zcode\.cjs' >/dev/null; then
  printf 'ERROR an existing ZCode runtime is active; refusing to replace its config\n' >&2
  pgrep -u "$(id -u)" -fa '/zcode\.cjs' >&2
  exit 71
fi

install -m 600 "$config_path" "$backup_path"

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
        "zcode_app_version": "0.16.3",
        "zai_captcha_bind_host": "0.0.0.0",
        "zai_captcha_port": 42122,
        "zai_captcha_public_base_url": os.environ["CAPTCHA_PUBLIC_ORIGIN"],
        "zai_captcha_timeout_seconds": 180,
    }
)
output_path = os.environ["CAPTCHA_OUTPUT_PATH"]
with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(headers, handle, separators=(",", ":"))
os.chmod(output_path, 0o600)
print("CAPTCHA_CAPTURED", flush=True)
PY

jq --slurpfile ciel "$ciel_config_path" --slurpfile captcha "$captcha_path" \
  '.provider["builtin:zai-start-plan"] = {
     "kind": "anthropic",
     "name": "Z.AI Start Plan control",
     "options": {
       "apiKey": $ciel[0].providers["zai-start-plan"].api_key,
       "apiKeyRequired": true,
       "baseURL": "https://zcode.z.ai/api/v1/zcode-plan/anthropic"
     },
     "headers": $captcha[0],
     "models": {
       "glm-5.3": {"name": "GLM-5.3"}
     }
   } |
   .model.main = "builtin:zai-start-plan/glm-5.3" |
   .model.lite = "builtin:zai-start-plan/glm-5.3"' \
  "$backup_path" >"$modified_path"
install -m 600 "$modified_path" "$config_path"

ZCODE_MODEL_RETRY_MAX_RETRIES=0 \
  timeout 120 "$zcode_bin" \
  --prompt 'Reply exactly OK' \
  --json \
  --verbose \
  --cwd /tmp
