# Wing workspace-state Remote Memory correction

## Task

Correct the 0.2.22 Remote Memory behavior for Wing, where the runtime launch
directory is the user's home directory and no separate project directory
exists.

## Contradiction found

- The deployed 0.2.22 implementation rejected a launch workspace equal to the
  user home.
- Wing's recorded router workspace is
  `C:\Users\daniel.yun.WING`; there is no separate project checkout.
- Therefore the rejection prevented every post-deployment synchronization and
  could not satisfy Wing's actual deployment model.

## Corrected boundary

- Downloaded memory is owned by the Ciel workspace state directory, not by the
  process current directory.
- The default target is `<workspace-state>/memory`.
- The saved index address is verified to resolve below that state directory
  before it is exposed to a prompt.
- Routed OpenAI Responses, OpenAI Chat, and Anthropic Messages requests receive
  the same managed memory-index block through the protocol-aware prompt
  injector.
- No Remote Memory pointer is created in home `AGENTS.md`, `CLAUDE.md`, or
  `GEMINI.md`. A block written by 0.2.22 is removed without altering other user
  text.
- The legacy `.ciel/memory` directory setting maps to the new state-local
  `memory` directory for upgrade compatibility.

## Verification evidence

- `python -m unittest discover -s tests -p "test_remote_memory*.py"`
  passed 15 tests, including a real loopback HTTP manifest/file download.
- `python -m unittest discover -s tests -p "test_prompt_injection.py"`
  passed 8 protocol transformation tests.
- `python -m unittest discover -s tests -p "test_event_settings_cli.py"`
  passed 25 configuration CLI tests.
- Remote Memory integration tests assert injection into OpenAI Responses,
  OpenAI Chat, and Anthropic Messages, plus idempotence.
- The first full-suite run found seven failures: one architecture line-budget
  failure and six object-identity regressions when memory was disabled. The
  injector was moved to its bounded module and changed to return the original
  request object when no prompt exists. Focused architecture, channel bridge,
  and Codex compatibility suites then passed.
- The verbose Remote Memory integration run passed all 15 tests. Its console
  output explicitly records the loopback HTTP download, home-launch/state-local
  storage, three protocol-family injections, and idempotence cases as `ok`.
- The final full suite passed: `Ran 2631 tests in 294.411s`, `OK (skipped=136)`.
- Targeted Ruff validation passed after removing one unused test variable.
- `npm pack --dry-run --json` succeeded for
  `@oneciel-ai/ciel-runtime@0.2.22`, reported 384 package entries, and included
  the changed `ciel_runtime.py`, `remote_memory.py`, documentation, and journal.
- `git status --short` confirms the pre-existing August 20 journal changes are
  still present as unstaged user changes; this task did not overwrite them.

## Deployment state

The correction is implemented and verified in the local worktree only. No git
commit, remote push, package publish, installation, or Wing process restart was
performed by this task.

## Wing load check after install command

- The agent screenshot reports loading `memoir` from
  `C:\Users\daniel.yun.WING\AGENTS.md`; it does not report the Ciel workspace
  state memory index.
- SSH inspection shows Wing still has
  `0.2.22-nightly.20260821-051020.13f5d91` installed.
- The installed `remote_memory.py` has the home-rejection message and has
  neither `DEFAULT_DIRECTORY = "memory"` nor
  `inject_current_memory_prompt`.
- The new workspace-state `memory` directory does not exist and contains zero
  files. The only state record is the stale 28-file record last written at
  `2026-08-21T04:45:14.4014143Z`, with root `.ciel/memory`.
- The latest launch logs at `02:21:12` and `02:21:31` record the same user-home
  rejection for Claude and Codex. No later successful memory update exists.
- Home instruction files contain zero Ciel Remote Memory pointer blocks, while
  both `AGENTS.md` and `CLAUDE.md` contain the separate `memoir` content.
- The npm registry confirms the `nightly` dist-tag still points to
  `0.2.22-nightly.20260821-051020.13f5d91`.

Conclusion: the newly corrected memory implementation is not deployed, and
Wing has not loaded it. Installing `@nightly` could only reinstall the old
nightly because the dist-tag has not moved.
