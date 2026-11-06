---
type: fix
title: Windows receipt replacement contention
description: Installed-beta failure reproduced with an open reader and corrected using bounded local atomic-replace retries.
tags: [windows, durability, side-intake, beta, regression]
timestamp: 2026-11-06
---

# Observed failure

First published beta `0.2.51-beta.20261106-185033.3a71929` passed CI and installed-code ctl/menu checks. Installed native runf32d625b nevertheless failed four-profile acceptance: primary answer processed/submitted, three shadow proposals durable, fourth shadow `os.replace` raised PermissionError/WinError5 in durable_json_file.py. The pane explicitly reported comparison result not recorded; no successful fourth proposal was invented.

Evidence: [native failure log](evidence/installed-first-beta-failed.txt), [failure screen](evidence/installed-first-beta-comparison-failed.png). This does not prove which reader held the original file at that historical instant.

# Reproduction and correction

A real Windows `path.open('rb')` held on the destination makes `os.replace` raise WinError5 while the original JSON remains readable. The new regression releases that reader after observing the real first denial; the unchanged writer originally failed, corrected writer completes the same fsynced replacement.

The correction reuses the project's bounded atomic-write approach:20attempts,2ms pauses, only PermissionError with Windows error5or32, only the local rename. It never repeats model/MCP/external side effects, never changes permissions, and never deletes the original file to force success. Permanent denial still raises after the bounded attempts; the old destination remains and temporary file is cleaned. Non-Windows denial is not retried. Directory fsync on POSIX is unchanged.

# Verification

- Real Windows reader collision:100/100passed after correction, each observed one real first denial before successful replacement.
- Targeted persistence/side suite:68passed on Python3.11and3.10.
- Four real side-comparison receipt writes were deliberately collided with open readers; all four recovered and primary delivery stayed submitted.
- Actual native production-router run35433734: whole25item Work, four answered profiles, one primary external send, pending0; [corrected screen](evidence/receipt-replace-fixed-profiles.png).
- Full supported groups running: unit/channel/runtime `bdxkrqlec`, router `bv9a4aa4f`. No final gate result inferred while running.

# Additional stress findings and final reader coordination

Writer retries alone were insufficient under sustained readers: four continuous readers exhausted the bounded retry with original generation0 preserved, and10ms-polling readers observed transient PermissionError while all200writes succeeded. Both failed stress reports are retained. A share-delete-handle experiment also failed the real held-reader replacement test and was removed; official CreateFileW sharing documentation alone was not treated as proof of observed replacement behavior.

Final implementation coordinates Windows mailbox/context reads and replacement commits with a distinct `.io.lock`, never the higher-level event/Work transaction lock. An in-process per-path lock prevents competing threads entering Windows byte-lock sleeps; a weak registry releases unused lock entries, and the existing cross-process byte lock remains. POSIX reads keep their original atomic-replace semantics. No model/network operation is performed while holding this I/O lock.

Measured final stress: four continuous readers,200writes,824reads,0errors,1.731seconds; four10msreaders,200writes,76reads,0errors,0.298seconds. Both ended at generation200with no temporary files. Separate-process probe: two Python readers184/276reads and100writes completed without errors, final generation100in6.105seconds. These are bounded local measurements, not a general throughput guarantee. The earlier cross-process-only coordination attempt passed correctness but took42.721seconds with polling readers; it was not accepted as the final implementation. Current focused suite70passed. Full gatebdxkrqlec passed its earlier loaded writer-only source (unit3323/50skip, channel604/80skip, runtime458/19skip), not the final reader coordination. Routerbv9a4aa4f passed1529assertions but the runner exited1during temporary-directory cleanup (WinError145, compaction-scope.json.lock remained); it is retained as a failed command, not a clean gate. Final frozen export receipt-release-4cff8d4f runs biwoqjetv(unit/channel/runtime) and bls4hby9b(router). Native run322efdca uses that exact frozen product and settles all four profiles with whole25item Work. A later test-only mock isolates its Windows retry check from platform locking on Linux; all five persistence tests pass locally with unchanged runtime source. A missing nested receipt regression removes the reader precheck in isolation and observes creation of `missing/receipt.json.io.lock`; the retained precheck raises `FileNotFoundError` without creating the directory or sidecar.

# Architecture gate correction

Frozen unit tests correctly rejected an import cycle: channel_message_repository → durable_json_file → channel_message_repository. The native flow alone did not reveal it. The existing advisory lock primitive was extracted unchanged into durable_file_lock.py; channel_message_repository retains compatibility exports, and the JSON helper imports the lower-level primitive directly. No architecture expectation was relaxed. Focused architecture/lock/side356tests passed with42skips and Ruff passed. Earlier frozen unit failure and clean router1529/channel604/runtime458 results remain recorded. Final validation is rerun on the extracted primitive. Python3.10unit passed3324/52skip. Native frozen run from the acyclic snapshot also passed. The final current-tree run `side-production-5513f87a` passed with the whole 25-item Work board, all four full/recent × main/custom profiles answered, one primary submitted reply, zero pending mail, and resolution before the main turn ended. Its JSON result is retained; the already-inspected acyclic native screenshot remains the decisive visual pane evidence. Current-tree group results are recorded after background gates settle.

# Release boundary

The correction shipped only through the new beta `0.2.51-beta.20261106-201241.a5794b4` at commit `a5794b443b99c64f756c7a9b0830e34ab5bdc336`. The already-installed3a71929snapshot was not edited in place; the corrected registry tarball was integrity/source verified and installed into the new immutable a5794b4snapshot. Stable/latest and nightly remained untouched. Live routers retained their PIDs and source snapshots.
