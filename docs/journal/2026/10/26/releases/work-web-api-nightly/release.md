---
type: Release
title: Work API Nightly
description: Publication and local deployment verification for authenticated Work API and native plan guard integration.
tags: [work, release, nightly]
timestamp: 2026-10-27T01:15:00Z
---

# Commits

- 701ed64e08cf3da94fa3e0092f6135c3fbb0f937: Work API, runtime metadata and native plan guard. [CI](https://github.com/OneCielAI/ciel-runtime/actions/runs/40428466758) and [Publish](https://github.com/OneCielAI/ciel-runtime/actions/runs/40428466778) failed before publication on the facade line-count contract (5032 > 4980); nothing was published ([failure](evidence/first-publish-failure.txt)).
- e6514c80b2ab84e7eba8d32b65a56b093e135236: moves Work HTTP composition to work_http_runtime.py and preserves the existing observer boundary. Facade remains 4980 lines; 303 exact architecture/binding/Work tests passed (42 skipped), also on Python 3.10 ([3.10](evidence/python310-focused.txt)).

# Publication

- [CI 40429074652](https://github.com/OneCielAI/ciel-runtime/actions/runs/40429074652): success, all six jobs ([status](evidence/ci-status.json)).
- [Publish 40429074624](https://github.com/OneCielAI/ciel-runtime/actions/runs/40429074624): success ([status](evidence/publish-status.json)). The nightly dist-tag points at `0.2.51-nightly.20261027-010349.e6514c8`, gitHead e6514c8.
- Registry tarball sha1 and sha512 integrity matched the registry metadata; tarball sha256 8ea55da983d1793c1d1344f249102ffb5e1d49abc21ce6d7b94ee1d82f1694f5, 511 files ([record](evidence/01-published.json)).
- Eleven Work, router and facade source files in the package are byte-identical (line endings normalized) to commit e6514c8 ([comparison](evidence/02-source-identity.txt)).

# Local installation

- New snapshot `~/.local/share/ciel-runtime-e6514c80b2ab84e7eba8d32b65a56b093e135236`: 458 files compared with the package, no mismatch or extra file, Work mod manifest present ([snapshot](evidence/03-snapshot.json)).
- Installed snapshot production HTTP check (isolated config, free port): empty board, four-node add, reorder, stale `expected_version` returned 409 `version_conflict`, `codex:other` stayed empty, help returned 200. Runtime metadata reports the snapshot version, its source fingerprint and configured model with `source: configuration`; no observation is reported as unavailable rather than invented ([record](evidence/05-installed-http.json)). The script's final printed `conflict_status` field reflects the last request and is not the conflict status; the conflict is asserted inside the script and recorded in the JSON.
- Installed snapshot real Claude Code 2.1.310 run passed all seven checks: native plan written, repository write denied, fixture reached idle, original band, external HTTP mutation, idle band refresh and runtime build metadata; one model prompt; the egress sentinel blocked downloads.claude.ai and github.com ([result](evidence/installed-c6473525/result.json), [driver](evidence/06-installed-cli.txt), [before](evidence/installed-c6473525/01-native-plan-and-before-api.png), [after](evidence/installed-c6473525/02-http-idle-refreshed.png)).
- Local switch completed: User `CIEL_RUNTIME_HOME` now points to the new snapshot, the statusline matches the snapshot template, and the previous statusline is backed up ([switch](evidence/07-switch.json)).
- Exact npm global install completed for `0.2.51-nightly.20261027-010349.e6514c8`; npm and `.local` launch shims both report that version ([install](evidence/08-npm-install.txt), [launch paths](evidence/09-launch-paths.txt)).
- Live routers 9505 (PID 77432, fingerprint 72c6bfb456e30cd7) and 9524 (PID 25276, fingerprint f86ee3638ee760f3) were unchanged before and after. No live session was restarted; running sessions keep the code they were launched with until their next restart.

# Source verification

[Work web API verification](/docs/journal/2026/10/26/features/work-web-api/verification.md).
