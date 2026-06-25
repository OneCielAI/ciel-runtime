$ErrorActionPreference = "Stop"

if ($env:CIEL_RUNTIME_HOME) {
    $script = Join-Path $env:CIEL_RUNTIME_HOME "ciel_runtime.py"
} else {
    $script = Join-Path $HOME ".local\share\ciel-runtime\ciel_runtime.py"
}

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
