#!/usr/bin/env bash
set -euo pipefail

readonly endpoint="http://127.0.0.1:8898/v1/messages"
body_path="$(mktemp /tmp/ciel-start-plan-response.XXXXXX.json)"
headers_path="$(mktemp /tmp/ciel-start-plan-headers.XXXXXX.txt)"
readonly body_path headers_path

cleanup() {
  rm -f -- "$body_path" "$headers_path"
}
trap cleanup EXIT INT TERM

http_status="$({
  curl --silent --show-error \
    --max-time 360 \
    --output "$body_path" \
    --dump-header "$headers_path" \
    --write-out '%{http_code}' \
    --request POST "$endpoint" \
    --header 'content-type: application/json' \
    --header 'anthropic-version: 2023-06-01' \
    --header 'x-api-key: ciel-local-probe' \
    --data-binary '{"model":"ciel-runtime-zai-start-plan-glm-5.3-1m","max_tokens":64,"stream":false,"messages":[{"role":"user","content":"Reply exactly CIEL_START_PLAN_OK"}]}'
} || true)"

content_type="$(awk 'BEGIN{IGNORECASE=1} /^content-type:/{sub(/^[^:]+:[[:space:]]*/, ""); sub(/\r$/, ""); value=$0} END{print value}' "$headers_path")"
printf 'HTTP_STATUS=%s\n' "$http_status"
printf 'CONTENT_TYPE=%s\n' "$content_type"
printf '%s\n' 'RESPONSE_BODY'
node - "$body_path" <<'NODE'
const fs = require('fs');
const path = process.argv[2];
const text = fs.readFileSync(path, 'utf8');
try {
  const value = JSON.parse(text);
  console.log(JSON.stringify(value));
} catch {
  console.log(JSON.stringify({ non_json_length: text.length, preview: text.slice(0, 500) }));
}
NODE
