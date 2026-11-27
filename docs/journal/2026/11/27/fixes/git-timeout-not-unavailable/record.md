---
type: fix-record
title: 일시 git 타임아웃을 워크스페이스 사용 불가로 기록하던 결함
description: 검증 git 한 번이 시간 예산을 넘긴 것만으로 정상 워크트리를 workspace_unavailable(stranded)로 기록하던 경로를, 타임아웃은 재시도 가능한 실패로 다루도록 수정
tags: [work-parallel, workspace, git-timeout, stranded, celly, n1635, fail-first]
timestamp: 2026-11-27T08:20:00Z
---

# 일시 git 타임아웃을 워크스페이스 사용 불가로 기록하던 결함

## 라이브 근거 (Celly n1635)

- stranded 행의 `reason`은 `git -c core.quotePath=false timed out after 39 s`였다(행 갱신 04:50:05Z). 같은 시각 호스트 커밋 사용률은 97%였다.
- 같은 워크트리(`work-parallel-e922c98f…`)는 이후 `git log`과 `merge-base --is-ancestor` 검사가 정상이었다. 즉 소유권이 깨진 워크트리가 아니었다.
- 이 stranded 행이 [stranded-legacy-head](../stranded-legacy-head/record.md)의 영구 거부로 이어졌고, 결과는 fresh 워크스페이스 재작업으로 밀려났다.

## 원인 (코드)

- `work_parallel_workspace._git()`은 `subprocess.TimeoutExpired`를 일반 `WorkspaceError`로 바꿔 던진다.
- `work_parallel_mcp._workspace_unavailable_reason()`은 `observe()`의 모든 `WorkspaceError`를 사용 불가 사유로 돌려준다.
- 그런데 `_publish_pending_review`는 "소유권 검증 실패만 stranded 행이 된다"고 명시한다. 타임아웃은 워크트리에 대해 아무것도 증명하지 않으므로 이 규칙 위반이다.

## 수정

- `WorkspaceError`에 `transient` 플래그를 추가했다. `_git()`의 시간 예산 초과만 `transient=True`로 던진다(내용 기준선 데드라인과 크기 제한은 결정적이므로 제외).
- `_workspace_unavailable_reason()`은 transient 오류에 `None`을 돌려준다. 모든 호출부(검수 결정, 게시, idle 기록, salvage, reconcile)가 이미 `None`이면 원래 오류를 다시 던지므로 stranded 행을 만들지 않고 재시도 가능한 실패로 끝난다.

## 검증

- **fail-first:** `tests/test_work_parallel_rework_worktree.py::BrokenRetainedWorktreeTests::test_git_timeout_at_decision_is_not_a_broken_workspace`. 재작업 결과를 게시한 뒤 `_GIT_TIMEOUT_SECONDS=0.0001`로 결정한다.
  - 수정 전: stranded 행이 생겼고(`reason 'git rev-parse --show-toplevel timed out after 2 s'`), 레거시 검수 행은 제거됐다.
  - 수정 후: 결정은 `timed out` 오류로 실패하고, stranded 행이 없고, 검수 행과 워크트리가 그대로 남는다. 다음 결정은 `finalized`다.
- 회귀 결과는 [사이클 4 회귀](../../deployments/cycle-4/record.md)에 있다.
