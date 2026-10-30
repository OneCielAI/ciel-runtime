---
type: Diagnostic
title: Grant screenshot17 identity hold and SSE observations
description: Approval event arrival correlates with a completed routed turn; receiver transport was connected when captured, while exact identity hold cause remains unavailable.
tags: [sse, identity, grant, work]
timestamp: 2026-10-30
---
# Evidence captured before user restart

Screenshot17 shows `SSE gate on: held(identity_pending)`, an injected approval101176 message, Work mode notification and idle prompt. Captured local8872 health identifies workspace `E:/aap/u22/home/ganti-ai`, account `aapw-a20u22`, installedb79701c. The receiver last_event_id exactly matches the screenshot event99aff300/101176/message.approved. This establishes event correlation absent from the previous report.

The approval receiver event timestamp is01:42:49.589 local. Routed turn starts01:42:50.502, completesHTTP200 at01:42:52.944, and native agent ends `end_turn` at01:42:55.236. Current observation shows SSE receiver `connected`, worker alive, heartbeat observed, EOFError phase read retained as last failure and30recoveries/31attempts. No claim is made that the receiver was uninterrupted.

# Boundaries

A displayed gate hold does not prove a dead SSE transport. Receiver transport connection, private input queue status and compaction identity admission are different observations. The captured receiver delivery queue reports zero, while an earlier general runtime status reported one; these are different observation times/scopes and are not proof of lost input. Work hook text appears as the routed request input projection; that projection alone does not prove whether approval content was or was not in the full request.

Direct sandbox router.log and native session metadata remain denied by Windows ACL. Telemetry logs list is empty. Exact identity binding transition, reason for repeated EOF and whether Work causes either condition remain unverified. No forced restart, queue/cursor clearing, settings mutation or identity bypass was performed. User stated they would restart after capture; this is not evidence a restart already occurred.

# Local evidence

- [Capture receipt](evidence/capture.json)
- [Selected timeline](evidence/timeline-summary.json)
- [Receiver state](evidence/receivers.json)
- [Screenshot17](evidence/screenshot17.png)

Source analysis completed. No incident root-cause repair or verified recovery is claimed; the independently reproduced safety defect below is distinct from the reported prolonged hold.

# Restart observation

User reported restarting. Port8872 was temporarily connection-refused, then returned with PID98652, b79701c and active client42456 (previous PID100900/client98444). This is an observed user restart, not an agent-initiated repair. [Post-restart capture](evidence/after-user-restart.json) shows SSE connected/worker alive with8attempts,6recoveries, last failure EOFError phase read. EOF/reconnect therefore also occurs after restart. Idle timeout was null and no heartbeat observed at that snapshot; these differ from the pre-restart observation and do not prove why the peer closes the stream.

A read-only `recover_sse_delivery` action=status on the exact sandbox router reports `admission_paused:false`, `pause_observable:true`, no queued inputs, no problem and reset=false. [Response](evidence/after-restart-delivery-evidence.json). This supplies current launcher gate evidence unlike the generic delivery-health field. It does not reconstruct the prior held transition.

# Independently reproduced safety defect and source fix

`RuntimeCompactionBinding._observe_request()` captures sessionA, estimates its request, then writes request_observed using only the launch epoch. A same-epoch published retarget to busy sessionB can occur between capture and write, incorrectly proving B ready with A's stale request. This is a false-release defect, not evidence of the reported prolonged hold.

- [Baseline regression](evidence/race-baseline.txt) fails because successorB request_observed becomes true (one test/one assertion failure, no errors).
- `_update()` now optionally validates expected_session under the same scope file lock; request_observed supplies the captured scope.session_id. No identity timeout, process ownership validation, or handoff guard is relaxed.
- [Focused regression result](evidence/final-focused-tests.json):133pass, zero failures/errors across binding, identity, readiness, control, SSE gate, native handoff and receiver tests. Ruff for the changed source/test and git diff --check pass.
- [Real-thread runtime witness](evidence/threaded-runtime-witness.json) uses owned published records and durable scope files. Paused requestA resumes after same-epoch retargetB: B stays held(identity_pending); the exact B request then opens it. All four checks pass. This is an actual source runtime fixture, not a live sandbox end-to-end recovery claim.

# Remaining incident boundary

Exact pre-restart identity scope/native record/log transition is still ACL-denied. Cannot identify which of scope change, missing readiness publication, peer process change or display timing produced screenshot17. Source correction above is not installed or published, and b79701c live router does not contain it. No forced unblock, repeated input, queue/cursor clear, or live-session mutation was performed. Original prolonged-hold root-cause repair remains blocked on the missing identity evidence; repeated EOF cause also remains unconfirmed.

# Recurrence diagnostic improvement

A later user-reported recurrence correlated with router68568/b79701c. Current read-only recovery evidence still says admission_paused=false, queued0; receiver connected and routed tool requests continue. This does not disprove the screen symptom. The prior statement that any120second hold necessarily implies a new identity was too strong: status writers/snapshot timing also need investigation.

The existing `recover_sse_delivery` status API now returns bounded payload-free persisted identity and gate evidence: hashed epoch/session, identity age/readiness/request proof/handoff flags, launcher PID, gate snapshot age/state/reason and observer PID. The persisted snapshot is captured before the fresh gate evaluation can overwrite it. This is diagnostic observation only, never bypass authority. No new tool is needed. Missing/corrupt/oversized files remain explicit unknown; raw paths/socket/session identifiers are not returned.165focusedtests pass before the final snapshot-order adjustment; final regression evidence is retained separately.

Raw sandbox API observations and screenshots are retained locally only; do not publish their private message contents. Diagnostic code and owned-fixture regression evidence can be distributed without those captures.
