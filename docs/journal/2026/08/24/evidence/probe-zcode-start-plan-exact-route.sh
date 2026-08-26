#!/usr/bin/env bash
set -euo pipefail

readonly ciel_python_root="/home/mia/.npm-global/lib/node_modules/@oneciel-ai/ciel-runtime"
readonly credential_probe="/tmp/probe-official-zcode-start-plan-auth.cjs"
readonly public_captcha_origin="http://100.95.132.58:42122"
jwt_path="$(mktemp /tmp/zcode-exact-route-jwt.XXXXXX)"
captcha_path="$(mktemp /tmp/zcode-exact-route-captcha.XXXXXX.json)"
readonly jwt_path captcha_path

cleanup() {
  rm -f -- "$jwt_path" "$captcha_path"
}
trap cleanup EXIT INT TERM

ZCODE_JWT_OUTPUT_PATH="$jwt_path" node "$credential_probe"
chmod 600 "$jwt_path"

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
        "zai_captcha_port": 42122,
        "zai_captcha_public_base_url": os.environ["CAPTCHA_PUBLIC_ORIGIN"],
        "zai_captcha_timeout_seconds": 180,
    }
)
with open(os.environ["CAPTCHA_OUTPUT_PATH"], "w", encoding="utf-8") as handle:
    json.dump(headers, handle, separators=(",", ":"))
os.chmod(os.environ["CAPTCHA_OUTPUT_PATH"], 0o600)
print("CAPTCHA_CAPTURED", flush=True)
PY

JWT_PATH="$jwt_path" CAPTCHA_PATH="$captcha_path" python3 - <<'PY'
import json
import os
import platform
import urllib.error
import urllib.request

with open(os.environ["JWT_PATH"], encoding="utf-8") as handle:
    jwt = handle.read().strip()
with open(os.environ["CAPTCHA_PATH"], encoding="utf-8") as handle:
    captcha_headers = json.load(handle)
with open(os.path.expanduser("~/.zcode/v2/telemetry-state.json"), encoding="utf-8") as handle:
    telemetry = json.load(handle)

url = "https://zcode.z.ai/api/v1/zcode-plan/anthropic"
body = json.dumps(
    {
        "model": "glm-5.3",
        "max_tokens": 16,
        "stream": False,
        "messages": [{"role": "user", "content": "Reply with exactly OK."}],
    },
    separators=(",", ":"),
).encode("utf-8")
headers = {
    "Accept": "application/json",
    "Authorization": f"Bearer {jwt}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://zcode.z.ai",
    "User-Agent": "ZCode/3.9.1",
    "X-Client-Language": "en-US",
    "X-Client-Timezone": "America/Chicago",
    "X-Device-Mid": telemetry["deviceMid"],
    "X-Os-Category": "linux",
    "X-Os-Version": platform.release(),
    "X-Platform": "linux-x64",
    "X-Release-Channel": "stable",
    "X-Title": "Z Code@electron",
    "X-ZCode-Agent": "glm",
    "X-ZCode-App-Version": "3.9.1",
    "anthropic-version": "2023-06-01",
    "x-api-key": jwt,
    **captcha_headers,
}
request = urllib.request.Request(url, data=body, headers=headers, method="POST")
try:
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode("utf-8", "replace")
        print(json.dumps({"url": url, "status": response.status, "body": raw[:8000]}))
except urllib.error.HTTPError as error:
    raw = error.read().decode("utf-8", "replace")
    print(json.dumps({"url": url, "status": error.code, "body": raw[:8000]}))
    raise SystemExit(1)
PY
