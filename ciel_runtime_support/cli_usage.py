"""User-facing command-line usage presentation."""

def cli_usage_text() -> str:
    return """Usage:
  ciel-runtime                         Launch Claude Code through ciel-runtime router
  ciel-runtime codex [args...]         Launch Codex through ciel-runtime router
  ciel-runtime codex-app-server [args...]  Launch Codex app-server through ciel-runtime
  ciel-runtime agy [args...]           Launch Google Antigravity CLI through ciel-runtime
  ciel-runtime resume                  Resume Codex/AGY when that runtime provider is selected

Control plane, runs before Claude Code and does not require LLM connectivity:
  ciel-runtime version                 Print ciel-runtime version
  ciel-runtime language [en|ko|ja|zh] Set display language
  ciel-runtime provider                Pick provider with arrow-key TUI
  ciel-runtime provider list           List providers
  ciel-runtime provider PROVIDER       Set provider
  ciel-runtime base-url PROVIDER URL   Set provider base URL
  ciel-runtime model MODEL_ID          Set current provider model
  ciel-runtime advisor-model MODEL_ID  Set current provider advisor model (off disables)
  ciel-runtime models [PROVIDER]       List models
  ciel-runtime api-key PROVIDER        Store API key securely
  ciel-runtime api-key PROVIDER clear  Clear stored API key(s)
  ciel-runtime set-api-key PROVIDER KEY
  ciel-runtime set-api-keys PROVIDER KEY1,KEY2
  ciel-runtime web-search [on|off]     Auto-attach DuckDuckGo MCP for non-native providers
  ciel-runtime web-fetch [on|off]      Auto-attach fetch MCP for web page content
  ciel-runtime log-level [LEVEL]       Show or set router log level
  ciel-runtime channels [cmd]          Configure external channel specs
  ciel-runtime channel-delivery [stdin|native]
                                      Select PTY wake proxy or native claude/channel bridge
  ciel-runtime ollama-native [on|off]  Use Ollama's official Claude Code env path
  ciel-runtime ollama-options [provider] [key=value ...]
                                      Set Ollama num_ctx/options/keep_alive/think
  ciel-runtime provider-options [provider] [key=value ...]
                                      Set vLLM/NIM/NVIDIA output/context/timeouts
  ciel-runtime ollama-catalog          Download Ollama model/context catalog
  ciel-runtime test [seconds] [mode]   Test compatibility; mode is auto, quick, smoke, or full
  ciel-runtime stop                    Stop router/proxy

Headless setup flags, namespaced to avoid Claude CLI collisions:
  ciel-runtime --ca-provider PROVIDER  Set provider, then launch
  ciel-runtime --ca-env-file PATH      Load CIEL_RUNTIME_* values from a .env file
  ciel-runtime --ca-runtime claude|codex|codex-app-server|agy
                                      Select Claude Code, Codex, Codex app-server, or AGY for this launch
  ciel-runtime --ca-menu               Apply setup values, then open the menu
  ciel-runtime --ca-language en|ko|ja|zh
  ciel-runtime --ca-base-url URL       Set current provider base URL, then launch
  ciel-runtime --ca-model MODEL_ID     Set provider model, then launch
  ciel-runtime --ca-advisor-model MODEL_ID
  ciel-runtime --ca-auto-llm-options [MODEL_ID]
                                      Apply recommended LLM options for MODEL_ID or the saved model
  ciel-runtime --ca-api-key KEY        Set current provider API key, then launch
  ciel-runtime --ca-api-key clear      Clear current provider API key(s), then launch
  ciel-runtime --ca-api-key-env ENVVAR Set current provider API key from env, then launch
  ciel-runtime --ca-api-keys KEY1,KEY2 Set current provider API keys with round-robin
  ciel-runtime --ca-api-keys-env ENVVAR
                                      Set current provider API keys from env, then launch
  ciel-runtime --ca-set-api-key PROVIDER KEY
  ciel-runtime --ca-set-api-key-env PROVIDER ENVVAR
  ciel-runtime --ca-set-api-keys PROVIDER KEY1,KEY2
  ciel-runtime --ca-set-api-keys-env PROVIDER ENVVAR
  ciel-runtime --ca-provider-option KEY=VALUE
                                      Set a provider option for the current provider
  ciel-runtime --ca-set-provider-option PROVIDER KEY=VALUE
                                      Set a provider option for a specific provider
  ciel-runtime --ca-ollama-num-ctx VALUE
  ciel-runtime --ca-ollama-ctx-range MIN MAX
  ciel-runtime --ca-ollama-option KEY=VALUE
  ciel-runtime --ca-max-output-tokens VALUE
  ciel-runtime --ca-context-window VALUE
  ciel-runtime --ca-request-timeout-ms VALUE
  ciel-runtime --ca-stream-idle-timeout-ms VALUE
  ciel-runtime --ca-rate-limit-rpm VALUE
  ciel-runtime --ca-rate-limit-status on|off
  ciel-runtime --ca-stream on|off
  ciel-runtime --ca-stream-word-chunking on|off
  ciel-runtime --ca-log-level LEVEL    Set router log level: SILENT, ERROR, WARN, INFO, DEBUG, TRACE
  ciel-runtime --ca-web-search         Force DuckDuckGo MCP for this launch
  ciel-runtime --ca-no-web-search      Disable DuckDuckGo MCP for this launch
  ciel-runtime --ca-web-fetch          Enable fetch MCP
  ciel-runtime --ca-no-web-fetch       Disable fetch MCP
  ciel-runtime --ca-channel SPEC       Add an official/approved Claude Code channel
  ciel-runtime --ca-channel-delivery MODE
                                      Set channel delivery: stdin or native
  ciel-runtime --ca-clear-channels     Clear saved channel specs
  ciel-runtime --ca-no-self-update-check
                                      Skip Ciel Runtime npm self-update check
  ciel-runtime --ca-no-update-check    Skip runtime update check for this launch
  ciel-runtime --ca-upgrade-and-exit   Update Ciel Runtime, Claude Code, Codex, and AGY without prompts, then exit
  ciel-runtime --ca-no-launch          Apply setup flags/env values, then exit without launching a runtime
  ciel-runtime --ca-stop               Stop router/proxy
  ciel-runtime --                      Pass all following args directly to the selected runtime

Provider names: agy, agy-routed, anthropic, ollama, ollama-cloud, deepseek, opencode, opencode-go, kimi, z.ai, vllm, lm-studio, nvidia-hosted, self-hosted-nim, openrouter, fireworks
Any other arguments are passed through to the selected runtime. Use -- before runtime
flags that collide with ciel-runtime setup flags."""
