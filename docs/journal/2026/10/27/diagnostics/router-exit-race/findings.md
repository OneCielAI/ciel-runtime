---
type: Diagnostic
title: Router restart after CLI exit
description: An in-flight client supervisor could spawn a replacement router after lifetime cleanup had finished.
tags: [router, lifecycle, shutdown, concurrency]
timestamp: 2026-10-27
---
# Report boundary

The user observes that exit and relaunch sometimes time out, whereas a restart works. This is a hypothesis about the failing session, not proof that an orphan router caused that particular timeout. This record covers one independently reproduced exit defect. The separate self-update launch-lease chain and upstream Base URL timeout are not claimed fixed here.

# Confirmed defect

`RouterLifetimeRunner.run` set its supervisor stop event but neither passed cancellation into an active probe nor waited for the supervisor thread. A probe already running could finish its down-confirmation loop and call `start_router` after `release_client` and `stop_if_idle` had completed.

An [owned process proof](evidence/scripts/prove_exit.py) drives the real lifecycle classes around a short-lived Python CLI and a TCP listener child; health is controlled to force the race. It terminates only children it created.

- [Installed 7792965 baseline](evidence/baseline.json): `release -> stop -> spawn`; client lease gone, owned router PID 49832 still alive and port listening after the CLI returns. The proof cleans that child up afterwards.
- [Fixed source](evidence/fixed.json): `release -> stop`; no new child, no listener after return.

# Change

- Supervisor probes accept their lifetime stop event and stop after in-flight health checks or during the down-confirmation wait.
- The runner sets the event and joins its own supervisor before releasing the client lease and stopping an idle router. An already-started restart settles before final cleanup.
- Client release failure no longer skips the idle-stop attempt.
- Shared active clients and foreign-router ownership checks are unchanged. No live router was stopped to test this fix.

# Verification

- Three new deterministic regressions: cancelled in-flight probe, join of already-started restart, release failure still invokes idle stop.
- 69 targeted lifecycle/selector tests passed (13 subtests), 88 unrelated cases deselected. Lint passed.
- Actual CLI exit/relaunch verification is tracked separately; this process proof does not establish Codex-specific behavior.

# Actual launcher verification

[Run launch-source-153351](evidence/launch-source-153351/result.json): actual `ciel_runtime.py cli --ca-runtime claude` with Work on, isolated HOME/config/free port and outbound sentinel. Seven checks pass: first CLI ready/router up; `/exit` returns the launcher and closes the TCP listener; the next launch is ready; second `/exit` again returns and closes the listener. Screenshots: [first exit](evidence/launch-source-153351/02-first-exit.png), [relaunch](evidence/launch-source-153351/03-relaunch.png), [second exit](evidence/launch-source-153351/04-second-exit.png). No model generation was needed. Both live router PIDs were unchanged.

The earlier `launch-source-153204` checked failed health rather than a refused TCP connection; only the later run is the decisive port-closure proof. Full-launch native CLI was 2.1.310; the separate Work-gate/model inheritance proofs used 2.1.311.

# Codex boundary

[Actual Codex 0.158.0 app-server](evidence/codex-source-recheck/summary.json): Work instructions appear only when enabled; `work_plan_set`, `work_execute`, `work_status` run through native MCP metadata; the turn completes, a second conversation stays empty and missing identity is rejected. This did not reproduce a Work-mode-specific startup crash. The first fixture failed on its obsolete instruction substring (`Ciel Work mode is on.`); changing only the expected substring to the current hybrid wording allowed the actual protocol run. No product behavior was changed for that fixture mismatch.

# Native Codex TUI exit proof

[Codex TUI run codex-launch-source-155433](evidence/codex-launch-source-155433/result.json): actual launcher selected Codex 0.164.0 with Work on; first entry, `/exit`, closed TCP listener, second entry, second `/exit`, closed TCP listener all pass (7/7). [Relaunch screenshot](evidence/codex-launch-source-155433/03-relaunch.png), [second exit](evidence/codex-launch-source-155433/04-second-exit.png).

Earlier Codex TUI fixture runs are retained as failed verification attempts. They typed `/exit` while native hook-review dialogs were still open. The final fixture waits past the banner and skips those dialogs with Escape (does not trust hooks or change native permission policy) on both entries. Those earlier attempts do not establish a product crash or successful exit. The app-server proof separately executes Work tools.

# Installed proof

[Published snapshot run launch-installed-155420](evidence/launch-installed-155420/result.json): seven exit/relaunch/port-close checks passed using snapshot 3160858. Live 9505 PID 62984 and 9524 PID 8544 unchanged throughout.
