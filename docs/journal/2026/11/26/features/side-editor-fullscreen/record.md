---
type: feature-record
title: Side SSE 편집기 전체 화면
---

Work 콘솔의 Side SSE 지침 편집기에 전체 화면과 원래 크기 전환을 추가했다. 브라우저 탭 안에서만 확장하며, 작성 중인 내용과 미리보기는 유지된다. 저장 경로는 변경하지 않았다.

렌더링한 실제 Work 콘솔과 격리된 API 응답으로 Edge/Playwright에서 1440×900 및 390×844를 검증했다. 두 크기 모두 전체 뷰포트를 채우고 Apply가 화면 안에 남았으며, 복원 후 초안 유지와 Escape 닫기를 확인했다. 운영 설정은 쓰지 않았다.

- [데스크톱](evidence/fullscreen-1440.png)
- [모바일](evidence/fullscreen-390.png)
