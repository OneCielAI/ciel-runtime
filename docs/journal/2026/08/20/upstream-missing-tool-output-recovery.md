# Upstream missing tool-output recovery

## Observed failure

- Kevin's session returned HTTP 400 with
  `No tool output found for custom tool call call_aul297D4jWf5nUXpEmbine7E`.
- This is a new call ID after the earlier replay prefilter release, so the
  earlier claim that prefiltering alone fully resolved the failure is false.

## Confirmed source gap

- Replayed Responses input was balanced before dispatch.
- The Codex backend HTTP adapter handled upstream 400 verdicts for
  unverifiable encrypted reasoning, but did not handle a missing tool-output
  verdict received after provider-side validation.
- When that verdict occurred, the adapter relayed the 400 unchanged.

## Implemented boundary

- Parse the call ID only from the upstream's explicit missing-output verdict.
- Remove function/custom call and output records only when their `call_id`
  exactly equals the upstream-named ID.
- Rebalance the remaining replay input and retry before any response bytes are
  sent to the client.
- If the verdict has no parseable ID or the named ID is absent from the
  request, preserve and relay the original HTTP error without guessing.
- The rule is request-shape based and contains no agent, person, machine, or
  session-specific condition.

## Verification evidence

- `test_responses_input_compatibility.py`: 17 tests passed, including the
  reported call ID, exact-pair removal, unrelated-pair preservation, and
  nonmatching-error preservation.
- `test_codex_backend_item_repair.py`: 16 tests passed, including an actual
  simulated upstream 400 followed by a successful second request, plus an
  absent-ID case that relays the original error unchanged.
- The repository's full suite passed after the change: unit 1,105, router 902,
  channel 369, and runtime 244 tests; 136 declared environment-dependent tests
  were skipped. Full Ruff and `git diff --check` also passed.
- Live Kevin/Wing confirmation is not recorded: SSH to Wing on port 1004 was
  rejected by authentication, so the active runtime version and raw request
  cannot currently be inspected from this workstation.

## Current pre-release verification

- The combined focused release group passed 96 tests, including the exact
  missing-output verdict retry boundary.
- The complete suite passed: unit 1,105, router 902, channel 372, and runtime
  247; 136 declared environment-dependent tests were skipped.
- Ruff and `git diff --check` passed.

## Separate Wing symptom

- Wing also displayed the runtime's `empty end_turn` notice while using an
  Ollama Cloud DeepSeek model.
- That notice proves only that the translated result contained neither visible
  text nor a tool call. It does not prove that it has the same cause as the
  missing tool-output HTTP 400.
- The exact upstream stream shape remains unverified without Wing logs.
