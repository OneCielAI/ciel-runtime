# Wing remote-memory replication verification

## Scope

- Host: `WING` (`wing\\daniel.yun`, SSH port 1004)
- Workspace: `C:\Users\daniel.yun.WING`
- Installed package: `@oneciel-ai/ciel-runtime@0.2.22-nightly.20260821-043829.b7ea382`

## Configuration evidence

- Remote Memory is enabled.
- Manifest endpoint:
  `http://100.94.212.25:3600/api/v1/integrations/agent-memory/manifest.json`
- Authorization is stored; its value was not printed.
- Destination: `.ciel/memory`

## Replication evidence

- Workspace state file:
  `C:\Users\daniel.yun.WING\AppData\Roaming\ciel-runtime\workspaces\f895679997f4\remote-memory.json`
- State timestamp: `2026-08-21T04:45:14.4014143Z`
- State records `reason=launch`, index `.ciel/memory/index.md`, and 28 files.
- The destination contains 28 files totaling 50,689 bytes.
- `index.md` SHA-256:
  `81A345506C84B8E6D4FD0FA16DD36B4EF625761677CEF6D2B97763C6D64DB1A5`
- `AGENTS.md` lines 505-507 contain the managed pointer block with
  `Memory index: .ciel/memory/index.md`.

## Runtime log evidence

- Router log:
  `C:\Users\daniel.yun.WING\AppData\Roaming\ciel-runtime\router-instances\9139-f895679997f4\router.log`
- Successful launch synchronizations: 3.
- Failed synchronizations: 0.
- The latest success at `2026-08-20T23:45:14` reports
  `remote_memory_updated runtime=codex files=28 index=.ciel/memory/index.md reason=launch`.

## Conclusion

Wing's workspace memory tree was downloaded from the configured server during
runtime launch, written to `.ciel/memory`, and projected into `AGENTS.md` via
the managed local index pointer.

## Scope correction

- The live router health response identifies its workspace as
  `c:\users\daniel.yun.wing`, which is the Wing user's home directory rather
  than a project directory.
- The implementation derives both the memory root and instruction target from
  `Path.cwd()`: memory is placed at `<workspace>/.ciel/memory`, while the Codex
  pointer is placed at `<workspace>/AGENTS.md`.
- Therefore the synchronization mechanism is workspace-relative in source,
  but this live process selected the user home as that workspace. On Wing the
  observed placement is consequently home-scoped, not project-scoped.
- The earlier replication-success conclusion remains valid only for download
  and projection behavior; it does not establish correct project isolation.

## Repair scope

- Remote Instruction and Remote Memory now use the router's normalized launch
  workspace rather than independently reading the router process working
  directory.
- Remote Memory rejects a user-home or filesystem-root workspace before any
  HTTP request and removes a stale managed pointer from the native instruction
  file.
- The rule is path-scope based and applies to every supported runtime. It does
  not contain a Wing, user, session, provider, model, or agent exception.

## Repair verification

- Focused Remote Memory and runtime integration suite: 13 tests passed.
- Full pre-release suite: unit 1,108, router 902, channel 372, and runtime 247
  tests passed; 136 declared environment-dependent tests were skipped.
- Ruff, Python compilation, documentation metadata, and `git diff --check`
  passed.
- A standalone loopback-HTTP probe synchronized `alpha` and `beta` into two
  independent `.ciel/memory/index.okf` trees and injected a pointer into each
  project's `AGENTS.md`. Four HTTP requests were observed: one manifest and one
  file download for each workspace.
- The same probe used the synthetic user home as workspace. It returned
  `failed`, made zero HTTP requests, removed the managed pointer, and did not
  create a parent/global memory tree.

## Wing post-deployment recheck

- Wing is running installed nightly
  `0.2.22-nightly.20260821-051020.13f5d91`; router health reports the same
  version and source fingerprint `7e5d2ce08c25c07e`.
- The only registered and launched workspace remains
  `C:\Users\daniel.yun.WING`, the user's home directory.
- Four post-deployment launches logged `remote_memory_failed` with the exact
  project-scope rejection. No post-deployment `remote_memory_updated` entry
  exists.
- The old state and 28 downloaded files remain on disk with their original
  `2026-08-21T04:45:14Z` timestamps. They are stale artifacts from the earlier
  home-scoped implementation, not a new project-scoped dump.
- `AGENTS.md` contains zero managed memory-pointer matches. Its pointer-removal
  write occurred at `2026-08-21T06:39:34Z`.
- The active Codex process started at `2026-08-21T06:39:51Z`, after pointer
  removal, so this active session did not start with the stale memory index
  pointer.

## Post-deployment conclusion

The scope guard is working, but Wing does not currently have a usable
project-scoped memory dump because it is still launched from the user home.
The 28 files visible under the home directory are stale and unreferenced.
