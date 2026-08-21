# Nightly push and local deployment

## Release

- Nightly branch commits: `7263f9a`, `4a5f562`.
- Published npm version: `0.2.21-nightly.20260820-192230.4a5f562`.
- Publish workflow: `32408099096` (success).
- CI workflow: `32408099125` (success).

## Deployment

- Installed the exact published version globally on the local workstation.
- Installed the exact published version globally on Wing through SSH port 1004.
- Local and Wing `windows_conpty.py` SHA-256:
  `4B688420173E6335D47B92132D6FD001DD7C6AA1895D64D176962A208F2C3E7B`.
- Local and Wing `tool_side_effect_dedupe.py` SHA-256:
  `EA6ED7C6DBC6BE063DF6754C42F5E324C6CA773B5B357F4A62EE4A121E3F419C`.

## Runtime verification

- Local and Wing routed `codex --version` launches both returned
  `codex-cli 0.148.0` with exit code 0.
- Both installed packages contain zero `SetConsoleCP(` calls and one
  `ReadConsoleW(` call.
- A real interactive Wing ConPTY session rendered
  `나이트리한글입력검증-가나다라마바사` exactly in the Codex prompt.
- The interactive verification session shut down cleanly with Ctrl-D and SSH
  reported `Connection to wing closed.`

## Orphan-tool and cross-runtime environment release

- Scope: cross-runtime MCP environment projection and replay repair for
  unmatched Responses function/custom tool calls and outputs.
- Pre-publish verification: 2601 tests passed, 136 skipped; Ruff and
  `git diff --check` passed; `npm pack --dry-run` included the changed runtime
  modules and task journals.
- Branch synchronization before commit: local `nightly` and `origin/nightly`
  had zero commits of divergence.
- Published version, workflow results, local installation, and runtime evidence
  are recorded after the registry artifact becomes available.
