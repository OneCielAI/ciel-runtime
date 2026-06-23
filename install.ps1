$ErrorActionPreference = "Stop"

$prefix = if ($env:PREFIX) { $env:PREFIX } else { Join-Path $HOME ".local" }
$shareDir = if ($env:CLAUDE_ANY_HOME) { $env:CLAUDE_ANY_HOME } else { Join-Path $prefix "share\claude-any" }
$binDir = Join-Path $prefix "bin"

New-Item -ItemType Directory -Force -Path $shareDir, $binDir | Out-Null

Copy-Item -Force "claude_any.py" (Join-Path $shareDir "claude_any.py")
$supportDir = Join-Path $shareDir "claude_any_support"
if (Test-Path $supportDir) {
    Remove-Item -Recurse -Force $supportDir
}
Copy-Item -Recurse -Force "claude_any_support" $supportDir
Copy-Item -Force "claude-any-menu.py" (Join-Path $binDir "claude-any-menu.py")
Copy-Item -Force "claude-any-tool-guard.py" (Join-Path $binDir "claude-any-tool-guard.py")
Copy-Item -Force "claude-any" (Join-Path $binDir "claude-any")
Copy-Item -Force "claude-any.cmd" (Join-Path $binDir "claude-any.cmd")
Copy-Item -Force "claude-any.ps1" (Join-Path $binDir "claude-any.ps1")
Copy-Item -Force "claude-anyctl" (Join-Path $binDir "claude-anyctl")
Copy-Item -Force "claude-anyctl.cmd" (Join-Path $binDir "claude-anyctl.cmd")
Copy-Item -Force "claude-anyctl.ps1" (Join-Path $binDir "claude-anyctl.ps1")
Copy-Item -Force "claude-any-stop" (Join-Path $binDir "claude-any-stop")
Copy-Item -Force "claude-any-stop.cmd" (Join-Path $binDir "claude-any-stop.cmd")
Copy-Item -Force "claude-any-stop.ps1" (Join-Path $binDir "claude-any-stop.ps1")

Write-Host "Installed Claude Any to $shareDir"
Write-Host "Add $binDir to PATH if claude-any is not found."

