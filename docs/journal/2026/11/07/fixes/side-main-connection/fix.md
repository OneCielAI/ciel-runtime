---
type: fix
title: Side actor on the main session connection
description: "The configured side combo _API_KEYS-SSE-1 had no executable targets, and the previous build injected raw SSE into main. Primary side processing now always inherits the main session model, provider connection and authentication."
tags: [side-intake, sse, model-routing, cielairouter, walkie]
timestamp: 2026-11-07
---

# Observed

- Walkie workspace config (`aae7db361c7f/config.json`) had `work_side_intake.model = "_API_KEYS-SSE-1"`. The main session uses `cielairouter` / `_Walkie_Opus55-Sol61-Fast`.
- A request through the router to the SSE combo returned HTTP 404 `model_not_found`, "Combo has no executable targets", with `poolSize 0`, `attempted 0`, `terminalReason no_executable_targets` and recovery `switch-combo` ([evidence](evidence/api-keys-sse.json)). The `API KEYS - SSE` copies returned the same error.
- The same router and token on the main combo returned 200 `pong` ([evidence](evidence/main-probe.json)).
- Build `95421d0` then sent the raw SSE to the main session as a plugin prompt (receipt `e55f54c82f35`). The `main_fallbacks` poll loop could submit it again while main was busy (user screenshot showed two identical blocks). The receipt was settled to `queued` by hand so no more resubmissions happen.
- Live inbox: 39 pending events (06:30–12:23) were quarantined as `turn_error` on the SSE combo's fingerprint.

# Fix (`f37cbac`)

- `runtime_settings` always returns `model = inherit` for primary side processing. A persisted side model is ignored, and `set_value` rejects anything other than `inherit`. Only the read-only comparison model can still name a catalog model.
- The launch environment no longer drops `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` because of a legacy primary side model.
- Removed raw main-session injection from the mod: `fallbackToMain`, `fallbackPrompt`, and the `main_fallbacks` resubmit loop.
- An unavailable model is no longer quarantined. The event becomes retryable immediately (`use_main_connection`).
- A legacy `main_fallback: pending` row is reclaimed by the side instead of being given to main.
- The side menu offers only `inherit` for the primary model. The setting description is updated in en/ko/ja/zh.

# Verification

- Focused isolated tests (all `CIEL_RUNTIME_*` unset) pass: 431 tests OK, 42 skipped. Groups: side intake MCP/mod/mailbox/inbox, side outcome, Work mod gate, settings menu, side model menu, launch data path, Claude environment, architecture contracts.
- Installed `0.2.51-local.20261107-f37cbac` into the snapshot `C:\Users\djlov\.local\share\ciel-runtime-f37cbac…`. User `CIEL_RUNTIME_HOME` points there, and `--version` prints the local version.
- Replaying claims on a copy of the live inbox with the installed logic yields `model inherit` and makes all 39 retained events claimable.
- **Not yet verified in the live app.** The running Walkie session still runs `95421d0`. It has to be relaunched by the user; Ciel never restarts it automatically.

# Citations

- Prior side-model fix: [side model context isolation](../side-model-context-isolation/fix.md)
