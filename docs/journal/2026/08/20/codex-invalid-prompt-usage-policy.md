---
title: "Codex 'Invalid prompt: usage policy' 반복 오류 — 근본 원인 격리"
date: 2026-08-20
status: verified
session: 01a01dfb-f4bd-7ec0-a74d-6f69534124fe (ciel-runtime 폴더, provider=openai)
---

# 증상

`C:\Users\djlov\ciel-runtime`에서 쓰던 Codex 세션(및 그 fork `01a02016`)이 어떤 입력을 보내도
즉시 실패:

```
Invalid prompt: your prompt was flagged as potentially violating our usage policy.
```

이 세션은 `model_provider: openai` (native)로, ciel-runtime을 경유하지 않았다.

# 방법 (재현 하니스)

1. temp `CODEX_HOME`에 auth.json + rollout 사본 구성
2. `codex exec --sandbox read-only resume <id> "Reply with exactly: OK"` 로 재현 — 원본 그대로 → 항상 FLAG
3. rollout의 response_item 761개를 prefix 이분 탐색 (`scratchpad/prune.py`, `probe.sh`)

# 이분 탐색 결과

| 유지 범위 | 결과 |
|---|---|
| [0,380) [0,570) [0,617) [0,641) [0,653) [0,659) [0,662) [0,664) | OK |
| [0,665) | FLAG |
| 전체 761개 − item 663/664 pair | OK |
| 전체 761개, item 664 출력만 REDACTED | OK |
| 전체 761개, item 664 출력에서 "Line : 1486 … compacted" 블록만 제거 | OK |

# 확정된 원인

rollout 파일 line **1508**(custom_tool_call) / **1510**(custom_tool_call_output) 한 쌍.
이 도구 출력은 cielarvis 세션(`01a01dff…`)의 rollout을 PowerShell로 덤프한 것으로,
그 안에 DeepSeek cross-provider compact가 만든 `compacted` 레코드 인용문이 들어 있다:

> "Another language model started to solve this problem and produced a summary of its
> thinking process. You also have access to the state of the tools that were used by that
> language model. Use this to build on the work that has al…" (240자 절단 인용)

이 인용 블록 하나를 제거하면 나머지 히스토리 전체(같은 문구의 후속 인용 포함)를 그대로 두어도
통과한다. 즉 OpenAI 업스트림의 프롬프트 정책 검사가 이 특정 블록(주변 문맥 포함)을 차단하고,
Codex는 매 턴 전체 히스토리를 재전송하므로 이후 모든 턴·fork가 동일하게 실패한다.

# 함의

- ciel-runtime 결함 아님 — native openai provider 세션에서 재현·격리됨.
- compact는 정책-플래그 콘텐츠를 제거하지 못한다 (cielarvis 세션에서 실측).
- 오류 verdict에 항목 ID가 없어 codex-rejected-reasoning 방식의 자동 제거는 불가.

# 검증된 복구 절차 (사본에서 검증 완료, 실 파일은 미수정)

1. 해당 codex 세션/앱 종료 (파일 잠금 회피)
2. `~/.codex/sessions/2026/08/20/rollout-…01a01dfb….jsonl` line 1508·1510 두 줄 삭제,
   또는 line 1510의 output에서 문제 블록만 제거
3. `codex resume 01a01dfb-…` — temp 사본에서는 이 상태로 정상 응답("OK") 확인됨
4. fork `01a02016`은 parent 수정 후 재확인 필요

관련 도구: scratchpad `prune.py` / `prune2.py` / `prune3.py` / `probe.sh`

---

# 후속 (같은 날, 세션 2·3 해소 확인)

OneCielDMSUI(`01a007ca`)·cielarvis(`01a01dff`)는 **콘텐츠 트리거가 없었다**.
동일 히스토리 + 격리 ciel-runtime 라우터 + 동일 계정 + 실제 실패 메시지("계속")로
재생 시 전부 통과했고, cielarvis는 아무 수정 없이 사용자 실사용에서도 정상 동작 확인.

- 두 세션은 16:14~16:15Z에 동시에 실패 시작, ~40분간 재시도 전부 실패, 이후 해소
- 라우터 access 로그는 전 요청 200 — 플래그는 SSE 스트림 내 업스트림 오류
- 결론: 업스트림(OpenAI 정책 검사)의 일시적 차단 상태. 내부 원인은 외부에서 확인 불가
- `01a01dfb`만 별개: 인용 블록 콘텐츠 트리거가 오늘도 결정적으로 재현됨 (미수정 상태)
