---
type: probe-result
title: Claude Code 2.1.322 plugin reload and restart fork probe
description: An owned real-console probe on Claude Code 2.1.322. A live native fork stays in the reloaded module's agent.list after a same-process hot reload. No process or agent row survives a kill and --continue.
status: recorded
timestamp: 2026-11-11T18:20:00Z
tags: [work-parallel, restart-recovery, plugin-reload, native-fork, probe, claude-code-2.1.322]
---

# Question

Can a live native fork be missing from `$.agent.list()`?

- **(a)** After a plugin hot reload inside the same `claude.exe`.
- **(b)** After the owned `claude.exe` tree is killed and relaunched with `--continue`. Do old forks survive?

This decides whether the host's absence assertion in [the restart fix](restart-submitted-terminal.md) can mistake a live fork for an ended one.

# Setup

- **Script:** `e2e-source/reload-probe-f63b94ea/reload_probe.py`, sha256 `70638adf424aea5b74f914bc744609c1cbe7d2f01815ae63c8faa933998485fa`.
- **CLI:** `C:\Users\djlov\.local\bin\claude.exe`; `--version` printed `2.1.322 (Claude Code)`.
- **Isolation:**
  - Every inherited `CIEL_RUNTIME_*`, `CLAUDE*`, `ANTHROPIC*`, `CODEX*` and `OPENAI*` variable was removed. After the strip, no inherited `CIEL_RUNTIME_*` remained.
  - `CIEL_RUNTIME_TEST_ISOLATED=1` was set, with a temp `CIEL_RUNTIME_CONFIG_DIR` and a temp `CLAUDE_CONFIG_DIR`.
  - A loopback recording gateway stood in for the provider. No real provider or live router was used.
- **Mod:** a `--plugin-dir` mod.
  - Generation A spawns one `fork`. The gateway holds that fork's model stream open, so the fork stays `running`.
  - The script then rewrites the module (generation B). Generation B's `session.start` and two timers record `$.agent.list()`.

# Result (a): same-process hot reload

The live fork stays listed, with its exact id and `running` status.

| Step | Evidence |
|---|---|
| Fork spawned by generation A | `agentId a116d8b5b09ef2dca`, `after-spawn-0`: `running`, name `reload-probe-fork`, `spawnedBy reload-probe` |
| Engine reload in the same process | `phase1-debug.txt:350-351`: `session.start: raised for reload-probe (loaded later)` and `hooks module reload-probe@inline reloaded in 9.4ms` |
| Same `claude.exe` | pid 66124 with the same creation time before and after the reload; the held fork stream still open |
| Reloaded module's list | `session.start-before-next`, `session.start-after-next`, `reloaded+3s`, `reloaded+8s`: all list `a116d8b5b09ef2dca` as `running` |

So a plugin reload does not hide a live fork from `$.agent.list()` on 2.1.322. The probe observed no omission.

# Result (b): kill, then `--continue`

No process or agent row survived.

- The owned tree at kill time was `claude.exe` pid 66124 alone. `taskkill` returned rc 0, and no row with that pid and creation time remained.
- The held fork stream closed at the kill with `ConnectionResetError`. That is consistent with the fork loop living inside the killed process.
- `--continue` started pid 78592. It attached to the same session id: the `jobs/9de45374` watch path appears in both debug logs.
- Its `session.start` and timed `agent.list()` calls returned an empty list, and no fork model request arrived.
- Limitation: this main transcript had no user or assistant turn, and this 2.1.322 run logged no `orphaned` line. The 2.1.317 run `../../03/features/work-parallel/evidence/crash-resume-dbc5991d/phase2-debug.log:129` had a real main turn, and it logged `resume: 1 background agent(s) orphaned by previous process exit`.

# Live-profile guard

- The live-profile hashes before and after matched, and no live file contains an owned path.
- One protected `python.exe` (pid 70224, parent 69228) existed when the probe started and was gone afterwards.
  - It was created at 18:15:12.917Z, 0.25 s before the probe's first `claude.exe`.
  - It was never in the probe's owned tree. The probe's only kill commands targeted its own `claude.exe` pids 66124 and 78592.
  - Why it exited is not established.

# Conclusion

Neither condition for the launch-epoch redesign was observed:

- (a) showed no omission;
- (b) showed no survival.

The automatic restart path is not changed.
