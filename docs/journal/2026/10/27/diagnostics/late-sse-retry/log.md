# Directory Update Log

## 2026-10-27
- **Creation**: Live-log diagnosis, baseline reproduction on installed 8ebacfa (-p and interactive), `_PreOutputSSE` guard with close handling, 72 transient tests, source proofs (fast, 70 s, exhaustion, interactive).
- **Fix**: CI router group failed on f54104c (held `response.created` lost on a following upstream read error); held bytes are now delivered before the failure. 75 transient tests, 25 related router files, source proofs rerun.
