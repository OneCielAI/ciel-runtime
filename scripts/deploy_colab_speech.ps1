param(
    [ValidateSet('Login', 'Status', 'Start', 'Deploy', 'Recreate')]
    [string]$Action = 'Deploy',
    [string]$Profile,
    [switch]$ResetAuthentication,
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
if ([string]::IsNullOrWhiteSpace($Profile)) { $Profile = [string]$settings.profile }
if ([string]::IsNullOrWhiteSpace($Profile)) { $Profile = "default" }
if ([string]::IsNullOrWhiteSpace($AsrSession)) { $AsrSession = [string]$settings.asr_session }
if ([string]::IsNullOrWhiteSpace($TtsSession)) { $TtsSession = [string]$settings.tts_session }
if ([string]::IsNullOrWhiteSpace($AsrAccelerator)) { $AsrAccelerator = [string]$settings.asr_accelerator }
if ([string]::IsNullOrWhiteSpace($TtsAccelerator)) { $TtsAccelerator = [string]$settings.tts_accelerator }
if ($Distribution -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "Invalid WSL distribution name." }
if ($ColabAuth -notin @('adc', 'oauth2')) { throw "ColabAuth must be adc or oauth2." }
if ($Profile -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "Invalid Colab account profile name." }
foreach ($session in @($AsrSession, $TtsSession)) {
    if ($session -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { throw "Invalid Colab session name: $session" }
}
foreach ($accelerator in @($AsrAccelerator, $TtsAccelerator)) {
    if ($accelerator -notin @('T4', 'L4', 'G4', 'A100', 'H100')) { throw "Unsupported Colab accelerator: $accelerator" }
}
$wslRepo = (& wsl -d $Distribution -- wslpath -a ($repo -replace '\\', '/')).Trim()
if (-not $wslRepo) { throw "Could not resolve the repository path in WSL." }
$wslHome = (& wsl -d $Distribution -- bash -lc 'printf %s "$HOME"').Trim()
$colabExecutable = (& wsl -d $Distribution -- bash -lc 'command -v colab').Trim()
if (-not $wslHome -or -not $colabExecutable) { throw "Could not locate the WSL home directory or Colab CLI." }
$profileHome = if ($Profile -eq 'default') { $wslHome } else { "$wslHome/.config/ciel-runtime/colab-profiles/$Profile" }
& wsl -d $Distribution -- mkdir -p $profileHome
if ($LASTEXITCODE -ne 0) { throw "Could not create the Colab account profile directory." }

function Invoke-Colab([string[]]$Arguments) {
    & wsl -d $Distribution -- env "HOME=$profileHome" $colabExecutable --auth $ColabAuth @Arguments
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

Write-Host "Checking Colab CLI authentication..."
Invoke-Colab @('sessions') | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Colab profile '$Profile' is not authenticated with '$ColabAuth' in WSL '$Distribution'. Run -Action Login -Profile '$Profile' first."
}

function Ensure-ColabSession([string]$Session, [string]$Accelerator, [string]$Role) {
    Invoke-Colab @('status', '--session', $Session) *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Reusing $Role $Accelerator session: $Session"
        return
    }
    Write-Host "Creating $Role $Accelerator session: $Session"
    Invoke-Colab @('new', '--gpu', $Accelerator, '--session', $Session)
    if ($LASTEXITCODE -ne 0) { throw "Could not create $Role Colab session." }
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

if ($Action -eq 'Start') {
    Write-Host "Colab sessions are allocated for profile '$Profile'. Run -Action Deploy to install and connect the workers."
    exit 0
}

Write-Host "Installing Qwen3-ASR and its Tailscale service..."
$asrArguments = @('exec', '--session', $AsrSession)
if ($env:TAILSCALE_AUTHKEY) { $asrArguments += @('--env', "TAILSCALE_AUTHKEY=$($env:TAILSCALE_AUTHKEY)") }
if ($env:CIEL_SPEECH_API_KEY) { $asrArguments += @('--env', "CIEL_SPEECH_API_KEY=$($env:CIEL_SPEECH_API_KEY)") }
$asrArguments += @('--file', "$wslRepo/scripts/colab/bootstrap_qwen_asr.py")
$asrOutput = (Invoke-Colab $asrArguments 2>&1 | Tee-Object -Variable asrDisplay) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "ASR bootstrap failed." }

Write-Host "Installing MOSS-TTS-Nano and its Tailscale service..."
$ttsArguments = @('exec', '--session', $TtsSession)
if ($env:TAILSCALE_AUTHKEY) { $ttsArguments += @('--env', "TAILSCALE_AUTHKEY=$($env:TAILSCALE_AUTHKEY)") }
if ($env:CIEL_SPEECH_API_KEY) { $ttsArguments += @('--env', "CIEL_SPEECH_API_KEY=$($env:CIEL_SPEECH_API_KEY)") }
$ttsArguments += @('--file', "$wslRepo/scripts/colab/bootstrap_moss_tts.py")
$ttsOutput = (Invoke-Colab $ttsArguments 2>&1 | Tee-Object -Variable ttsDisplay) -join "`n"
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
& python (Join-Path $PSScriptRoot "configure_speech_workers.py") --asr-base-url $asr.base_url --tts-base-url $tts.base_url --distribution $Distribution --auth $ColabAuth --profile $Profile --asr-session $AsrSession --tts-session $TtsSession --asr-accelerator $AsrAccelerator --tts-accelerator $TtsAccelerator
if ($LASTEXITCODE -ne 0) { throw "Workers started, but Ciel speech configuration failed." }

Write-Host "Both services are running and connected to Web Chat > Speech Settings."
