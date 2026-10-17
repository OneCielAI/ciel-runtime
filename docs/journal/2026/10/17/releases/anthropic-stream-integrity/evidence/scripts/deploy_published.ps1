param([Parameter(Mandatory=$true)][string]$PackageDir, [Parameter(Mandatory=$true)][string]$Version, [Parameter(Mandatory=$true)][string]$Sha)
$ErrorActionPreference = "Stop"
Get-ChildItem Env: | Where-Object { $_.Name -like "CIEL_RUNTIME_*" } | ForEach-Object { Remove-Item -LiteralPath ("Env:" + $_.Name) }
$prefix = (& npm.cmd prefix -g).Trim()
if ($prefix -ne (Join-Path $env:APPDATA "npm")) { throw "Unexpected npm global prefix: $prefix" }
$target = Join-Path $HOME ".local\share\ciel-runtime-$Sha"
if (Test-Path -LiteralPath $target) { throw "Snapshot exists; will not overwrite" }
$env:PREFIX = Join-Path $HOME ".local"
"prefix=$prefix target=$target policy=$(Get-ExecutionPolicy)"
& npm.cmd install -g "@one-ciel-ai/ciel-runtime@$Version"
if ($LASTEXITCODE -ne 0) { throw "npm global install failed" }
$env:CIEL_RUNTIME_INSTALL_HOME = $target
& (Join-Path $PackageDir "install.ps1")
"registered=" + [Environment]::GetEnvironmentVariable("CIEL_RUNTIME_HOME", "User")
