---
type: verification
title: Reviewed side processing release validation
description: Final native router evidence, retained failed regression and beta-only release boundary.
tags: [side-intake, beta, work, context, verification]
timestamp: 2026-11-06
---

# Native execution evidence

The owned CLI fixtures run Claude Code 2.1.310 under normal native permissions, with explicit fixture MCP allowlists. Main and side inference use an owned loopback provider, not the authorized live combo. Counts and usage are fixture values: these runs establish routing and execution boundaries, not real tokenizer accuracy, cache hits, billing savings or semantic classification quality.

- `de4b15df`: production Claude HTTP handler, native side model override, recent context, whole Work board25, no pending requests, answer submitted before main finishes. Only one available conversation boundary selected100%; it was not falsely displayed as30%.
- `12fe0ee8`: production handler, four answered shadow profiles, one primary connector send, no shadow Work application or reply.
- `afaf7460`: production handler with an explicitly synthetic completed-history corpus and whole Work board25. Primary recent and both recent shadows exclude `OLDER_MARKER`, retain `RECENT_MARKER`, and select32024/77123 fixture tokens (41.5233%) at a complete turn boundary. Both full shadows preserve the exact parent system/tools/message prefix. All four shadows receive the exact same frozen Work projection, including24queued goals; tool_choice is none, results are proposals only, and there is exactly one external connector send. Routine original is absent from captured main requests.
- `122334d3`: real browser reply dispatcher, native main advice → answer → resumed side → final reply. Original receipt now displays `Main handoff: answered`; follow-up is `not_required`, both submissions are recorded, one Work root and zero pending mail remain.

[Machine-readable assertions](evidence/reviewed-native-verification.json), [labeled full/recent + model profiles](evidence/release-comparison-profiles.png), [answered handoff screen](evidence/reviewed-handoff-answered.png).

A further native run `04772971` verifies the final pane labels context mode, model and selected percentage for every shadow arm. Menu run `settings-description-b77febd8` verified all24visible rows and descriptions, including comparison model. The actual Windows ctl wrapper rerun listed26settings and passed seven list/get/set calls against an owned temporary configuration. Earlier `settings-result-final.json` was a23row historical run; it is not used as proof of the final24row menu. [Final menu result](evidence/release-menu-result.json), [comparison setting screenshot](evidence/release-settings-comparison.png), [ctl wrapper results](evidence/release-ctl-wrapper.json).

The screenshot renderer replays actual captured ConPTY output; it does not fabricate UI elements. Native request bodies/config homes stay under local scratch and are not published with this journal.

# Reproduced and corrected receipt defect

The board acknowledgment changed the side decision's handoff state, but the independent durable inbox receipt still said `requested`. A regression asserted `answered` and failed (`answered != requested`). The host now synchronizes only the handoff field under the per-event lock, preserves reply delivery state, and repairs the receipt from the acknowledged board intent on pickup after a crash. The host sends the repaired receipt to the pane even after actor state is gone. The actual browser/native round trip above verified the visible result. No approval, reply retry or Work selection policy changed.

# Oversized capture freshness correction

A new regression proved that rejecting an oversized parent request left `latest.json` pointing to the older, smaller request: both new and reused actors silently prepared that stale history as full. The two negative assertions failed before the fix. Capture now durably writes an unavailable marker for the new observation; primary preparation and new comparison plans fail closed until a valid current capture arrives. Existing in-flight actor snapshots are not rewritten or deleted. The regression covers new/reused actors, comparison freeze refusal and recovery on the next valid capture. Side/UI107tests and source lint passed after this fix.

# Gate history, including failures

- `broo9p7wb`: unit3321, skipped50, one failure at `test_model_compact_safeguard.SafeguardTests.test_high_fresh_metadata:21`; subsequent groups did not run because the command used `&&`. This is a failed gate, not completion evidence. Isolated safeguard class subsequently passed13tests; that alone does not explain the failure.
- The safeguard investigation reproduced a fixture-isolation defect: a previously started in-process transcript poller uses the same replacement global scanner as the test, causing an immediate busy refusal and an empty cached transcript path. A real owned-poller reproduction failed the unchanged line21assertion10/10before the fixture-only cleanup and passed100/100after; focused repetitions passed370tests. The guard stops/asserts exit of only the already-loaded test-process delivery service before replacing the scanner. It is included in the frozen export and its3319unit pass; that pass is not an unfixed rerun. Production thresholds and assertions are unchanged. Original broo9p7wb lacked state, so its exact interleaving is not retrospectively proven. Detailed evidence: `docs/journal/2026/11/06/diagnostics/safeguard-fixture/verification.okf`.
- `biovadxgk`: router1528passed; channel604passed/80skipped; runtime458passed/19skipped. Later handoff changes require a new final snapshot/gate.
- Side-focused65passed before retiring the unused native-selector prototype; handoff/native-hook44passed after the repair.
- Product/test/script Ruff and compilation passed. An initial repository-wide Ruff included untracked `.local` probe files and failed; the release source check excludes local scratch, not product defects. Eleven real test formatting violations were corrected.
- Unused native selector and comment-only observer were moved to local scratch, not silently discarded or packaged as working paths.
- Native plugin validation passed with the existing missing-icon catalog warning; directory listing acceptance is not claimed.

# Explicit unsupported or unavailable cases

- `full/recent` require the routed path. Direct gateway does not capture parent requests and fails explicitly rather than silently changing mode.
- Recent context requires the provider Messages count endpoint. No character/token estimate fallback.
- Parent context16MiB ceiling fails closed; an oversized snapshot is never called full. Provider-hosted execution tools are refused to preserve native child permission boundaries.
- Recent/custom-model branches explicitly omit parent reasoning blocks; preserved-thinking acceptance across all real providers is not established by these fixtures.
- The authorized `API KEYS - SSE` combo returned404/model_not_found, `no_executable_targets`, pool_size0. No alternate model was selected. Real cache/cost measurements remain unavailable.
- External reply binding infrastructure is verified against owned connectors. No live Walkie reply contract is connected here, so live Walkie delivery is not claimed.
- Exact historical startup screenshot command and `wg-mw4dy55o-1` correlation remain unlocated. UTF8 subprocess handling and fail-closed MCP read reconnect are verified independently, not claimed as proof of those historical causes.
- Comparison result receipts are durable; the in-memory scheduling queue and native actor transcript mapping are not claimed to resume automatically after a host restart. Pending primary requests retain lease fencing and reconciliation.

# Frozen release tree

`release-tree-3b6a7f24` is an index-only export of8171files, excluding untracked scratch and the preexisting unrelated `tests/test_side_intake_inbox.py` edit. Clean-export CI Ruff and metadata checks passed. Gate task `b8ifkihzt` under Python3.11.6passed3319unit tests/50skipped in364.353seconds. Task `b2q1o3rcg` passed router1529, channel604/80skipped and runtime458/19skipped on the same immutable export with all inherited Ciel/Claude/provider environment removed. Source hashes were unchanged while the gates ran. The clean export excludes one unrelated inbox test and the retired prototype test, so its unit count is two lower than the earlier3321test working-tree gate. The later stale-capture regression belongs to the router group. Native CLI run `5369022f` imports this exact product tree and loads its packaged mod:25Work items, zero pending, four answered read-only profiles and one submitted primary external reply. Outcomes of still-running gates are not inferred from the native run.

# Python minimum compatibility correction

CI also runs the unit group under Python3.10. The new CP949 encoding tests initially failed locally on actual Python3.10.6 because they patched `subprocess.locale`, which that version does not expose. After switching to version-compatible patching, one test still failed with Windows error87 because it passed an empty child environment. The test now preserves the real child environment while forcing the CP949 default decoder path. Encoding/compatibility tests passed under Python3.10.6, 3.13.5 and 3.14.2. Product code did not change. The corrected test-only export `release-tree-ba8d9c8c` ran Python3.10 unit task `b25rwl9oq`:3319tests,52skips, five Muse fixture errors and one remote-provision fixture failure. Both separately reproduced on3.10; the Muse child passed an empty environment and the provisioning authorization test omitted SystemRoot. A minimal direct child probe returned Windows error87 without SystemRoot and exit0with it. Both fixtures now pass only that required Windows variable without inheriting provider credentials; their41focused tests passed. Runtime code and expectations are unchanged. Final corrected export `release-tree-29a7bee2` passed Python3.10unit task `b0naq1gmq`:3319tests,52skips,376.525seconds. All58tests in the four edited fixture modules also passed under release Python3.11.6from this export. The prior frozen gates remain evidence for unchanged runtime source; this focused run covers the four later test edits.

# Release boundary

Only beta is authorized for this feature. Stable/latest and nightly tags/branches must remain unchanged. No live session or router may be restarted. Publication, installation, exact registry integrity and installed-code smoke must be recorded separately before any end-to-end release claim.
