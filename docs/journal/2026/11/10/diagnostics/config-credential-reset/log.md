# Change log

## 2026-11-10
- Published and registry-verified beta `46398fc`, installed it locally, and replayed the incident on the installed code: no credential or provider change.
- Reproduced the exact 2026-11-09 broad test command with the live session's inherited environment redirected into a sandbox: six configured provider credentials became zero and the workspace provider became Codex.
- Bisected the mutation to 12 Codex launch tests and captured the real save stack through `codex_config_loader`.
- Added fail-closed test-profile isolation and credential delta/CAS merge under a cross-process lock.
- Replayed the exact incident command: 243 tests passed, both sandbox configs were unchanged, live router PIDs and the real config mtime were unchanged.
