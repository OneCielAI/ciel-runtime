$ErrorActionPreference = "Stop"

$prefix = if ($env:PREFIX) { $env:PREFIX } else { Join-Path $HOME ".local" }
$shareDir = if ($env:CIEL_RUNTIME_HOME) { $env:CIEL_RUNTIME_HOME } else { Join-Path $prefix "share\ciel-runtime" }
$binDir = Join-Path $prefix "bin"

New-Item -ItemType Directory -Force -Path $shareDir, $binDir | Out-Null

Copy-Item -Force "ciel_runtime.py" (Join-Path $shareDir "ciel_runtime.py")
$supportDir = Join-Path $shareDir "ciel_runtime_support"
if (Test-Path $supportDir) {
    Remove-Item -Recurse -Force $supportDir
}
Copy-Item -Recurse -Force "ciel_runtime_support" $supportDir
Copy-Item -Force "ciel-runtime-menu.py" (Join-Path $binDir "ciel-runtime-menu.py")
Copy-Item -Force "ciel-runtime-tool-guard.py" (Join-Path $binDir "ciel-runtime-tool-guard.py")
Copy-Item -Force "ciel-runtime" (Join-Path $binDir "ciel-runtime")
Copy-Item -Force "ciel-runtime.cmd" (Join-Path $binDir "ciel-runtime.cmd")
Copy-Item -Force "ciel-runtime.ps1" (Join-Path $binDir "ciel-runtime.ps1")
Copy-Item -Force "ciel-runtimectl" (Join-Path $binDir "ciel-runtimectl")
Copy-Item -Force "ciel-runtimectl.cmd" (Join-Path $binDir "ciel-runtimectl.cmd")
Copy-Item -Force "ciel-runtimectl.ps1" (Join-Path $binDir "ciel-runtimectl.ps1")
Copy-Item -Force "ciel-runtime-stop" (Join-Path $binDir "ciel-runtime-stop")
Copy-Item -Force "ciel-runtime-stop.cmd" (Join-Path $binDir "ciel-runtime-stop.cmd")
Copy-Item -Force "ciel-runtime-stop.ps1" (Join-Path $binDir "ciel-runtime-stop.ps1")

Write-Host "Installed Ciel Runtime to $shareDir"
Write-Host "Add $binDir to PATH if ciel-runtime is not found."

