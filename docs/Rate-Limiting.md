# Rate Limiting — API 키 로테이션과 레이트 리밋

> 소스: `ciel_runtime.py` — `select_provider_api_key()`, `post_json_with_rate_retry()`, `open_provider_request_with_key_retry()`

---

## 개요

여러 API 키를 등록하여 자동 로테이션하고,  
레이트 리밋(429) 발생 시 쿨다운 후 재시도한다.

---

## API 키 관리

### 등록
```bash
ciel-runtimectl api-key sk-ant-...                    # 단일 키
ciel-runtimectl api-keys sk-ant-key1 sk-ant-key2      # 다중 키
```

### 키 선택 로직

`select_provider_api_key(provider, pcfg, rotate=True)`:

1. 설정에서 API 키 목록 로드 (`provider_config_api_keys()`)
2. 쿨다운 상태의 키 제외
3. 라운드로빈 방식으로 다음 키 선택 (`_API_KEY_ROTATION_CURSOR`)
4. 사용 가능한 키가 없으면 최소 쿨다운 키 사용

### 키 저장 위치 우선순위

1. `config.json` `providers.<provider>.api_key`
2. 환경변수 (`ANTHROPIC_API_KEY` 등)
3. NCP `.env` 파일 (`~/.config/nvd-claude-proxy/.env`)
4. Claude settings (`~/.claude/settings.json`)

---

## 레이트 리밋 상태

저장 위치: `RATE_LIMIT_STATE_PATH` = `CONFIG_DIR / "rate-limit-state.json"`

```json
{
  "provider:key_fingerprint": {
    "limited_at": 1234567890.0,
    "retry_after": 60.0,
    "scope": "requests",
    "detail": "Too many requests"
  }
}
```

---

## 재시도 로직

`post_json_with_rate_retry()`:

```
attempt 1 → 429 수신 → Retry-After 파싱 → 대기
attempt 2 → 성공 또는 다른 키로 재시도
...
attempt N (configured_gateway_retries())
```

### 재시도 대기 시간

```python
def upstream_retry_wait_seconds(attempt: int) -> float:
    # 지수 백오프: 1s, 2s, 4s, 8s, ...
```

### 재시도 가능 예외

`retryable_upstream_exception(exc)`:
- `urllib.error.HTTPError` (429 등)
- 연결 오류
- 타임아웃

---

## 쿨다운 메시지

레이트 리밋 발생 시 Claude Code에 노출되는 메시지:
```python
upstream_rate_limit_retry_message(attempt, total)
# → "Rate limited (attempt 1/3). Retrying in 60s..."
```

---

## 키 쿨다운 요약 표시

`_status_key_cooldown_summary(provider, pcfg, state, now)`:
- `ciel-runtimectl status`에서 키별 쿨다운 상태 표시

---

## API 키 쿨다운 테스트

관련 테스트: `tests/test_api_key_cooldown.py`, `tests/test_api_key_rotation.py`

---

## 관련 문서
- [[Providers]] — 제공자별 API 키 설정
- [[Configuration]] — 설정 파일 위치
- [[CLI-Reference]] — `api-key` 커맨드
