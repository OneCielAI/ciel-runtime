# Log

## 2026-11-18

- 사용자 요구에 따라 구현 전에 스펙과 기술 리뷰를 작성했다. 코드는 바꾸지 않았다.
- 현재 코드(3fb838e)와 Celly의 사이드 포크 기록을 읽기 전용으로 조사했다. Walkie 이벤트는 필드 이름과 건수만 사용했다.
- [spec.md](spec.md), [technical-review.md](technical-review.md)
- 사용자 결정을 반영했다: Walkie 기존 승인 체계 사용, P0도 승인 대기, 제안서는 메인 작성, 답변만 필요한 요청은 사이드 직접 답변 또는 메인에 바로 전달.
- 추가 요구 반영: 단계별 프롬프트의 웹·REST 관리, 제안서의 UI 벤치마킹과 목업, 메인이 바쁠 때 스펙·리뷰 포크 위임.
- 추가: 승인 SSE 누락 대비로 설정 주기마다 사이드 액터가 차단 항목을 일괄 점검(메인 작업 중에도). KOBE에서 발견한 문제(점검 정지, 172초 시작 처리, mod 언로드)를 함께 고칠 범위에 넣었다.
- 구현: 스펙 전체(§3-§10, §9.1)와 §11.1을 한 변경으로 구현했다. 상세는 [implementation.md](implementation.md).
- KOBE 수정: 차단 항목 점검이 메인 작업 중에도 돌고(`not_idle` 제거), 타이머를 세션 시작의 맨 앞에서 켜며, P0 → P1 → 나머지 순으로 점검한다. 세션 시작의 호스트 단계(사이드 시작, hydrate, 복구, 첫 펌프)를 시작 훅 밖에서 실행한다. mod 언로드는 원인 미확정이며 KOBE에서 다시 재야 한다.
- 시험: 새 Python 시험 43건(4개 파일), node 하네스 1개, mod 시험 6건. HEAD에서 모두 실패, 변경 후 통과. Linux Python 3.10 3개 샤드 4,960건 → 5,003건 통과. `claude plugin test` 104 → 110 통과. mod·하네스 관련 Python 시험 501건 통과. 결과: [evidence/tests/results.md](evidence/tests/results.md).
- E2E: 실제 런처와 claude.exe, 대역 업스트림·Walkie로 SSE 요청 → 제안(목업) → 승인 요청 등록 → 승인 SSE 누락을 주기 점검이 회수 → 포크 → 증거 보고 → rework → approve → completed 통과. 프로세스 잔여 0, 스크래치 삭제. [evidence/e2e/run](evidence/e2e/run/result.json).
- E2E에서 찾아 고친 결함 3건: 바쁠 때 붙인 알림 뒤 재깨움 없음, 붙인 행이 다음 요청에 보이지 않음(깨움 프롬프트에 호스트 문장으로 할 일을 담음), 원장 게시 전 리뷰 알림.
- 미완: "page less"(상태 읽기 횟수 감소)는 실제 백엔드 시험의 호출 순서를 바꿔 되돌렸다. 제안·리뷰 포크의 실제 claude.exe 실행은 E2E에 넣지 못했다.
- 범위 변경(리드 지시): KOBE 항목 4개(메인 작업 중 점검·시작 맨 앞 타이머·P0 우선 순서, 가벼운 세션 시작, 긴 스레드 로그, 필터된 모델 오류 문구)는 핫픽스 1b42a73으로 분리됐다. 작업을 그 위로 rebase하고 해당 영역은 핫픽스 구현을 그대로 썼다. 주기 승인 점검은 핫픽스의 점검 틱과 `effective_priority_for` 위에 올렸다. 위의 KOBE 수정 줄은 이 변경이 아니라 핫픽스의 것이다.
- rebase 뒤 재검증: Linux 3개 샤드 4,974 → 5,016건 통과, `claude plugin test` 111 pass, 관련 Python 시험 494건, 실제 백엔드 117건 통과, 실제 E2E 다시 통과. [evidence/tests/results.md](evidence/tests/results.md).
- 승인 재점검 변경(사용자 결정): 주기 점검이 스레드 전체를 읽지 않고, 등록된 승인 요청 메시지 한 건만 읽는다(`walkie_read_messages(after = id - 1)`). 같은 대화의 요청은 한 번에 읽고, 한 틱의 결과는 라우터 호출 한 번으로 보낸다. 행에 `approvals[]`가 없을 때만 커서 이후 답글을 최대 2페이지 읽는다. 커서와 다이제스트는 보드 밖(세션 폴더)에 둔다. 리더는 `register.js`의 자체 완결 구역이며 사이드 접수 코드를 쓰지 않는다. [decisions/approval-recheck-request-only.md](decisions/approval-recheck-request-only.md).
- 검증: 새 시험 7건(50f7d11에서 실패), Linux 3개 샤드 5,023건, `claude plugin test` 111 pass, 관련 Python 시험 501건, 실제 백엔드 117건 통과, 실제 E2E 다시 통과(재점검의 Walkie 호출은 1회).
- 승인 재점검을 차단 항목 점검 틱의 같은 배치로 합쳤다(승인 대기 goal당 요청 메시지 1회 읽기, 결과는 그 틱의 두 번째 점검 호출로, 판정은 호스트 코드, 포크 없음). 두 번째 핫픽스가 커밋되면 rebase해서 그 틱의 스레드 읽기 호출과 하나로 합친다. [decisions/approval-recheck-request-only.md](decisions/approval-recheck-request-only.md).
- 두 번째 핫픽스(9cba321 + 3caf256) 위로 rebase했다. 충돌은 핫픽스 쪽을 유지했다(스레드 선읽기 없음, `work_gate_threads.py`, 일괄 점검 틱, 실행 중 goal의 사이드 메모는 `progress`). 승인 재점검을 그 틱의 같은 두 번째 호출로 합쳤다(`threads_read`와 `approvals_read`를 한 호출에). SSE 승인 이벤트도 요청 메시지 리더(`side-intake.js`의 `sideApprovalRead`)로 판정한다. 새 시험(라우터 1, mod 1)은 2b11374에서 실패, 변경 후 통과. [decisions/approval-recheck-request-only.md](decisions/approval-recheck-request-only.md), [evidence/tests/results.md](evidence/tests/results.md).
