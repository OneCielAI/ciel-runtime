---
type: diagnosis-record
title: Prody 메인 세션 중단(2026-11-25 03:30–04:49Z)
description: ciel-work 언로드로 Work 식별이 끊기고, 세션 자체 재기동 후 라우터·런처가 사라진 경위와 근거
tags: [prody, ciel-work, hooks-worker, restart-session, claude-bg]
timestamp: 2026-11-25T05:00:00Z
---

# Prody 메인 세션 중단

모든 시각은 UTC다. 읽기 전용 SSH 조회(.12, sandbox u41)만 했다. 운영 세션·라우터 재기동과 상태 파일 수정은 하지 않았다.

## 확인된 경과

| 시각 | 출처 | 관측 |
| --- | --- | --- |
| 11-24 18:18:25 | `~/.claude/daemon.log` | Claude Code 데몬(2.1.331, pid 6032)이 `bg spawned 8ee391dd (slash)`. `jobs/8ee391dd/state.json`의 template은 `bg`. 메인 세션은 이때부터 데몬이 호스팅하는 bg 작업(워커 claude.exe 11504/11220)으로 실행됨 |
| 03:29:58 | 메인 전사 `8ee391dd` | `ciel-work: Work gate check (host round): re-pumped · attention P5 · discovery asked` |
| 03:30:54.022 | 메인 전사 | `ciel-work: ciel-work was unloaded: it crashed the hooks worker` → 직후 `Client hooks/observatory.client.js: Client: the plugin is not loaded` |
| 03:30:54–03:46:06 | 메인 전사 | Work 도구 호출 없음(Walkie, Bash, TaskList만) |
| 03:45:39 | 메인 전사 | fork watch 알림 도착 |
| 03:46:06 | 메인 전사 | 첫 Work 호출 `work_parallel_status` → `conversation identity is unavailable; no shared Work fallback is allowed`. 같은 시각 `ciel-work:side-fresh` 에이전트 유형 제거 알림 |
| 03:46–04:26 | 메인 전사 | Work 호출 7건 모두 같은 거부. `n1293` 승인 기록과 `n1294` 배정 불가 |
| 04:26:41 | 메인 전사 | Prody 모델이 `restart_session(resume=true)` 호출, 요청 `5504b117…` 대기열 등록 |
| 04:27:31 | 메인 전사·bg timeline | 마지막 기록 |
| 04:27:33 | `restart-takeover.json` | 런처의 재실행 직전 스냅샷 기록(`runtime_launch.py`의 `_restart_takeover`) |
| 04:27:36 | `sse-gate-status.json` | `held / native_handoff_pending`, observer pid 7628 |
| 04:34–04:43 | 프로세스·포트 조회 | 9592 미수신. Prody용 ciel-runtime 런처·라우터·observer 7628 없음. 18:18 생성 bg 워커 11220(2.1.331)은 생존 |
| 04:48:35 | `daemon.log` | `bg settled 8ee391dd (killed)` |
| 04:48:42 | 프로세스 조회 | 11-22부터 떠 있던 PowerShell 4000에서 `ciel-runtime --continue` 시작 → 라우터 2684(04:49:01), CLI 8100(04:49:09). 9592 수신 재개 |

## 판단

- **Work 식별 상실의 직접 원인(확인):** ciel-work가 언로드되면서 Work MCP 호출에 `session_id`가 붙지 않았다. 라우터는 이 경우 `work_mcp_tools.py:405`에서 바로 거부한다. Prody 모델은 03:45 fork watch를 원인으로 적었지만 전사상 언로드가 15분 먼저 일어났고, 그 사이 Work 호출은 없었다. fork watch 알림이 원인이라는 근거는 없다.
- **재기동으로 회복되지 않은 이유(부분 확인):** 재기동 경로는 런처가 소유한 CLI를 종료하고 다시 실행한다. ciel-work를 잃은 프로세스는 데몬 소유 bg 워커 11220이었다. 이 워커는 04:27 재기동 뒤에도 04:48:35 kill까지 살아 있었다. 따라서 재기동은 문제의 워커를 교체하지 못했다. 04:27:33 이후 런처와 라우터가 종료된 원인은 확인하지 못했다. 그 시점 런처 기록이 없다(아래 로그 공백).
- **hooks worker 크래시 자체의 원인: 미확인.** Prody에는 `~/.claude/debug`가 없다. 엔진 기록에는 `crashed the hooks worker` 외 사유가 없다. 2.1.332 변경 기록에도 관련 항목을 찾지 못했다.

## 같은 크래시의 분포(최근 30시간 전사)

- KOBE `9b0de8fe` 01:00:46·03:09:00, KOBE bg `a0e19ac8` 11-24 23:45:54·02:45:11.
- Prody bg dispatch `2e7bf245` 11-24 23:47:34·02:56:21, Prody 메인 `8ee391dd` 03:30:54.
- 모두 Claude Code 2.1.331 기록이다. Andy(로컬 2.1.329), Celly, Betty 전사에서는 0건이었다.
- 직전 행은 대부분 ciel-work의 게이트 틱이었다(`Gate re-check queued for 0 …` 뒤 21–41초, 또는 host round 뒤 56초·2분 11초). 다만 Prody 메인 03:29–03:31에는 side 영수증이 없었다(REST). side actor 기동이 원인이라는 근거는 없다.

## 함께 확인된 사항

- Prody의 Claude Code 자동 업데이트가 30분 간격으로 `npm install --global @anthropic-ai/claude-code@2.1.332`를 반복했다(04:05:15, 04:35:15; `.last-update-result.json` 04:35:51 `2.1.331→2.1.332`). 데몬은 03:35:38(바이너리 삭제), 03:49:00, 04:05:41, 04:36:12에 업그레이드 재시작했다. 03:30:54 크래시는 03:35:38 삭제보다 먼저였다. 두 사건의 인과 근거는 없다.
- `router.log`의 시각 기록 행은 11-22 18:37:55Z가 마지막이다. 이후에는 표준 오류 추적만 있다(파일 35.5 MB, 회전 한도 1 MB). 이전 틱의 Prody "라우터 경고 0"은 이 공백 때문에 무효다. 기록이 멈춘 원인은 확인하지 못했다.
- 04:48 재개를 누가 실행했는지는 확인하지 못했다(PowerShell 4000, 부모 3460).

## 남은 확인

- hooks worker 크래시 사유: 엔진 디버그 기록이 있는 세션에서 재현해야 확인할 수 있다.
- 04:27:33 이후 런처 종료 경위: 해당 시각 런처 출력이나 로그가 필요하다.
- router.log 시각 기록 공백 원인.
