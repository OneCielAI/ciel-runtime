#!/usr/bin/env bash
set -euo pipefail

readonly config_path="/home/mia/.zcode/v2/config.json"
readonly setting_path="/home/mia/.zcode/v2/setting.json"

printf '%s\n' 'START_PLAN_PROVIDER'
jq '
  (.provider["builtin:zai-start-plan"]
    // (.providers[]? | select(.id == "builtin:zai-start-plan")))
  | {
      id,
      kind,
      name,
      enabled,
      apiFormat,
      source,
      endpoints,
      options: (.options // {}
        | del(.apiKey, .token, .secret, .authorization)),
      headers_keys: ((.headers // {}) | keys),
      models,
      modelSupportedFormats,
      defaultKind,
      providerMappings
    }
' "$config_path"

printf '%s\n' 'SETTINGS'
jq '{
  modelProviderFamilyModes,
  modelProviderFamilySelectedKeys,
  providerFamilyDomain
}' "$setting_path"
