---
type: Diagnostic
title: Background task bypasses Work wait reassessment
description: Exact current-session Stop evidence and bounded reassessment correction.
tags: [work, stop, continuation]
timestamp: 2026-11-05
---

# Observed current session

Transcript f129ddaa-21b3-4513-a939-9e349231c6c8 at 2026-11-05T07:53:25.410Z records `Ciel Work auto continue waits: 1 background task(s) still running will wake this session, and the next stop checks unfinished items again.` Following stop_hook_summary has hookErrors=[], preventedContinuation=false. Thus this Stop was intentionally allowed by the Work mod; not an inferred model-only omission.

Router9524 logs source d40b895 at startup. Native Work lineage marker explicitly maps claude:f129ddaa... to claude:a1911ed6-0d04-4e21-a3c9-f1ecf5ca72cc (SHA2568d346ec...), matching current board version776. No newest-session guess or board migration performed.

# Verified defect and correction

The Stop callback returned early whenever background_tasks existed and actionable was zero, before bounded blocked recheck and discovery. Existing n586 and n598 were explicitly waiting for publication/connector, so no claim they were runnable at that historical stop. However a background task suppressed any reassessment of those mixed dependencies.

Remove broad background early return. In the no-runnable branch, request one reassessment per exact session+board version for waiting records as well as blocked records. Preserve all waits, approval gates and the no-progress bound. Tell the model not to poll or duplicate a running task, not to clear approval holds, and to split only independently authorized investigation from dependent execution. An unchanged board parks after one check; this is not an endless continuation loop.

# Repeated current-session evidence

Same background-wait notice appears at2026-11-05T07:50:05.320Z (two tasks) and07:53:25.410Z (one task), plus earlier instances. Both most recent user-reported stops have exact transcript evidence. Prior response framing this only as model stopping was incomplete.

# Verification

34 scripted-host mod/architecture tests pass, including unchanged-state bound, waiting and blocked backgrounds, native session/turn/compact invalidation and approval boundaries. Native engine suite:87 pass,7 fail; new wait/background reassessment and all changed Stop assertions pass. Baseline dcbf7be was independently copied into an owned temporary mod and run under the same native engine: identical seven named failures reproduced (`.local/native-stop-baseline/output.txt`). Thus no added native-suite failures; no full-suite-green claim. Actual owned CLI2.1.318 probe `.local/side-busy-9214bb15/result.json`: first model1793866008.896, Stop status read1793866033.952, second model1793866033.997 without new input, confirming real end-turn->automatic reassessment. Prior9977504 CI/Publish succeeded, actual PATH nightly075431 installed and smoke passed. Extended owned native probe `.local/side-busy-ff513742/result.json`: first end-turn automatically starts second model; after second end-turn, unchanged wait state produces no third model through the extra observation window.107 focused tests pass1skip. Current live session not restarted and loaded code not overwritten; package installation cannot retroactively replace the old router process.
