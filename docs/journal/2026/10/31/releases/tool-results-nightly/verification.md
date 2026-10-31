---
type: Release Verification
title: Tool result status and polling preservation
description: Preserve legitimate repeated execution checks, neutral tool result semantics, and long-output head and tail in chat projections.
tags: [release, tools, polling, projection]
timestamp: 2026-10-31T00:00:00Z
---

# Scope

Three verified defects at5f1b588: repeated health checks dropped after two results; pending/running and exit2 misclassified as success; non-error results acquired completion/no-repeat instructions; ordinary results lost everything after12000characters. The third supplied restart-module link was unrelated.

# Repair

Shell execution is not treated as a mutation solely by tool name or repeated arguments. Explicit messaging side-effect TTL protection is retained; known completed file mutation dedupe remains independent. Tool return wrappers preserve the error flag and body without inventing task completion or adding no-repeat user-role instructions. Tool output projection retains head and tail, marks the exact omission, and leaves persisted-output envelopes intact. Generic context/input truncation is not changed.

# Verification

Baseline pending/running/exit2 projection assertions fail; actual converter baseline loses failure tail and inserts completion instructions. Source159 targeted tests ran:158 passed,1 skipped by existing condition. Actual loopback status endpoint was queried four times (running,running,completed,completed); no call was dropped. A real isolated process emitted24045characters and exited2; both Ollama and OpenAI projections retained the initial marker, final FAILED summary and Exit code2 without a success claim. Persisted-output exact preservation and long cached-read tail are covered. No live sessions are restarted and no private incident captures are staged.

# Release

Pending CI, npm exact commit and installed verification.
