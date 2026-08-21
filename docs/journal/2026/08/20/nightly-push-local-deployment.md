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
- Commit: `0ea4cce` (`fix: harden runtime replay and MCP environment`).
- Published npm version:
  `0.2.21-nightly.20260821-003258.0ea4cce`.
- Registry `gitHead`: `0ea4ccefce5955e3dbd37eff3b38cf8e122e0a75`.
- Publish workflow `32432946530`: success, including npm test, publish, and
  published-tarball verification.
- CI workflow `32432946541`: success for minimum Python, unit, router,
  channel, runtime, quality, documentation, and package checks.
- The exact registry version was installed globally on the local workstation.
  Its package manifest reports the nightly version above.
- Source and installed SHA-256 values match for all three changed runtime
  modules:
  - `responses_input_compatibility.py`:
    `A0B38961262AC7870317F99C892C4A1535525979C93EF632A177571C3C8DCDC2`
  - `runtime_launch.py`:
    `036E0323E6DD9FBF5B5DD3EC591017CB47FDCB2EE7CB6361C47143F734BDD609`
  - `workspace_mcp.py`:
    `C79DE505E5CA27DF0562DC006038CB68DAB1B2D1FA010D4E9160B1B38384AAAF`
- Installed-module runtime checks:
  - the reported orphan custom call ID was removed while its following user
    message remained;
  - generic MCP projection produced `Authorization=Bearer test-token`,
    `X-Env=projected`, and retained the static header;
  - `ciel-runtime --version` exited successfully and printed the base runtime
    compatibility version `0.2.21`; the nightly artifact identity is verified
    separately from the installed package manifest and npm registry metadata.

## Remote memory and routed-turn recovery release

- Commits pushed to `nightly`:
  - `c93e4b6` — remote workspace-memory manifest synchronization
  - `fb1f871` — exact missing tool-output verdict repair
  - `6d7ffb8` — bounded routed-Codex empty-end-turn recovery
- Published npm version:
  `0.2.21-nightly.20260821-040635.6d7ffb8`.
- Registry `gitHead`:
  `6d7ffb8a90a37a662d1721ae33476f5a545c130d`.
- CI workflow `32445593344`: success.
- Publish workflow `32445593325`: success, including npm test, publish, and
  published-tarball verification.
- The published tarball contains `remote_memory.py`, `Remote-Memory.md`,
  `codex_turn_recovery.py`, and `responses_input_compatibility.py`.
- The exact published version was installed globally on Wing through SSH port
  1004. Installed and source SHA-256 values match:
  - `remote_memory.py`:
    `C00F72CD469436D597D8D9C286C714E65F6B890A06324AC63C811AB0192750A0`
  - `codex_turn_recovery.py`:
    `270D064CDBCEEB4E4D84351F8481620A5B9B219B80740DB81F4E9EBD22DE7F37`
  - `responses_input_compatibility.py`:
    `B69FB5690F0BC2AD0253F79689D6A7195A5AD95117874E8CEF3ACFE4B4289279`
- Wing's installed `ciel-runtimectl remote-memory` command exited 0 and
  reported all manifest, destination, authorization, timeout, and size-limit
  settings.
- An installed-module probe on Wing presented the exact runtime-generated
  empty-end-turn notice. It performed exactly one retry, accepted the visible
  retry result, and did not replay the synthetic notice into the retry request.
- No Python, Node, or Codex process remained active on Wing after installation,
  so no stale in-memory runtime needs to be terminated; the next launch loads
  the installed version.
