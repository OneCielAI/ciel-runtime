#!/usr/bin/env bash
set -euo pipefail

readonly config_path="/home/mia/.zcode/cli/config.json"
readonly evidence_dir="/tmp/ciel-zcode-config-capture"
readonly capture_config="${evidence_dir}/capture-config.json"
readonly backup_config="${evidence_dir}/original-config.json"
readonly lock_path="${evidence_dir}/capture.lock"
readonly zcode_bin="/home/mia/.npm-global/bin/zcode"

mkdir -p "${evidence_dir}"
chmod 700 "${evidence_dir}"

exec 9>"${lock_path}"
flock -n 9 || {
  echo "ERROR capture lock is already held" >&2
  exit 70
}

if pgrep -u "$(id -u)" -fa '/zcode\.cjs' >/dev/null; then
  echo "ERROR an existing ZCode runtime is active; refusing to replace its config" >&2
  pgrep -u "$(id -u)" -fa '/zcode\.cjs' >&2
  exit 71
fi

test -f "${config_path}"
test -f "${capture_config}"

install -m 600 "${config_path}" "${backup_config}"
readonly original_hash="$(sha256sum "${backup_config}" | awk '{print $1}')"

restore_config() {
  install -m 600 "${backup_config}" "${config_path}"
  local restored_hash
  restored_hash="$(sha256sum "${config_path}" | awk '{print $1}')"
  echo "RESTORE original_sha256=${original_hash} restored_sha256=${restored_hash}"
  test "${original_hash}" = "${restored_hash}"
}
trap restore_config EXIT INT TERM

install -m 600 "${capture_config}" "${config_path}"
echo "CAPTURE_CONFIG sha256=$(sha256sum "${config_path}" | awk '{print $1}')"

ZCODE_MODEL_RETRY_MAX_RETRIES=0 \
  "${zcode_bin}" --prompt 'Reply only OK' --no-color --verbose
