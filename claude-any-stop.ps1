$ErrorActionPreference = "Stop"

if ($env:CLAUDE_ANY_HOME) {
    $script = Join-Path $env:CLAUDE_ANY_HOME "claude_any.py"
} else {
    $script = Join-Path $HOME ".local\share\claude-any\claude_any.py"
}

if ($env:CLAUDE_ANY_PYTHON) {
    & $env:CLAUDE_ANY_PYTHON $script cli stop
    exit $LASTEXITCODE
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    & $py.Source -3 $script cli stop
} else {
    & python $script cli stop
}
exit $LASTEXITCODE
