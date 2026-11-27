---
type: fix-record
title: 라우터의 "stream ended before message_stop" 오류를 재시도하지 않아 포크가 슬롯을 쥔 채 멈추던 결함
description: 라우터가 상류 스트림이 message_stop 없이 끝났을 때 내는 API Error 문구가 일시 전송 오류 목록에 없어, 포크(와 메인)의 자동 재시도 대상에서 빠지던 경로를 수정
tags: [fork, retry, transport, streaming, andy, n2956, fail-first]
timestamp: 2026-11-27T13:05:00Z
---

# "stream ended before message_stop" 미재시도

## 라이브 근거 (Andy, 12:46Z 틱)

- 틱 finding: `slot held, fork absent since 12:23: n2956 a1838a3e P5 running`.
- lease `a1838a3e`(n2956, P5): `running`, `slot=true`, host `a77850735eb2c1c70`, 12:10:23Z bound.
- 포크 전사 `agent-a77850735eb2c1c70.jsonl`:
  - 12:15–12:16Z: 모델(`glm` 계열)이 도구 입력을 깨진 JSON으로 반복해서 보냈다(`__unparsedToolInput`, 크기 증가).
  - 마지막 기록 12:21:19.925Z: `isApiErrorMessage: true`, `error: "unknown"`, `apiErrorStatus: null`, 본문 `API Error: Upstream Anthropic stream ended before message_stop.`
  - 그 뒤로 기록이 없다.
- 같은 시각 라우터 로그 로컬 06:21:19: `anthropic_sse_upstream_incomplete provider=cielairouter model=_Walkie_P4-5 … message_delta=False`.
- `classify_fork_transcript()`가 이 전사에 대해 `None`을 돌려줬다. 재시도 대상이 아니었으므로 모드가 재시도를 예약하지 않았다(전사에 `Work fork API error … scheduling` 알림도 없다).

## 원인 (코드)

- 라우터 `streaming_anthropic.report_incomplete_stream()`은 상류가 `message_stop` 없이 끝나면 `"Upstream Anthropic stream ended before message_stop."`을 `api_error`로 보낸다.
- `runtime_input_retry.CLAUDE_TRANSPORT_FAILURE_TEXTS`에는 예외로 끊긴 경우의 `"… stream failed before message_stop ("`만 있고, 이 문구는 없었다.
- 그래서 `classify_claude_api_error()`가 `error=unknown`, 상태 없음으로 판정해 `transient=False`가 됐다.
- 이 목록은 포크 재시도(`work_fork_api_retry`)와 메인 typed resend(`claude_typed_turn_resend`)가 함께 쓴다.

## 수정

- `CLAUDE_TRANSPORT_FAILURE_TEXTS`에 `"API Error: Upstream Anthropic stream ended before message_stop."`을 추가했다.
- 이제 이 오류는 `transport_interrupted`(transient)로 분류되고, 포크는 모드의 1m×10, 10m×10 재시도를 받는다.

## 검증

- **fail-first:**
  - `tests/test_work_fork_api_retry.py`에 `upstream_incomplete` 사례를 추가했다. 수정 전에는 `classify_fork_transcript`가 `None`을 돌려줘 실패했다.
  - `tests/test_claude_typed_turn_resend.py::test_router_and_fetch_transport_failures_are_transient`에 같은 문구를 추가했다. 수정 전 실패였다.
  - 수정 후 두 파일과 `test_runtime_input_retry.py`는 58 passed, 42 subtests passed다.
- **라이브 전사 재분류:** Andy n2956 전사를 수정 코드로 분류하면 `{'kind': 'transport_interrupted', 'retryable': True}`다.
- 영향 회귀(9개 파일: fork_api_retry, typed_turn_resend, runtime_input_retry, codex_desktop_injection, channel_inflight, channel_pending_poll, channel_transcript, fork_turn_watch, architecture_contracts): Windows 9/9 파일 통과, Linux py310·py311 직렬 9/9 `failures=0 errors=0`.
