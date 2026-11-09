# Change log

## 2026-11-08
- Beta `0.2.51-beta.20261109-003242.1581aa5` published, registry verified, installed locally; installed real console run passed all four phases.

- Owned real console run 18:03: model switch, triage-only tools, and one durable wake that main acknowledged without a human prompt.
- Fixed review and real-run findings: spawn-hook model binding, stricter allowlist, real Walkie names, no handoff for answer/clarify or main-reply follow-ups, a durable wake ledger, a single path to main, and decision field normalization.

- Traced the stale side model to a same-turn `agent.register` + `agent.spawn` without a spawn `model`; the spawn now names it.
- Replaced inherit-all side MCP with a fail-closed read/reply/status allowlist, stopped spawning native forks for external events, and filtered projected parent tool schemas to the offered set.
- Rewrote the side prompt: code/config/test/build/deploy requests get read-only investigation and are queued for main; replies say main resume is pending.
- Added a durable side-to-main handoff for waiting/blocked targets and P1 results, a `$.prompt.submit` wake, `clear_wait` limited to recorded unchanged waits, and `main_handoffs` in `work_side_status`.
- Side note appends no longer return an executing goal to planning; the board is refreshed right after a side actor completes.
