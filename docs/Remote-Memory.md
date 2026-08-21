# Remote Memory

Remote Memory downloads a workspace-scoped memory tree from a separate HTTP
manifest endpoint before an interactive runtime starts. It does not place the
memory contents in the system prompt. Ciel adds only the verified local
memory-index address to its managed system/developer prompt context.

## Configure and synchronize

```powershell
ciel-runtimectl remote-memory `
  enabled=true `
  manifest_url=https://memory.example/v1/manifest.json `
  authorization="Bearer {CIEL_MEMORY_TOKEN}" `
  sync
```

The default destination is `.ciel/memory` below the runtime's actual launch
workspace. On Windows, launching from `C:\work\project` produces
`C:\work\project\.ciel\memory`. A custom destination must remain a portable
relative path inside that launch workspace:

```powershell
ciel-runtimectl remote-memory directory=team-memory
```

Use `ciel-runtimectl remote-memory` without values to display the current
settings. Authorization supports `%NAME%`, `${NAME}`, and `{NAME}` environment
references. The resolved secret is not written to the memory state file.

## Manifest contract

The endpoint returns UTF-8 JSON:

```json
{
  "version": 1,
  "index": "index.okf",
  "files": [
    {
      "path": "index.okf",
      "url": "files/index.okf",
      "format": "okf",
      "sha256": "optional 64-character SHA-256"
    },
    {
      "path": "projects/ciel-runtime/current.md",
      "download_url": "files/ciel-runtime-current.md",
      "format": "markdown"
    },
    {
      "path": "agents/kevin/state.json",
      "url": "https://cdn.example/kevin-state.json",
      "format": "json"
    }
  ]
}
```

Relative download URLs resolve against the manifest URL. Supported declared
formats and file extensions are:

| Format | Extensions |
|---|---|
| `okf` | `.okf` |
| `markdown` or `md` | `.md`, `.markdown` |
| `json` | `.json` |
| `yaml` or `yml` | `.yaml`, `.yml` |
| `toml` | `.toml` |
| `text` or `txt` | `.txt`, `.text` |

If `format` is omitted, it is inferred from the extension. `index` must name
one of the downloaded files.

## Launch behavior

Every launch fetches the manifest and every declared file again. Downloads go
to a sibling staging directory. Only after all paths, UTF-8 bodies, size limits,
and optional SHA-256 values pass does Ciel replace the previous memory tree.
Files omitted by the new manifest therefore disappear. A failed synchronization
keeps the previous tree unchanged and logs `remote_memory_failed`.

The router's launch-workspace identity selects the destination. A machine with
no separate project checkout may use its user home as the launch workspace; in
that case Ciel creates `<home>/.ciel/memory`. Different launch workspaces receive
different trees. Ciel keeps only synchronization metadata in its workspace
state directory.

After a successful synchronization, routed OpenAI Responses, OpenAI Chat, and
Anthropic Messages requests receive a managed prompt block like this:

```markdown
<!-- ciel-runtime:remote-memory:begin -->
Memory index: C:\work\project\.ciel\memory\index.okf
<!-- ciel-runtime:remote-memory:end -->
```

The absolute address is verified to remain below the current launch workspace
before injection. The block is inserted idempotently. Ciel does not create or append a Remote
Memory block in the launch directory's `CLAUDE.md`, `AGENTS.md`, or `GEMINI.md`.
During migration it removes a block previously managed by Ciel while preserving
the rest of the user-authored file.

## Safety and limits

- Paths containing traversal, Windows drive syntax, backslashes, or duplicates
  are rejected.
- Manifest and file URLs must resolve to HTTP or HTTPS.
- Authorization is attached only to downloads on the manifest origin;
  cross-origin public download URLs do not receive it.
- Defaults: 256 files, 1 MiB manifest, 4 MiB per file, and 32 MiB total.
- Downloaded files must be valid UTF-8 text in one of the supported formats.
