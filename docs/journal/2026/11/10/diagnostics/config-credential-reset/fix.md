---
type: Implementation
title: Test processes cannot bind a real profile; saves cannot delete shared credentials
description: Two independent defenses for the 2026-11-09 credential reset.
timestamp: 2026-11-10T05:10:00Z
tags: [config, credentials, tests, isolation]
---

# 1. A test process never uses an inherited profile (`runtime_paths.py`)

- **Detection.** A process counts as a test when either:
  - `CIEL_RUNTIME_TEST_ISOLATED` is set; or
  - its own entrypoint is unittest or pytest (`__main__` is `unittest.__main__`, a `test_*.py` file, or pytest is loaded).
- **Detection never fires on imports alone.** `test_isolation_guard.loose_test_entrypoint` deliberately ignores a merely imported `unittest`, so a production router or CLI is never moved to a temporary profile.
- **Which directory a test may use.** It accepts a `CIEL_RUNTIME_CONFIG_DIR` only when that directory lies under the system temp root; the group runner's directory does. Otherwise the process creates its own `ciel-runtime-test-*` directory, which is removed at exit. The process also gets a free router port.
- **Child processes.** The chosen directory and the isolation flag are exported to them. The live session's `CIEL_RUNTIME_STATE_DIR` and `CIEL_RUNTIME_WORKSPACE_STATE_DIR` are removed.
- **Effect on the incident.** This covers the incident command exactly: no flag, a live `CONFIG_DIR` inherited from the session.

# 2. Shared credentials survive partial and stale saves (`config_repository.py`)

`WorkspaceConfigRepository.save` no longer rebuilds the shared credentials from the config being saved.

- **Missing is not delete.** A credential absent from the saved config is kept.
- **Unchanged is not a write.** A credential equal to the value this repository loaded is skipped, so a stale full config does not undo a newer rotation made by another runtime.
- **Conflicting edits fail.** If this caller changed a credential and the shared file now holds a different newer value, `StaleCredentialWriteError` is raised and nothing is written.
- **Locking.** The merge runs under a cross-process lock beside the shared file (`config.json.credentials.lock`) and reads the file fresh, without the cache.
- **Write only on change.** The shared file is written only when credentials actually changed.

An explicit credential change is still written: a new value, or `""` set deliberately to clear one.
