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
