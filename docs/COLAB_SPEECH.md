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

The script reuses matching active sessions when possible, otherwise creates them, installs Qwen3-ASR-0.6B and MOSS-TTS-Nano, starts Tailscale in userspace networking mode, publishes each localhost model server with Tailscale Serve, and saves both returned `base_url` values into Web Chat > Speech Settings automatically.

MOSS-TTS-Nano is a voice-cloning model without built-in speakers. Deployment configures the project's official `zh_1.wav` sample so the first request works immediately. In Web Chat > Speech Settings, upload a reference voice clip (10 MB maximum) to replace it. Ciel stores uploaded audio only in the local protected runtime configuration, omits it from configuration responses, and adds it to TTS requests automatically. API clients can instead pass `ref_audio` as an HTTP(S) URL or base64 audio data URL to `POST /v1/audio/speech`.

Colab sessions are ephemeral. Re-run the bootstrap after a runtime reset. The workers are reachable only by devices in the same tailnet unless an administrator separately enables Tailscale Funnel.

## API surface

- `GET|POST /ca/speech/config`
- `GET /ca/speech/health`
- `POST /v1/audio/transcriptions`
- `POST /v1/audio/translations`
- `POST /v1/audio/speech`
- `POST /v1/audio/speech/batch`
- `GET|POST /v1/audio/voices`
- `GET /ca/web/chat/api` lists chat, model, message, response, file, and speech endpoints.
