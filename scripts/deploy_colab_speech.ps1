param(
    [string]$Distribution = "Ubuntu-26.04",
    [string]$AsrSession = "ciel-asr",
    [string]$TtsSession = "ciel-tts"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$wslRepo = (& wsl -d $Distribution -- wslpath -a ($repo -replace '\\', '/')).Trim()
if (-not $wslRepo) { throw "Could not resolve the repository path in WSL." }

Write-Host "Checking Colab CLI authentication..."
& wsl -d $Distribution -- bash -lc "colab status >/dev/null"
if ($LASTEXITCODE -ne 0) {
    throw "Colab CLI is not authenticated. Configure ADC in WSL (gcloud auth application-default login), then rerun."
}

Write-Host "Creating ASR T4 session: $AsrSession"
& wsl -d $Distribution -- bash -lc "colab new --gpu T4 --session '$AsrSession'"
if ($LASTEXITCODE -ne 0) { throw "Could not create ASR Colab session." }

Write-Host "Creating TTS T4 session: $TtsSession"
& wsl -d $Distribution -- bash -lc "colab new --gpu T4 --session '$TtsSession'"
if ($LASTEXITCODE -ne 0) { throw "Could not create TTS Colab session." }

Write-Host "Installing Qwen3-ASR and its Tailscale service..."
$asrOutput = (& wsl -d $Distribution -- bash -lc "colab exec --session '$AsrSession' --file '$wslRepo/scripts/colab/bootstrap_qwen_asr.py'" 2>&1 | Tee-Object -Variable asrDisplay) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "ASR bootstrap failed." }

Write-Host "Installing MOSS-TTS-Nano and its Tailscale service..."
$ttsOutput = (& wsl -d $Distribution -- bash -lc "colab exec --session '$TtsSession' --file '$wslRepo/scripts/colab/bootstrap_moss_tts.py'" 2>&1 | Tee-Object -Variable ttsDisplay) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "TTS bootstrap failed." }

function Read-BootstrapResult([string]$Text, [string]$Role) {
    $matches = [regex]::Matches($Text, '(?s)\{\s*"ok"\s*:\s*true.*?\}')
    if ($matches.Count -eq 0) { throw "Could not find the $Role bootstrap result in Colab output." }
    $result = $matches[$matches.Count - 1].Value | ConvertFrom-Json
    if ($result.role -ne $Role -or -not $result.base_url) { throw "Invalid $Role bootstrap result." }
    return $result
}

$asr = Read-BootstrapResult $asrOutput "asr"
$tts = Read-BootstrapResult $ttsOutput "tts"
& python (Join-Path $PSScriptRoot "configure_speech_workers.py") --asr-base-url $asr.base_url --tts-base-url $tts.base_url
if ($LASTEXITCODE -ne 0) { throw "Workers started, but Ciel speech configuration failed." }

Write-Host "Both services are running and connected to Web Chat > Speech Settings."
