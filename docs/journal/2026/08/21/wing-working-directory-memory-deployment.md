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

Pending nightly publication and Wing installation.
