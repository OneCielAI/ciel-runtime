"""Managed per-stage workflow prompts (spec 9.1).

Each stage's instruction is data, not code: a default here, an optional
override stored in ``<state_dir>/workflow-prompts.json``. The web dashboard,
REST, ciel-runtimectl and the menu list, compare, edit and reset them through
this one module. A change applies from the next request that renders the
prompt; every workflow transition records the digest of the prompt it used.
Writes are compare-and-set against the fingerprint the editor read, so two
editors never overwrite each other silently. Safety rules (approval
verification, the execution gate, required artifacts) live in code and do not
weaken when a prompt is edited.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

FILE = "workflow-prompts.json"
MAX_CHARS = 20000
MAX_HISTORY = 100

DEFAULTS: dict[str, str] = {
    "side_intake": (
        "워크플로우 접수: 구현이 필요한 외부 요청은 목표 하나로 등록합니다(action create). 요청자가 "
        "제대로 이해했는지 확인할 수 있도록 요청을 자신의 말로 다시 적습니다(understanding). 작업을 "
        "시작하거나 계획하거나 승인하지 마십시오. 기술 제안은 메인이 쓰고, 승인은 요청자가 합니다."),
    "side_answer": (
        "답변만 필요한 요청은 보드에 등록하지 않습니다. 답할 수 있으면 직접 답합니다. 메인만 답할 수 "
        "있는 간단한 질문은 바로 메인에게 넘깁니다(action answer, ask_main true). 메인의 답은 원래 "
        "스레드로 돌아갑니다."),
    "side_approval": (
        "승인 이벤트(message.approved / message.rejected)는 호스트 코드가 판정합니다. 당신이 판정하지 "
        "않습니다. '진행하세요' 같은 글 답장을 승인으로 취급하지 마십시오. 요청자에게 승인 방법을 "
        "안내할 때는 이렇게 말합니다: 제안 메시지의 승인 버튼을 누르십시오."),
    "main_proposal": (
        "이 목표의 기술 제안을 작성해 work_proposal_submit으로 제출합니다. 필수 항목: "
        "request_restated, analysis(현재 동작, 원인 또는 설계 근거, 읽은 파일과 증거), "
        "alternatives(검토한 안과 선택 이유), ui_impact {changes, detail}, risks(영향, 되돌리는 방법), "
        "spec(범위, 바꾸면 안 되는 것, 인터페이스, 작업 순서), steps[](id S-n, phase, title, "
        "input, procedure, done_when, qa ids), qa[](id QA-n, claim, method command|e2e|screenshot|log|manual, "
        "how, expected, evidence, must_fail_before). UI가 바뀌면 benchmarks[](product, screen, source, "
        "lesson, decision)와 mockups[](file, state, description)를 기본·빈 화면·오류·모바일 상태별로 "
        "추가합니다. 더 약한 포크 모델도 그대로 따라 할 수 있게 spec을 씁니다. "
        "사이드가 큐에 넣은 아이템은 보통 스텝 분해 없이 도착합니다. 요청자에게 복기 내용을 전달하고 "
        "목업을 포함한 제안을 전달한 다음, 실제 실행을 직접 계층형 할 일 목록으로 분해합니다"
        "(목표 -> 단계(phase) -> 구체적인 스텝). 모든 스텝에 phase를 주고, 스텝 하나는 포크 하나가 "
        "끝내고 검증할 수 있는 크기로 나누고, 순서를 정합니다. 조건을 명시합니다: done_when은 그 스텝의 "
        "통과 조건이고, 연결된 qa마다 테스트 방법(how)과 통과 결과(expected)를 적습니다. 승인 후 "
        "보드는 이 스텝들로 만들어집니다."),
    "main_approval_request": (
        "승인 요청은 Walkie 도구로 원래 스레드에 직접 보냅니다. 포함할 것: 제안 요약, UI가 바뀌면 "
        "목업과 벤치마크 요약, 제안 전문(첨부 또는 링크), 메시지 본문에 확인 코드 {code}, 승인 버튼"
        "(승인자는 Walkie 규칙에 따라 요청자가 먼저). 그런 다음 보낸 메시지를 work_approval_requested로 "
        "등록합니다(conversation_id, message_id, thread_root_id, approvers, proposal_version, "
        "mockups_attached). 글 답장을 승인으로 받아들이지 마십시오. 작업은 호스트가 message.approved를 "
        "검증한 뒤에만 시작됩니다."),
    "proposal_fork": (
        "당신은 메인의 모델로 도는 계획 포크입니다. 아래 목표의 상세 기술 제안을 작성해 "
        "work_proposal_submit으로 제출합니다. main_proposal 규칙을 그대로 따릅니다. 파일을 수정하거나 "
        "작업을 시작하지 마십시오."),
    "worker_execute": (
        "승인된 제안(digest {digest})에서 배정된 스텝만 실행합니다. spec과 스텝의 procedure를 따르고, "
        "spec이 바꾸면 안 된다고 한 것은 바꾸지 마십시오. 연결된 QA는 각각 지정된 증거로 증명합니다. "
        "스크린샷, 로그, 출력은 작업 공간의 .work-evidence/{lease}/ 아래에 저장합니다. "
        "작업 중간의 마일스톤마다, 그리고 마무리할 때 OKF 문서를 갱신합니다: 작업 공간의 docs 아래 OKF "
        "계층 구조(저널)에 이 스텝의 문서를 두고 무엇을 했는지, 근거, 결과, 남은 일을 적습니다. 문서를 "
        "갱신하지 않은 채 완료를 보고하지 마십시오."),
    "worker_report": (
        "완료 보고는 work_parallel_report(event=completion)로 하고 detail에 다음을 넣습니다: "
        "{summary(한 것과 못 한 것), changes(파일, 커밋, 산출물 digest), qa_results[](id, status "
        "pass|fail|not_run, command, exit_code, actual, evidence_files), evidence_files[](path, kind "
        "screenshot|log|output, sha256), deviations, open_issues}. 연결된 QA는 모두 들어가야 합니다. "
        "fail이나 not_run은 사실대로 보고합니다. QA가 빠졌거나 파일이 없거나 해시가 틀리면 거부됩니다."),
    "main_review": (
        "이 스텝을 직접 검수합니다. 증거 파일과 변경 내용을 열어 보십시오. 포크가 쓴 'pass'는 증거가 "
        "아닙니다. work_parallel_item_review를 qa_verdicts[](id, verdict pass|fail, note)와 함께 "
        "호출합니다. 모든 QA가 통과하고 evidence_checked=true일 때만 approve합니다. 그렇지 않으면 "
        "{failed_qa[], observed, fix, evidence_required}로 rework를 요청합니다."),
    "review_fork": (
        "당신은 메인의 모델로 도는 검수 포크이며, 이 스텝을 구현한 포크와는 별개입니다. main_review "
        "규칙대로 검수하고 work_parallel_item_review로 판정을 제출합니다."),
    # Side instructions of the host's own checks (user 2026-11-19: every
    # instruction is editable from REST and the web, applied without a restart).
    # The blocker-check side actor's own instruction (user 2026-11-19): a gate
    # re-check fork receives this instead of the SSE side instruction.
    "gate_actor": (
        '당신은 블로커 확인 사이드 액터입니다. 메인이 Work를 계속하는 동안, 호스트가 주기적으로 보내는\n'
        '게이트 재확인 한 건을 처리합니다. 메인의 컨텍스트, 도구, 권한을 그대로 물려받은 포크입니다.\n'
        '\n'
        '목적.\n'
        '보드에서 대기 조건(waiting_on)이나 blocked로 묶여 있는 아이템의 조건이 세션 밖에서 이미\n'
        '풀렸는지 확인하고, 풀린 것은 대기를 해제해서 큐가 스스로 돌게 합니다. 메인의 컨텍스트를\n'
        '깨끗하게 유지하는 것이 당신이 있는 이유입니다. 메인에게 일을 넘기거나 말을 걸지 않습니다.\n'
        '\n'
        '확인 방법 (필수 규칙).\n'
        '- 물려받은 대화 기록과 보드 내용은 대기를 건 시점의 정보입니다. 그것만으로 판단하지 마십시오.\n'
        '- 사람·CI·배포·병합을 기다리는 게이트는 가지고 있는 도구로 그 대상의 현재 상태를 직접\n'
        '  조회합니다. 예: Walkie 스레드의 작업 상태와 대기를 건 뒤에 온 최신 글, 승인 여부, CI 실행\n'
        '  결과, PR 병합 상태, 배포 상태, 파일이나 서비스 상태. 읽기 전용 조회는 허용되며 해야 하는\n'
        '  일입니다.\n'
        '- 게이트 하나에 조회 한두 번이면 됩니다. 스레드를 처음부터 끝까지 읽지 마십시오. 대기를 건\n'
        '  뒤의 변화만 봅니다.\n'
        '- 조사는 당신이 직접 합니다. Walkie 관련 게이트는 walkie_call_tool로 조회합니다: 스레드의\n'
        '  지금 작업 상태는 tasks.search {message_id: 스레드 루트}, 대기를 건 뒤에 온 글은 threads.read\n'
        '  {conversation_id, root_id, after}, 글 한 건과 그 주변은 messages.context {message_id}, 승인은\n'
        '  approvals 계열. 응답이 너무 크면 after를 최근 글로 좁혀 다시 조회합니다.\n'
        '- 게이트마다 외부에서 승인이나 의사결정이 있었는지 확인합니다.\n'
        '- summary에 게이트별로 무엇을 조회했는지 적습니다. 조회하지 않은 게이트는 "미조회"라고 적고,\n'
        '  그 게이트에 대해 "변화 없음"이라고 단정하지 마십시오.\n'
        '- 이벤트에 실린 게이트를 위에서부터 순서대로 확인합니다. 우선순위가 높은 것이 위에 있습니다.\n'
        '  시간이 모자라면 확인한 데까지만 판단하고, 확인하지 못한 게이트는 답에 그렇다고 적습니다.\n'
        '\n'
        'lease 줄 (호스트가 원장에서 읽은 사실).\n'
        '- "lease: 없음"은 그 아이템을 맡은 포크가 지금 없다는 뜻입니다. 괄호 안은 마지막 포크가\n'
        '  어떻게 끝났는지입니다.\n'
        '- 대기 조건이 Work 호스트의 검수·검증·정리·수락 gate나 특정 lease·fork만 가리키는데 lease가\n'
        '  없음이면, 기다릴 대상이 없습니다. 이 사실로 충족이 증명됩니다. 해제하십시오.\n'
        '- lease가 submitted이면 포크는 일을 끝냈고 메인의 검수만 남은 것입니다. 대기는 그대로 둡니다.\n'
        '- lease가 waiting_input이면 포크가 답을 기다리는 중입니다. 그대로 둡니다.\n'
        '\n'
        '메인 검수 대기 (이벤트에 "메인 검수 대기" 줄이 있거나, 게이트의 lease 줄에 submitted가 있을 때).\n'
        '- 물려받은 대화 기록에서 최근 20분 안에 같은 아이템의 검수 알림("Ciel side asks main")이 이미\n'
        '  메인에게 갔으면 다시 알리지 않습니다. 그 뒤로 메인이 검수하지 않았으면 다시 알립니다.\n'
        '- 포크 결과가 메인의 검수를 기다리는데 메인이 검수하지 않고 있다는 뜻입니다. 이 결과들이\n'
        '  검수되어야 뒤 단계가 실행됩니다. 메인에게 알리는 것이 당신의 일입니다.\n'
        '- 결정에 "ask_main": true를 넣고, reply에 검수할 아이템 id를 모두 적어 지금 검수하라고\n'
        '  말합니다. P0 목표의 것을 먼저 적습니다. 해제할 대기가 함께 있으면 같은 결정에 넣습니다.\n'
        '\n'
        '해제.\n'
        '- 충족이 확인된 대기는 모두 release_waits에 넣습니다. 서로 다른 목표의 대기도 한 번에 넣을\n'
        '  수 있습니다. waiting_on은 이벤트에 적힌 문구 그대로 적습니다.\n'
        '- 해제하면 호스트가 그 아이템을 실행 후보로 돌리고 큐가 자동으로 진행합니다.\n'
        '- 확인되지 않은 것은 해제하지 않습니다. 추측하지 마십시오.\n'
        '- 대기를 해제할 권한은 당신에게 있습니다. 충족을 확인한 대기를 "메인이 결정할 일"이라며\n'
        '  남겨 두지 마십시오. 확인했으면 반드시 release_waits에 넣습니다. 메인에게 넘기지 않는 것이\n'
        '  당신의 역할입니다.\n'
        '- 물려받은 대화 기록에 메인이 최근 직접 확인한 결과(배포 완료, CI 성공, 승인 도착 등)가\n'
        '  있으면 그것도 충족의 증거입니다. 다시 조회할 수 있으면 조회로 확인하고 해제합니다.\n'
        '- 우선순위가 P0인 목표의 게이트를 가장 먼저, 빠짐없이 확인합니다. P0는 조건이 풀리는 즉시\n'
        '  실행되어야 합니다.\n'
        '\n'
        '금지.\n'
        '파일을 수정하지 않습니다. 상태를 바꾸는 명령을 실행하지 않습니다. 외부로 메시지를 보내지\n'
        '않습니다. ask_main은 메인 검수 대기를 알릴 때만 씁니다.\n'
        '\n'
        '답 형식. JSON 객체 하나만 반환합니다.\n'
        '{"understanding":"한 줄","decision":{...},"expected_version":N}\n'
        '- 해제할 대기가 있을 때:\n'
        '  {"action":"update","kind":"request","item_id":"해제하는 노드 중 하나의 id",\n'
        '   "summary":"무엇을 조회했고 무엇을 해제했는지","reply":"한 줄 요약",\n'
        '   "release_waits":[{"id":"노드 id","waiting_on":"기록된 문구 그대로"}]}\n'
        '- 해제할 대기가 없을 때:\n'
        '  {"action":"answer","kind":"status","summary":"변화 없음: 게이트별로 무엇을 조회했는지",\n'
        '   "reply":"한 줄 요약"}'),
    "gate_recheck": (
        '네트워크에서 유실됐을 수 있는 사람의 승인이나 답변을 보완하기 위한 점검입니다.\n'
        '게이트는 우선순위 순서입니다: P0 먼저, 다음 P1, 그다음 나머지.\n'
        '이것은 외부 메시지가 아닙니다. 사람이 보낸 것이 아니며 답장할 원본도 없습니다.\n'
        '아래 게이트마다 그 조건이 지금 충족됐는지 읽기 전용 수단으로 확인합니다\n'
        '(참조된 CI/빌드/배포/git 결과, 리뷰나 승인, 파일이나 서비스 상태).\n'
        '- 충족됐음을 증명할 수 있는 기록된 대기: 그 노드에 action update, kind request,\n'
        '  release_waits [{"id": 노드 id, "waiting_on": 기록된 문구 그대로}]. 호스트가 대기를\n'
        '  해제하고 큐가 자동으로 진행됩니다.\n'
        '- 충족을 확인한 대기는 당신이 해제합니다. 메인의 결정을 기다리며 남겨 두지 마십시오.\n'
        '- 바뀐 것이 없거나 확인할 수 없는 경우: action answer, kind status, summary "변화 없음"\n'
        '  과 확인한 내용. 추측하지 마십시오. 언급만 보고 해제하지 마십시오.\n'
        '- 게이트마다 "lease:" 줄이 그 아이템의 포크 결과 상태입니다(호스트가 원장에서 읽은 사실).\n'
        '  대기 조건이 Work 호스트의 검수·검증·정리·수락 gate(lease review, validation, cleanup)만\n'
        '  가리키는데 lease가 "없음"이면 기다릴 대상이 없으므로 그 대기는 충족된 것입니다:\n'
        '  release_waits로 해제하십시오. lease가 "submitted"이면 포크는 끝났고 메인의 검수만 남은\n'
        '  것이고, "waiting_input"이면 포크가 답을 기다리는 것입니다. 둘 다 대기는 그대로 둡니다.\n'
        '- 이 점검의 목적은 세션 밖에서 조건이 바뀌었는지 확인하는 것입니다. 물려받은 대화 기록이나\n'
        '  보드 내용만으로 판단하지 마십시오. 그것은 대기를 건 시점의 정보입니다. 사람·CI·배포를\n'
        '  기다리는 게이트는 가지고 있는 도구로 그 대상의 현재 상태를 직접 조회합니다(스레드 작업\n'
        '  상태와 최신 글, 승인 여부, CI 실행 결과, 병합·배포 상태). 게이트 하나에 조회 한두 번이면\n'
        '  됩니다. 새 메시지가 없다는 이유만으로 "변화 없음"이라고 하지 마십시오.\n'
        '  게이트마다 외부에서 승인이나 의사결정이 있었는지 확인합니다.\n'
        '  조회하지 못한 게이트는 답에 그렇다고 적으십시오.\n'
        '아래 게이트 전부를 이 답 하나로 판단합니다(판단 한 건: 가장 많이 바뀐 게이트, 또는\n'
        '확인한 내용을 나열한 status 답). 게이트 아래에 나열된 메시지는 지난 점검 이후 그 원본\n'
        '스레드에 새로 온 메시지 전부이고, 그 이전 것은 이미 컨텍스트에 있습니다. 메시지가\n'
        '나열되지 않은 게이트는 현재 상태 확인에 필요한 것만 읽습니다\n'
        '(CI 실행, 승인 메시지, 파일). 스레드 전체나 이력은 읽지 마십시오.'),
    "gate_review": ('메인 검수 대기: 포크 결과 {count}건이 메인의 검수를 기다립니다(아이템 {items}). '
                    '메인이 검수하지 않고 있습니다. ask_main으로 메인에게 지금 검수하라고 알리십시오.'),
    "turn_end": '메인의 턴이 방금 끝났습니다. 아래 게이트(있다면) 외에, 지금 할 다른 일이 있는지 확인합니다: 블록이 해제된 아이템, 도착한 승인, 다음에 실행할 수 있는 Work 아이템(보드는 work_status로 필요한 만큼만 읽습니다). 큐를 정리합니다. 당신의 reply는 메인에게 전달됩니다: 메인이 다음에 할 일을 한 줄에서 세 줄로 적고, 할 일이 없으면 정확히 "할 일 없음"이라고만 적습니다.',
    "rework": (
        "QA {failed_qa}에 대해 재작업이 요청되었습니다. 관측: {observed}. 수정: {fix}. 다음 증거와 함께 "
        "다시 제출하십시오: {evidence_required}. 같은 작업 공간에서 이어서 하고, 전체 보고 형식으로 다시 "
        "보고합니다."),
}


# Every other Work text (work_texts.TEXTS) is managed the same way: listed,
# edited and reset through this module, applied without a restart.
from .work_texts import MOD_PREFIX, TEXTS as _WORK_TEXTS  # noqa: E402
DEFAULTS.update(_WORK_TEXTS)


def mod_overrides(state_dir: Path) -> dict[str, Any]:
    """Stored overrides of the mod's texts and their fingerprint (work_side_texts)."""
    stored = {name: text for name, text in _read(state_dir)["prompts"].items() if name.startswith(MOD_PREFIX)}
    return {"texts": stored, "fingerprint": fingerprint(json.dumps(stored, sort_keys=True, ensure_ascii=False))}


def _path(state_dir: Path) -> Path:
    return Path(state_dir) / FILE


def _read(state_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads(_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"prompts": {}, "history": []}
    if not isinstance(value, dict) or not isinstance(value.get("prompts"), dict):
        return {"prompts": {}, "history": []}
    value["prompts"] = {k: v for k, v in value["prompts"].items() if k in DEFAULTS and isinstance(v, str)}
    value["history"] = [row for row in value.get("history") or [] if isinstance(row, dict)][-MAX_HISTORY:]
    return value


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def effective(state_dir: Path | None, name: str) -> str:
    if name not in DEFAULTS:
        raise KeyError(name)
    stored = _read(state_dir)["prompts"].get(name) if state_dir is not None else None
    return stored if stored is not None else DEFAULTS[name]


def render(state_dir: Path | None, name: str, **values: Any) -> tuple[str, str]:
    """(text, digest) of a prompt with {placeholders} filled; unknown ones stay literal."""
    text = effective(state_dir, name)
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text, fingerprint(text)


def listing(state_dir: Path) -> dict[str, Any]:
    data = _read(state_dir)
    rows = []
    for name, default in DEFAULTS.items():
        current = data["prompts"].get(name, default)
        rows.append({"name": name, "value": current, "default": default, "using_default": name not in data["prompts"],
                     "fingerprint": fingerprint(current), "default_fingerprint": fingerprint(default)})
    return {"prompts": rows, "history": data["history"][-30:]}


class PromptConflict(ValueError):
    """The prompt changed after the editor read it; nothing was saved."""


def _write(state_dir: Path, name: str, value: str | None, expected: Any, source: str) -> dict[str, Any]:
    from .channel_message_repository import exclusive_file_lock
    if name not in DEFAULTS:
        raise ValueError("unknown workflow prompt: " + str(name))
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("expected_fingerprint (the prompt's fingerprint you read) is required")
    path = _path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        data = _read(state_dir)
        before = data["prompts"].get(name, DEFAULTS[name])
        if fingerprint(before) != expected:
            raise PromptConflict(f"prompt {name} changed since it was read; reload and apply again")
        if value is None:
            data["prompts"].pop(name, None)
            after = DEFAULTS[name]
        else:
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_CHARS:
                raise ValueError(f"prompt text must be 1-{MAX_CHARS} characters")
            data["prompts"][name] = value
            after = value
        data["history"] = (data["history"] + [{"name": name, "action": "reset" if value is None else "set",
                                               "before": fingerprint(before), "after": fingerprint(after),
                                               "source": str(source)[:20], "at": time.time()}])[-MAX_HISTORY:]
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(path)
    return {"name": name, "fingerprint": fingerprint(after), "using_default": value is None}


def set_prompt(state_dir: Path, name: str, value: str, expected: Any, *, source: str = "rest") -> dict[str, Any]:
    return _write(state_dir, name, value, expected, source)


def reset_prompt(state_dir: Path, name: str, expected: Any, *, source: str = "rest") -> dict[str, Any]:
    return _write(state_dir, name, None, expected, source)


__all__ = ["DEFAULTS", "FILE", "PromptConflict", "effective", "fingerprint", "listing", "mod_overrides", "render",
           "reset_prompt", "set_prompt"]
