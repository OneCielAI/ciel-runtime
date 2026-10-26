---
type: Release
title: Work hints, triage and queue - nightly and local release
description: Nightly publication and local installation of the Work usage hints, request triage and goal queue, including the start-retry fix found by the installed-snapshot run.
tags: [release, nightly, work, local-install]
timestamp: 2026-10-26T18:30:00Z
---
# Request

"빠진부분 보강해서 개선 테스트후 로컬 나이트리 배포" - change: [work-mode-hints](../../features/work-mode-hints/verification.md).

# First publication: e1826009

- CI 40370921168 and Publish 40370921328 succeeded on `e1826009db5e9bffa50355f7edb3e12709a50bdd` ([check](evidence/package-vs-commit.txt)).
- Registry: `nightly` = `0.2.51-nightly.20261026-175255.e182600`, gitHead matches, `latest` stays `0.2.51`; sha1 and sha512 of the downloaded tarball match ([published.json](evidence/published.json)); the five changed source files are byte-equal to the commit.
- New snapshot `C:\Users\djlov\.local\share\ciel-runtime-e1826009db5e9bffa50355f7edb3e12709a50bdd`: 454 files compared, no mismatch ([install-snapshot.json](evidence/install-snapshot.json)).
- Real CLI against that snapshot, run-111efba6: 8 of 10. The start notice and the empty-plan line were missing because the router was not yet connected on the mod's first load. Diagnosis and fix: [start on a mod's first load](../../features/work-mode-hints/verification.md). This build was not switched in locally.

# Second publication: start retry

Recorded below once published.
