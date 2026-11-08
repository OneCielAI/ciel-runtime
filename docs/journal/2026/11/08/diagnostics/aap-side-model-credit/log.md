# Change log

## 2026-11-08

- Traced Betty's "Combo has no executable targets" through the .12 gateway log. The `_API_KEYS-SSE-1` combo's single Anthropic API-key target returned "credit balance too low" (HTTP 400). The gateway then marked the provider exhausted and emptied the pool.
- Confirmed that side forks on both hosts moved to `_Walkie_Haiku_Terra` and make Walkie read/send calls. Both hosts now run 6933b8d. All checks were read-only; nothing was changed on .12.
