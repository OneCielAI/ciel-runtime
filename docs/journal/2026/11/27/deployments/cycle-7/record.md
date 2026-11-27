---
type: deployment-record
title: 사이클 7 — 결정 저장 직전 claim 만료 보존, work_status parallel 신원
description: 2026-11-27 19시 배포. f202a92·5278150(CHANGELOG c8363a3)의 영향 회귀, npm 발행, 7개 루트 해시 확인 설치, 라우터 다섯 개 교체로 라이브 적용한 기록
tags: [deployment, regression, side-intake, work-status, andy, celly, kobe, prody, betty]
timestamp: 2026-11-27T19:25:00Z
---

# 사이클 7 배포

## 포함한 수정

| 기록 | 커밋 | 파일 |
| --- | --- | --- |
| [side-understanding-claim-expiry](../../fixes/side-understanding-claim-expiry/record.md) | `f202a92` | `claude_mods/ciel-work/hooks/side-intake.js` |
| [work-status-parallel-identity](../../fixes/work-status-parallel-identity/record.md) | `5278150` | `work_mcp_tools.py` |

- `c8363a3`은 CHANGELOG 항목만 바꿨다. 설치 대상이 아니다.

## 회귀

- **임시 격리 Windows:** `test_side_ack_gate`, `test_side_intake_mod`, `test_side_intake_mcp`, `test_work_mcp_tools` 결과는 171 passed, 1 skipped, 139 subtests passed다.
- **Linux py310·py311**(`run_serial.sh`, 파일 단위): 4개 파일 모두 `failures=0 errors=0`이다. 로그는 [linux-py310-py311](../../fixes/side-understanding-claim-expiry/evidence/linux-py310-py311.txt)이다.
- **`claude plugin validate`:** ciel-work와 ciel-work-gate 모두 통과했다.
- **`claude plugin test ciel-work`:**
  - 첫 실행은 98/1이었다. 실패는 `workflow.test.ts`의 gateCalls 4 기대에 3을 받은 것이다.
  - 이 변경으로 3회, HEAD 파일로 3회 다시 실행해 모두 99/0이었다([plugin-repeat](../../fixes/side-understanding-claim-expiry/evidence/cycle10-plugin-repeat.txt)).
  - 첫 실패는 재현되지 않았고 원인은 확인하지 못했다.

## 발행

- beta push는 `1117ed3..c8363a3`이다. `Publish to npm` 런 `46053947715`가 성공했다.
- npm beta는 `0.2.51-beta.20261127-190338.c8363a3`(`gitHead c8363a3517a1`)이다.

## 파일 설치

- 오버레이 `overlay-c8363a3`의 기반은 `165aa17`이다. 버전 파일 stamp `stamp-165aa17`은 165aa17 오버레이의 `.new` 파일로 만들었다(`39da1511a0b0`, `79037a2e5d93`).
- tarball과 git이 같은지 확인했다.

| 파일 | 해시 변화 |
| --- | --- |
| `side-intake.js` | `9aa9b6df4fec → 3f5e518ae6ce` |
| `work_mcp_tools.py` | `94799e41978d → b3bb18fbcab2` |
| `runtime_constants.py` | `39da1511a0b0 → c8ed1d87ea52` |
| `package.json` | `79037a2e5d93 → 6f47efbed4d1` |

- 순서: check 7/7 `base` → 19:15:50Z apply 7/7 `ok=True` → recheck 7/7 `new`.
- 로컬 2개 루트의 package.json은 `skipped_absent`다.

## 라이브 적용

| 호스트 | 라우터 교체(UTC) | 모드 리로드 |
| --- | --- | --- |
| Andy `9515` | `115520` → `116036`, 19:16:47–19:17:01Z | 19:16:58Z `ciel-work: reloaded` |
| Celly `9385` | `78204` → `33968`, 19:17:27–19:19:36Z(권한 상승 스크립트) | 19:16:06Z `ciel-work: reloaded`(파일 설치 뒤, 교체 전) |
| KOBE `9779` | `2452` → `15328`, 19:19:58–19:20:16Z | 다음 틱에 확인 |
| Prody `9592` | `7052` → `4924`, 19:20:27–36Z | 다음 틱에 확인 |
| Betty `9564` | `11060` → `6236`, 19:20:48–19:21:04Z | 다음 틱에 확인 |

- 모든 교체에서 런처와 Claude 세션은 그대로다(`claude same True`).
- 새 라우터는 모두 `0.2.51-beta.20261127-190338.c8363a3`이다.

## 라이브 효과

- **work_status parallel 신원(Andy):** 교체 후 `work_status`(json)의 `parallel.runtime`은 `claude`다. 이 값은 board version 12814에서 `work_status_parallel_probe.py`로 읽었다. 이전 보고는 `runtime=codex`였다.
- **claim 만료 보존:** 해당 이벤트가 오면 다음 틱에 확인한다. 확인 대상은 `side claim expired`로 인한 `invalid_answer` 격리가 다시 생기는지와, 생긴다면 renew가 거절된 경우인지다.

## 재시작 부작용

- 다음 틱에 각 호스트의 포크 오류와 MCP 재연결을 확인한다.
- 사이클 6에서는 Andy 포크 하나가 교체 순간 `StreamTruncated`를 받았다. 이번에도 같은 종류의 부작용이 생길 수 있다.
