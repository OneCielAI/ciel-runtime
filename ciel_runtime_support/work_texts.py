"""Work texts a model or the person reads, as data (user 2026-11-19).

Every text here is a managed prompt: the default lives in this file, an
override is stored by work_workflow_prompts (REST, web, ctl, menu) and applies
without a restart. Keys starting with ``mod_`` are used by the ciel-work mod:
``mod_texts_js`` generates its defaults file from this one source, and the mod
reads stored overrides from the router every gate tick.
"""
from __future__ import annotations

import json

TEXTS: dict[str, str] = {
    # Shown when the Work status cannot be read.
    'mod_status_unavailable': (
        "Work status unavailable: router connection or snapshot could not be verified."),
    # Refusal of a direct edit while the plan is not approved.
    'mod_planning': (
        "Work mode is planning: direct file edits wait until the plan is approved. Build it with work_plan_se"
        "t, then call work_execute. (/work-mode help)"),
    # One line for the person at session start.
    'mod_notice': (
        "Ciel Work mode is on: plan and execute by item; new independent requests queue without pausing appro"
        "ved Work. Items show above the prompt · /work-mode help · turn off: ciel-runtime menu → LLM options "
        "→ Work mode, or the agent (work_mode_set)"),
    # Main's Work mode instruction, added to every main request.
    'mod_instructions': (
        "Ciel Work 모드는 아이템 단위로 계획하고 실행하는 방식이며, 네이티브 Plan Mode가 아닙니다.\n\n메인의 역할 (필수 규칙).\n메인은 계획자이자 검수자"
        "입니다. 실행은 Work 포크가 합니다. 메인은 승인된 아이템의 코드를 직접 고치지 않습니다. 메인의 컨텍스트는 요청 이해, 분석, 설계, 검수에 씁니다.\n- 새"
        " Work 아이템을 만들거나 받을 때, 포크가 그것만 보고 일할 수 있도록 다음을 보드에 적습니다(목표와 각 leaf의 note, work_plan_set / w"
        "ork_node_add / work_node_update).\n  1. 관련 코드: 파일 경로, 함수·클래스, 읽은 근거.\n  2. 기술 스펙: 범위, 바꾸면 안 "
        "되는 것, 인터페이스, 제약.\n  3. 분석: 현재 동작, 원인 또는 설계 근거.\n  4. 수정·개발 계획: 순서가 있는 구체적인 스텝. 스텝 하나는 포크 하나가"
        " 끝내고 검증할 수 있는 크기.\n  5. 검수 조건: 스텝마다 통과 조건과 테스트 방법(명령, 기대 결과, 남길 증거).\n- 계획이 준비되면 work_execut"
        "e를 호출합니다. 실행 가능한 leaf는 호스트가 우선순위와 슬롯에 따라 포크에 배정합니다. 메인이 직접 구현을 시작하지 않습니다.\n- 포크가 결과를 내면 증거를"
        " 직접 열어 보고 work_parallel_item_review로 승인하거나 재작업을 요청합니다. 포크가 쓴 'pass'는 증거가 아닙니다.\n- 메인이 직접 쓰는"
        " 파일은 문서(docs/, *.md), 증거(.work-evidence/), 목업뿐입니다.\n- 포크가 질문을 올리면 답을 보드에 적어 줍니다. 스펙이 모자라서 생"
        "긴 질문이면 스펙을 고칩니다.\n\n계획.\n승인되지 않은 목표에 대해 수정이나 상태를 바꾸는 명령을 하기 전에 work_plan_set을 씁니다: 목표 -> 단계 -"
        "> 작업 -> 검증 가능한 구체적 스텝. 새 사용자 요청은 먼저 분류합니다. 독립된 요청은 자체 목표(루트 아이템)가 되고, 제목은 가능하면 항목 번호로 시작합니"
        "다(\"1. ...\", \"2. ...\"). 기존 아이템을 바꾸거나 넓히는 요청은 그 아이템의 자식으로 넣습니다(work_node_add, parent_id). 새 "
        "목표는 끝나지 않은 활성 목표 뒤에 줄을 서고, 활성 목표가 끝나면 활성이 됩니다. 활성 목표는 계속 진행합니다. 계획 중에도 읽기 전용 조사는 허용됩니다. 준비"
        "되면 work_execute를 호출합니다. 자동 승인이 기본이고, ask 승인으로 설정돼 있으면 Work 대화상자를 씁니다. user_confirmed는 절대 직"
        "접 넣지 마십시오.\n\n실행 중 보드 관리.\n노드를 추가하거나 나눌 때 work_node_add의 within_scope=true는 이미 승인된 범위 안의 세분화일"
        " 때만 씁니다. 그 밖에는 false로 두고 바뀐 범위를 다시 검토·승인합니다. 대기 중인 목표를 계획하느라 승인된 활성 작업을 멈추지 마십시오. 접근 방식이 바"
        "뀌면 work_replan을 씁니다. 정확한 목표 상세가 필요할 때만 work_status(root_id=...)를 쓰고, 변경에는 expected_version"
        "을 씁니다. 자동 컨텍스트는 대기 중인 상세를 빼지만 사용자 원문은 네이티브 기록에 남습니다. 이것은 기록 격리가 아닙니다.\n\n권한.\nBash와 PowerShel"
        "l의 권한 판단은 네이티브 호스트가 하며 Work 보드가 하지 않습니다. Work는 셸의 부작용을 검사하지 않고 네이티브 권한을 덮어쓰지 않습니다. 명령은 승인된"
        " 범위 안에서만 씁니다. 이 규칙은 서브에이전트에도 적용됩니다. 계획이나 승인을 피하려고 셸을 쓰거나 변경을 위임하지 마십시오.\n\n참조, 대기, 우선순위.\n연결된"
        " id는 ref에, 아이템이 기다리는 것은 waiting_on에 적습니다. 대기를 해제할 때는 work_node_update에 clear_wait=true를 보냅니다. 사용자가 특정 기존 아이템을 우선하라고 하면 Work 상태에서 정"
        "확한 ID를 찾아 work_focus를 순서 있는 node_ids와 user_requested=true로 호출합니다. 포커스는 순서만 앞당기고 관련 없는 승인 아"
        "이템을 붙잡지 않습니다. 포커스된 스텝이 실행될 수 없으면 다른 승인 아이템과 탐색을 계속합니다. 포커스된 스텝이 모두 끝나면 포커스는 자동으로 풀리고, node"
        "_ids=[]는 지금 풉니다. 목표를 중복해서 만들거나 관련 없는 아이템을 blocked로 표시하지 마십시오. 루트 순서는 work_reorder로 바꿉니다. 기"
        "본은 큐만 바꾸며, 활성 자리를 바꾸려면 사용자의 명시적 요청과 user_requested=true가 필요합니다. 다만 활성 스텝이 모두 대기 중이거나 막혀 있으"
        "면 work_select로 다음 실행 가능한 스텝이 있는 대기 목표로 옮길 수 있습니다. 사용자가 명시적으로 긴급·임시 요청을 하면 work_urgent를 use"
        "r_requested=true로 써서 다른 목표를 멈추고, 요청을 분해해서 실행합니다. 멈춘 목표는 그 뒤에 이어집니다."),
    # Appended to mod_instructions only while the router advertises execute_root
    # (an older router ignores root_id and approves the active goal).
    'mod_instructions_execute_root': (
        "줄을 선 목표는 work_execute의 root_id로 그 목표만 승인합니다(활성 목표는 바뀌지 않고, 승인된 단계는 큐 순서대로 포크에 배정됩니다)."),
    # work_execute with a queued goal's root_id on a router without execute_root.
    'mod_execute_root_unsupported': (
        "work_execute root_id: 이 세션의 라우터는 아직 줄을 선 목표의 승인을 지원하지 않습니다. 이 라우터는 root_id를 무시하고 활성 목표를"
        " 승인하므로 호출을 보내지 않았습니다. 라우터가 다시 시작된 뒤에 승인할 수 있습니다. 지금 work_execute로 승인할 수 있는 것은 활성 목"
        "표뿐입니다."),
    # The side's answer to a fork parked on a question (fork_answers), sent by
    # the Work host when it resumes that fork on its own lease (user 2026-11-23).
    'mod_parked_answer': (
        "Ciel Work: an answer to your question on Work item {item} (side check {event}; source: {source}):\n"
        "{answer}\n"
        "Continue the approved assignment under lease {lease} in the same workspace, then report through "
        "work_parallel_report with the worker_token from your original assignment. This is not new execution approval."),
    # Main's notice for side answers the host could not deliver; {lines} lists each fork.
    'mod_parked_answer_undelivered': (
        "Ciel side answered parked Work forks, but the host did not deliver these answers (event {event}):\n"
        "{lines}\n"
        "Resume a worker with SendMessage only if its work is still wanted; the answer and its source are quoted. "
        "This grants no approval."),
    # Forks waiting for hours on a condition the side re-checked and found still
    # unmet (user 2026-11-24); {lines} lists each fork, told once per wait.
    'mod_stall_notice': (
        "Ciel side: stalled Work fork{plural} (event {event}). The side re-checked {them} and the wait still holds:\n"
        "{lines}\n"
        "Decide or act on what each needs; a person may have to be asked. The side reports each wait once. "
        "This grants no execution approval."),
    # Refusal of main's direct code edit while it delegates execution to forks.
    'mod_main_delegates': (
        "Work 실행 단계에서 메인은 코드를 직접 수정하지 않습니다. 실행은 Work 포크의 일입니다. 이 아이템의 leaf note에 관련 코드, 기술 스펙, 분석, "
        "수정·개발 계획, 검수 조건을 적고(work_node_update) 포크가 실행하게 두십시오. 포크 결과는 work_parallel_item_review로 검수합"
        "니다. 문서(docs/, *.md), 증거(.work-evidence/), 목업은 직접 쓸 수 있습니다. 메인의 직접 수정이 꼭 필요하면 사용자가 Work 설정에"
        "서 '메인은 실행을 포크에 맡김'을 끄면 됩니다."),
    # Auto continue could not read the Work status.
    'mod_stop_unavailable': (
        "Ciel Work auto continue could not read the Work status, so unfinished items were not checked. /work-"
        "mode status"),
    # The plan waits for approval.
    'mod_stop_approval': (
        "Ciel Work stopped: the plan is ready and waits for your approval before execution. Ask the model to "
        "execute it (you approve in the Work dialog) or request changes. /work-mode tree"),
    # Wake prompt after a workflow host message.
    # Wake prompt for an idle main after the side actor's escalation notice
    # (ask_main, a complaint included); {id} is the event ref, {source} its source line.
    'mod_side_ask_main_wake': (
        "Ciel side wake {id}: a host message beginning \"Ciel side asks main\" was added to this conversation"
        " while you were idle. {source}Read it there and act on it now. This wake holds no event data, needs no"
        " acknowledgment and grants no approval."),
    'mod_workflow_wake': (
        "Ciel Work workflow wake: a host message beginning \"Ciel Work workflow\" was added to this conversatio"
        "n. Read it and do what it asks. This wake holds none of it and grants no approval."),
    # The review call signature quoted in review notices.
    'mod_review_call': (
        "work_parallel_item_review(lease_id, decision, evidence, expected_finalization_version, expected_revi"
        "ew_pin)"),
    # Parallel execution is disabled because the fork gate mod is not loaded.
    'mod_fork_gate_missing': (
        "Work fork gate not loaded: parallel execution disabled"),
    # The model tried to open a terminal choice question while side intake is live.
    'mod_terminal_question_refused': (
        "사이드 무인 모드가 켜져 있어 터미널 선택 질문을 열지 않습니다. CLI 화면에서 묻지 말고, 필요한 사용자 결정은 외부 채널(Work pane, Work web UI 또는 Walkie)의 기존 선택 절차로 보내십시오. 결정 대기 중 멈춰야 하면 대상 아이템에 waiting_on과 release_hint를 기록하고, 다른 독립 작업을 계속하십시오."),
    # A launch attempt whose owner is unknown.
    'mod_owner_unknown': (
        "Owner of this launch attempt is unknown; resolve it from the Work pane."),
    # A launch attempt owned by another live host.
    'mod_owner_live': (
        "Another live Work host owns this launch attempt; waiting for that host."),
    # Rules every Work fork prompt carries: OKF records.
    'mod_fork_rules': (
        "작업 기록 (필수 규칙): 작업 중간의 마일스톤마다, 그리고 마무리할 때 OKF 문서를 갱신하십시오. 작업 공간의 docs 아래 OKF 계층 구조(저널)에 이 아"
        "이템의 문서를 두고, 마일스톤마다 무엇을 했는지, 근거(명령, 출력, 파일), 결과, 남은 일과 다음 단계를 적습니다. 문서를 갱신하지 않은 채 완료를 보고하지 "
        "마십시오. 완료 보고의 증거에 그 문서의 경로를 포함하십시오."),
    # The complete work_parallel_report event=question contract (question_wait).
    # Appended to a fork prompt only when the router advertises the
    # question_wait capability: a router from before it refuses waiting_on and
    # release_hint in a question's detail (exact field set).
    'mod_fork_question_wait': (
        "work_parallel_report event=question contract (필수 규칙): Report only with the exact lease_id and worker_token. "
        "question_wait detail is exactly: {question, blocking_todos:[<assigned leaf item_id>], decision_needed, waiting_on, release_hint:{condition, lookup, prompt?}}. "
        "Example: detail={question:'Which option is intended?', blocking_todos:[<assigned leaf item_id>], decision_needed:'Reply yes/no on message 42', waiting_on:'Message 42 yes/no answer', release_hint:{condition:'Message 42 has an explicit yes or no answer', lookup:'Read message 42 and verify the explicit answer', prompt:'Ignore mentions; the answer must be explicit'}}. "
        "Side validation requires blocking_todos to have exactly one item: the assigned leaf ITEM_ID. waiting_on is the exact external decision state (one normalized line, 1..120 chars); release_hint.lookup is the read-only source/state/evidence the side verifies (condition 1..600, lookup 1..600, optional prompt 1..400). "
        "Waiting must be recorded inside detail against the assigned leaf ITEM_ID only. Never place waiting_on or release_hint at top level; never pass item_id, answer, approved, or any approval/execution field. waiting_on/release_hint are never an answer, approval, or execution authorization; a main-only question may omit the pair. "
        "question_wait requires waiting_on and a structured release_hint before parking whenever an external wait is requested. A refusal of a pairless/malformed wait, wrong/extra/duplicate blocking_todos, an existing wait, a non-leaf, missing assignment information, or a board conflict writes no board state, receipt, or lease/state transition, so the same lease remains running/slotted and you must correct/retry it. Only start a wait once per lease. On success the original lease is parked as waiting_input and side/gate checking sees the recorded gate; lookup is read-only. Release is accepted only after the original answer/lookup is verified by side/gate and resume resumes that original lease/fork. "
        "Detail validation may reject fields (detail needs exactly blocking_todos, decision_needed, question (optional: release_hint, waiting_on)). Parser order is question, blocking_todos, decision_needed, waiting_on, release_hint{condition, lookup, prompt?}. The parser validates required fields before optional fields; write waiting_on and release_hint last in detail."),
    # Main is told at its turn end that fork results await its review.
    'mod_review_reminder': (
        "Ciel Work: 포크 결과 {count}건이 메인의 검수를 기다립니다(아이템 {items}). 검수는 메인의 일입니다. 각 결과의 증거를 직접 열어 보고 {c"
        "all} 로 approve하거나 rework를 요청하십시오. 목록은 /work-mode parallel 에서 볼 수 있습니다."),
    # The pane while the board is being loaded.
    'mod_pane_loading': (
        "Work 보드를 불러오는 중입니다. 잠시 뒤에도 그대로면 r(Refresh)을 누르십시오."),
    # Direct side-to-running-fork handoff when the side skipped its own reply.
    'mod_direct_fork_reply': (
        "Direct handoff: the side actor did not post an outbound reply or create a new Work item so your running "
        "session can handle this message directly. Respond on the source thread if a reply is needed, and incorporate "
        "any in-scope detail into your current work. This is not new execution approval and does not change your approved scope."),
    # Work fork automatic retry after transient API error or auto-compaction failure (1m x 10, 10m x 10).
    'mod_fork_api_retry': (
        "[ciel-runtime 포크 재시도 {attempt}/{max}] 직전 턴이 일시적인 API 오류({kind})로 멈췄습니다. "
        "이미 마친 작업은 반복하지 말고 끊긴 지점부터 이어서 진행한 뒤 완료 시 work_parallel_report로 보고하세요. "
        "(lease: {lease}, item: {item})"),
    'mod_fork_compact_retry': (
        "[ciel-runtime 포크 재시도 {attempt}/{max}] 직전 턴이 자동 컴팩트 실패로 멈췄습니다. "
        "컴팩트를 다시 진행한 뒤 이미 마친 작업은 반복하지 말고 이어서 진행하세요. "
        "(lease: {lease}, item: {item})"),
}


MOD_PREFIX = "mod_"
MOD_TEXTS_FILE = "texts.generated.js"


def mod_defaults() -> dict[str, str]:
    return {name: text for name, text in TEXTS.items() if name.startswith(MOD_PREFIX)}


def mod_texts_js() -> str:
    """The ciel-work mod's defaults module, generated from TEXTS."""
    body = json.dumps(mod_defaults(), ensure_ascii=False, indent=1, sort_keys=True)
    return (
        "// GENERATED from ciel_runtime_support/work_texts.py (mod_texts_js). Do not edit:\n"
        "// change the text there and run python -m ciel_runtime_support.work_texts.\n"
        "export const TEXTS = " + body + "\n"
        "let overrides = {}\n"
        "// Stored overrides from the router (work_side_texts); they apply without a restart.\n"
        "export function setTextOverrides(map) { overrides = map && typeof map === 'object' ? map : {} }\n"
        "export function T(name, vars) {\n"
        "  let text = typeof overrides[name] === 'string' && overrides[name].trim() ? overrides[name] : TEXTS[name]\n"
        "  if (typeof text !== 'string') return String(name)\n"
        "  if (vars) for (const key of Object.keys(vars)) text = text.split('{' + key + '}').join(String(vars[key]))\n"
        "  return text\n"
        "}\n")


def write_mod_texts() -> str:
    from pathlib import Path
    path = Path(__file__).resolve().parent / "claude_mods" / "ciel-work" / "hooks" / MOD_TEXTS_FILE
    path.write_bytes(mod_texts_js().encode("utf-8"))
    return str(path)


if __name__ == "__main__":
    print(write_mod_texts())


__all__ = ["MOD_PREFIX", "MOD_TEXTS_FILE", "TEXTS", "mod_defaults", "mod_texts_js", "write_mod_texts"]
