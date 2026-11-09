# Change log

## 2026-11-09
- Owned real console, main busy 420 s: the fixed build answered a status question 23 s after send during main's turn. On 83b4427 the same question waited until main's turn ended, and main's handoff was refused with the identity error.
- Wake prompts now settle in the background with one in flight per conversation. The `work_side_handoff` intercept returns router errors to main. Regression tests fail on the old files.
- Diagnosed Prody 118635: one awaited wake stopped every side claim from 00:54:10 to 01:57:34 UTC.
