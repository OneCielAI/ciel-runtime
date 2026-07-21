# Providers — 지원 LLM 제공자

> 단일 등록 원천: `ciel_runtime_support/provider_adapters.py`와 `providers/` 패키지

---

## 제공자 목록

| 내부 ID | 레이블 | 프로토콜 | 기본 URL |
|---------|--------|---------|----------|
| `anthropic` | Claude Native | Anthropic Messages | Anthropic 공식 API |
| `ollama` | Ollama | Ollama Chat | `http://localhost:11434` |
| `ollama-cloud` | Ollama Cloud | Ollama Chat | 원격 Ollama |
| `deepseek` | DeepSeek.com | OpenAI Chat | `https://api.deepseek.com` |
| `opencode` | OpenCode Zen | Anthropic Messages / OpenAI Chat | `https://opencode.ai/zen` |
| `opencode-go` | OpenCode Go | Anthropic Messages / OpenAI Chat | `https://opencode.ai/zen/go` |
| `kimi` | Kimi.com | Anthropic Messages / OpenAI Chat | `https://api.kimi.com/coding` |
| `zai` | Z.AI GLM | Anthropic Messages | `https://api.z.ai/api/anthropic` |
| `vllm` | vLLM | OpenAI Chat | 로컬/원격 vLLM |
| `lm-studio` | LM Studio | OpenAI Chat | 로컬 LM Studio |
| `nvidia-hosted` | Nvidia Hosted | OpenAI Chat | NVIDIA NIM Cloud |
| `self-hosted-nim` | Self Hosted NIM | OpenAI Chat | 로컬 NIM |
| `openrouter` | OpenRouter | OpenAI Chat | `https://openrouter.ai/api` |
| `fireworks` | Fireworks.ai | OpenAI Chat | `https://api.fireworks.ai/inference` |

### 선언형 OpenAI Chat 호환 제공자

아래 제공자는 동일한 OpenAI Chat/Models 계약을 공유하지만 각각 독립된 내부 ID,
레이블, 기본 endpoint, 모델 fallback과 별칭을 갖는다. API 키 또는 발급받은 access
token은 Bearer header로 전송하며, 사용자 지정 `base_url`로 사설 gateway도 사용할 수
있다.

| 범주 | 내부 ID |
|------|---------|
| 글로벌 API | `openai`, `gemini`, `xai`, `groq`, `cerebras`, `cohere`, `huggingface`, `mistral`, `together`, `perplexity`, `openrouter` |
| 모델 gateway | `blackbox`, `chutes`, `featherless`, `hyperbolic`, `nebius`, `siliconflow`, `venice`, `vercel-ai-gateway` |
| 중국·아시아 | `alicode`, `alicode-intl`, `alims-intl`, `byteplus`, `glm-cn`, `volcengine-ark`, `xiaomi-mimo`, `xiaomi-tokenplan`, `mimo-free`, `mmf` |
| 수동 OAuth token | `cline`, `clinepass`, `codebuddy-cn`, `github`, `gitlab`, `iflow`, `kilocode`, `kimchi`, `qwen` |
| 동적 cloud endpoint | `cloudflare-ai`, `vertex`, `vertex-partner` |

`cloudflare-ai`, `vertex`, `vertex-partner`는 계정·프로젝트·region마다 URL이 달라
기본 URL을 추측하지 않는다. 각 서비스에서 발급된 OpenAI-compatible endpoint를
`base_url`로 명시해야 한다. “수동 OAuth token” 범주는 transport가 구현된 상태를
뜻하며, Ciel이 브라우저 로그인이나 refresh token을 대신 발급한다는 뜻은 아니다.

### Anthropic Messages 호환 제공자

`minimax`와 `minimax-cn`은 OpenAI 변환을 거치지 않고 Anthropic Messages 계약,
thinking 보존, `/v1/messages` endpoint를 사용한다.

### Azure OpenAI

`azure`는 deployment root를 `base_url`로 받고 `/chat/completions`를 결합한다.
인증은 Azure 규격의 raw `api-key` header를 사용하며 `api_version` 설정값을 query로
추가한다. 기본값은 `2024-10-21`이다.

### 범위 경계

9router 레지스트리의 검색, 임베딩, 이미지 생성, TTS/STT 항목은 LLM Provider
Adapter로 위장해 등록하지 않는다. 이들은 Ciel의 향후 capability별 port/adapter에
속한다. 또한 Cursor/Kiro의 비공개 wire protocol, 웹 cookie 재사용, client 위장
방식은 공식적이고 안정적인 API 계약이 아니므로 일반 Provider로 취급하지 않는다.

---

## 제공자 별칭 (PROVIDER_ALIASES)

다수의 별칭이 정규 ID로 매핑된다.  
예: `claude`, `native`, `claude-code` → `anthropic`  
예: `ds`, `deepseek.com`, `deepseek-api` → `deepseek`  
예: `or`, `openrouter.ai` → `openrouter`

---

## Anthropic (Native / Routed)

- **Native 모드**: Claude Code가 직접 Anthropic API 호출. Router가 중계하지 않음.
- **Routed 모드**: Router가 중계하며 `ROUTED_COMPAT_PROMPT`를 시스템 프롬프트에 주입.
- 공개 모델 ID 목록 (`ANTHROPIC_PUBLIC_MODEL_DEFAULT_IDS`):
  - `claude-fable-5`, `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, `claude-haiku-4-5`
- 제한 접근 모델 (`ANTHROPIC_LIMITED_ACCESS_MODEL_IDS`):
  - `claude-mythos-5`, `claude-mythos-preview`

---

## Ollama / Ollama Cloud

- 로컬 Ollama 인스턴스 또는 원격 Ollama 서버 지원.
- 컨텍스트 윈도우 크기: `ollama_num_ctx_for_payload()`로 동적 계산.
- 모델 카탈로그: `https://ollama.com/api/tags` 에서 24시간 TTL 캐시.
- 라이브러리 페이지 파싱으로 태그별 컨텍스트 크기 자동 감지.
- 주요 모델 프리셋:

| 모델 | compat_max_tokens | thinking |
|------|------------------|---------|
| `glm-5.2:cloud` | 64 | ✅ |
| `glm-4.7` | 64 | ✅ |
| `deepseek-r1` | 64 | ✅ |
| `qwen3-coder` | 16 | ❌ |
| `llama3.3:70b` | 16 | ❌ |

Ollama의 GLM-5.2 모델 메타데이터와 Chat API에 맞춰 `think: true`와 최대
`options.num_ctx: 999424`(표시값 976K)를 사용한다. `tools`, `keep_alive`,
`options.num_predict`는 Ollama Chat API의 공식 필드 위치를 유지한다. Z.AI의
Anthropic thinking 객체나 문서로 확인되지 않은 GLM-5.2 effort 문자열은 Ollama
요청에 복사하지 않는다.

---

## DeepSeek

- OpenAI Chat 호환 API.
- API 키 필요.

---

## OpenCode (Zen / Go)

- Anthropic Messages 및 OpenAI Chat 엔드포인트 모두 지원.
- IPv6 preferred 기본값 (`default_provider_ip_family()` 반환 `"ipv6-preferred"`).
- 엔드포인트 별칭:
  - `messages` / `anthropic` → `anthropic-messages`
  - `chat` → `openai-chat`
  - `responses` → `openai-responses`
  - `gemini` / `google` → `google-generative`

---

## Kimi (Moonshot)

- 기본 모델: `kimi-for-coding`
- Claude Code 경로는 Kimi 공식 Claude Code 설정과 맞춰 Anthropic Messages 호환 엔드포인트를 우선 사용한다.
- Codex/Codex App 경로는 Codex의 OpenAI Responses 입력을 OpenAI Chat 호환 요청으로 변환해 `https://api.kimi.com/coding/v1/chat/completions`로 보낸다.

---

## ZAI (Z.AI GLM)

- GLM 시리즈 모델 제공.
- 기본 모델: `glm-5.2[1m]`
- Managed MCP 서버 포함: `web-search-prime`, `web-reader`, `zread`
- 컨텍스트 힌트:

| 모델 접두사 | 컨텍스트 |
|-----------|---------|
| `glm-5.2` | 1,000,000 |
| `glm-5-turbo` | 200,000 |
| `glm-4.7` | 200,000 |

---

## vLLM / LM Studio

- OpenAI 호환 로컬 서버.
- LM Studio 최소 컨텍스트: 32,768 토큰.
- LM Studio 기본 컨텍스트: 65,536 토큰.

---

## NVIDIA Hosted / Self-Hosted NIM

- NVIDIA NIM Cloud 또는 로컬 NIM 인스턴스.
- NVIDIA 전용 베이스 URL 검증 함수: `invalid_nvidia_hosted_base_url()`.
- 기본 컨텍스트 크기 (`nvidia_hosted_context_default()`):
  - Kimi K2.6: 262,144
  - DeepSeek: 131,072
  - GLM/Qwen: 65,536

---

## OpenRouter

- 단일 API로 다수 모델 접근.
- OpenAI Chat 호환.

---

## Fireworks.ai

- 고속 추론 서비스.
- OpenAI Chat 호환.
- 기본 계정 ID: `fireworks`

---

## IP 패밀리 정책

일부 제공자(특히 OpenCode)는 IPv6 preferred 정책을 사용한다.

| 값 | 의미 |
|----|------|
| `auto` | 시스템 기본 |
| `ipv4` | IPv4만 허용 |
| `ipv6` | IPv6만 허용 |
| `ipv4-preferred` | IPv4 우선, IPv6 폴백 |
| `ipv6-preferred` | IPv6 우선, IPv4 폴백 |

---

## 관련 문서
- [[Architecture]] — 제공자 아키텍처 계층
- [[Configuration]] — 제공자 설정 방법
- [[Rate-Limiting]] — API 키 관리
