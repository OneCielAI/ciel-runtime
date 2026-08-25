#!/usr/bin/env bash
set -euo pipefail

readonly old_pid="${1:-none}"
readonly config_dir="/tmp/ciel-start-plan-e2e-600e230/config"
readonly runtime="/home/mia/.local/share/ciel-runtime/ciel_runtime.py"
readonly ctl="/home/mia/.local/bin/ciel-runtimectl"
readonly router_port="8898"
readonly workspace="/tmp/ciel-start-plan-e2e-600e230/workspace"

if [[ "$old_pid" != "none" ]]; then
  test -r "/proc/$old_pid/cmdline"
  tr '\0' ' ' <"/proc/$old_pid/cmdline" | grep -Fq "$runtime serve"
  tr '\0' '\n' <"/proc/$old_pid/environ" \
    | grep -Fxq "CIEL_RUNTIME_CONFIG_DIR=$config_dir"
  tr '\0' '\n' <"/proc/$old_pid/environ" \
    | grep -Fxq "CIEL_RUNTIME_ROUTER_PORT=$router_port"
fi

CIEL_RUNTIME_CONFIG_DIR="$config_dir" "$ctl" provider zai-start-plan
CIEL_RUNTIME_CONFIG_DIR="$config_dir" "$ctl" model 'glm-5.3[1m]'
CIEL_RUNTIME_CONFIG_DIR="$config_dir" "$ctl" zai-oauth import --profile start-plan

if [[ "$old_pid" != "none" ]]; then
  kill "$old_pid"
  for _ in 1 2 3 4 5; do
    if ! kill -0 "$old_pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  if kill -0 "$old_pid" 2>/dev/null; then
    printf 'ERROR isolated router PID %s did not stop\n' "$old_pid" >&2
    exit 70
  fi
fi

log_path="/tmp/ciel-start-plan-e2e-600e230/router-$(date +%Y%m%dT%H%M%S).log"
cd "$workspace"
CIEL_RUNTIME_CONFIG_DIR="$config_dir" \
CIEL_RUNTIME_ROUTER_PORT="$router_port" \
  nohup python3 "$runtime" serve >"$log_path" 2>&1 </dev/null &
new_pid="$!"

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl --silent --fail "http://127.0.0.1:$router_port/health" >/dev/null; then
    printf 'ROUTER_READY old_pid=%s new_pid=%s port=%s log=%s\n' \
      "$old_pid" "$new_pid" "$router_port" "$log_path"
    exit 0
  fi
  sleep 1
done

printf 'ERROR replacement router did not become ready; pid=%s log=%s\n' \
  "$new_pid" "$log_path" >&2
tail -80 "$log_path" >&2 || true
exit 71
