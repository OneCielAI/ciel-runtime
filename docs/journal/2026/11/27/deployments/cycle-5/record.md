---
type: deployment-record
title: 사이클 5 — 대기 항목 매칭 축소와 미완료 스트림 재시도
description: 4f65d4e(대화방·회사 UUID 비증거화)와 fd96473(라우터 incomplete-stream 오류 재시도)를 beta/npm으로 발행하고, 7개 루트에 해시 확인 설치한 뒤 라우터 다섯 개를 교체해 라이브로 적용한 기록
tags: [deployment, side-intake, retry, router, andy, celly, kobe, prody, betty]
timestamp: 2026-11-27T13:20:00Z
---

# 사이클 5 배포

## 포함한 수정

- [side-wait-match-places](../../fixes/side-wait-match-places/record.md): `side-intake.js` (`4f65d4e`)
- [fork-upstream-incomplete-retry](../../fixes/fork-upstream-incomplete-retry/record.md): `runtime_input_retry.py` (`fd96473`)

## 회귀

| 수정 | Windows(임시 격리) | Linux py310 | Linux py311 | 기타 |
| --- | --- | --- | --- | --- |
| 매칭 | 8/8 파일 | 8/8 | 8/8 | plugin test 99/0 |
| 재시도 | 9/9 파일 | 9/9 | 9/9 | — |

- Linux는 `run_serial.sh`로 파일을 하나씩 실행했다(`docker exec -d`). `cycle5-py31x.log`, `cycle6-py31x.log`.

## 발행

- `4f65d4e`: `Publish to npm` 런 `46001841740` 성공, `0.2.51-beta.20261127-124925.4f65d4e`.
- `fd96473`: 런 `46003278233` 성공, `0.2.51-beta.20261127-130006.fd96473`(`gitHead fd964732b7ac` 일치).

## 파일 설치

- 오버레이 `overlay-fd96473`, 기반 `2fe04af`, stamp `stamp-2fe04af`(2fe04af 오버레이의 `.new` 버전 파일).
  - 코드 2개는 tarball = git이다: `side-intake.js 06e7e1d3e42b → 4e68ae848165`, `runtime_input_retry.py a3f6ad1ea9f1 → 9ce0950717fc`.
  - 버전 파일: `runtime_constants.py → bb508304ba64`, `package.json → 38016c142aab`.
- check 7/7 `base` → apply 7/7 `ok=True` → 재check 7/7 `new`.

## 라이브 적용

| 호스트 | 모드 리로드 | 라우터 교체(UTC) |
| --- | --- | --- |
| Andy `9515` | 13:13:03Z | `51288` → `99124`, 13:13:22–40Z |
| Celly `9385` | 13:13:10Z | `58896` → `79720`, 13:14:17Z(권한 상승 스크립트) |
| KOBE `9779` | 13:12:57Z | `16120` → `17112`, 13:14:57Z |
| Prody `9592` | 13:15:14Z | `7716` → `8412`, 13:15:28Z |
| Betty `9564` | 13:13:18Z | `11296` → `9172`, 13:15:55Z |

- 모든 교체에서 런처와 Claude 세션이 같다(`claude same True`, 클라이언트 pid 유지).

## 라이브 효과

- **재시도 수정:**
  - Andy n2956 포크(`a77850735eb2`)는 12:21Z부터 재시도 없이 슬롯을 쥐고 있었다.
  - 13:15:05Z 모드 알림: `Work fork API error (transport_interrupted) on n2956 … scheduling up to 20 retries`.
  - 13:15:00Z 포크 전사에 `Work recovery: continue approved Work item n2956 …` 메시지가 들어갔다.
- **매칭 수정:** 다음 대기 항목 이벤트의 `wait_items` 수로 확인할 예정이다(13:18Z 틱 시점 미관측).

## 재시작 부작용(13:18Z 틱)

- **Andy:**
  - 13:13:29–31Z `Work reconciliation … MCP server "ciel-runtime-router" is not connected` 4건, `work_host_page … connection closed before the tool call was answered` 1건.
  - n2974 포크(`a350f22cf628`)가 13:13:29Z `ECONNREFUSED`를 받았다. 모드가 13:15:05Z에 재시도를 예약했다. **제 Andy 라우터 교체(13:13:22–40Z)가 원인**이다.
- **Prody:** n1394(`a97670202ed5`)의 `empty_stream` 재시도가 13:15:35Z에 예약됐다. Prody 라우터 교체(13:15:12–28Z) 구간과 겹친다(교체 영향으로 추정, 미확인).
- **Celly·KOBE·Betty:** 교체 뒤 새 실패는 0이다.
  - Celly `auto continue could not read the Work status` 2건(13:12:33, 13:16:48Z)은 교체 전후에 걸친 기존 반복 패턴이다.
