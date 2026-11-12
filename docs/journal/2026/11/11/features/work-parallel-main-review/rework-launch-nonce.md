---
type: fix-journal
title: Rework launch nonce scoped to the claude.exe process
description: An owned Claude Code 2.1.322 probe measured how long three candidate process identities live. The rework launch nonce now uses the one that lives exactly one process, $.env.set, and fails closed when it is unavailable.
status: implemented-uncommitted
timestamp: 2026-11-11T20:55:00Z
tags: [work-parallel, rework-launch, launch-nonce, claude-code-2.1.322, probe, plugin-reload, single-flight]
---

# Defect

`register.js` generated `reworkLaunchNonce` once per module load. A hot reload of the module in the same `claude.exe` therefore produced a new nonce. A reloaded host could then send `rework_launch_absent` for its own in-flight spawn as if a later process had proved it absent. The backend allows that resolution when the stored nonce differs from the observer's (`work_parallel_finalization.py:538`).

# Probe: identity lifetime on Claude Code 2.1.322

- **Script:** `e2e-source/nonce-lifetime-probe-44e40778/` (script sha256 `fab73a0f`, `result.json` `9ca5bde3`).
- **Isolation:**
  - Every inherited `CIEL_RUNTIME_*`, `CLAUDE*`, `ANTHROPIC*`, `CODEX*` and `OPENAI*` variable was removed. After the strip, no inherited `CIEL_RUNTIME_*` remained.
  - `CIEL_RUNTIME_TEST_ISOLATED=1` was set, with a temp config and a temp `CLAUDE_CONFIG_DIR`.
  - A loopback gateway stood in for the provider.
- **Recording:** values are recorded as SHA-256 digests only (12-hex prefixes below).
- **Earlier attempt:** `nonce-lifetime-probe-b3b4f855` stopped at `/clear` because of a harness bug. It is kept as failed-harness evidence.

| Event | `$.state` (set with `ifVersion: 0`) | `$.env.set` literal var | launcher env var |
|---|---|---|---|
| Process 1 start | `cb6036a4c41a` v1 | `9d8722b67d9c` | `ab10c87f27c2` |
| Module hot reload | `cb6036a4c41a` v1 | `9d8722b67d9c` | `ab10c87f27c2` |
| `/clear` | reset: absent, v0 | `9d8722b67d9c` | `ab10c87f27c2` |
| Hooks-worker respawn | not observed: the blocking module ran, but no replacement module's `session.start` was recorded | | |
| `--continue` (process 2) | absent, v0 | absent | `6332aea3bf50` (new) |
| `--resume <id>` (process 3) | absent, v0 | absent | `c1e4bd0e81df` (new) |

Conclusions from the table:

- `$.state` survives a reload, but `/clear` resets it. That reset is also documented ("/clear, /resume, or /branch reset `$.state`"). Rejected.
- `$.env.set` survives a reload and `/clear`, and is absent in each new process. Its lifetime is the process.
- The launcher variable has the same lifetime, but it needs a launcher change.

## Guard results

- **`~/.claude.json` hash changed** between the probe's start and end. No owned path or probe id appears in it. Other live Claude sessions were running during the probe. Which process wrote the change is not established.
- **One protected `python.exe` (pid 76412, parent 75868) was missing afterwards.**
  - It was created at 18:35:22.797Z, inside the probe window.
  - Its command line was not captured.
  - Parent 75868 is not in the protected snapshot.
  - It was not in the probe's owned tree, and it was not a child of the probe (pid 60264).
  - Whether it belonged to a test runner or to a live session cannot be decided from the recorded evidence.

# Fix (`register.js` only)

- `reworkLaunchNonce($)`:
  - It reads the literal variable `CIEL_WORK_HOST_PROCESS_NONCE`.
  - If the variable is unset or empty, it generates 128 random bits once, writes them with `$.env.set`, re-reads the variable, and returns only the value it read back.
  - Any other value, a `deny`, or a thrown error returns `null`.
  - It never regenerates per module load.
- **Fail closed.**
  - With no nonce, a rework dispatch records no `rework_launch_intent` and starts no fork; it reports `launch_nonce_unavailable`.
  - `rework_launch_status` is sent without `launch_nonce`, so `same_process` stays unknown.
  - Reconcile never sends `rework_launch_absent`. The launch shows as `Rework launch stuck`, and only the existing user-confirmed manual path resolves it.
- **Legacy rows unchanged.** An intent stored with `host_nonce: null` is still resolved by a host that has a nonce, as before.
- **No backend, schema, ledger or coordination change.** Test harness output carries digests, counts and equality flags only.

# First-establishment hardening (function level)

This is function-level hardening. It does not fix a product defect that has been shown to be reachable. Version labels name the `register.js` each measurement ran on. Cited line numbers are for the final `register.js` `09255642`; it keeps the 3040-line layout of `8d02d969`, so both share every line number outside `reworkLaunchNonce` (`:1026-1054`).

Each fact below carries one label:
- **Measured**: observed in a run.
- **Static**: read from source, not tested.
- **Unmeasured**: not established either way.

## The race

- **Measured, function level.** Two concurrent first calls of `reworkLaunchNonce` can both read an empty variable, each set its own value, and each return what it read back after its own write. One caller gets X, the other gets Y.
  - The fake env forces the interleave: every caller reads first, and then each write lands after the previous writer's read-back.
  - On `135fc5e6`, whose function bytes `a923bb10` keeps unchanged:
    - harness scenario `nonce-concurrent-function`, calling the function directly: 2 callers got 2 distinct values, and 3 callers got 3;
    - with the set refused or throwing: one set per caller (3 and 4 sets).
  - On `cfa51320`: one value, one shared set, and a shared `null` on failure.
- **Measured, fixture-forced hooks.** In harness scenario `parallel-rework-nonce-race`, the fixture host dispatches session.start and `turn.complete` concurrently.
  - `a923bb10` sent `rework_launch_status` with 2 different nonces, after 2 sets. `135fc5e6` gave the same result.
  - `cfa51320` sends 1 nonce, after 1 set.
  - Only the fixture forces that dispatch.
- **Static.** These are the nonce callers within one module instance (line numbers are for `8d02d969` and the final `09255642`, which share them; `8d02d969` differs from `cfa51320` only by six added comment lines inside `reworkLaunchNonce`):
  - `parallelHydrate` (`:1849`), run only by session.start (`:2429`). session.start awaits it before recovery and before its own pump (`:2430-2431`).
  - The pump, which is single-flight (`:2208-2211`). It reaches `reworkLaunchStatus`, `reconcileReworkLaunch` and the rework branch of `parallelDirectSpawn`.
  - The tool path also reaches `parallelDirectSpawn`, but first refuses any role other than worker (`:1515`), so it never reaches the rework branch.
  - `parallelHydrate` runs outside the pump's single-flight. So source order alone does not rule out a pump started by another hook overlapping session.start's hydrate.
- **Unmeasured.** Whether Claude Code dispatches other hooks while it awaits a session.start hook.

## Change (`register.js` `cfa51320`, comment-only refreeze `8d02d969`, first-read refreeze `09255642`; `reworkLaunchNonce` `:1013-1055`)

- Single flight within one module instance. In `cfa51320`/`8d02d969` the shared flight covered the set and the read-back, but each caller made its own first env read before joining it. In `09255642` the flight covers the whole establishment: first read, then set if unset, then read back. Every caller that arrives while the flight is in progress receives the same result.
- The promise is cleared when it settles, and only if it is still the current one.
- A call that starts while no flight is in progress reads the env fresh. A value already established is never cached in module state; only the in-flight promise is shared. Hot reload and fresh process behaviour stay as measured in probe 44e40778.
- A `deny`, a thrown set, or a read-back that does not match still returns `null` to every waiting caller, so everything fails closed as before.
- **Reordered first read** (`09255642`). This case is a function-level hypothesis from review-restart-terminal (`probe_nonce_reorder.mjs`); the real host's delivery order is unmeasured.
  - In `8d02d969`, each caller still made its own first env read before joining the flight. If B's first read (issued before A's set) was delivered after A's set and read-back, B saw the variable unset and started a second establishment. Result: two nonces.
  - Under FIFO delivery the result was single-valued.
  - It is now covered because the flight includes the first read. A call that finds a flight returns it without reading; a call that starts after the flight settles reads the env fresh; no value is cached.
  - Harness `nonce-reorder-function`, test `test_function_level_reordered_first_read_delivery_still_returns_one_nonce`: fails on `8d02d969` (2 values, 2 sets), passes after (1 value, 1 set).

## Where the value goes (comment `:1013-1022`)

- The old comment said the value was "never ... written anywhere but the router call". That was false.
- `$.env.set` writes the `claude.exe` process env.
  - The plugin typings say so directly. `ciel_runtime_support/claude_mods/ciel-work/.claude-plugin/types/claude-code/index.d.ts:3826-3827` (file sha256 `bfbbc814`, re-read for this entry): "The environment of this process, the one every Bash child, MCP server and `$.process.run` command started after inherits."
  - Probe cbdc86cb measured a `$.process.run` child inheriting the value (below).
- The value is also stored in `work-parallel-finalization.json` as the launch's `host_nonce`.
- What holds: it authorizes nothing by itself. Host-only events also need the host token, which lives in the same env. The raw value is never logged or shown.

## Real engine: probe cbdc86cb (reviewer, Claude Code 2.1.322)

- **Location:** `%TEMP%\review-restart-aSSV\real-probe\race-lifetime-probe-cbdc86cb\`.
  - Script: `%TEMP%\review-restart-aSSV\tools\race_lifetime_probe.py`, sha256 `95531097`.
  - Results: `result.json`, sha256 `2bfe5dc4`.
- **Isolation:** `CIEL_RUNTIME_TEST_ISOLATED=1`, a temp config and `CLAUDE_CONFIG_DIR`, a loopback gateway, and no inherited `CIEL_RUNTIME_*`. Values are recorded as digests.
- I read the facts below in its `result.json` and `phase1-debug.txt`, and in the debug log of attempt `race-lifetime-probe-657c2387`.

Measured:

- **One hooks module per plugin.** In attempt 657c2387, Claude Code refused a second `hooks.json` entry: "hooks.json `modules` names one hooks module per plugin; a second entry is refused". So within one plugin, a second module cannot race the first.
- **After a same-process hot reload, the old instance produced no further `$` effects.**
  - The engine logged "hooks module race-probe@inline reloaded" at 20:58:07.159Z.
  - After that, no further record from the old instance appears in the log or the event files, although the probe kept waiting at least 30 s. Its 1 s heartbeat made `$.env.get` and `$.fs.write` calls; the probe recorded no failure outside `$`, so a stopped loop cannot be told apart from failing `$` calls.
  - The old instance's last record (heartbeat 6, `result.json` `hb_after_reload`) was at 20:58:07.023Z: 653.5 ms after the new module file was written (`reload_at`), and 136 ms before the reload log line.
- **The old instance's pending `$.clock.after` never fired.**
  - It was due at about 20:58:30Z. The phase 1 process ran until after `/clear` at 20:58:41Z, and the callback never fired.
  - No log line says "cancelled"; the observation is only that it did not fire.
- **The real engine's `$.env.set` is last writer wins.**
  - Two concurrent flows in one instance each read the variable empty and each wrote their own value.
  - Both read back the second flow's value, so the first write was replaced. Nothing protects the first writer.
- **A `$.process.run` child inherited the value.** The child's printed value had the same digest as the env value.
- Also consistent with probe 44e40778: the env value survived the reload and `/clear`, and was absent after `--continue` and after `--resume`.

## Cross-instance first establishment: not observed

- On the real engine, a second module instance of this plugin cannot exist beside the first. After a hot reload, no `$` effect of the old instance was observed. Overlap of two instances' first establishments was therefore not observed.
- This is not a proof at the level of in-flight awaits. The probe did not test whether a `$.env.set` the old instance issued just before the reload can still land after it, and it did not test whether a pending `$` call of the old instance throws or hangs.
- No code was changed for it.

## Tests (`tests/work_mod_harness.mjs`, `tests/test_work_mod_write_gate.py`)

- The fake env's `nonceRace` holds the first N reads until all N callers have read, so every caller sees the variable empty. It then applies each write only after the previous writer's read-back.
- Function level: `nonce-concurrent-function` takes the exact `reworkLaunchNonce` section from the `register.js` under test and calls it directly, with 2 and 3 concurrent first callers. Raw values never leave the harness.
- Fixture-forced hooks: `parallel-rework-nonce-race` has the fixture host run session.start and `turn.complete` concurrently, with one unbound resume lease. It is not evidence that Claude Code does this.
- `nonceSent` (harness `parallel-rework-launch`) now inspects the router args (`_host.launch_nonce`) instead of the fake env. The no-nonce case asserts that no router call carried the field. `restart-no-row` asserts that the four launch facts that carry it did.

| Test | `135fc5e6` (before) | `cfa51320` (after) |
|---|---|---|
| function level: 2 and 3 concurrent first callers get one value, equal to the env, with one set | fails: 2 and 3 distinct values | passes |
| function level: set `deny` or throw with 2 and 3 callers gives all `null` and one shared attempt | fails: one set per caller (3 and 4 sets) | passes |
| fixture-forced concurrent hooks: one nonce on every launch status | fails: 2 distinct nonces, 2 sets | passes |
| no-nonce launch: no router call carries `launch_nonce` | passes | passes |
| later process: the nonce reaches the router on the four launch facts | passes | passes |

# Tests (real `register.js` against the real backend)

Every harness `start` is labelled `process` (fresh env and native list) or `reload` (re-import with the same env). The three scenarios whose forks stay listed are reloads, because only the same process lists them (probe `reload-probe-f63b94ea`).

| Test | 63854b9b (before) | after |
|---|---|---|
| reload keeps the nonce; no automatic absent; manual resolves | fail | pass |
| env read/set failure: no intent, no automatic resolution | fail | pass |
| fresh process: new nonce, absent resolves | pass (behaviour kept) | pass |
| legacy `host_nonce: null`: a new process resolves it | pass (behaviour kept) | pass |
| harness output carries nonce digests only | pass | pass |
