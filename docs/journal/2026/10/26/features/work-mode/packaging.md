---
type: Verification
title: Work mod packaging
description: package.json ships the Claude Work mod the launcher points at; focused test and npm pack dry-run.
tags: [work, packaging, npm, verification]
timestamp: 2026-10-26T00:00:00Z
---

# Scope

`package.json` `files` now lists `ciel_runtime_support/claude_mods/`. The launcher adds `--plugin-dir` only when `ciel-work/.claude-plugin/plugin.json` exists, so a package without the directory would silently run Claude without Work mode.

# Verified evidence

- [Focused test output](evidence/packaging/packaging-test.txt): `tests/test_work_mode_packaging.py`, 4 tests passed through the isolated runner `docs/journal/2026/10/25/fixes/teammate-model-inheritance/evidence/run_focused.py`. Checks the `files` entry, that `CLAUDE_MOD_DIR` is inside it, that the manifest, `hooks/hooks.json` (module `./register.js`) and `hooks/register.js` (registers `work-mode`) exist, and that the real mod dir yields `--plugin-dir`.
- [npm pack summary](evidence/packaging/npm-pack-dry-run-summary.txt), [full JSON](evidence/packaging/npm-pack-dry-run.json), [stderr (empty)](evidence/packaging/npm-pack-dry-run.stderr.txt), produced by [npm_pack_dry_run.py](evidence/packaging/npm_pack_dry_run.py): `npm pack --dry-run --json --ignore-scripts`, npm 11.12.1, scrubbed `CIEL_RUNTIME_*`/`CLAUDE*`/`CODEX*`/`OPENAI*`/`ANTHROPIC*`/`npm_config_*` variables, temporary HOME, cache and npmrc files. Exit 0; 506 entries; manifest, hooks.json and register.js present.

# Observations and limits

The directory entry also ships `ciel-work/tests/work.test.ts` and `ciel-work/tsconfig.json`; not changed in this task. No tarball was written, nothing was installed or deployed, and no full test group or live process was touched. Installed-package Claude E2E is not covered here.
