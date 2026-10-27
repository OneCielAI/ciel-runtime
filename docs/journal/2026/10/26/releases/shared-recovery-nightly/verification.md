---
type: Release Verification
title: Shared recovery nightly
description: Exact package evidence for recovery ownership, compaction gates and hybrid Work.
tags: [nightly, recovery, hybrid]
timestamp: 2026-10-27T05:55:00Z
---
# Status

Published, installed into a fresh local snapshot, verified with 8 installed native proofs, and switched locally. Live routers and native sessions were not restarted or modified. Managed hosts (.14, 10.0.0.238) were not touched.

# Publication

| Item | Result |
|---|---|
| Final commit | `eec7d694af216308e2a95d4103535f43c5546f09` on `nightly` and `nightly-20261026` |
| Integrated source | `dc9914c` (recovery ownership, compaction gates, hybrid Work, shell authority, SendMessage policy) + `f2d090a` (full-suite contract alignment) + `eec7d69` (first corrupt-state diagnostic regression) |
| First CI on `dc9914c` | run 40452232440 failed on three stale test expectations only ([ci-initial-failed.txt](evidence/ci-initial-failed.txt)) |
| CI `f2d090a` | run 40452623683 success, 6 jobs ([ci-f2d090a.txt](evidence/ci-f2d090a.txt)) |
| CI `eec7d69` | run 40454834973 success: python-minimum, unit, channel, router, runtime, quality ([ci-eec7d69.json](evidence/ci-eec7d69.json)) |
| Publish `eec7d69` | run 40454835001 success ([publish-eec7d69.json](evidence/publish-eec7d69.json)) |
| npm | `0.2.51-nightly.20261027-053202.eec7d69`, `gitHead` = `eec7d69`, dist-tag `nightly`, SHA-1 `68ad0c70…` and SHA-512 integrity verified against downloaded bytes ([published.json](evidence/published.json)) |
| Package vs git | 460 runtime files byte-equal to the `eec7d69` tree except the publish-stamped `VERSION` line and CRLF endings of one `.ps1`; non-packaged scripts excluded by the `files` allow-list ([package-vs-git.json](evidence/package-vs-git.json)) |
| Snapshot | `C:/Users/djlov/.local/share/ciel-runtime-eec7d694af216308e2a95d4103535f43c5546f09`, 460 files compared, no mismatch or extra ([snapshot-install.json](evidence/snapshot-install.json)) |

# Installed native proofs

All run by [scripts/run-installed.py](evidence/scripts/run-installed.py) against the snapshot, with inherited runtime/provider environment removed, isolated HOME/config, owned loopback fixtures and Claude Code 2.1.310 / Codex CLI 0.158.0. Summary: [installed-results.json](evidence/installed-results.json).

| Case | Checks | Decisive evidence |
|---|---|---|
| Compact idle-timeout + queued SSE | 5/5: compact dispatched once, one bounded retry (2 compact requests), phase `verified`, queued SSE replied exactly once, live unchanged | [run dir](../../diagnostics/compact-retry/evidence/repro/run-installed-shared-004041/result.json), `03-retry-success.png`, `04-queued-sse-replied.png` |
| Hybrid Work | 11/11 incl. incoming request queued while active turn busy, implicit priority rejected, queued write denied until approved, active resumed, raw history not isolated (disclosed) | [installed-hybrid](evidence/installed-hybrid/result.json), `02-incoming-request-during-active-turn.png` |
| Shell native authority | 6/6: Bash and PowerShell markers created under bypass while Work planning; direct Write still blocked | [installed-shell](evidence/installed-shell/result.json), `01-shell-execution.png` |
| Edward background handoff | 13/13 through the production launcher | [installed-handoff](evidence/installed-handoff/result.json), `02-background-injected.png` |
| ARA Work Stop | 8/8: notice once, no model retry when blocked, actionable push, bounded no-progress pause | [installed-work-stop](evidence/installed-work-stop/result.json), two PNGs |
| Codex durable recovery | 3/3: typed 504 retry then replied, native start ACK loss no replay, native start crash no replay | [installed-codex](evidence/installed-codex), [native-status.jsonl](evidence/native-status.jsonl) |
| SendMessage CielAiRouter | `/v1/messages`: tool advertised, background child received marker, child ACK reached parent | [run](evidence/installed-sendmessage/run-installed-cielairouter-default-004851/result.json) |
| SendMessage vLLM | `/v1/chat/completions`: same three checks pass | [run](evidence/installed-sendmessage/run-installed-vllm-default-004859/result.json) |

# Local switch

- [local-switch.json](evidence/local-switch.json): User `CIEL_RUNTIME_HOME` moved from the `0555b6c` snapshot to the `eec7d69` snapshot; statusline replaced atomically with the snapshot template (backup `ciel-runtime-statusline.py.before-eec7d69-20261027`).
- [npm-install.txt](evidence/npm-install.txt): `npm install -g` of the exact nightly succeeded.
- [launch-paths.txt](evidence/launch-paths.txt): `.local` and npm shims both report `0.2.51-nightly.20261027-053202.eec7d69` with inherited runtime/provider environment removed.
- Router 9524 runs from its own separate snapshot (`ciel-runtime-work-breakdown-20261026-122744`), so neither the npm update nor the home switch touched its files.

# Live identities

- 9524: pid 25276, fingerprint `f86ee3638ee760f3`, identical before and after install ([pre](evidence/live-pre-install-eec7d69.json), [post](evidence/live-post-install-eec7d69.json)).
- 9505: pid 77432 had already stopped **before** the install at 2026-10-27T00:33:22 with `router_lifetime_stopped reason=claude_exit`, after its Claude child pid 68232 exited with `returncode=0` at 00:33:17 (instance `router.log`). Release commands issued in that window were git, gh, OKF validation, npm registry download and file comparison; none signal processes. Who closed that Claude session is not recorded in the log. At exit, `message_id=15066` was logged as `channel_wake_child_exit_quarantined`; that live queue was left untouched.

# Remaining limits

- Remote original stdout owner (n167) and the 10.0.0.238 startup log/version (n228) remain unavailable: blocked incident diagnoses, not fixes.
- Walkie SSE EOF is an external endpoint closing around 60–62 s with cursor reconnect; no tied loss proven and the endpoint is outside Ciel Runtime.
- SendMessage proof covers owned fixtures only: no remote model/schema acceptance, no cross-session delivery; `SendMessageTool` and team tools stay blocked.
- Work queue isolation is a prompt-context bound: raw user messages remain in native history; scope classification is model-mediated.
- Existing native sessions keep the code they were launched with until they are restarted by their owners.
