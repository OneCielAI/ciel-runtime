---
type: Verification
title: Credential reset defenses — deterministic evidence
description: Exact before/after reproduction, focused regressions and live-state checks.
timestamp: 2026-11-10T05:20:00Z
tags: [config, credentials, tests, verification]
---

# Exact incident command, same inherited environment shape

[`incident_repro.sh`](evidence/incident_repro.sh) copies the real shared/workspace configs into a sandbox and supplies the inherited live-session variables, redirected to that sandbox. It then runs exactly:

```
python -m unittest discover -s tests -p "test_*runtime.py"
```

| Code | Tests | Shared provider credentials | Shared file | Workspace provider |
|---|---:|---:|---|---|
| Before fix `9d9bfca` | 243 OK (12 skipped) | 6 → 0 | changed | `cielairouter` → `codex` |
| Working tree after fix | 243 OK (12 skipped) | 6 → 6 | byte-identical | `cielairouter` unchanged |

- [Before summary](evidence/repro-before-fix-9d9bfca.json) / [output](evidence/repro-before-fix-test-output.txt)
- [After summary](evidence/repro-after-fix.json) / [output](evidence/repro-after-fix-test-output.txt)

The after-fix run did not change either sandbox config file. On the real machine, router ports 9505/9524 stayed on PIDs 76496/82844 and the real shared config mtime stayed `2026-11-09 19:10:37.283137300 -0600`.

# Focused tests

| Suite | Result |
|---|---|
| `test_config_repository.py` | 11 passed |
| `test_runtime_paths.py` | 7 passed |
| `test_unisolated_test_guard.py` | 8 passed |
| `test_credential_management.py` | 13 passed |
| `test_api_key_rotation.py` | 46 passed |
| Ruff, changed Python files | clean |
| `git diff --check` | clean |

New deterministic cases verify:

- a partial/minimal config cannot delete any existing shared credential;
- a stale full config cannot undo another runtime's newer credential rotation;
- two conflicting credential edits fail closed;
- a credential previously loaded by the caller can still be explicitly deleted (the existing API-key clear contract);
- `TEST_ISOLATED=1` with no config directory gets a process temp profile;
- `TEST_ISOLATED=1` with an inherited non-temp profile rejects it;
- a loose `test_*.py` entrypoint with no flag rejects an inherited live profile;
- a production entrypoint retains its configured profile.

# Full regression

All four groups ran with every inherited `CIEL_RUNTIME_*` variable removed, through `scripts/run_test_group.py` ([output](evidence/full-tests/all-groups.txt)).

| Group | Result |
|---|---|
| unit | OK, 50 skipped |
| router | 1552 tests OK |
| channel | 607 tests OK, 80 skipped |
| runtime | 458 tests OK, 19 skipped |

Before and after the run:

- Ports 9505 and 9524 stayed on PIDs 76496 and 82844.
- The real `%APPDATA%\ciel-runtime\config.json` mtime stayed `2026-11-09 19:10:37.283137300 -0600`.

# Release and installed-package proof

- CI and npm Publish passed for commit `46398fc`.
- **Registry.** Beta `0.2.51-beta.20261110-062933.46398fc` has a matching git head and SHA512. All 519 source files match the commit ([registry](evidence/registry-verification-46398fc.json)). `latest` remains `0.2.51`; `nightly` is unchanged.
- **Install.**
  - The immutable snapshot and the global npm install match on all 573 files.
  - User `CIEL_RUNTIME_HOME` points to the snapshot.
  - No live router or session was restarted.
- **Incident replay on the installed code.** The exact incident command ran with the installed runtime code (repository tests, since the tarball ships no tests):
  - 243 tests passed;
  - the shared credentials stayed at 6;
  - the workspace provider stayed `cielairouter`;
  - both configs were unchanged ([summary](evidence/repro-installed-46398fc.json)).
- **Sandbox cleanup.** All sandbox copies of the real config were deleted after each run.
