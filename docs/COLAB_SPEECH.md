# Colab speech workers

Ciel Runtime can proxy its web chat and OpenAI-compatible audio API to two Colab workers over a tailnet-only Tailscale tunnel.

## One-time prerequisites

1. Authenticate the Colab CLI inside WSL. The installed CLI currently uses Google Application Default Credentials, so run `~/google-cloud-sdk/bin/gcloud auth application-default login` in `Ubuntu-26.04`.
2. Create a reusable or ephemeral Tailscale auth key. In each Colab account/notebook Secret store, add `TAILSCALE_AUTHKEY` and grant notebook access.
3. Optionally add the same `CIEL_SPEECH_API_KEY` secret to both workers. Enter that value once in Web Chat > Speech Settings; Ciel stores it server-side and never returns it to the browser.

## Deploy

From PowerShell at the repository root:

```powershell
.\scripts\deploy_colab_speech.ps1
```

The script creates `ciel-asr` and `ciel-tts` T4 sessions, installs Qwen3-ASR-0.6B and MOSS-TTS-Nano, starts Tailscale in userspace networking mode, publishes each localhost model server with Tailscale Serve, and saves both returned `base_url` values into Web Chat > Speech Settings automatically.

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
