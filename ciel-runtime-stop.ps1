$ErrorActionPreference = "Stop"

if ($env:CIEL_RUNTIME_HOME) {
    $script = Join-Path $env:CIEL_RUNTIME_HOME "ciel_runtime.py"
} else {
    $script = Join-Path $HOME ".local\share\ciel-runtime\ciel_runtime.py"
}

if ($env:CIEL_RUNTIME_PYTHON) {
    & $env:CIEL_RUNTIME_PYTHON $script cli stop
    exit $LASTEXITCODE
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    & $py.Source -3 $script cli stop
} else {
    & python $script cli stop
}
exit $LASTEXITCODE
