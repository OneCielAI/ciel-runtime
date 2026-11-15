---
type: implementation-plan
title: Native fork model settings disabled surfaces
description: Minimal beta implementation plan for read-only unsupported model-setting status with inert historical config preservation.
timestamp: 2026-11-14T07:40:00Z
tags: [work, fork, model, settings, unsupported, surfaces]
---

# Scope

Implement only the accepted NO-GO surface contract. Do not add router remap, prompt/token parsing, agent/model pins, second-request fallback, active selectors or config migration/deletion.

Exact reason:

> Per-priority and side-fork model selection is unavailable on this native fork runtime because its first request has no authenticated dispatch identity. Forks inherit the main model; prompt/token parsing and later-request remap are disabled.

Evidence: `work-fork-first-request-bridge-design.json`, SHA-256 prefix `82d6ff19106e1ff4`.

# Exact storage grammar

Historical Work model values may remain under **any** provider config, including providers that are not current:

```text
config.providers[<every provider id>].work_parallel_models = {
  "P0"|"P1"|"P2"|"P3"|"P4"|"P5": <arbitrary stored string>
}
config.providers[<every provider id>].work_side_intake_fork_model = <arbitrary stored string>
```

Historical side aliases/retired values under `config.work_side_intake` are `model`, `comparison_model`, `comparison_enabled`, `context_mode`, `context_percent` (the existing `side_intake_settings.RETIRED`). They remain untouched/inert. `stored_inert` scans the complete `providers` mapping, not only current provider, and is true if any provider has any priority map entry/provider side-fork key or any retired top-level side key is present—regardless of known/unknown id and without catalog lookup, normalization or value projection. Nested object order/unknown ids remain unchanged because no save occurs.

Recognized disabled setting keys are exactly:

```text
work_parallel_p0_model ... work_parallel_p5_model
work_side_intake_fork_model
```

They are recognized only to return the unsupported refusal, never added as writable choices. Retired raw side aliases remain non-settable/hidden; truly other unknown keys keep existing `unknown Work setting` behavior.

# Exact files to announce before scratch edits

Fresh scratch only; rejected `.local/p0-scratch` files are not copied or adapted.

- `ciel_runtime_support/work_live_settings.py` — **sole authority** for unsupported projection, recognized-disabled keys and pre-save refusal.
- `ciel_runtime_support/work_settings_menu.py` — initial settings/menu consumes the shared informational row; no choices/edit action.
- `ciel_runtime_support/recent_menu_text.py` — exact localized label/reason/help keys; remove no unrelated labels and leave no active-selector wording.
- `ciel_runtime_support/work_http_controller.py` — REST settings read/update uses shared authority; exact refusal body/status.
- `ciel_runtime_support/work_web_ui.py` — read-only disabled row, no selector/save control.
- `ciel_runtime_support/work_settings_cli.py` — recognizes disabled keys before generic unknown-key rejection and returns shared refusal; status uses shared projection.
- `ciel_runtime_support/work_mcp_tools.py` is definitively excluded. `UnsupportedNativeForkIdentity` subclasses `ValueError`, so its exact reason already flows through the existing ValueError/OSError catch to `respond(request_id,text,is_error=True)`; no responder/schema change.
- `ciel_runtime_support/work_ctl.py` is excluded: measured generic model-setting set/status surface is `work_settings_cli.py`; no parser/help change is needed in work_ctl.
- focused tests for shared authority, initial/menu, translations/help census, web, REST and ctl plus byte-for-byte config preservation.

`work_parallel_menu.py` is ledger-policy UI and is not the initial settings composition point; no change is planned there unless a concrete call-site census proves otherwise.

No router/provider/request-builder files change. No product apply until review/GO.

# Minimal data contract

```text
fork_model_settings = {
  supported: false,
  capability: "authenticated_first_request_dispatch_identity",
  status: "unsupported_native_fork_identity",
  effective: "inherit",
  reason: <exact reason>,
  stored_inert: boolean
}
```

No `choices`, selected model, update URL/action or writable schema field. `stored_inert=true` means historical values/unknown model ids exist and were left byte-for-byte unchanged; values themselves need not be projected on human surfaces.

# Write behavior

Define shared `UnsupportedNativeForkIdentity(ValueError)` carrying code `unsupported_native_fork_identity`, exact reason and `writes=false`. A shared disabled-key preflight recognizes the seven keys **before** value type, writable/save callback, fingerprint, CAS/version and generic unknown-key checks on every surface. Thus missing/stale fingerprint, non-string value, `save=None`/read-only context and stale settings snapshot still return the exact unsupported refusal without reading a catalog or saving. Truly unrelated unknown keys retain existing unknown behavior.

`work_live_settings.known_keys()` includes the seven recognized-disabled keys only to route to preflight. `_set/settings/_write` all invoke preflight before their current validation/CAS ordering. `work_settings_cli` invokes it before its current known-key and fingerprint/apply flow. HTTP controller invokes it immediately after bounded body/key extraction and before fingerprint validation or operator receipt/effect.

REST `settings_update` body `{key:<disabled key>,value:<any bounded value>}` returns the existing error envelope below with HTTP **422** and `writes:false`. ctl `work set KEY VALUE` recognizes the same key and exits nonzero with the same code/reason. Initial menu/web contain no mutation control, so no value is submitted there.

Exact envelopes:

- REST: HTTP **422**, `{"ok":false,"error":"unsupported_native_fork_identity","message":"<exact reason>","writes":false}`.
- ctl JSON: same object, exit **1**.
- ctl text: exit **1**, no stdout, stderr exactly `work: <exact reason>`.
- MCP `work_live_settings`: current responder supports only `(request_id,text,is_error)`, so exact contract is the exact reason text with `is_error:true`; there is no code metadata or invented JSON payload. `UnsupportedNativeForkIdentity(ValueError)` is caught by the existing ValueError branch; `work_mcp_tools.py` is unchanged.
- initial/menu/web have no mutation action.

It does not normalize, validate against current catalog, delete, rewrite or apply historical values. Existing unrelated settings remain writable through their existing paths.

# Surface behavior

- initial/menu: one informational disabled row; no picker/options/action;
- web: one disabled status panel; no select/save control;
- REST: read-only object above; mutation request returns exact refusal;
- ctl: status prints reason/effective inherit; any set action exits nonzero with exact refusal.

Direct-path/cache text may appear only as secondary informational context and cannot imply model remap availability.

# Verification

1. snapshot config bytes containing full per-provider P0-P5 map, provider side-fork key, retired side keys and unknown model ids; assert `stored_inert` by key presence only;
2. assert seven disabled keys are recognized-and-refused before missing/stale fingerprint, non-string value, save=None/read-only and stale CAS; REST is exactly 422; MCP exact reason+is_error through ValueError catch with no work_mcp_tools edit; unrelated unknown key remains unknown;
3. render/read initial `work_settings_menu`, web, REST and ctl and assert exact unsupported object/reason, zero choices, zero select and zero Apply/settings-update action;
4. attempt every update path and assert exact refusal plus config bytes unchanged;
5. launch/request unit spies assert historical values never reach runtime routing/model selection;
6. grep produced status/logs for historical model values when privacy projection omits them;
7. prove no changes under router/provider/request-builder files;
8. run real initial/menu/web/REST/ctl surfaces or state environmental limitation; screenshots/API/CLI evidence required before completion.

# Proposed review diff shape

A bounded scratch diff should delete active-selector composition and replace it with one shared immutable projection/refusal helper. Surface-specific layers format that object but do not independently infer support. Tests import the frozen scratch package by path and include fail-before/pass-after plus config byte hashes.
