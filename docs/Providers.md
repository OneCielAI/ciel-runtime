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
| `zai-api` | Z.AI Model API (API Key) | OpenAI Chat | `https://api.z.ai/api/paas/v4` |
| `zai-coding-plan` | Z.AI Coding Plan | Anthropic Messages / OpenAI Chat | `https://api.z.ai/api/coding/paas/v4` |
| `zai-start-plan` | Z.AI Start Plan | Anthropic Messages | `https://zcode.z.ai/api/v1/zcode-plan/anthropic` |
| `vllm` | vLLM | OpenAI Chat | 로컬/원격 vLLM |
| `lm-studio` | LM Studio | OpenAI Chat | 로컬 LM Studio |
| `nvidia-hosted` | Nvidia Hosted | OpenAI Chat | NVIDIA NIM Cloud |
| `self-hosted-nim` | Self Hosted NIM | OpenAI Chat | 로컬 NIM |
| `openrouter` | OpenRouter | OpenAI Chat | `https://openrouter.ai/api` |
| `tabitoken` | TaBiAI (Tabitoken.com) | Anthropic Messages / OpenAI Chat | `https://tabitoken.com` |
| `fireworks` | Fireworks.ai | OpenAI Chat | `https://api.fireworks.ai/inference` |
| `xai` | xAI | OpenAI Responses / Chat | `https://api.x.ai/v1` |

### 선언형 OpenAI Chat 호환 제공자

아래 제공자는 동일한 OpenAI Chat/Models 계약을 공유하지만 각각 독립된 내부 ID,
레이블, 기본 endpoint, 모델 fallback과 별칭을 갖는다. API 키 또는 발급받은 access
token은 Bearer header로 전송하며, 사용자 지정 `base_url`로 사설 gateway도 사용할 수
있다.

| 범주 | 내부 ID |
|------|---------|
| 글로벌 API | `openai`, `gemini`, `groq`, `cerebras`, `cohere`, `huggingface`, `mistral`, `together`, `perplexity`, `openrouter` |
| 모델 gateway | `blackbox`, `chutes`, `featherless`, `hyperbolic`, `nebius`, `siliconflow`, `venice`, `vercel-ai-gateway` |
| 중국·아시아 | `alicode`, `alicode-intl`, `alims-intl`, `byteplus`, `glm-cn`, `volcengine-ark`, `xiaomi-mimo`, `xiaomi-tokenplan`, `mimo-free`, `mmf` |
| 수동 OAuth token | `cline`, `clinepass`, `codebuddy-cn`, `github`, `gitlab`, `iflow`, `kilocode`, `kimchi`, `qwen` |
| 동적 cloud endpoint | `cloudflare-ai`, `vertex`, `vertex-partner` |

`cloudflare-ai`, `vertex`, `vertex-partner`는 계정·프로젝트·region마다 URL이 달라
기본 URL을 추측하지 않는다. 각 서비스에서 발급된 OpenAI-compatible endpoint를
`base_url`로 명시해야 한다. “수동 OAuth token” 범주는 transport가 구현된 상태를
뜻하며, Ciel이 브라우저 로그인이나 refresh token을 대신 발급한다는 뜻은 아니다.

### Alibaba Model Studio Singapore

`alims-intl`의 기본 모델은 Singapore International scope의 rolling alias
`qwen3.8-max`다. 고정 스냅샷 `qwen3.8-max-0902`도 모델 목록에서 선택할 수 있다.
Token Plan은 공식 지원 ID인 rolling alias `qwen3.8-max`를 사용하며, Responses
API에서는 `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` reasoning
effort를 그대로 전달한다. Chat Completions의 축약 effort 매핑과 혼용하지 않는다.
Alibaba Responses session cache의 공식 opt-in 계약에 따라
`x-dashscope-session-cache: enable`을 Responses 요청에 기본 전송한다. Chat과
Anthropic Messages 요청에는 이 header를 추가하지 않는다.
공식 한도에 맞춰 context window는 1,000,000, 최대 입력은 일반 모드 991,808,
thinking 모드 983,616, 최대 출력은 131,072로 취급한다. Codex에는 thinking 모드의
안전한 입력 상한인 983,616을 model catalog context로 제공한다.

신규 workspace endpoint는 계정별 Workspace ID가 필요하므로 Ciel이 이를 추측하지
않는다. Model Studio console에서 발급된 다음 OpenAI-compatible URL을 `base_url`에
입력한다.

```text
https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
```

이 URL을 사용하면 Claude용 native endpoint는 같은 workspace의
`/apps/anthropic`으로 파생된다. `alitoken`은 별도 구독·과금 계약의 Singapore Token
Plan endpoint를 계속 사용하므로 일반 `alims-intl` workspace URL과 혼용하지 않는다.

`kimi-k3`는 `alims-intl`과 `alitoken`의 준비 카탈로그에 포함된다. 공식 hard limit은
context와 최대 출력 모두 1,048,576 tokens다. Ciel의 기본 대화 출력 예산은 입력
history 공간을 보존하도록 131,072로 설정하며 사용자가 명시적으로 조정할 수 있다.
OpenAI Responses 요청도 upstream에는 Chat Completions로 변환한다. K3는 thinking-only이므로
`enable_thinking=true`와 thinking history 보존을 적용하고, 지원하지 않는
`thinking_budget` 및 가변 reasoning effort는 upstream에 보내지 않는다. Anthropic
Messages 요청은 공식 `/apps/anthropic` 계약을 그대로 사용한다.

Token Plan에서 인증된 `/models` 조회가 성공하면 그 계정별 결과가 준비 카탈로그보다
우선한다. 따라서 Alibaba가 해당 구독/계정에 K3를 실제 배포하기 전에는 Ciel이 모델을
강제로 노출하지 않는다. Coding Plan의 2026-08-26 공식 목록에는 K3가 없으므로
`alicode`와 `alicode-intl`에는 추가하지 않는다.

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

### xAI / Grok

`xai`는 일반 OpenAI 호환 목록이 아니라 전용 adapter다. `/v1/models`를 권위 있는
카탈로그로 사용하고, 연결할 수 없을 때 현재 문서화된 text 모델을 fallback으로
제공한다.

| 모델 | context | upstream |
|------|--------:|----------|
| `grok-4.6` | 500K | Responses / Chat, `xhigh` reasoning |
| `grok-build-0.1` | 256K | Responses / Chat |
| `grok-4.5` | 500K | Responses / Chat |
| `grok-4.3` | 1M | Responses / Chat |
| `grok-4.20-*` | 1M | Responses / Chat |

Codex의 `/v1/responses` 요청은 변환하지 않고 xAI Responses로 전달한다.
`POST /v1/responses/compact`도 xAI의 opaque `compaction` item과
`encrypted_content`를 수정하지 않고 전달한다. Responses 요청의
`prompt_cache_key`는 그대로 보존한다. Chat conversation affinity가 필요한 경우
workspace provider option `conversation_id`를 설정하면 `x-grok-conv-id` header로
전달한다.

Imagine image/video, Voice, STT/TTS 모델 ID는 text LLM 선택 메뉴에 섞지 않는다.
이 모델들은 media/speech capability adapter의 대상이다.

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
- Routed 모드는 기본적으로 표준 200K 컨텍스트를 사용한다. 1M 사용 크레딧
  beta를 명시적으로 사용할 때만 모델 ID에 `[1m]`을 붙인다.
- 공개 모델 ID 목록 (`ANTHROPIC_PUBLIC_MODEL_DEFAULT_IDS`):
  - `claude-fable-5-1`, `claude-fable-5`, `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, `claude-haiku-4-5`
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

Z.AI의 API key, Coding Plan, Start Plan을 서로 다른 provider profile로 보존한다.
기존 `zai` provider와 그 수동 API key는 마이그레이션하거나 덮어쓰지 않는다.

| profile | 인증 | Ciel OpenAI 경로 | Anthropic 경로 |
|---------|------|------------------|----------------|
| `zai` | 기존 수동 API key | 해당 없음 | `https://api.z.ai/api/anthropic` |
| `zai-api` | 일반 종량제 API key | `https://api.z.ai/api/paas/v4` | 공식 모델 문서에서 일반 API용 별도 Anthropic URL을 확인하지 못했으므로 사용하지 않음 |
| `zai-coding-plan` | Coding Plan API key 또는 `--profile coding-plan` OAuth | `https://api.z.ai/api/coding/paas/v4` | `https://api.z.ai/api/anthropic` |
| `zai-start-plan` | ZCode OAuth shared Start Plan JWT | 제공되지 않음 | `https://zcode.z.ai/api/v1/zcode-plan/anthropic` |

Coding Plan의 OpenAI Chat/Anthropic URL은 Z.AI 공식 Tool Integration 문서에
기재된 값이다. Start Plan 계약은 Mia에 설치된 공식 ZCode Desktop 3.9.1의
`~/.zcode/v2/config.json`, 배포 번들 및 실행 로그에서 확인했다. 이 프로필은 Coding
Plan 주소를 상속하지 않으며 최종 요청은
`https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages`로 전송한다. 모델 요청마다
공식 Aliyun CAPTCHA SDK가 발급한 일회성 결과를
`X-Aliyun-Captcha-Verify-Param`, region을
`X-Aliyun-Captcha-Verify-Region`으로 전송한다. Ciel은 이 값을 우회하거나 위조하지
않고, 요청을 일시 정지한 뒤 state-bound 검증 페이지에서 받은 결과만 사용한다.

```bash
ciel-runtimectl zai-oauth login --profile coding-plan
ciel-runtimectl zai-oauth status --profile coding-plan
ciel-runtimectl zai-oauth login --profile start-plan
ciel-runtimectl zai-oauth status --profile start-plan
```

Coding Plan 로그인은 CLI의 최종 plan API key만 `zai-coding-plan`에 저장한다.
Start Plan 로그인은 같은 공식 ZCode OAuth init/poll 흐름이 저장한 암호화
`zcodejwttoken`을 `zai-start-plan`에 가져온다. 이미 로그인된 환경에서는
`ciel-runtimectl zai-oauth import --profile start-plan`을 사용할 수 있으며, 이전
ZCode 버전의 선택된 Desktop provider는 fallback으로만 읽는다. 두 프로필은
자격증명과 endpoint를 공유하지 않으며, 기존 `zai` credential도 변경하지 않는다.

- 기본 모델: `glm-5.3[1m]`
- GLM-5.3은 reasoning을 끌 수 없으며 `low`, `high`, `max` effort만 사용한다.
- `zai`, `zai-api`, `zai-coding-plan`은 Managed MCP 서버 `web-search-prime`,
  `web-reader`, `zread`를 사용할 수 있다.
- 공식 모델 한도:

| 모델 접두사 | 컨텍스트 | 최대 출력 |
|-----------|---------:|----------:|
| `glm-5.3` | 1,000,000 | 131,072 |
| `glm-5.2` | 1,000,000 | 131,072 |
| `glm-5.1` | 200,000 | 131,072 |
| `glm-5-turbo` | 200,000 | 모델별 동적 값 |
| `glm-4.7` | 200,000 | 모델별 동적 값 |

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

## TaBiAI (Tabitoken.com)

- 내부 ID: `tabitoken` (`tabi`, `tabiai`, `tabi-token` 별칭 지원).
- 공식 공개 pricing catalog의 활성 모델 4개를 기본 catalog로 제공한다:
  `claude-opus-4-8`, `claude-opus-4-8-thinking`, `claude-opus-5`,
  `claude-opus-5-thinking`.
- Claude 요청은 `POST /v1/messages`, Codex/OpenAI 요청은
  `POST /v1/chat/completions`로 보낸다.
- 모든 endpoint에 `Authorization: Bearer <TOKEN>`을 사용한다. 공식 API Detail에
  표시된 `x-api-key`는 Anthropic 형식 endpoint가 추가로 허용하는 대체 인증이다.
- `-thinking` 모델의 OpenAI Chat 요청에만 `reasoning_effort`를 전달한다.

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
