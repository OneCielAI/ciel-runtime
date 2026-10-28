---
type: investigation
title: Shared launcher stdout and Codex router startup verification
description: Local supported production launches pass; original stdout owner and remote router failure remain unproven. Safe startup provenance added.
tags: [launcher, codex, startup, diagnostics, n167, n228]
timestamp: 2026-10-26T23:42:00Z
---

# Scope and source evidence

Only n167 and n228. Read the codex-stdout-not-terminal and router-start-1347817 journal indexes, findings, logs and evidence first. Their conclusions remain unchanged: native Codex emits the tty guard with redirected stdout; no evidence ties the original window to a Ciel path. The .238 router log and installed identity are unavailable. No external host operation was performed.

# Source change

`ciel_runtime_support/router_process_lifecycle.py` now logs launcher version, source fingerprint, executable, entrypoint and log path before spawning. On the existing 30-second health timeout it polls only the launch-owned process handle and records PID, exit code, child-exited versus health-timeout state and elapsed time in the error/log. It does not echo environment, provider payloads, arbitrary log contents or credentials. It does not change launch stdio, routing, wait timing, retry policy or termination behavior. App-server, injector and terminal-proxy source files were not changed by this task.

# Verified execution

Real installed `C:/Users/djlov/.local/bin/ciel-runtime.cmd`, with its supported CIEL_RUNTIME_HOME_OVERRIDE pointing at source or installed nightly; no launch monkeypatch, no listener-probe widening, no TEST_ISOLATED in production-console runs. Fresh stripped provider/Ciel variables, HOME, USERPROFILE, APPDATA, LOCALAPPDATA, CODEX_HOME, config, workspace and random ports. Provider is a loopback synthetic CielAiRouter; no real credential or external model call. Native Codex npm CLI 0.158.0 and wrapper-selected Python 3.14.2.

| Run | Result | Evidence |
| --- | --- | --- |
| Changed source Codex TUI + app-server | Router health, authenticated app-server ready, TUI ConPTY, typed prompt reached fixture, reply rendered | [result](evidence/run-changed-remote-233529/result.json), [terminal PNG](evidence/run-changed-remote-233529/02-reply.png), [screen text](evidence/run-changed-remote-233529/screen-02-reply.txt), [router log](evidence/run-changed-remote-233529/router-combined.log) |
| Installed nightly Codex TUI + app-server | Same pass; health version 0.2.51-nightly.20261027-031627.0555b6c, fingerprint 11b9fec1d2441894 | [result](evidence/run-installed-remote-233555/result.json), [terminal PNG](evidence/run-installed-remote-233555/02-reply.png) |
| Changed source plain Codex | Router health, ConPTY, typed prompt/reply pass | [result](evidence/run-changed-plain-233623/result.json), [terminal PNG](evidence/run-changed-plain-233623/02-reply.png) |
| Installed nightly plain Codex | Same pass | [result](evidence/run-installed-plain-233835/result.json), [terminal PNG](evidence/run-installed-plain-233835/02-reply.png) |
| Native Codex stdout redirected | Exact stdout-not-terminal message and exit 1 without Ciel | [result](evidence/run-native-tty-negative-233829/result.json), [terminal PNG](evidence/run-native-tty-negative-233829/01-native-stdout-not-terminal.png) |
| Changed source synthetic router failure | Invalid numeric bind 127.0.0.256 caused local child exit 1; terminal error includes version, source, PID, exit, log path | [result](evidence/run-changed-failure-233707/result.json), [terminal PNG](evidence/run-changed-failure-233707/01-startup-failure.png), [log](evidence/run-changed-failure-233707/router-combined.log) |

The synthetic bind failure is diagnostic verification only, NOT evidence of the .238 cause. Existing healthy router startup times were 1.5 seconds (changed remote/plain and installed remote) and 2.1 seconds (installed plain). The native screenshot message is reproducible under deliberate redirection but not in these supported console launches.

Focused isolated startup tests: [8 tests pass](evidence/focused-startup-tests.txt), including exited/alive child timeout provenance and non-disclosure sentinel. [Harness](evidence/owned_launch.py), [focused runner](evidence/focused_startup_tests.py), [machine-readable run summary](evidence/verification-summary.json).

# Safety and invalid harness attempts

All recorded runs retained live identities 9505 PID 77432 fingerprint 72c6bfb456e30cd7 and 9524 PID 25276 fingerprint f86ee3638ee760f3, before and after. Every screenshot targets only its owned console via PrintWindow, 993x519 pixels; foreground sampler recorded zero focus hits. Teardown used only run-path process identity/owned launcher descendants. No full test group, remote write, remote restart, install, commit or push.

The first two source-remote attempts used bad cmd /s /c harness quoting and exited before capture; a third fixture attempt lacked import re and failed after startup. They remain retained, marked failed, and are not production defect evidence. The corrected unmodified-source remote run at 233416 passed before the provenance change. Its initial harness exit code was misleadingly 1 because copied compact-test success labels had not yet been updated; result status and all actual acceptance steps are pass. Subsequent harness versions exit 0 on pass.

# Disposition and blockers

- n167 original incident: BLOCKED on the original starter/parent process or recurrence trace. Native redirection behavior confirmed, supported local plain/remote launches pass. No root cause or Ciel fix asserted.
- n228 remote incident: BLOCKED on C:/Users/1347817/AppData/Roaming/ciel-runtime/router-instances/9683-a2f58d1342e0/router.log and installed version/fingerprint from .238. Local supported startup passes; local failure provenance now verified. No remote cause asserted.
- The source diagnostic delta is not in installed nightly 0555b6c. Installed-nightly launch verification is a baseline only; integration/local-nightly deployment of this delta belongs to the parent release owner. No app-server/injector ownership-boundary edit was needed.
