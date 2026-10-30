---
type: Verification
title: Work resume continuity tests
description: Evidence for exact parked-handoff linking and fail-closed isolation.
tags: [work, resume, tests]
timestamp: 2026-10-30
---
# Results

- Owned baseline reproduction: parent board1 unfinished node/executing; changed child identity reads empty board while parent remains intact.
- Exact lineage tests cover persistence across native-record removal, mismatch/dead/reused identity, ambiguity, pre-existing split boards, corrupt/missing source, unrelated session isolation and lock behavior.
- Existing unrelated Windows atomic-replace test may fail under concurrent antivirus/file locking; it does not authorize weakening the store lock.

# Current integrated result

93focused tests pass across lineage, MCP, HTTP, statusline and compact liveness. Original62teststore-focusedrunhad1WindowsconcurrentatomicreplacePermissionError; retained, not marked green. Ordinary and already-linked snapshot reads remain lock-free; first-lineage establishment is a transaction. Observer callbacks moved outside lineage/store locks, with reentrant observer regression.

# Harness corrections

First two actualCLI attempts accidentally inherited `work_mode:false` from the SSE-only template, so API mutation correctly returned409. Next attempt seeded board successfully but waited for root title although compact UI deliberately displays current leaf PRESERVED-STEP. Harness assertions corrected; actual restart/handoff acceptance still pending.

NativeCLI fixture also needed the existing package-aware launch shim for a subprocess started with Python isolated mode. Direct `python -I ciel_runtime.py` excluded the package root and failed before any restart. This is a harness invocation correction, not a Work fix.

# Cross-process persistence

94integrated tests now pass, including a fresh Python process reopening the alias after native records were removed. Same version/node payload is preserved, not copied to a new plan. Actual native restart proof uses queue_session_restart addressed only to the fixture-owned launcher/instance, because test isolation collapses instance paths and the global CLI selector did not discover that fixture.

# Native restart observed

The owned native CLI did restart to a new PID with the same sessionIDand displayed the samePRESERVED-STEPboard after the restart notice. Subsequent idle left-key did not start parked background mode; latest harness starts an actual loopback model turn before backgrounding. No live user session was restarted.

Retention and architecture23/23tests pass independently.

# Accepted and unaccepted native paths

Native restart→same-session resume board preservation was observed in two owned runs (same node IDs/version and visible current step). Further typed input through that restarted console watcher did not reach the fixture model; this does not establish a product input defect. Native handoff is now exercised independently from a fresh owned console, not silently counted as restart success.

# Actual native handoff acceptance

Fresh owned interactiveCLIstartedarealturn,parkedintoanewnativebgidentity,returnedwithEscanddisplayedthesamePRESERVED-STEPboard. HTTPreadsofparentandchildhadsamenodeIDs/version;childmutationwasvisiblefromparent;unrelatedidentityremainedempty.0foregroundhits/824samples;livehealthunchanged. [Result](evidence/native-handoff-visible/result.json), [continued Work screenshot](evidence/native-handoff-visible/03-native-handoff-board-preserved.png). Same-IDnative restart observed separately; donotattributeitschecktothefreshhandoffrun.

# Reviewer regression evidence

Before correction, the two new A→B→C and empty-nonzero-version cases failed in the97casefocusedrun. Failure output is retained underreleaseevidence/review-repro-integrated-tests.*. Immediateedgevalidation+recursivecanonicalresolutioncorrectbothwithoutrelaxingPID/key/cwd/domainmatching. Addedintermediatesplitboardandduplicatejobnegatives.
