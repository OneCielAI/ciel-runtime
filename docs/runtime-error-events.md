# Runtime error events

Runtime errors are forwarded to the existing authenticated event interface:

- SSE: `GET /ca/events/stream?category=runtime.error`
- WebSocket: `GET /ca/events/ws?category=runtime.error`
- Recent buffer: `GET /ca/events/recent?category=runtime.error`

Events have `level: error`, `category: runtime.error`, `message`, and a
`source` of `cli-transcript` or `router-response`. Transcript events identify
`session_id`; routed errors identify `request_id`. These are not automatically
equivalent to the ID returned by a webchat submission.

The default transcript watcher recognizes explicit Codex error/stream-error
records and Claude assistant API errors, API retry system records, and failed
result records. It forwards provider error codes and retry metadata when present,
including errors concerning network access, authentication, billing or limits.
It does not classify ordinary conversation text or tool-result failures as CLI
failures. Retry notifications do not mean a request has permanently failed.

`runtime_error_events.enabled: false` disables transcript error projection;
turning off `tool_call_events.enabled` alone does not disable it. Shared event
logging must remain enabled. Router response errors also continue to appear on
the existing TUI observation stream and now enter the shared event stream.

Coverage is limited to routed response errors and errors actually recorded in
the selected CLI transcript. Terminal-only messages, OS-level kills and errors
before a transcript exists require separate lifecycle/transport instrumentation.
The recent event buffer is bounded and in-memory, not a durable crash archive.
The built-in Web Chat subscribes separately and displays runtime errors as
system bubbles, including whether the CLI is retrying. Duplicate event IDs are
suppressed. These display-only bubbles are not posted back into the CLI input
queue or persisted in channel history. Other applications can keep consuming
the same independent event interface. No new webhook destination is created.
