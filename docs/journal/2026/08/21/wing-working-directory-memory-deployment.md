# Wing working-directory Remote Memory deployment

## Task

Store each remotely synchronized memory tree below the runtime's actual launch
workspace, inject its verified index address into routed model instructions, and
publish/install the result as a nightly build for an end-to-end Wing check.

## Evidence before the correction

- Wing runs Ciel from `C:\Users\daniel.yun.WING`.
- Wing configuration explicitly sets `remote_memory.directory=.ciel/memory`.
- The installed `0.2.22-nightly.20260821-072901.5e5bdec` build instead mapped
  that value to its AppData workspace-state directory.
- A production-pipeline capture on Wing showed the AppData index address in
  both the translated Anthropic `system` block and the first Ollama `system`
  message. This proves the address was not dropped by protocol conversion; it
  also proves the destination did not match the requested working-directory
  placement.
- A real routed model turn nevertheless claimed that no memory index was
  present. That answer contradicts the captured wire payload and is not used as
  evidence that the router dropped the prompt.
- After the first working-directory nightly was installed, the real model turn
  again denied seeing the pointer, searched the home tree, then found and read
  `.ciel/memory/index.md`. Source inspection showed that the pointer was placed
  before Codex's later developer context and Ciel's execution reminder, not at
  the requested bottom of the final system text.

## Correction

- Interpret `remote_memory.directory` relative to the resolved launch
  workspace and restore `.ciel/memory` as the default.
- Permit a user-home launch workspace while retaining path traversal and
  workspace-boundary checks.
- Keep synchronization metadata in the per-workspace Ciel state directory.
- Record the resolved workspace in that metadata and refuse to inject an index
  when the record belongs to a different launch workspace.
- Continue removing obsolete Ciel-managed pointer blocks from native runtime
  instruction files; no new `AGENTS.md`, `CLAUDE.md`, or `GEMINI.md` pointer is
  created.
- During Responses-to-Anthropic conversion, remove any earlier managed pointer
  and append it after all converted developer context. During OpenAI/Ollama wire
  projection, move it behind execution reminders and state messages so it is
  the final text in the system message.

## Local verification

- Remote Memory suites: 17 tests passed, including loopback HTTP downloads,
  overwrite behavior, home-workspace placement, cross-workspace rejection,
  protocol-family injection, and Responses-to-Ollama wire projection.
- Event settings suite: 25 tests passed.
- Configuration repository suite: 7 tests passed.
- Ruff and `git diff --check` passed for the changed implementation and tests.
- The full suite passed: `Ran 2633 tests in 290.487s`,
  `OK (skipped=136)`.
- `npm pack --dry-run --json` succeeded with 385 package entries and included
  the changed runtime, Remote Memory implementation, documentation, tests' task
  journal, and package metadata.

## Deployment verification

- Commit `66483657e033943a8388b805c90a468d1104c83a` published as
  `0.2.22-nightly.20260821-075242.6648365`; npm `gitHead` matched.
- GitHub npm publish run `32460413577` completed successfully, including its
  published-tarball verification.
- Wing installed that exact version and synchronized 28 files (50,689 bytes)
  to `C:\Users\daniel.yun.WING\.ciel\memory`; the index existed and its first
  line was `---`.
- The first real model test only reached the correct two-line result after a
  broad filesystem search. That is recorded as a failed prompt-visibility
  check, not as successful instruction use.
- After correcting final-system placement, the focused Remote Memory and prompt
  injection suites passed 25 tests, Ruff passed, and the second full suite
  passed: `Ran 2633 tests in 292.956s`, `OK (skipped=136)`.
- Final nightly publication, Wing installation, and a new no-search model read
  remain pending.
