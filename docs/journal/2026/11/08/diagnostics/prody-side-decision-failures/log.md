# Change log

## 2026-11-08

- Follow-up: offered-versus-attempted tools across 40 actors. Native tools are refused as unavailable. One actor queued `restart_session` through the inherited router MCP.
- Follow-up: side actors still request `_Walkie_Haiku_Terra` at 18:50Z. Prody's workspace config is unreadable from the read-only context. Local walkie stores `model=_Walkie_Haiku_Terra` and `comparison_model=_API_KEYS-SSE-1`.

- Confirmed Prody runs beta `857fd52`; receiver is connected/read and its cursor advances.
- Parsed 63 side actor transcripts read-only.
- Confirmed all 370 recorded model turns requested `_Walkie_Haiku_Terra`, not `_API_KEYS-SSE-1`.
- Replayed the installed decision parser and host validation rules: 14 valid, 49 invalid/incomplete.
- Recorded the failure categories without event bodies, private paths, URLs, credentials or user identifiers.
