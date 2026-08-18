$ErrorActionPreference = "Stop"

$registeredHome = [Environment]::GetEnvironmentVariable("CIEL_RUNTIME_HOME", "User")
$runtimeHome = if ($env:CIEL_RUNTIME_HOME_OVERRIDE) {
    $env:CIEL_RUNTIME_HOME_OVERRIDE
} elseif ($registeredHome -and (Test-Path (Join-Path $registeredHome "ciel_runtime.py"))) {
    $registeredHome
} elseif ($env:CIEL_RUNTIME_HOME) {
    $env:CIEL_RUNTIME_HOME
} else {
    Join-Path $HOME ".local\share\ciel-runtime"
}
$script = Join-Path $runtimeHome "ciel_runtime.py"

if ($env:CIEL_RUNTIME_PYTHON) {
    & $env:CIEL_RUNTIME_PYTHON $script cli @args
    exit $LASTEXITCODE
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    & $py.Source -3 $script cli @args
} else {
    & python $script cli @args
}
exit $LASTEXITCODE
