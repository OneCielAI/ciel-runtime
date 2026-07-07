# ciel-runtime (ciel-runtime) — Wiki 홈

> **패키지명**: `@oneciel-ai/ciel-runtime`  
> **버전**: 0.1.1  
> **제작**: One Ciel LLC  
> **라이선스**: MIT

---

## 프로젝트 개요

**ciel-runtime**(`ciel-runtime`)은 Claude Code, Codex, AGY 등 AI 코딩 에이전트를 위한 **범용 모델 라우팅 레이어**다.  
로컬/원격 LLM 제공자(Anthropic, Ollama, DeepSeek, OpenCode, Kimi, ZAI, vLLM, LM Studio, NVIDIA, OpenRouter, Fireworks 등)에 Claude Code의 Anthropic Messages 요청과 Codex의 OpenAI Responses 요청을 중계한다.

```
Claude Code (클라이언트)
       │  Anthropic Messages API
Codex CLI (클라이언트)
       │  OpenAI Responses API
       ▼
 ciel-runtime Router (HTTP, localhost)
       │  provider별 변환
       ▼
Anthropic / Ollama / DeepSeek / OpenCode / ... (upstream LLM)
```

---

## 목차

### 핵심 개념
- [[Architecture]] — 시스템 아키텍처 및 소유권 경계
- [[Router]] — HTTP 라우터 동작 방식
- [[Providers]] — 지원 LLM 제공자 목록 및 설정

### 기능 상세
- [[Configuration]] — 설정 파일, 환경 변수, 경로
- [[Tool-Guard]] — 툴 필터링, 입력 검증, 이름 정규화
- [[MCP-Channels]] — MCP 서버 연동 및 채널 시스템
- [[Thinking-Passthrough]] — Extended Thinking 지원
- [[Plan-Mode]] — Plan Mode 처리
- [[Rate-Limiting]] — API 키 로테이션과 레이트 리밋
- [[Observability]] — EventBus, 로그, SSE 이벤트 스트림

### 실행 및 설치
- [[Installation]] — 설치 방법 (npm, shell script, Windows)
- [install.md](install.md) — 프롬프트/메뉴 설정, 무인 설정, `.env` 설정 예시
- [[CLI-Reference]] — CLI 커맨드 전체 참조

### 개발
- [[Test-Suite]] — 테스트 파일 목록 및 구성
- [[Module-Map]] — 파일별 역할 요약

### 리서치
- [[Codex-CLI-Research]] — Codex CLI 지원 가능성 분석 (2026-06-24)
