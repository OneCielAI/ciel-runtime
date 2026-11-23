---
type: fix-record
title: Celly의 "can't rule out a dangerous removal" 승인 창이 자동 허용되지 않던 결함
description: 위험 삭제 자동 허용 옵션이 있어도 Claude Code 2.1.330의 unparsed_line 삭제 질문은 막을 수 없었다. 원인을 배포본 코드와 실엔진으로 확인했고, Claude Code 자체 면제 스위치와 PermissionRequest 처리기 제외로 고쳤다.
tags: [dangerous-rm, claude-code-2.1.330, permission-request, celly]
timestamp: 2026-11-23T19:05:00Z
---

# 사용자 보고 (2026-11-23 17:3xZ, 화면 캡처)

- Celly 세션에 "Claude Code can't be sure it read this command the way the shell will, so it can't rule out a dangerous removal. … Do you want to proceed?"가 떴다.
- 사용자는 이 질문을 무조건 승인하는 설정을 넣어 왔는데 동작하지 않는다고 했다.

# 확인

- ✅ 질문을 낸 쪽: Celly Work 포크 a5fbb4f9(n1389)의 Bash 호출이다(17:28:28Z). `cd …; python -I - <<'EOF' …`로 시작하는 6,474자 heredoc이었다.
  - guard 기록(`tool-events.jsonl`)에 PermissionRequest가 있고 guard 응답은 없었다.
  - 결과는 17:34:29Z에 왔다(사람이 승인한 것으로 보임).
- ✅ Celly 실행에는 옵션이 꺼져 있었다.
  - 관리자 권한으로 프로세스 환경을 읽기 전용으로 확인했다. Celly claude.exe(pid 70860, 82176)에는 `CIEL_RUNTIME_AUTO_ALLOW_DANGEROUS_RM`가 없다.
  - Andy 세션(pid 85692, 65012)에는 `=1`이 있다.
  - Celly 루트 config.json에는 키가 없다. 워크스페이스 config는 ACL 때문에 읽을 수 없다.
- ✅ 옵션이 켜져 있어도 이 질문은 막히지 않는다(실엔진 baseline).
  - guard는 삭제 동사(`rm`·`del`·`Remove-Item` 등)가 있을 때만 답한다. 이 명령에는 삭제 동사가 없다.
  - 이 질문의 판정 사유(`circuitBreaker: dangerousRemoval`, `removalRule: unparsed_line`)는 훅 입력에 실리지 않아 guard가 구분할 수 없다.
- ✅ 배포된 Claude Code 2.1.330 바이너리 코드에서 확인한 것
  - `dangerousRemoval`만 `waivableInBypass: true`다.
  - bypassPermissions에서 이 질문을 건너뛰는 조건은 다음 넷이 모두 맞을 때다.
    - `CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS`가 설정됨.
    - classic `PermissionRequest` 훅 등급이 `none`.
    - Bash·PowerShell·Read를 지정한 권한 규칙이나 `blockReadsOutsideWorkingDirectories`가 없음.
    - managed 설정 출처를 읽을 수 있음.
  - Ciel은 모든 이벤트에 guard를 설치하므로 PermissionRequest 훅이 늘 있다. 그래서 이 스위치를 넣어도 면제되지 않는다(실엔진 hookwaive).

# 실패 테스트 먼저 (수정 전 HEAD에서 실패 확인)

- `tests/test_dangerous_rm_auto_allow.py::test_waiver_env_is_set_from_the_option_and_never_inherited`
- `tests/test_tool_guard.py::test_install_without_permission_request_keeps_other_handlers`
- `tests/test_dangerous_rm_launch.py` 2건(실제 `launch_claude` 경로): 옵션이 켜지면 두 스위치를 넣고 처리기를 빼는지, 꺼지면 둘을 지우고 처리기를 넣는지 확인한다.

# 수정

- `dangerous_rm_auto_allow.apply_launch_env`
  - 옵션이 켜지면 `CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS=1`도 넣는다.
  - 꺼지면 상속된 값까지 지운다.
- `tool_guard_hooks.install_tool_guard_hook_settings(without_permission_request=…)`
  - 옵션이 켜지면 guard의 PermissionRequest 처리기를 설치하지 않고 기존 것도 뺀다.
  - 사람이 넣은 다른 처리기는 남긴다.
  - bypass의 계획 종료 자동 허용은 PreToolUse 경로(`handle_plan_exit_pre_tool`)가 계속 맡는다.
- `runtime_launch.run_claude`
  - 옵션 결과를 훅 설치에 넘긴다.
  - INFO 로그 `claude_dangerous_rm_auto_allow … permission_request_hook=withheld`를 남긴다.
- 문서: `docs/Module-Map.md`.

# 검증

- 실엔진(Claude Code 2.1.330, 격리 `CLAUDE_CONFIG_DIR`, 스텁 API, 실제 자격 증명 없음). 명령은 Celly 포크 명령 그대로이고 cd 경로만 스크래치로 바꿨다. 스크립트는 없는 파일을 읽다 실패해 아무것도 쓰지 않는다.

  | 구성 | PermissionRequest 훅 | 면제 스위치 | 질문 | 명령 실행 |
  |---|---|---|---|---|
  | baseline(수정 전) | 있음 | 없음 | 표시 | 안 됨 |
  | hookwaive | 있음 | 있음 | 표시 | 안 됨 |
  | fixed(수정 후 런처 코드) | 없음 | 있음 | 없음 | 됨(`Exit code 129`, FileNotFoundError) |

  - 화면: [baseline](/docs/journal/2026/11/23/fixes/dangerous-removal-waiver/evidence/baseline-screen.png), [hookwaive](/docs/journal/2026/11/23/fixes/dangerous-removal-waiver/evidence/hookwaive-screen.png), [fixed](/docs/journal/2026/11/23/fixes/dangerous-removal-waiver/evidence/fixed-screen.png).
  - fixed 화면 상단에 Claude Code 경고 "CLAUDE_CODE_EVEN_MORE_DANGEROUSLY_SKIP_PERMISSIONS is set …"가 뜬다.
  - 라이브 `~/.claude.json`은 hookwaive 실행 중 해시가 바뀌었다. 테스트 없이 18:46:03Z에도 바뀌어서, 라이브 세션이 쓴 것으로 본다. 테스트는 격리 `CLAUDE_CONFIG_DIR`를 썼다.
- 영향 회귀(사용자 지시 "응급상황"에 따라 영향 파일만)
  - Windows 격리: 10개 파일 394/0(skip 42).
  - py310·py311 컨테이너: 9개 파일 각 378/0(skip 42).
  - ruff 0.15.22 통과.

# 남은 것

- Celly에 효과가 나려면 Celly 워크스페이스 설정에서 옵션(메뉴 `17. Dangerous delete prompt`)을 켜고 세션을 다시 띄워야 한다.
  - 이 옵션은 메뉴에만 있어서(REST·ctl 없음) 이 작업에서 Celly 설정을 바꿀 수 없다.
  - 운영 세션 재시작은 금지 범위다.
- 이 면제는 Claude Code가 이 스위치를 지원하는 버전에서만 동작한다. 지원 시작 버전은 확인하지 않았다(2.1.330에서 확인).

# 배포 (2026-11-23)

- 커밋 3d475f9를 `feature/priority-models`·`beta`에 push했다.
  - npm beta `0.2.51-beta.20261123-185837.3d475f9`, gitHead 3d475f9가 일치한다.
  - 변경 파일 4개의 배포본 해시가 저장소와 같다.
- 해시 확인 오버레이 `overlay-3d475f9`(4개 파일)를 설치했다. 설치 전에는 모두 base(27b62eb), 설치 후에는 모두 new였다.

  | 대상 | 설치 시각 |
  |---|---|
  | 로컬 hotfix5, `.local/share`, npm 전역 | 19:09:54Z |
  | Celly(관리자 권한) | 19:10:07Z |
  | KOBE, Prody(ssh) | 19:12:07Z까지 |
- 라이브 효과(파일 설치와 구분): 이 코드는 Claude 실행 시점에 env와 훅을 정한다. 그래서 이미 떠 있는 세션에는 효과가 없다.
  - Celly 세션에 반영하려면 다음 둘이 모두 필요하다. 둘 다 이 작업의 금지 범위 밖이다.
    - Celly 워크스페이스에서 옵션 `17. Dangerous delete prompt`를 켠다. 지금 실행 중인 Celly claude.exe에는 옵션 env가 없다.
    - Celly 세션을 다시 실행한다.
