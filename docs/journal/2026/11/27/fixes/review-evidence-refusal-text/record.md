---
type: fix-record
title: 검수 판정 evidence 길이 초과가 "review decision" 한 마디로만 거절되던 결함
description: 메인의 work_parallel_item_review가 evidence 2,000자 상한을 넘으면 어떤 필드가 왜 틀렸는지 없이 "review decision"으로만 거절되던 것을 필드별 문구로 바꾸고, 도구 스키마에 maxLength를 공개한 수정
tags: [work-parallel, finalization, review, refusal-text, celly, fail-first]
timestamp: 2026-11-27T19:50:00Z
---

# 검수 판정 evidence 거절 문구

## 관측 (Celly `9385`, 2026-11-27)

- 19:24:11Z, 메인이 `work_parallel_item_review`를 호출했다.
  - 대상: lease `72f4006b`(n1669)
  - 인자: `decision rework`, `decision_id n1669-rework-20261127-1924`, `expected_finalization_version 3798`, `expected_review_pin rp1:…`, evidence 길이 **2,181자**
- 19:24:27Z 결과는 `work_parallel_item_review: review decision`였다.
- 19:24:32Z 실패 기록 `decision_refused_stop 72f4006b x1`이 생겼다. 이번 틱의 Celly `new_failures`가 이 건이다.

## 원인 (코드)

- `work_parallel_finalization.py` `decide()`가 다섯 조건을 `require(... and ..., "review decision")` 하나로 묶었다.
  - 다섯 조건: decision 값, decision_id 1–200자, evidence 1–2,000자, NUL 없음, 다이제스트
  - 그래서 어느 조건이 깨져도 같은 두 단어만 나온다.
- 도구 스키마의 `evidence`는 `{"type": "string"}`뿐이었다. 상한이 공개되지 않아 호출자가 미리 알 수 없었다.

## 수정

- `decide()`는 이제 조건마다 자기 문구로 거절한다.
  - evidence 거절 예: `review evidence must be 1-2000 characters without NUL (got 2181)`
- 상한은 `EVIDENCE_LIMIT = 2_000` 한 곳에서만 정한다.
- `work_parallel_item_review` 스키마의 `evidence`에 `maxLength: 2000`을 넣었다.
- 거절 문구는 이 파일의 기존 `require` 관례(코드 리터럴 127개)를 따른다. 관리형 프롬프트가 아니라 라우터의 오류 문자열이다.

## 검증

- **fail-first:** `tests/test_work_parallel_finalization.py::test_an_oversized_evidence_refusal_names_the_field_and_its_limit`
  - 수정 전: `"evidence.*2000.*2181" does not match "review decision"`
  - 수정 후: 통과. 판정이 기록되지 않음(`decision None`)과 스키마의 `maxLength 2000`도 함께 확인한다.
- 영향 회귀와 배포는 [사이클 8](../../deployments/cycle-8/record.md)에 기록한다.
