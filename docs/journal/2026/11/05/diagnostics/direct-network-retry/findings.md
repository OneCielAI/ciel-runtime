---
type: diagnostic
title: Direct network retry wire evidence
description: Distinguish CLI direct gateway traffic from router native-protocol forwarding before changing retries.
tags: [cielairouter, retry, wire, diagnostic]
timestamp: 2026-11-05
---

# Confirmed source paths

runtime_launch.py486-492 resolves launch data path and passes it to environment projection. claude_environment.py637-648 assigns CLAUDE_CODE_MAX_RETRIES=configured_retry_limit only for direct launches and zero for routed launches. launch_data_path.py explicitly documents direct model HTTP bypassing local request interception. upstream_retry.py517 names router-to-provider native protocol forwarding upstream_direct_request; that log label alone is NOT proof of CLI bypass.

# Owned actual CLI wire measurement

Two actual installed binaries tested against owned loopback HTTP fixtures, not mocked openers:

| CLI | First response | Message HTTP hits | Result |
|---|---|---:|---|
| 2.1.310 and 2.1.318 | empty HTTP502 | 2 | ok |
| 2.1.310 and 2.1.318 | connection closed before response | 2 | ok |
| 2.1.310 and 2.1.318 | HTTP200 transient SSE error | 2 | ok |
| 2.1.310 and 2.1.318 | HTTP200 empty SSE | 1 | API Error: Stream ended without receiving any events |

Controls: MAX_RETRIES=3, RETRY_WATCHDOG=0, NONSTREAMING_TIMEOUT_RETRIES=0, DISABLE_NONSTREAMING_FALLBACK=1. Bare print-mode probes are not yet proof for interactive/resume mode. Empty stream is a confirmed unhandled class, not yet proven to explain the user's reported HTTP502.

# Live local observation

Router9524 launch log explicitly has ANTHROPIC_BASE_URL=http://127.0.0.1:9524. Its process loads immutable a2e92bf. At13:53:46 four router-origin HTTP502 records reached local POST /v1/messages. Earlier same operation timeout retries spent1/3,2/3,3/3 at approximately300-second intervals. This establishes that particular session's routed path, not all CielAiRouter sessions' path. No restart or configuration change performed.

# Grant sandbox read-only observation

SAMA router8872 health: account aapw-a20u22, ganti-ai workspace, version0c3b177. Recent runtime observations show routed /v1/messages HTTP200 and ongoing tools. A snapshot of161 events contains25 work_node_update calls,19 tool errors,0 network error events. Screenshot14 supplies repeated id-only input and NO CHANGE errors; recent API intentionally excludes raw tool arguments, so input stripping vs generation is not independently resolved. Direct router.log read was refused; no access bypass attempted. SSE worker connected/heartbeat observed with recent EOF reconnection history; this does not establish side mailbox consumption.

# Reproduced router native-protocol transport gap

Owned real HTTP server consumes the request then closes without sending response headers. Production native passthrough before change: upstream_hits=1, RemoteDisconnected, downstream_bytes=0. Native direct CLI retries the same class with MAX_RETRIES=3; routed CLI has native retries disabled, while runtime's retry policy excluded this typed disconnect. The live transcript separately records WinError10054/ConnectionResetError failures. This identifies a concrete mismatch, not proof that every502 shares this cause.

Source correction admits only ConnectionResetError/RemoteDisconnected (including typed URLError.reason) raised by open_once into the existing request-scoped retry budget. Read-time failures, generic transport strings, broken pipe and TLS EOF remain excluded. Already exposed output prevents retry. This can duplicate upstream computation/billing just like the existing open-timeout policy; no claim is made that a consumed request was unsent.

After correction the same real HTTP fixture reaches upstream_hits=2, returns211 bytes and one downstream200 response.48 focused retry/integration/initial-stream tests pass; a permanent real-socket regression verifies exact byte preservation and one downstream success.

# Direct empty-stream native mod proof

Native MAX_RETRIES and nonstreaming fallback controls were set explicitly in owned wire probes; the measured MAX_RETRIES3 configuration still fails HTTP200 empty SSE. The official environment-variable reference was fetched, but current excerpts do not independently establish those exact control descriptions. Installed plugin-authoring types StreamNext12987 explicitly allow a second next(e) to open a fresh model request. Actual mod observation (not guessed exception catching) sees one opaque engine chunk, step result answer empty/toolUses empty/stopReason null/usage null, and session.messages last assistant exactly `API Error: Stream ended without receiving any events`; turn.complete reason error. The error is returned, not thrown to the hook.

A dedicated ciel-network-retry mod now only on explicit direct-path launch can reopen this exact empty-event error, bounded by configured retries. It refuses typed model chunks, non-null stop/usage, tool calls, unknown/auth error text, interruption or newer user input. Work/SSE settings do not control whether this recovery mod loads. No prompt is injected. Installed CLI owned actual proof: before mod1hit exit1; with mod2hits exit0 stdoutok; persistent empty stream stops4hits with retries3. Plugin validate passes (author/icon catalog warnings only). Launch/restart env includes explicit direct flags and removes them on routed fallback. Subsequent launch suite50passes include WorkOFF and restart fallback.

# Recovery guard revalidation

After adding a guard requiring a newly appended assistant error (not a stale transcript error), actual CLI run `empty-observer-cf0a70cf` still makes two message POSTs and returns exit0 / `ok`. Normal run `508be920` makes one POST; persistent-empty run `23c5e9c7` makes four HTTP200 zero-byte SSE responses and exits1 at retries3; disabled run `ac0c298b` makes one HTTP200 zero-byte SSE response and exits1. Wire response timestamps and lengths are saved in those result.json files.

Native-only (`MOD_RETRIES=0`) runs `294b97dd` (auth) and `8d093cc7` (partial SSE) both make two POSTs and return `ok`, matching enabled observations. These cases do NOT establish that the dedicated mod initiated a retry or that native CLI never replays partial streams. The native mechanism has not been identified.

Scripted generator guards cover text, tool, thinking, tool input, stop, non-null usage, unknown error, cancellation, disabled setting, newer user input, and stale transcript error: one open, zero mod retry logs each. Production launch harness explicitly verifies plugin counts 1/1/0 and direct flags 1/1/0 across gateway1, gateway2, router fallback. 49 launch tests and two recovery tests pass. Plugin validate passes with author/icon catalog warnings; focused Ruff and git diff checks pass.

Full isolated unit gate ran3263 tests with one failure at test_model_compact_safeguard.py176. All13 tests in that file pass separately; cause not established, so this is not a full-suite pass. A fresh isolated full gate is running. The installed a178aba release contains only the earlier routed-disconnect fix, not this uncommitted direct-empty-stream mod. No live sessions restarted.

Native retry isolation additionally sets `CLAUDE_CODE_MAX_RETRIES=0`: partial-SSE runs `2ad90a84` (mod3) and `c7be3c90` (mod0) both make one POST, return the received `ok`, and receive523 bytes without message_stop; auth runs `48ecfeea` (mod3) and `6d00b4a9` (mod0) both make one POST and exit1 with401. This verifies the mod adds no retry in these owned cases; it is not proof that native retry-enabled partial handling is safe. Production native retry setting remains unchanged.

Second full unit gate `bqwbuccjd` failed at test_runtime_compaction_binding.py263, expecting awaiting_shrink but observing awaiting_checkpoint (3263 tests,51 skipped). The full file then passed24 tests alone. This differs from the first gate failure; root cause remains unknown. Do not release from these failed full gates. Latest launch suite includes WorkOFF test and passes50; architecture267 pass42skip.

A real HTTP truncation variant sends523 SSE bytes after advertising4619, then closes. With native MAX_RETRIES0, enabled-mod run `e4393327` and disabled-mod run `fbd01f93` each issue exactly one message POST (no dedicated replay). Their native terminal results differ: enabled returned received `ok`/exit0, disabled returned ECONNRESET/exit1. A single pair does not identify the reason for this discrepancy; preserve it rather than claiming identical native error behavior.

# Interactive recovery evidence

Owned actual Claude2.1.310 interactive ConPTY run `interactive-network-0fb6c3fb` uses isolated config/workspace, gateway-style AUTH_TOKEN, native MAX_RETRIES0, direct-mod retries3, no Work/SSE plugin or MCP server. Socket submits one fixture request. Wire records first HTTP200 empty SSE, second HTTP200 valid797-byte SSE; transcript and actual terminal show exact error, `retry1/3`, then `ok`. No recovery prompt is injected. Evidence preserved in `evidence/interactive/`: harness.py, result.json, rawscreen.txt and screen.png (actual ConPTY VT replay viewed). The first API_KEY probe stopped at native key-confirmation UI with zero model requests; it is not a recovery failure.

Six true-truncation runs all make one message POST with native retries0; native result alternates ok/ECONNRESET in both enabled and disabled modes, so the terminal-result discrepancy is not exclusively an enabled-mod observation. Combined compaction suite369passed; diagnostic full unit gate `bd10q8pqx` running with state/log capture on either known failure. It does not change production compaction logic or relax assertions.

Package dry-run must target the worktree explicitly after primary cwd changed. `npm pack --dry-run --json ./.local/tmp/work-parallel` includes loader plus plugin manifest/hooks/register and developer tsconfig (five files). No package publication performed.

# Remaining

Diagnose failed full gates before release. Diagnostic full gate bd10q8pqx passed3264/51skip, but uninstrumented b4ktqwckw failed3264/51skip at safeguard176 again; result-callback-only failure capture btechotv6 is pending. No failed assertion is relaxed. Channel602pass80skip and runtime458pass19skip. Latest engine API review documents session.messages agent deny results; mod now explicitly skips custom recovery on a non-array transcript before/after next(e), with scripted denied/denied-after cases passing. Owned interactive revalidation69ca29fb again produces2POSTs/ok and preserved terminal evidence. Interactive fixture proves recovery in native terminal but is not a production gateway fault injection. Do not change production retry policy from empty-stream evidence alone or replay after tool/output exposure. JSON mod rendering and selection-description defects tracked separately; no claim they are fixed.
