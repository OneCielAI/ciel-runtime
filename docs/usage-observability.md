# Usage observability

Ciel Runtime stores routed model usage in a workspace-scoped SQLite ledger and
exposes authenticated event, stream, and period-snapshot interfaces.

## Startup environment

The first router start can provision consumer keys and outbound delivery without
writing secrets into `config.json`.

```powershell
$env:CIEL_RUNTIME_USAGE_API_KEYS = '[{"id":"auditor","key":"replace-me","scopes":["usage:read","usage:stream"]}]'
$env:CIEL_RUNTIME_USAGE_PUSH_ENDPOINTS = '[{"id":"audit-service","url":"https://audit.example/v1/ciel-usage","authorization":"Bearer ${AUDIT_SERVICE_API_KEY}","start_mode":"tail","audit_interval_seconds":86400}]'
$env:AUDIT_SERVICE_API_KEY = 'replace-me'
ciel-runtime
```

Single-destination variables are also supported:

```text
CIEL_RUNTIME_USAGE_API_KEY
CIEL_RUNTIME_USAGE_PUSH_ID
CIEL_RUNTIME_USAGE_PUSH_URL
CIEL_RUNTIME_USAGE_PUSH_AUTHORIZATION
CIEL_RUNTIME_USAGE_PUSH_API_KEY
CIEL_RUNTIME_USAGE_AUDIT_INTERVAL_SECONDS
CIEL_RUNTIME_USAGE_BACKFILL_PATHS
```

The same persistent workspace settings can be injected with
`ciel-runtimectl`. This writes the workspace `config.json`; it does not mutate
the parent shell's environment:

```powershell
ciel-runtimectl usage-events endpoint_id=audit enabled=true url=https://audit.example/v1/ciel-usage 'authorization=Bearer ${AUDIT_SERVICE_API_KEY}' start_mode=tail audit_interval_seconds=86400
ciel-runtimectl usage-events endpoint_id=audit
ciel-runtimectl usage-api-key issue name=auditor 'scopes=read,stream' ttl_seconds=2592000
ciel-runtimectl usage-api-key list
ciel-runtimectl usage-api-key revoke key_id=uk_replace_me
```

`usage-events` accepts `endpoint_id`, `enabled`, `url`, `authorization`,
`api_key`, `timeout_seconds`, `poll_interval_seconds`, `start_mode`,
`audit_interval_seconds`, `audit_emit_on_start`, `jsonl_enabled`, and
`backfill_paths`. On
Windows, delimit multiple `backfill_paths` with `;`; on POSIX systems use `:`.
The issued usage API key is displayed once, while only its keyed digest is
persisted. A running router reloads outbound destinations on its next poll;
new `backfill_paths` are scanned on the next router start.

To provision the exact identity and secret supplied through
`CIEL_RUNTIME_USAGE_API_KEYS`, pass `key_id=... api_key=... expires_at=...` to
`usage-api-key issue`. Omitting those values generates a new ID and secret.

`CIEL_RUNTIME_USAGE_PUSH_ENDPOINTS` is a JSON array. It replaces configured
`usage.push_endpoints` for that process. Each item supports `id`, `url`,
`authorization` or `api_key`, `timeout_seconds`, `poll_interval_seconds`,
`start_mode` (`tail` or `beginning`), `audit_interval_seconds`, and
`audit_emit_on_start`.

Authorization values may contain `${ENVIRONMENT_VARIABLE}` references. A
missing reference prevents delivery and does not advance the cursor.

## Consumer keys

Consumer keys are distinct from upstream provider credentials and the router's
external administration token. Only a keyed digest is stored. The plaintext is
returned once when a key is issued.

Issue a key locally, or externally with the router administration token:

```http
POST /ca/usage/keys
Content-Type: application/json
Authorization: Bearer <router-administration-token>

{"name":"auditor","scopes":["usage:read","usage:stream"]}
```

List key metadata with `GET /ca/usage/keys`. Revoke a key with:

```json
{"action":"revoke","key_id":"uk_..."}
```

sent to `POST /ca/usage/keys` under router administration authentication.

## Interfaces

All consumer routes accept `Authorization: Bearer <usage-api-key>` or
`x-ciel-usage-key: <usage-api-key>`.

- `GET /ca/usage/events?from=<RFC3339-or-epoch>&to=<...>&after=<seq>&limit=200`
  returns immutable events. Optional filters are `runtime`, `provider`, and
  `model`.
- `GET /ca/usage/stream?after=<seq>` streams durable events with the SQLite
  sequence as the SSE `id`. Reconnect with the last acknowledged sequence.
- `GET /ca/usage/snapshot?from=<RFC3339-or-epoch>&to=<...>` returns totals,
  provider/model/runtime groups, sequence coverage, estimated/incomplete event
  counts, requests per hour, tokens per hour, and cache-read ratio.

When `from` and `to` are omitted, snapshot and event queries cover the previous
24 hours.

## Outbound delivery and audit

Each configured receiver gets two CloudEvents:

- `ai.oneciel.ciel-runtime.usage.recorded` for each new usage event.
- `ai.oneciel.ciel-runtime.usage.audit` for each configured audit interval.

The audit event contains the exact period boundaries, first and last ledger
sequence, totals, rates, and provider/model/runtime groups. A percentage of a
provider quota is not fabricated when the provider does not expose that quota.

Delivery state is keyed by endpoint ID, URL, and authorization-key fingerprint.
HTTP failure leaves both the event cursor and audit-period cursor unchanged.
Retries reuse the same CloudEvent ID and `Idempotency-Key`; a cursor advances
only after a 2xx response.

The ledger is stored under the current workspace state directory at
`usage/usage.sqlite3`, so different workspaces do not share usage data.

## Pre-deployment backfill

At router startup Ciel imports workspace-owned `usage-events.jsonl` files and
their `.1` rotation into the SQLite ledger. Each source has a durable byte
cursor, incomplete trailing records are left for the next startup, and legacy
records without an event ID receive a deterministic content ID so rotation does
not duplicate them.

Older global files cannot be assigned to a workspace from their contents. They
are therefore imported only when explicitly named:

```powershell
$env:CIEL_RUNTIME_USAGE_BACKFILL_PATHS = '["C:\\Users\\me\\AppData\\Roaming\\ciel-runtime\\usage-events.jsonl"]'
```

Backfilled legacy rows without cache/reasoning fields are marked
`is_incomplete=true`. A period in which neither Ciel nor the native CLI stored a
usage record cannot be reconstructed by this importer.
