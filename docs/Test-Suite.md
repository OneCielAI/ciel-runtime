# Test Suite — 테스트 파일 목록 및 구성

> 위치: `tests/`  
> 실행: `python -m unittest discover -s tests -p "test_*.py"`  
> npm: `npm test`

---

## 테스트 파일 목록

### 아키텍처 / 컨트랙트

| 파일 | 테스트 대상 |
|------|-----------|
| `test_architecture_contracts.py` | `architecture.py` 추상 클래스 구현 계약 |
| `test_runtime_routers.py` | Claude/Codex 라우터 경로 소유권 및 공통 capability parity |

---

### 제공자별 테스트

| 파일 | 제공자 |
|------|--------|
| `test_anthropic_model_switch.py` | Anthropic 모델 전환 |
| `test_anthropic_native_output_tokens.py` | Anthropic Native 출력 토큰 처리 |
| `test_anthropic_upstream_query.py` | Anthropic 업스트림 쿼리 |
| `test_claude_native_provider.py` | Claude Native 제공자 |
| `test_deepseek_provider.py` | DeepSeek 제공자 |
| `test_fireworks_provider.py` | Fireworks.ai 제공자 |
| `test_kimi_provider.py` | Kimi 제공자 |
| `test_lm_studio_provider.py` | LM Studio 제공자 |
| `test_ollama_provider_options.py` | Ollama 옵션 처리 |
| `test_opencode_provider.py` | OpenCode 제공자 |
| `test_openrouter_provider.py` | OpenRouter 제공자 |
| `test_vllm_provider.py` | vLLM 제공자 |
| `test_zai_provider.py` | Z.AI 제공자 |

---

### 라우터 및 프로토콜

| 파일 | 테스트 대상 |
|------|-----------|
| `test_router_debug.py` | 라우터 디버그 기능 |
| `test_upstream_filter.py` | 업스트림 필터링 |
| `test_upstream_cancel.py` | 업스트림 요청 취소 |
| `test_provider_wire_normalization.py` | 제공자 Wire 형식 정규화 |
| `test_channel_bridge.py` | 채널 브릿지 |

---

### 툴 관련

| 파일 | 테스트 대상 |
|------|-----------|
| `test_tool_guard.py` | Tool Guard 필터링/검증 |
| `test_tool_name_canonicalization.py` | 툴 이름 정규화 |
| `test_cron_tools.py` | Cron 툴 처리 |

---

### API 키 / 레이트 리밋

| 파일 | 테스트 대상 |
|------|-----------|
| `test_api_key_cooldown.py` | API 키 쿨다운 로직 |
| `test_api_key_rotation.py` | API 키 로테이션 |
| `test_rate_limit_defaults.py` | 레이트 리밋 기본값 |

---

### Thinking / Extended Reasoning

| 파일 | 테스트 대상 |
|------|-----------|
| `test_thinking_passthrough.py` | Thinking 패스스루 처리 |

---

### 채널 / 설정

| 파일 | 테스트 대상 |
|------|-----------|
| `test_channels_config.py` | 채널 설정 파싱 |
| `test_live_llm_options.py` | 실시간 LLM 옵션 |

---

### Advisor

| 파일 | 테스트 대상 |
|------|-----------|
| `test_advisor_feedback.py` | Advisor 피드백 처리 |
| `test_advisor_native_standard_flow.py` | Advisor Native 표준 흐름 |
| `test_advisor_oauth_system.py` | Advisor OAuth 시스템 |

---

### 기타

| 파일 | 테스트 대상 |
|------|-----------|
| `test_empty_end_turn_recovery.py` | 빈 end_turn 복구 |
| `test_headless_update_checks.py` | 헤드리스 업데이트 확인 |
| `test_install_diagnostics.py` | 설치 진단 |
| `test_log_level.py` | 로그 레벨 처리 |
| `test_menu_key_debug.py` | 메뉴 키 디버그 |
| `test_observability.py` | EventBus 및 관찰성 |
| `test_rate_limit_defaults.py` | 레이트 리밋 기본값 |
| `test_recommended_timeout.py` | 권장 타임아웃 계산 |
| `test_review_command_passthrough.py` | Review 커맨드 패스스루 |
| `test_statusline.py` | Statusline 출력 |
| `test_version_sync.py` | 버전 동기화 (package.json ↔ ciel_runtime.py) |
| `test_web_chat_ui.py` | 웹 채팅 UI |

---

## 테스트 실행 방법

### 전체 테스트
```bash
python -m unittest discover -s tests -p "test_*.py"
```

### 특정 테스트
```bash
python -m unittest tests.test_tool_guard
python -m unittest tests.test_api_key_rotation
```

### npm으로 실행
```bash
npm test
```
문법 검사 + 전체 테스트 실행.

---

## 린트

```bash
npm run lint
# 또는
python -m ruff check .
```

---

## 관련 문서
- [[Architecture]] — 아키텍처 계약
- [[Tool-Guard]] — 툴 가드 상세
- [[Rate-Limiting]] — 레이트 리밋 상세
