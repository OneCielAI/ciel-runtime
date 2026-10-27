---
type: Release
title: Kobe Work gate nightly
description: Concurrent Edit gates fixed, published and installed as 0.2.51-nightly.20261027-194324.273856d.
tags: [work, nightly, release]
timestamp: 2026-10-27
---
# Release

Commit `273856db23939e9a60c0c8d7ac634cf2051b72d9`; [diagnosis](/docs/journal/2026/10/27/diagnostics/kobe-work-gate/findings.md).

- [CI 40570433719](evidence/ci.json): every job succeeded.
- [Publish 40570433650](evidence/publish.json): succeeded, including published tarball verification.
- [Registry](evidence/published.json): `0.2.51-nightly.20261027-194324.273856d`, exact gitHead, SHA-1/SHA-512 verified, tarball SHA-256 `8847ddfd428dd2eb65f47b531f5aebe9d50f3f4b195c9df8710e2dc12099e0ab`, 513 files.
- [Snapshot install](evidence/snapshot-install.json): 460 files compared, no mismatch/extra; new snapshot `C:/Users/djlov/.local/share/ciel-runtime-273856db23939e9a60c0c8d7ac634cf2051b72d9`.
- Actual installed CLI proof: [result](/docs/journal/2026/10/27/diagnostics/kobe-work-gate/evidence/run-670a7c76/result.json), [screenshot](/docs/journal/2026/10/27/diagnostics/kobe-work-gate/evidence/run-670a7c76/01-concurrent-edits.png). Checks 5/5: waiting-goal handoff, planning Write denied, three concurrent children each made five successful Edits; no child tool error.
- First installed proof `run-dc368f81` was stopped too soon because the driver waited for the parent's end-turn instead of the children's final requests. It is retained as failed fixture evidence, not counted as a passing run. The corrected driver waits for every child terminal request and exits nonzero if a check fails.
- [Local switch](evidence/local-switch.json): HKCU home and generated statusline switched; [npm global](evidence/npm-install.txt) installed the exact same version.

# Live sessions

Routers 9505 PID 35748 and 9524 PID 8544 stayed on `7792965` with fingerprint `11b9fec1d2441894`. No router or live CLI was restarted. Running sessions do not acquire new router/mod code solely from an npm install.
