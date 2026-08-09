param(
    [string]$Distribution,
    [string]$ColabAuth,
    [string]$AsrSession,
    [string]$TtsSession,
    [string]$AsrAccelerator,
    [string]$TtsAccelerator
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$settingsJson = (& python (Join-Path $PSScriptRoot "configure_speech_workers.py") --print-colab-settings) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "Could not read Ciel Colab settings." }
$settings = $settingsJson | ConvertFrom-Json
if ($settings.enabled -eq $false) { throw "Colab worker management is disabled in Web Chat > Speech Settings." }
if ([string]::IsNullOrWhiteSpace($Distribution)) { $Distribution = [string]$settings.distribution }
if ([string]::IsNullOrWhiteSpace($ColabAuth)) { $ColabAuth = [string]$settings.auth }
if ([string]::IsNullOrWhiteSpace($AsrSession)) { $AsrSession = [string]$settings.asr_session }
if ([string]::IsNullOrWhiteSpace($TtsSession)) { $TtsSession = [string]$settings.tts_session }
if ([string]::IsNullOrWhiteSpace($AsrAccelerator)) { $AsrAccelerator = [string]$settings.asr_accelerator }
if ([string]::IsNullOrWhiteSpace($TtsAccelerator)) { $TtsAccelerator = [string]$settings.tts_accelerator }
if ($Distribution -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "Invalid WSL distribution name." }
if ($ColabAuth -notin @('adc', 'oauth2')) { throw "ColabAuth must be adc or oauth2." }
foreach ($session in @($AsrSession, $TtsSession)) {
    if ($session -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "Invalid Colab session name: $session" }
}
foreach ($accelerator in @($AsrAccelerator, $TtsAccelerator)) {
    if ($accelerator -notin @('T4', 'L4', 'G4', 'A100', 'H100')) { throw "Unsupported Colab accelerator: $accelerator" }
}
$wslRepo = (& wsl -d $Distribution -- wslpath -a ($repo -replace '\\', '/')).Trim()
if (-not $wslRepo) { throw "Could not resolve the repository path in WSL." }
$bootstrapEnv = ""
if ($env:TAILSCALE_AUTHKEY) {
    if ($env:TAILSCALE_AUTHKEY -notmatch '^tskey-[A-Za-z0-9_-]+$') { throw "TAILSCALE_AUTHKEY has an unexpected format." }
    $bootstrapEnv += " --env TAILSCALE_AUTHKEY=$($env:TAILSCALE_AUTHKEY)"
}
if ($env:CIEL_SPEECH_API_KEY) {
    if ($env:CIEL_SPEECH_API_KEY -match '[\s''"]') { throw "CIEL_SPEECH_API_KEY cannot contain whitespace or quotes for CLI deployment." }
    $bootstrapEnv += " --env CIEL_SPEECH_API_KEY=$($env:CIEL_SPEECH_API_KEY)"
}

Write-Host "Checking Colab CLI authentication..."
& wsl -d $Distribution -- bash -lc "colab --auth $ColabAuth status >/dev/null"
if ($LASTEXITCODE -ne 0) {
    throw "Colab CLI is not authenticated with '$ColabAuth' in WSL '$Distribution'."
}

function Ensure-ColabSession([string]$Session, [string]$Accelerator, [string]$Role) {
    & wsl -d $Distribution -- bash -lc "colab --auth $ColabAuth status --session '$Session' >/dev/null 2>&1"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Reusing $Role $Accelerator session: $Session"
        return
    }
    Write-Host "Creating $Role $Accelerator session: $Session"
    & wsl -d $Distribution -- bash -lc "colab --auth $ColabAuth new --gpu $Accelerator --session '$Session'"
    if ($LASTEXITCODE -ne 0) { throw "Could not create $Role Colab session." }
}

Ensure-ColabSession $AsrSession $AsrAccelerator "ASR"
Ensure-ColabSession $TtsSession $TtsAccelerator "TTS"

Write-Host "Installing Qwen3-ASR and its Tailscale service..."
$asrOutput = (& wsl -d $Distribution -- bash -lc "colab --auth $ColabAuth exec --session '$AsrSession'$bootstrapEnv --file '$wslRepo/scripts/colab/bootstrap_qwen_asr.py'" 2>&1 | Tee-Object -Variable asrDisplay) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "ASR bootstrap failed." }

Write-Host "Installing MOSS-TTS-Nano and its Tailscale service..."
$ttsOutput = (& wsl -d $Distribution -- bash -lc "colab --auth $ColabAuth exec --session '$TtsSession'$bootstrapEnv --file '$wslRepo/scripts/colab/bootstrap_moss_tts.py'" 2>&1 | Tee-Object -Variable ttsDisplay) -join "`n"
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
& python (Join-Path $PSScriptRoot "configure_speech_workers.py") --asr-base-url $asr.base_url --tts-base-url $tts.base_url --distribution $Distribution --auth $ColabAuth --asr-session $AsrSession --tts-session $TtsSession --asr-accelerator $AsrAccelerator --tts-accelerator $TtsAccelerator
if ($LASTEXITCODE -ne 0) { throw "Workers started, but Ciel speech configuration failed." }

Write-Host "Both services are running and connected to Web Chat > Speech Settings."
