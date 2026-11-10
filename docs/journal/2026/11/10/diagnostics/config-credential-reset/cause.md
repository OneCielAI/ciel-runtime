---
type: Incident Analysis
title: A loose test run replaced the shared ciel-runtime config
description: Reproduced cause of every API key and the provider selection disappearing on this machine on 2026-11-09.
timestamp: 2026-11-10T05:10:00Z
tags: [config, credentials, tests, isolation, incident]
---

# What the user saw

At 2026-11-09 23:54Z the user reported that every ciel-runtime on this machine had lost its API keys and provider settings, and each one had to be restarted and re-entered.

# Trigger (from this session's transcript)

At 23:29:17Z a review subagent I started ran this command:

```
python -m unittest discover -s tests -p "test_*runtime.py"
```

- The working directory was the `work-parallel` worktree.
- It ran inside the live Claude session, so it inherited that session's environment, including `CIEL_RUNTIME_CONFIG_DIR=%APPDATA%\ciel-runtime` (the real profile) and `CIEL_RUNTIME_LAUNCH_CWD=C:\Users\djlov\ciel-runtime`.
- It did not set `CIEL_RUNTIME_TEST_ISOLATED`.
- It finished at 23:29:32Z: `Ran 239 tests … OK (skipped=12)`.

# Mechanism (reproduced, not inferred)

1. With the inherited variables, `runtime_paths.CONFIG_DIR` is the real profile. The shared repository is `%APPDATA%\ciel-runtime\config.json`, and the workspace repository is `workspaces\bd5f642b3583\config.json`.
2. Twelve tests in `tests/test_codex_runtime.py` call `ciel_runtime.launch_codex(...)` with `load_config` patched to a minimal fixture, for example `{"current_provider":"codex","providers":{"codex":{…}}}`. They do not patch `save_config`. ([list](evidence/bisect-result.txt))
3. `codex_config_loader` (added in `3fdfcf8b`, 2026-11-04) sees `work_mode` is not `False` and calls the real `save_config` with the fixture plus `work_mode: false` ([stack](evidence/save-stack.txt)).
4. `WorkspaceConfigRepository.save` (unchanged since `2588ee67`, 2026-08-14) rebuilt the shared file as `shared without credentials + credentials found in the config being saved`. A config with no keys therefore deleted every shared credential.
   - The workspace file was replaced with the fixture, so the provider became `codex`.

# Reproduction

[`incident_repro.sh`](evidence/incident_repro.sh):
- copies the real shared config and the workspace `bd5f642b3583` config into a sandbox;
- runs the exact incident command with the same inherited variables, pointed at the sandbox;
- has no `TEST_ISOLATED` flag.

Results on code before the fix (`9d9bfca`), [summary](evidence/repro-before-fix-9d9bfca.json):

- Shared config: 74 credential fields removed ([field names only](evidence/lost-fields-before-fix.txt)). These cover:
  - `api_key` of all 68 providers;
  - `speech.asr`/`tts` keys;
  - the `authorization` of `remote_instructions`, `remote_memory`, `remote_provision` and `transcript_events`.
- Workspace `C:\Users\djlov\ciel-runtime`: `current_provider` changed from `cielairouter` to `codex`.

This matches the reported symptom: keys and provider gone, re-entered by hand.

Bisecting with only `CIEL_RUNTIME_TEST_ISOLATED=1` (sandbox `APPDATA`) also overwrote the shared config. Only `test_codex_runtime.py` did so, through the same 12 tests. That flag alone left `CONFIG_DIR` on the real profile.

# What is not proven

- The live routers' logs from 23:29–23:31Z show no 401 or "missing API key" lines, so the logs do not show which running runtime first noticed the loss.
- The current real `config.json` was last written at 2026-11-10 01:10:37Z, after the user re-entered the keys. It is not the damaged file.
