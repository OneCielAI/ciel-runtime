---
type: Test Evidence
title: Launch probe retry mock-port verification
description: Scoped isolated Python unittest evidence for retry policy and readiness boundaries.
tags: [tests, launch, readiness]
timestamp: 2026-10-29
---
# Commands and isolation

Reproducible command: `python -I -X utf8 C:/Users/djlov/ciel-runtime-wt-work/docs/journal/2026/10/29/fixes/launch-probe-retry/evidence/run_unit_tests.py`. Final result: 21 tests passed (20 new retry tests plus the existing exact adapter policy assertion).

A Python -I -X utf8 subprocess driver creates a fresh temporary HOME, USERPROFILE, APPDATA, LOCALAPPDATA and CIEL_RUNTIME_CONFIG_DIR. Before setting that isolated config path it removes every inherited CIEL_RUNTIME_*, CLAUDE*, ANTHROPIC*, CODEX* and OPENAI* environment variable. Child Python uses -I -B -X utf8, a temporary working directory, and explicitly inserts only the source worktree and its tests directory into sys.path.

The unittest loader selects test_provider_status_retry plus test_cielairouter_provider.CielAiRouterProviderTests.test_adapter_contract_defaults. No full test group or actual network/CLI process is run. The transport ports are mocks. [Raw output](evidence/unit-tests.txt) records individual test names and result.

# Coverage

Initial success; direct and URLError-wrapped timeout followed by success; exhausted two attempts; non-timeout retry failure; default-disabled policy; provider registry opt-in isolation; HTTP 401/403/404/429/500/503, DNS, certificate, refusal and string-only timeout non-retries; headers/body timeout distinction; body retry recovery; large, oversized, malformed and structurally incomplete catalogs; catalog projection failure; context lookup success/failure isolation; existing preview behavior; launch gate still blocked after exhausted timeouts and cleared after actual mocked catalog recovery.

# Limits

These are source-level tests, not evidence of current upstream health, latest screenshot cause, installed behavior, or actual CLI success. Parent handles loopback/CLI validation. [Contract](contract.md).
