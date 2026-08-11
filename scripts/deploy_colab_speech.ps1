param(
    [ValidateSet('Login', 'Status', 'Start', 'Deploy', 'Recreate')]
    [string]$Action = 'Deploy',
    [string]$Profile,
    [switch]$ResetAuthentication,
    [string]$Distribution,
    [string]$ColabAuth,
    [string]$AsrSession,
    [string]$TtsSession,
    [string]$AsrModel,
    [string]$AsrAccelerator,
    [string]$TtsAccelerator,
    [string]$TtsBackend
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$settingsJson = (& python (Join-Path $PSScriptRoot "configure_speech_workers.py") --print-colab-settings) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "Could not read Ciel Colab settings." }
$settings = $settingsJson | ConvertFrom-Json
if ($settings.enabled -eq $false) { throw "Colab worker management is disabled in Web Chat > Speech Settings." }
if ([string]::IsNullOrWhiteSpace($Distribution)) { $Distribution = [string]$settings.distribution }
if ([string]::IsNullOrWhiteSpace($ColabAuth)) { $ColabAuth = [string]$settings.auth }
if ([string]::IsNullOrWhiteSpace($Profile)) { $Profile = [string]$settings.profile }
if ([string]::IsNullOrWhiteSpace($Profile)) { $Profile = "default" }
if ([string]::IsNullOrWhiteSpace($AsrSession)) { $AsrSession = [string]$settings.asr_session }
if ([string]::IsNullOrWhiteSpace($TtsSession)) { $TtsSession = [string]$settings.tts_session }
if ([string]::IsNullOrWhiteSpace($AsrModel)) { $AsrModel = [string]$settings.asr_model }
if ([string]::IsNullOrWhiteSpace($AsrModel)) { $AsrModel = "Qwen/Qwen3-ASR-0.6B" }
if ([string]::IsNullOrWhiteSpace($AsrAccelerator)) { $AsrAccelerator = [string]$settings.asr_accelerator }
if ([string]::IsNullOrWhiteSpace($TtsAccelerator)) { $TtsAccelerator = [string]$settings.tts_accelerator }
if ([string]::IsNullOrWhiteSpace($TtsBackend)) { $TtsBackend = [string]$settings.tts_backend }
if ([string]::IsNullOrWhiteSpace($TtsBackend)) { $TtsBackend = "moss" }
if ($Distribution -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "Invalid WSL distribution name." }
if ($ColabAuth -notin @('adc', 'oauth2')) { throw "ColabAuth must be adc or oauth2." }
if ($Profile -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "Invalid Colab account profile name." }
foreach ($session in @($AsrSession, $TtsSession)) {
    if ($session -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "Invalid Colab session name: $session" }
}
foreach ($accelerator in @($AsrAccelerator, $TtsAccelerator)) {
    if ($accelerator -notin @('T4', 'L4', 'G4', 'A100', 'H100')) { throw "Unsupported Colab accelerator: $accelerator" }
}
if ($TtsBackend -notin @('moss', 'cosyvoice3')) { throw "TtsBackend must be moss or cosyvoice3." }
if ($AsrModel -notin @('Qwen/Qwen3-ASR-0.6B', 'Qwen/Qwen3-ASR-1.7B')) { throw "Unsupported Qwen3-ASR model: $AsrModel" }
$wslRepo = (& wsl -d $Distribution -- wslpath -a ($repo -replace '\\', '/')).Trim()
if (-not $wslRepo) { throw "Could not resolve the repository path in WSL." }
$wslHome = (& wsl -d $Distribution -- bash -lc 'printf %s "$HOME"').Trim()
$colabExecutable = (& wsl -d $Distribution -- bash -lc 'command -v colab').Trim()
if (-not $wslHome -or -not $colabExecutable) { throw "Could not locate the WSL home directory or Colab CLI." }
$colabResolved = (& wsl -d $Distribution -- readlink -f $colabExecutable).Trim()
$colabShebang = (& wsl -d $Distribution -- head -n 1 $colabResolved).Trim()
$colabPython = $colabShebang -replace '^#!', ''
if (-not $colabPython.StartsWith('/')) { throw "Could not locate the Colab CLI Python interpreter." }
$profileHome = if ($Profile -eq 'default') { $wslHome } else { "$wslHome/.config/ciel-runtime/colab-profiles/$Profile" }
& wsl -d $Distribution -- mkdir -p $profileHome
if ($LASTEXITCODE -ne 0) { throw "Could not create the Colab account profile directory." }

function Invoke-Colab([string[]]$Arguments) {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & wsl -d $Distribution -- env "HOME=$profileHome" $colabExecutable --auth $ColabAuth @Arguments
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
}

if ($Action -eq 'Login') {
    if ($ResetAuthentication) {
        $tokenPath = "$profileHome/.config/colab-cli/token.json"
        $adcPath = "$profileHome/.config/gcloud/application_default_credentials.json"
        & wsl -d $Distribution -- rm -f $tokenPath $adcPath
        if ($LASTEXITCODE -ne 0) { throw "Could not reset authentication for profile '$Profile'." }
    }
    Write-Host "Authenticating isolated Colab account profile: $Profile ($ColabAuth)"
    if ($ColabAuth -eq 'adc') {
        $scopes = 'openid,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/colaboratory'
        & wsl -d $Distribution -- env "HOME=$profileHome" gcloud auth application-default login --scopes=$scopes
    } else {
        Invoke-Colab @('sessions')
    }
    if ($LASTEXITCODE -ne 0) { throw "Colab authentication failed for profile '$Profile'." }
    Write-Host "Colab profile '$Profile' is authenticated."
    exit 0
}

if ($Action -eq 'Status') {
    Write-Host "Colab sessions for isolated account profile: $Profile"
    Invoke-Colab @('sessions')
    if ($LASTEXITCODE -ne 0) { throw "Could not read Colab sessions for profile '$Profile'. Run the Login action first." }
    exit 0
}
if ($env:TAILSCALE_AUTHKEY) {
    if ($env:TAILSCALE_AUTHKEY -notmatch '^tskey-[A-Za-z0-9_-]+$') { throw "TAILSCALE_AUTHKEY has an unexpected format." }
}
if ($env:CIEL_SPEECH_API_KEY) {
    if ($env:CIEL_SPEECH_API_KEY -match '[\s''"]') { throw "CIEL_SPEECH_API_KEY cannot contain whitespace or quotes for CLI deployment." }
}
$ephemeralSecrets = @{}
foreach ($secretName in @('TAILSCALE_AUTHKEY', 'CIEL_SPEECH_API_KEY', 'CIEL_ASR_TAILSCALE_STATE', 'CIEL_TTS_TAILSCALE_STATE')) {
    $secretValue = [string][Environment]::GetEnvironmentVariable($secretName, 'Process')
    if (-not [string]::IsNullOrWhiteSpace($secretValue)) { $ephemeralSecrets[$secretName] = $secretValue }
    Remove-Item "Env:$secretName" -ErrorAction SilentlyContinue
}

function New-EphemeralBootstrap([string]$SourcePath) {
    if ($ephemeralSecrets.Count -eq 0) {
        return [pscustomobject]@{ WslPath = $SourcePath; LocalPath = $null }
    }
    $sourceWindows = (& wsl -d $Distribution -- wslpath -w $SourcePath).Trim()
    if (-not (Test-Path -LiteralPath $sourceWindows)) { throw "Could not resolve Colab bootstrap source: $SourcePath" }
    $source = Get-Content -LiteralPath $sourceWindows -Raw
    $future = 'from __future__ import annotations'
    if (-not $source.Contains($future)) { throw "Colab bootstrap is missing its future-import marker: $SourcePath" }
    $injected = [System.Collections.Generic.List[string]]::new()
    $injected.Add($future)
    $injected.Add('')
    $injected.Add('import base64 as _ciel_base64')
    $injected.Add('import os as _ciel_os')
    foreach ($secretName in $ephemeralSecrets.Keys) {
        $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$ephemeralSecrets[$secretName]))
        $injected.Add("_ciel_os.environ[`"$secretName`"] = _ciel_base64.b64decode(`"$encoded`").decode(`"utf-8`")")
    }
    $temporary = New-TemporaryFile
    $utf8NoBom = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($temporary.FullName, $source.Replace($future, ($injected -join "`n")), $utf8NoBom)
    $temporaryWsl = (& wsl -d $Distribution -- wslpath -a $temporary.FullName.Replace('\', '/')).Trim()
    return [pscustomobject]@{ WslPath = $temporaryWsl; LocalPath = $temporary.FullName }
}

Write-Host "Checking Colab CLI authentication..."
Invoke-Colab @('sessions') | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Colab profile '$Profile' is not authenticated with '$ColabAuth' in WSL '$Distribution'. Run -Action Login -Profile '$Profile' first."
}

function Ensure-ColabSession([string]$Session, [string]$Accelerator, [string]$Role) {
    $statusOutput = (Invoke-Colab @('status', '--session', $Session) 2>&1) -join "`n"
    $statusExit = $LASTEXITCODE
    $sessionMissing = $statusOutput -match '(?i)(session\s+.+\s+not found|no active sessions found)'
    if ($statusExit -eq 0 -and -not $sessionMissing) {
        Write-Host "Reusing $Role $Accelerator session: $Session"
        return
    }
    Write-Host "Creating $Role $Accelerator session: $Session"
    Invoke-Colab @('new', '--gpu', $Accelerator, '--session', $Session)
    if ($LASTEXITCODE -ne 0) { throw "Could not create $Role Colab session." }
}

function Get-ColabSessionEndpoint([string]$Session) {
    $statusOutput = (Invoke-Colab @('status', '--session', $Session) 2>&1) -join "`n"
    $endpointMatch = [regex]::Match($statusOutput, '(?m)^\[[^\]]+\]\s+([A-Za-z0-9._-]+)\s+\|')
    if ($endpointMatch.Success) { return $endpointMatch.Groups[1].Value }
    return ''
}

function Release-ColabEndpoint([string]$Endpoint, [string]$Role) {
    if ([string]::IsNullOrWhiteSpace($Endpoint) -or $Endpoint -notmatch '^[A-Za-z0-9._-]+$') { return }
    Write-Host "Releasing stale server-side $Role assignment: $Endpoint"
    $releaseCode = 'import sys; from colab_cli.auth import AuthProvider; from colab_cli.common import state; state.auth_provider=AuthProvider(sys.argv[2]); state.client.unassign(sys.argv[1])'
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & wsl -d $Distribution -- env "HOME=$profileHome" $colabPython -c $releaseCode $Endpoint $ColabAuth
    } finally {
        $releaseExit = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorAction
    }
    if ($releaseExit -ne 0) { throw "Could not release stale server-side $Role assignment '$Endpoint'." }
}

function Stop-ColabSession([string]$Session, [string]$Role) {
    Write-Host "Releasing existing $Role session if present: $Session"
    Invoke-Colab @('stop', '--session', $Session) *> $null
}

if ($Action -eq 'Recreate') {
    Stop-ColabSession $AsrSession "ASR"
    Stop-ColabSession $TtsSession "TTS"
}

Ensure-ColabSession $AsrSession $AsrAccelerator "ASR"
Ensure-ColabSession $TtsSession $TtsAccelerator "TTS"
$asrKnownEndpoint = Get-ColabSessionEndpoint $AsrSession
$ttsKnownEndpoint = Get-ColabSessionEndpoint $TtsSession

if ($Action -eq 'Start') {
    Write-Host "Colab sessions are allocated for profile '$Profile'. Run -Action Deploy to install and connect the workers."
    exit 0
}

Write-Host "Installing Qwen3-ASR and its Tailscale service..."
$asrArguments = @('exec', '--session', $AsrSession, '--timeout', '1800')
$asrArguments += @('--env', "CIEL_ASR_MODEL=$AsrModel")
$asrBootstrap = New-EphemeralBootstrap "$wslRepo/scripts/colab/bootstrap_qwen_asr.py"
$asrArguments += @('--file', $asrBootstrap.WslPath)
try {
    $asrOutput = (Invoke-Colab $asrArguments 2>&1 | Tee-Object -Variable asrDisplay) -join "`n"
    $asrExitCode = $LASTEXITCODE
    if ($asrOutput -match '(?i)(appears to be lost|session\s+.+\s+not found|404/401)') {
        Write-Host "ASR session became stale; creating a replacement and retrying once."
        Release-ColabEndpoint $asrKnownEndpoint "ASR"
        Ensure-ColabSession $AsrSession $AsrAccelerator "ASR"
        $asrOutput = (Invoke-Colab $asrArguments 2>&1 | Tee-Object -Variable asrDisplay) -join "`n"
        $asrExitCode = $LASTEXITCODE
    }
} finally {
    if ($asrBootstrap.LocalPath) { Remove-Item -LiteralPath $asrBootstrap.LocalPath -Force -ErrorAction SilentlyContinue }
}
Write-Host $asrOutput
$asrFailed = $asrExitCode -ne 0 -or $asrOutput -match '(?i)(Traceback \(most recent call last\)|RuntimeError|SyntaxError)'
if ($asrFailed) {
    Write-Host $asrOutput
    throw "ASR bootstrap failed."
}

Write-Host "Installing $TtsBackend and its Tailscale service..."
$ttsArguments = @('exec', '--session', $TtsSession, '--timeout', '1800')
$ttsBootstrap = if ($TtsBackend -eq 'cosyvoice3') { 'bootstrap_cosyvoice3.py' } else { 'bootstrap_moss_tts.py' }
$ttsBootstrapFile = New-EphemeralBootstrap "$wslRepo/scripts/colab/$ttsBootstrap"
$ephemeralSecrets.Clear()
$ttsArguments += @('--file', $ttsBootstrapFile.WslPath)
try {
    $ttsOutput = (Invoke-Colab $ttsArguments 2>&1 | Tee-Object -Variable ttsDisplay) -join "`n"
    $ttsExitCode = $LASTEXITCODE
    if ($ttsOutput -match '(?i)(appears to be lost|session\s+.+\s+not found|404/401)') {
        Write-Host "TTS session became stale; creating a replacement and retrying once."
        Release-ColabEndpoint $ttsKnownEndpoint "TTS"
        Ensure-ColabSession $TtsSession $TtsAccelerator "TTS"
        $ttsOutput = (Invoke-Colab $ttsArguments 2>&1 | Tee-Object -Variable ttsDisplay) -join "`n"
        $ttsExitCode = $LASTEXITCODE
    }
} finally {
    if ($ttsBootstrapFile.LocalPath) { Remove-Item -LiteralPath $ttsBootstrapFile.LocalPath -Force -ErrorAction SilentlyContinue }
}
Write-Host $ttsOutput
$ttsFailed = $ttsExitCode -ne 0 -or $ttsOutput -match '(?i)(Traceback \(most recent call last\)|RuntimeError|SyntaxError)'
if ($ttsFailed) {
    Write-Host $ttsOutput
    throw "TTS bootstrap failed."
}

function Read-BootstrapResult([string]$Text, [string]$Role) {
    $matches = [regex]::Matches($Text, '(?s)\{\s*"ok"\s*:\s*true.*?\}')
    if ($matches.Count -eq 0) { throw "Could not find the $Role bootstrap result in Colab output." }
    $result = $matches[$matches.Count - 1].Value | ConvertFrom-Json
    if ($result.role -ne $Role -or -not $result.base_url) { throw "Invalid $Role bootstrap result." }
    return $result
}

function Save-ColabTailscaleState([string]$Session, [string]$Role, [string]$RemotePath) {
    $temporary = New-TemporaryFile
    try {
        $temporaryWsl = (& wsl -d $Distribution -- wslpath -a $temporary.FullName.Replace('\', '/')).Trim()
        Invoke-Colab @('download', '--session', $Session, $RemotePath, $temporaryWsl) | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $temporary.FullName) -or (Get-Item -LiteralPath $temporary.FullName).Length -eq 0) {
            throw "Could not download the $Role Tailscale device state."
        }
        & python (Join-Path $PSScriptRoot 'store_colab_tailscale_state.py') --profile $Profile --role ($Role.ToLowerInvariant()) --input $temporary.FullName
        if ($LASTEXITCODE -ne 0) { throw "Could not encrypt the $Role Tailscale device state." }
    } finally {
        Remove-Item -LiteralPath $temporary.FullName -Force -ErrorAction SilentlyContinue
    }
}

$asr = Read-BootstrapResult $asrOutput "asr"
$tts = Read-BootstrapResult $ttsOutput "tts"
Save-ColabTailscaleState $AsrSession "ASR" "/tmp/ciel-asr-tailscaled.state"
Save-ColabTailscaleState $TtsSession "TTS" "/tmp/ciel-tts-tailscaled.state"
& python (Join-Path $PSScriptRoot "configure_speech_workers.py") --asr-base-url $asr.base_url --tts-base-url $tts.base_url --distribution $Distribution --auth $ColabAuth --profile $Profile --asr-session $AsrSession --tts-session $TtsSession --asr-model $AsrModel --asr-accelerator $AsrAccelerator --tts-accelerator $TtsAccelerator --tts-backend $TtsBackend
if ($LASTEXITCODE -ne 0) { throw "Workers started, but Ciel speech configuration failed." }

Write-Host "Both services are running and connected to Web Chat > Speech Settings."
