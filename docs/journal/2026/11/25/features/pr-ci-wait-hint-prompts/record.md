---
type: feature-record
title: GitHub PR·CI/CD 대기 힌트 기록 및 SSE 전달 프롬프트 강화
description: 포크 세션이 외부 PR·CI/CD 결과를 기다리기 전에 워크보드 리프에 대기 신호·식별자를 기록하고, 사이드·게이트 액터가 이를 대조해 포크 또는 메인에 라우팅하도록 관리 프롬프트 강화 및 배포·라우터 재시작
tags: [work-parallel, prompts, question-wait, side-intake, github-ci, beta-deploy, router-restart]
timestamp: 2026-11-25T00:36:00Z
---

# GitHub PR·CI/CD 대기 힌트 기록 및 SSE 전달 프롬프트 강화

## 변경 범위

- **관리 프롬프트 기본값** (`ciel_runtime_support/work_texts.py`, 생성물 `ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js`, `ciel_runtime_support/work_workflow_prompts.py`):
  - `mod_fork_question_wait`: GitHub PR 생성·갱신 후 CI/CD·배포 등 외부 결과를 기다리기 전에 전사·저널·백그라운드 폴링으로 슬롯을 점유하지 말고, 할당된 리프 Work 아이템에 `work_parallel_report(event=question)`으로 저장소, PR 번호, `head SHA`, 워크플로/체크, 확인된 `run ID`·`attempt`(없으면 지어내지 말고 read-only 조회 방법), 기다리는 결론(`release_hint.condition`), 검증 조회(`release_hint.lookup`)를 기록하고 성공 확인 후 턴을 끝내도록 강화.
  - `side_external_wait_routing` (신규 관리 프롬프트): GitHub PR·CI/CD·배포 SSE 도착 시 보드의 `waiting_on`·`release_hint`부터 대조(요약에 없으면 `work_status`로 관련 목표·리프 조회)하고, 정확한 저장소·PR·`head SHA`·워크플로/체크·`run ID`·`attempt`가 `release_hint.condition`을 충족할 때만 `release_waits`로 파킹 포크 재개를 요청하도록 규정. 이전 커밋·다른 실행·진행 중 알림·단순 언급 해제 금지, 실패·취소가 성공 대기를 충족하지 않으면 대기 유지, 실행 중 포크 대응 건은 `relay=true`, 대응할 포크가 없거나 조건 불충족·메인 판단 필요 건은 `ask_main=true`, 외부 결과는 새 실행 승인이 아님을 명시.
  - `gate_actor` 및 `gate_recheck`: 주기 게이트 점검에서도 GitHub PR·CI/CD·배포 게이트의 저장소·PR·`head SHA`·워크플로/체크·`run ID`·`attempt`와 `release_hint.condition` 일치를 검증하도록 보강.
- **코드 연결** (`ciel_runtime_support/work_workflow_tools.py`):
  - `side_instruction()`이 `side_complaint_and_urgency`, `side_running_fork_relay` 뒤에 `side_external_wait_routing`을 함께 조립해 라우터가 모든 `work_side_claim`에 전달하도록 1줄 연결.
- **스킬 문서** (`ciel_runtime_support/claude_mods/ciel-work/skills/work-operations/SKILL.md`):
  - `question_wait` 섹션에 외부 PR·CI/CD·배포 대기 시 필수 기록 항목 요약 추가.
- **테스트** (`tests/test_work_texts.py`, `tests/test_side_intake_mcp.py`):
  - `test_external_ci_wait_prompt_connects_the_worker_and_sse_actor` (RED 실패 확인 → GREEN 통과; `side_instruction` 기본값 및 `set_prompt` 오버라이드 포함).
  - `test_side_intake_mcp.py::test_both_context_profiles_receive_complete_current_work_not_preview`: 25노드 `work_side_claim` 응답이 8,000자 페이지 한도(`host_result_pages.PAGE_CHARS`)를 넘겨 `paged`로 반환될 때 `work_host_page`로 정상 재조립해 검증하도록 보강.

## 검증 (커밋 `2620310f51fb188d1f8a7c1f53eb53115c9211dc`)

- **격리 Windows 회귀** (`CIEL_RUNTIME_*` 제거, 임시 `CIEL_RUNTIME_CONFIG_DIR`, `CIEL_RUNTIME_TEST_ISOLATED=1`):
  - `test_work_texts.py` 9, `test_side_running_fork_relay.py` 3, `test_side_relay_notice.py` 13, `test_side_intake_mcp.py` 46(1 skip), `test_side_intake_mod.py` 72, `test_side_background_prompts.py` 9, `test_work_gate_check.py` 25, `test_work_gate_parked_forks.py` 9, `test_work_mcp_tools.py` 32, `test_router_io_performance.py` 9, `test_host_result_pages.py` 7, `test_work_parallel_finalization.py -k question_wait` 8 — 총 242 tests(1 skip, 0 fail).
  - `claude plugin validate` 통과, `claude plugin test` **95 pass / 0 fail**.
  - `ruff check`, `npm run check:docs`, `node --input-type=module --check`, `py_compile`, `npm pack --dry-run` 통과.
- **기존 Linux 컨테이너 회귀** (`ciel-runtime-test-py310`, `ciel-runtime-test-py311`):
  - 동일 12개 스위트 양쪽 컨테이너 각 242 tests(1 skip, 0 fail) 통과.

## 배포 및 Ciel 라우터 재시작 (2026-11-25 UTC, 사용자 명시 지시)

- `2620310f51fb188d1f8a7c1f53eb53115c9211dc`를 `feature/priority-models`·`beta`에 atomic push.
- `Publish to npm` 실행 `45517881285` 성공:
  - `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261125-002503.2620310`
  - `gitHead` = `2620310f51fb188d1f8a7c1f53eb53115c9211dc`
  - tarball 5개 변경 파일 SHA-256 전부 git 커밋과 일치.
- 해시 확인 오버레이 `overlay-2620310` (`00:32:19–00:32:47Z`):
  - 승인된 7개 베타 루트(로컬 `hotfix5`·`.local/share/ciel-runtime`·전역 npm, Celly, KOBE `u24`, Prody `u41`, Betty `u42`) 모두 설치 전 `base` → 설치 후 `new` 확인(`.cmd` 래퍼 미변경).
- Ciel 라우터 재시작 및 라이브 검증(5/5 모두 `version=0.2.51-beta.20261125-002503.2620310`, `source_fingerprint=5cff1e881e0801be`, 기존 `active_client_pids` 유지, REST `/runtime` `ok=True`, MCP `recover_sse_delivery(status)` `ok=True / gate=open`):
  - Andy(9505): `PID 92856 → 89532`, `clients=[88952]`, REST `/workflow/prompts` 3종(`side_external_wait_routing`, `gate_actor`, `mod_fork_question_wait`) 모두 `using_default=True`, `head SHA` 포함 확인.
  - Celly(9385): `PID 68448 → 102292`(권한 분리로 `router_debug` `off` 유지 인프로세스 재시작), `clients=[101552]`, `side_external_wait_routing`·`mod_fork_question_wait` `using_default=True` 확인; **`gate_actor`는 Celly에 저장된 사용자 지정 오버라이드(`using_default=False`)가 유지**되어 기존 오버라이드를 덮어쓰지 않음.
  - KOBE(9779): `PID 2008 → 18196`, `clients=[15784]`, 3종 모두 `using_default=True`, `head SHA` 포함 확인.
  - Prody(9592): `PID 8500 → 13056`, `clients=[7628]`, 3종 모두 `using_default=True`, `head SHA` 포함 확인.
  - Betty(9564): `PID 12480 → 5384`, `clients=[8964]`, 3종 모두 `using_default=True`, `head SHA` 포함 확인.
- 제외·미도달: Sarah(`100.64.0.25:1004`) SSH 배너 타임아웃으로 미업데이트·미재시작.
- **라이브 효과(파일 설치와 구분):**
  - `side_external_wait_routing`과 `gate_actor`/`gate_recheck`(기본값 사용 호스트)는 라우터가 `work_side_claim` 응답으로 직접 전달하므로 **라우터 재시작으로 즉시 라이브 반영**된다(Celly의 `gate_actor`는 저장된 커스텀 오버라이드 유지).
  - `mod_fork_question_wait`(`texts.generated.js`)과 `work-operations/SKILL.md`는 7개 루트 파일 설치가 완료됐으며, `claude.exe` 프로세스를 재시작하지 않았으므로 기존 실행 세션에서는 다음 세션/플러그인 재로드 시 기본값으로 반영된다(`work_side_texts`는 저장된 오버라이드만 전달).
