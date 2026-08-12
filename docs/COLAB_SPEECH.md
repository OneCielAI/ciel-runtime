# Colab speech workers

Ciel Runtime can proxy its web chat and OpenAI-compatible audio API to two Colab workers over a tailnet-only Tailscale tunnel.

## One-time prerequisites

1. Authenticate the Colab CLI inside WSL. The installed CLI currently uses Google Application Default Credentials, so run `~/google-cloud-sdk/bin/gcloud auth application-default login` in `Ubuntu-26.04`.
2. Create a reusable Tailscale auth key (two workers must register) and set it only for the current PowerShell process with `$env:TAILSCALE_AUTHKEY = Read-Host`. Alternatively, use a separate fresh key for each worker. The CLI passes the key without writing it to the repository. A Colab `TAILSCALE_AUTHKEY` Secret is also supported as a fallback.
3. Optionally set `$env:CIEL_SPEECH_API_KEY = Read-Host` before deployment. Enter that value once in Web Chat > Speech Settings; Ciel stores it server-side and never returns it to the browser.

## Deploy

From PowerShell at the repository root:

```powershell
.\scripts\deploy_colab_speech.ps1
```

Set the WSL distribution, authentication mode, ASR/TTS session names, and accelerators in **Web Chat > Speech Settings > Colab CLI connection**. These values are available through `GET|POST /ca/speech/config`; Ciel does not store Colab credentials. The deployment script reads the saved values automatically. Command-line parameters such as `-Distribution`, `-ColabAuth`, `-AsrSession`, and `-AsrAccelerator` override them for one run.

The script reuses matching active sessions when possible, otherwise creates them, installs the selected Qwen3-ASR model (0.6B or 1.7B) plus the selected TTS engine (MOSS-TTS-Nano or Fun-CosyVoice3-0.5B-2512), starts Tailscale in userspace networking mode, publishes each localhost model server with Tailscale Serve, and saves both returned `base_url` values into Web Chat > Speech Settings automatically. Choose both models in their STT/TTS sections before **Recover & deploy**, or pass `-AsrModel Qwen/Qwen3-ASR-1.7B` and `-TtsBackend moss|cosyvoice3` to the script.

### Session recovery and account profiles

Web Chat > Speech Settings exposes **Check sessions**, **Start missing**, **Recover & deploy**, and **Recreate all**. Start creates only missing or expired ASR/TTS sessions. Recover creates missing sessions and then runs both bootstrap scripts. Recreate explicitly releases both sessions before creating and deploying replacements. These actions are also available through `POST /ca/speech/colab/action`, while `GET /ca/speech/colab/job` returns the latest background-job state and redacted output.

The `default` account profile reuses the existing WSL Colab CLI login. Every other profile name receives an isolated WSL `HOME`, so its OAuth token, ADC credentials, session state, and history cannot mix with another Google account. Choose OAuth2 for the simplest multi-account flow, click **Copy login command**, run that command in a local terminal, and complete the copy/paste authorization prompt with the intended Google account. The optional reset checkbox removes credentials only inside the selected profile before login.

Equivalent CLI actions are:

```powershell
.\scripts\deploy_colab_speech.ps1 -Action Login -Profile second-account -ColabAuth oauth2
.\scripts\deploy_colab_speech.ps1 -Action Status -Profile second-account
.\scripts\deploy_colab_speech.ps1 -Action Start -Profile second-account
.\scripts\deploy_colab_speech.ps1 -Action Deploy -Profile second-account
.\scripts\deploy_colab_speech.ps1 -Action Recreate -Profile second-account
```

Tailscale and speech API keys entered in Web Chat are passed only to the selected background deployment process and are not persisted. They can instead be stored as authorized Colab Secrets for the selected Google account.

MOSS-TTS-Nano is a voice-cloning model without built-in speakers. Deployment configures the project's official `zh_1.wav` sample so the first request works immediately. In Web Chat > Speech Settings, upload a reference voice clip (10 MB maximum) to replace it. Ciel stores uploaded audio only in the local protected runtime configuration, omits it from configuration responses, and adds it to TTS requests automatically. API clients can instead pass `ref_audio` as an HTTP(S) URL or base64 audio data URL to `POST /v1/audio/speech`.

CosyVoice 3 deployment configures its official zero-shot reference clip and exact transcript, enables 24 kHz PCM output streaming, and starts browser playback as chunks arrive. For a custom cloned voice, upload the clip and provide its exact transcript; CosyVoice 3 requires both. **Test voice** unlocks browser audio playback and verifies the complete Ciel-to-worker path.

CosyVoice's bi-streaming means incremental text input and incremental audio output. Ciel currently benefits from the audio-output half: lower time to first sound, bounded buffering, and immediate cancellation when the user interrupts. The agent channel still delivers complete `spoken` fields, so using the text-input half later requires forwarding agent tokens or sentence fragments to a persistent streaming TTS connection. It is not by itself a full-duplex conversation protocol.

Colab sessions are ephemeral. Re-run the bootstrap after a runtime reset. The workers are reachable only by devices in the same tailnet unless an administrator separately enables Tailscale Funnel.

## Live voice

Web Chat's **Start live voice** button keeps the microphone open and uses browser-side voice activity detection (VAD). While the user is speaking, the browser sends rate-limited snapshots of the growing utterance to the Qwen worker and displays the latest best-effort partial transcript. A completed utterance is encoded as PCM WAV, transcribed once more for the final text, and sent to the active coding-agent session automatically. While MOSS TTS is generating or playing a reply, new speech stops it immediately and starts a new utterance (barge-in). Tune end-of-speech silence, minimum speech duration, and the VAD threshold in Speech Settings.

The composer also exposes a local microphone-sensitivity preset and a minimum transcript character count. Low sensitivity requires sustained audio before opening an utterance, which filters keyboard clicks and protects TTS from false barge-in. Empty Qwen wrapper output and transcripts shorter than the selected character count are never sent to the coding agent. Live partial text is shown above the input as an outgoing-message preview.

The Colab Qwen endpoint remains a batch HTTP API, so the live caption is progressive re-transcription rather than token-level server streaming. A future WebSocket or streaming HTTP worker can replace this transport without changing the final-turn behavior.

Web Chat requests carry an input mode and a structured response contract. The active agent first sends a short acknowledgement and then a final response containing `spoken`, `overview`, and optional `details` fields. The browser renders the fields separately and sends only `spoken` to TTS, avoiding long Markdown, URLs, code, and tables in synthesized speech. Legacy plain `message` replies remain supported.

### Multiple local runtime instances

Web backend ownership is scoped to the normalized workspace and router port. A saved Web/Tailscale configuration is not inherited by another workspace, and Ciel refuses to take an explicitly selected Tailscale HTTPS port that already proxies a different local router. `/health` advertises a stable `instance_id` derived from the workspace and port. Web Chat binds each browser origin to that ID, verifies it before sends, voice capture, and SSE reconnects, and stops delivery if a proxy begins returning another runtime. Use `?rebind=1` only when intentionally assigning that browser origin to a different instance.

## API surface

- `GET|POST /ca/speech/config`
- `GET /ca/speech/health`
- `POST /ca/speech/colab/action`
- `GET /ca/speech/colab/job`
- `POST /v1/audio/transcriptions`
- `POST /v1/audio/translations`
- `POST /v1/audio/speech`
- `POST /v1/audio/speech/batch`
- `GET|POST /v1/audio/voices`
- `GET /ca/web/chat/api` lists chat, model, message, response, file, and speech endpoints.
- `GET /ca/tui/status`, `GET /ca/tui/recent`, and `GET /ca/tui/stream` expose the routed coding-agent turn as status, cursor-based history, and authenticated SSE. `GET /ca/tui` is the live monitor UI.
