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
- Commit `21872ed694428e46d4246e4bc08389f84ae462de` published as
  `0.2.22-nightly.20260821-080802.21872ed`; npm `gitHead` matched and GitHub CI
  run `32461589362` completed successfully.
- Wing installed that exact version and again synchronized 28 files (50,689
  bytes) below the launch workspace. The index existed and its first line was
  `---`.
- A real no-search model turn still denied receiving the index address. A
  loopback Ollama wire capture then measured zero Remote Memory markers and no
  index path in the complete upstream JSON.
- A temporary, non-published probe of the same installed source recorded
  `marker_after=True` for both the `openai_responses` injection and the later
  `anthropic_messages` injection. The package and provider configuration were
  restored immediately afterward.

## Final wire truncation correction

- The measured transition is therefore: marker present after Anthropic
  injection, marker absent in Ollama wire projection.
- Source inspection identifies the destructive transition in
  `coalesce_ollama_system_messages`: it applies
  `compact_message_text_for_prompt` to the combined Anthropic system text.
  That helper has a fixed `PROMPT_MESSAGE_TEXT_LIMIT=20000`, so a pointer at the
  tail of Wing's longer Codex system prompt is removed before the existing
  post-projection placement helper runs.
- `move_memory_pointer_to_system_end` now accepts a verified fallback pointer.
  The final Ollama and OpenAI chat projection wrappers obtain that pointer from
  the current workspace state and restore it only when the managed block was
  removed by an intermediate projection.
- The regression test uses a 25,000-character system instruction and verifies
  that the ordinary system text is truncated while the workspace memory index
  is present exactly once at the final system-message tail.
- Focused Remote Memory and prompt-injection verification passed 25 tests;
  Ruff and `git diff --check` also passed. The full suite passed 2,633 tests
  with 136 skips, and `npm pack --dry-run --json` reported 385 entries.
  Nightly publication and a new Wing wire/model check remain required before
  closure.
