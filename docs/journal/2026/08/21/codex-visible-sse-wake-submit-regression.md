# Codex visible SSE wake submit regression

## Observed failure

- WING showed the injected wake body collapsed in the Codex editor as
  `[Pasted Content 1048 chars]`, with the cursor still in the editor.
- Runtime log message `1221` recorded two full-prompt injections at 10:03:54
  and 10:04:15, followed by `channel_windows_console_body_fallback`; no Codex
  transcript user record exists for `1221`.
- Message `1220` recorded injections at 10:02:36 and 10:02:56. Its Codex
  transcript user record at 15:02:57.722Z contains the same complete wake body
  twice in one submitted turn.

## Confirmed code path

- The affected WING process logged `transport=conpty`, `enter=cr`,
  `submit_retries=4`, `confirm_submit=True`, `bracketed_paste=True`, and
  `display_body=True`.
- `ChannelPromptInjector` performed multiple submit attempts only when its
  pre-submit snapshot was non-empty.
- The Windows ConPTY transport returned no input snapshot, while the injected
  host snapshot callback is the tmux reader and returned no snapshot on WING.
- Therefore the configured four attempts reduced to one Enter per full-prompt
  injection. The later inflight retry injected the entire body again rather
  than retrying only Enter.

## Correction

- Windows ConPTY now exposes its captured child-output tail as a submission
  snapshot.
- The injector uses the transport snapshot only when the host snapshot is
  unavailable. A lack of output change after Enter now advances to the next
  configured submit attempt without rewriting the prompt body.
- ConPTY deliberately keeps `supports_input_snapshot=False` for body-prefix
  verification. Codex replaces long pasted text with a placeholder, so the
  output tail cannot prove that the original prompt prefix is present.
- SSE receiving, event persistence, wake formatting, and Claude/AGY launch
  policies are unchanged.

## Local verification

- Focused injection tests: 4 passed.
- Windows ConPTY tests, including native transport execution: 13 passed.
- Full unittest discovery: 2,640 passed, 136 skipped, in 296.415 seconds.
- Ruff: passed.
- Documentation metadata check: passed before this journal was added.
- npm dry-run package: 385 entries.

## Deployment verification

- Pending nightly publication and WING installed-runtime execution evidence.
