---
type: deployment-record
title: 사이클 8 — 사이드 거절 원인 구분·보류 결정 보존, 검수 거절 필드명
description: 2026-11-27 21시 배포. 2fa83bc·1ce5c5c(문서 18aa41c)의 영향 회귀, npm 발행, 7개 루트 해시 확인 파일 설치 기록. 라우터 교체는 아직 하지 않아 라이브 적용 전이다
tags: [deployment, regression, side-intake, finalization, andy, celly, kobe, prody, betty]
timestamp: 2026-11-27T21:35:00Z
---

# 사이클 8 배포

## 포함한 수정

| 기록 | 커밋 | 파일 |
| --- | --- | --- |
| [review-evidence-refusal-text](../../fixes/review-evidence-refusal-text/record.md) | `2fa83bc` | `work_parallel_finalization.py`, `work_parallel_mcp.py` |
| [side-decision-refusal-causes](../../fixes/side-decision-refusal-causes/record.md) | `1ce5c5c` | `claude_mods/ciel-work/hooks/side-intake.js` |

- `18aa41c`는 CHANGELOG와 log.md만 바꿨다. 설치 대상이 아니다.

## 회귀

- **임시 격리 Windows**(파일 단위, 재부팅 후 다시 실행): 14개 파일 모두 실패 0([side](../../fixes/side-decision-refusal-causes/evidence/windows-side.txt), [finalization](../../fixes/review-evidence-refusal-text/evidence/windows-finalization.txt)).
  - 재부팅 전에 일괄 실행했을 때 F가 나왔지만, 그때는 CPU 99% 부하 구간이었고 파일 단위로 다시 돌리면 재현되지 않았다.
- **Linux py310·py311:** 14개 파일 모두 `failures=0 errors=0`([로그](../../fixes/side-decision-refusal-causes/evidence/linux-py310-py311.txt)).
- **모드:** `claude plugin validate ciel-work` 통과. `claude plugin test ciel-work` 99 pass / 0 fail([로그](../../fixes/side-decision-refusal-causes/evidence/plugin-test.txt)).

## 발행

- beta push: `c8363a3..18aa41c`.
- 런 `46069949494` 성공. npm beta는 `0.2.51-beta.20261127-212322.18aa41c`(`gitHead 18aa41ce4312`).

## 파일 설치

| 파일 | 해시 |
| --- | --- |
| `side-intake.js` | `3f5e518ae6ce → fce0107ddd9e` |
| `work_parallel_finalization.py` | `57a52860effb → b6bc40b09cbe` |
| `work_parallel_mcp.py` | `1334fb2c09c6 → ab7868d8a2ba` |
| `runtime_constants.py` | `c8ed1d87ea52 → f9e492bb4206` |
| `package.json` | `6f47efbed4d1 → a6d9c7401802` |

- 위 해시는 tarball과 git이 같은지 확인한 값이다.
- check 7/7 `base` → 21:32:47Z apply 7/7 `ok=True` → recheck 7/7 `new`([check](evidence/check-18aa41c.txt), [apply](evidence/apply-18aa41c.txt), [recheck](evidence/recheck-18aa41c.txt)).

## 라이브 적용 (미완)

- 21:33Z 틱 지시가 운영 세션·라우터 재시작 금지라서 라우터를 교체하지 않았다.
- 다섯 라우터는 c8363a3 코드로 돌고 있다: Andy `15960`, Celly `30732`, KOBE `15328`, Prody `4924`, Betty `6236`.
- `work_parallel_finalization.py`·`work_parallel_mcp.py`는 라우터가 다시 기동해야 적용된다.
- `side-intake.js`는 각 세션의 ciel-work 모드가 다시 로드될 때 적용된다. 설치 뒤 리로드는 아직 확인하지 않았다.
