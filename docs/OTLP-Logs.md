# OTLP log ingestion

Ciel Runtime accepts OpenTelemetry logs through the standard OTLP/HTTP Logs
path:

```text
POST /v1/logs
Content-Type: application/json
Content-Encoding: gzip  # optional
Authorization: Bearer <workspace OTLP ingest token>
```

The current receiver supports OTLP/HTTP JSON. Configure exporters with
`OTEL_EXPORTER_OTLP_PROTOCOL=http/json`. Binary Protobuf and OTLP/gRPC are not
accepted by this endpoint.

The endpoint always requires a workspace-scoped OTLP ingest token, including
for loopback requests. Set it explicitly with `CIEL_RUNTIME_OTLP_LOG_TOKEN`, or
read the generated token from `<workspace-state>/telemetry-logs/ingest-token`.
The token file is created with owner-only file mode on platforms that enforce
POSIX modes. `X-Ciel-Telemetry-Token` is accepted when a sender cannot set a
Bearer header.

The OTLP path performs this dedicated authentication itself, so remote log
senders do not receive the Router administrative token. The Router must still
be configured to bind to an externally reachable interface. Log reads remain
behind the built-in `/ca/mcp` Router authentication boundary; no public log
read endpoint is added. The endpoint stores log bodies under the current
workspace's `telemetry-logs` state directory and does not insert the batch into
model context.

## File selection

Use the OpenTelemetry log semantic-convention attribute `log.file.name`. Logs
with the same logical filename append to the same active segment. If only
`log.file.path` is supplied, Ciel uses its basename. Resource attributes are
accepted as a fallback, although OpenTelemetry recommends these attributes on
the LogRecord. If neither is present, Ciel uses `<service.name>.log` or
`otlp.log`.

Use `log.record.original` when the exact source line is available. Otherwise
Ciel renders the LogRecord body with its timestamp and severity.

## Per-file rolling and TTL policy

Send these Ciel extension attributes on a LogRecord or Resource:

| Attribute | Meaning | Default | Bounds |
|---|---|---:|---:|
| `ciel.log.roll.max_bytes` | Maximum active segment size | 8 MiB | 64 KiB–1 GiB |
| `ciel.log.retention.max_segments` | Retained segments for this logical file | 8 | 1–1024 |
| `ciel.log.retention.ttl_seconds` | Segment TTL; `0` disables TTL deletion | 7 days | 0 or 60 seconds–365 days |

LogRecord attributes override Resource attributes. A supplied value updates the
persisted policy for that logical file; omitted values retain its current
policy. Size rolling runs during append, and a Router background worker applies
TTL cleanup every 60 seconds.

Each logical filename has numbered immutable segments. This keeps cursors valid
after a later roll:

```json
{
  "file": "application.log",
  "segment": 4,
  "line_start": 101,
  "line_end": 120,
  "offset_start": 8920,
  "offset_end": 10442
}
```

## Runtime notification

One private `telemetry_notice` is queued per accepted OTLP export. It contains
only file, segment, line, byte-offset, and record-count ranges. The notification
sets:

```json
{
  "ack_required": false,
  "response_expected": false,
  "logs_embedded": false
}
```

The model-facing prompt explicitly states that no acknowledgement or response
is required. HTTP `200 {}` remains the OTLP transport-level success response;
it is separate from agent acknowledgement.

## `telemetry_logs` MCP tool

The built-in stateless MCP endpoint exposes `telemetry_logs` with these actions:

- `list`: list logical files, policies, and retained segment cursors.
- `read`: read by `file + segment + offset + max_bytes`, or by
  `file + segment + line_start + line_end`. Byte offsets are the fastest path.
- `configure`: update one logical file's rolling and TTL policy.
- `roll`: start a new immutable segment for a logical file.
- `delete`: delete one segment or the whole logical file; requires
  `confirm=true`.

Example tool arguments for an exact range from a telemetry notice:

```json
{
  "action": "read",
  "file": "application.log",
  "segment": 4,
  "offset": 8920,
  "max_bytes": 1522
}
```
