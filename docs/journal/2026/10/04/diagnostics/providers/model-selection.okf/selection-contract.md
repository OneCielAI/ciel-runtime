---
type: Diagnostic
title: Authoritative model selection consistency
description: Prevent selection success for configured IDs absent from the authoritative catalog.
timestamp: 2026-10-04
tags: [models, catalog, regression]
---

# Evidence

The model picker merges configured IDs with cached catalog IDs, while launch validation requires membership in the authoritative catalog. The selection controller previously persisted arbitrary IDs before this validation.

The specific reported `ciel-Opus 5.5` exclusion reason is not established. Capability filters are retained; no guessed alias mapping is introduced.

# CLI verification

Isolated local HTTP catalog, dummy credential, temporary configuration and subprocess CLI invocation (no live session launch):

- `cli models`: lists only `valid-model-a` and `valid-model-b`.
- `cli model stale-model`: exit 2, explicit rejection; saved current model remains `valid-model-a`. Does not claim the cache was cleared.
- `cli model valid-model-b`: exit 0, successful save confirmed by reading the isolated configuration.

# Regression verification

- unit: 1,719 tests, OK (50 skipped).
- router: 1,175 tests, OK.
- channel: 454 tests, OK (80 skipped).
- runtime: 362 tests, OK (19 skipped).
- Modified-file Ruff and git diff whitespace check passed.
- Live router 9611 health returned HTTP 200.
- Actual CLI command output is captured in `cli-results.json`; this is CLI text evidence, not a TUI screenshot.

No commit, publication, or user configuration modification has been performed for this fix. The actual service's availability of the reported model ID remains unverified.
