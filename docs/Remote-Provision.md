# Remote Provision

Remote Provision prepares a workspace before a session starts. Ciel reads a
manifest from an HTTP endpoint, places the listed files in the launch
workspace and runs the listed scripts there, for example to install the
programs the session needs. It runs before every runtime launch (Claude Code,
Codex, Codex app-server, AGY and the other CLIs), including `--continue`
restores, and can be run on its own before a session.

Every file and every script must carry a sha256. Nothing is written or run
unless the downloaded bytes match it. If the manifest cannot be read, a hash
does not match or a script fails, the launch stops:

```text
Ciel Runtime launch blocked:
- Provisioning failed: step install-node exited 1 (log ...\provision\logs\...-install-node.log)
```

Remote Provision is off until it is enabled.

## Configure

```powershell
ciel-runtimectl remote-provision `
  enabled=true `
  manifest_url=https://provision.example/v1/manifest.json `
  authorization="Bearer {CIEL_PROVISION_TOKEN}"
```

`ciel-runtimectl remote-provision` without values shows the settings.
Authorization supports `%NAME%`, `${NAME}` and `{NAME}` environment references
and is sent only to the manifest's origin, never to a file or script URL on
another host.

| Key | Default | Range |
|---|---|---|
| `enabled` | `false` | |
| `manifest_url` | | http:// or https:// |
| `authorization` | | header value |
| `timeout_seconds` | 30 | 1-120 (per download) |
| `max_manifest_bytes` | 1 MiB | 1 KiB-4 MiB |
| `max_file_bytes` | 64 MiB | 1 KiB-1 GiB (per file or script) |
| `max_total_bytes` | 256 MiB | 1 KiB-4 GiB (all files) |

## Run before a session

```powershell
ciel-runtimectl remote-provision run      # provision now; exits non-zero on failure
ciel-runtimectl remote-provision status   # last result and every step's exit code and log
```

A session launcher can call `remote-provision run` before it starts the
session; the launch itself then finds every `once` step already done.

## Manifest version 1

```json
{
  "version": 1,
  "files": [
    {"path": "tools/setup.zip", "url": "files/setup.zip", "sha256": "<64 hex>"}
  ],
  "steps": [
    {"id": "install-node", "shell": "powershell", "url": "steps/install-node.ps1",
     "sha256": "<64 hex>", "platform": "windows", "timeout_s": 600, "run": "once"},
    {"id": "install-node", "shell": "bash", "url": "steps/install-node.sh",
     "sha256": "<64 hex>", "platform": "linux"}
  ]
}
```

Relative URLs resolve against the manifest URL; redirects must stay on
http/https.

**files** are written to `path` below the launch workspace. Paths must be
portable relative paths inside the workspace (no `..`, `\` or drive letters).
A file whose current content already has the listed sha256 is not downloaded
again; a changed file is replaced atomically.

**steps** run in manifest order with the workspace as the working directory:

| Field | Values |
|---|---|
| `id` | 1-64 letters, digits, `.`, `_`, `-`; unique |
| `shell` | `powershell` (Windows `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File`, elsewhere `pwsh`), `bash`, `python` (the Python running Ciel) |
| `platform` | `windows`, `linux`, `macos`, `any` (default); other platforms skip the step |
| `timeout_s` | 1-86400, default 600; the step's process tree is stopped when it runs out |
| `run` | `once` (default): runs again only when its sha256 changes or its last run failed; `every_launch`: runs on every launch |

No shell interprets the command line: Ciel starts the interpreter with the
script path. Scripts receive `CIEL_WORKSPACE` (the launch workspace),
`CIEL_PROVISION_DIR` (Ciel's provisioning directory) and
`CIEL_PROVISION_STEP` (the step id).

## Where results are kept

In the workspace state directory, under `provision/`:

- `provision-state.json`: last status, manifest URL and, per step, its sha256,
  exit code, finish time and log path.
- `logs/<time>-<step>.log`: the step's combined stdout and stderr.
- `scripts/<sha256>.<ext>`: verified scripts, reused while the hash is unchanged.

The launch also prints each result line (`Provisioning: step install-node ran`)
before the session starts. `remote_provision_ok` and `remote_provision_failed`
lines reach the router log only when the router's log already exists, as with
`remote-provision run` next to a running router; a launch's own record is
`provision-state.json` and the step logs.
