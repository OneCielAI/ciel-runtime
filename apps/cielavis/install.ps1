[CmdletBinding()]
param(
    [string]$Source = (Join-Path $PSScriptRoot "src-tauri\target\release\cielavis-desktop.exe"),
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Cielavis")
)

$ErrorActionPreference = "Stop"
$resolvedSource = (Resolve-Path -LiteralPath $Source).Path
if ([System.IO.Path]::GetExtension($resolvedSource) -ne ".exe") {
    throw "Cielavis source must be a Windows executable: $resolvedSource"
}

$destinationRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$destination = Join-Path $destinationRoot "cielavis.exe"
$binRoot = Join-Path $HOME ".local\bin"
$launcher = Join-Path $binRoot "cielavis.ps1"

New-Item -ItemType Directory -Force -Path $destinationRoot, $binRoot | Out-Null
Copy-Item -LiteralPath $resolvedSource -Destination $destination -Force

$launcherBody = @'
$ErrorActionPreference = "Stop"
$executable = Join-Path $env:LOCALAPPDATA "Cielavis\cielavis.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Cielavis is not installed at $executable"
}
& $executable @args
exit $LASTEXITCODE
'@
[System.IO.File]::WriteAllText($launcher, $launcherBody, [System.Text.UTF8Encoding]::new($false))

Write-Host "Cielavis installed: $destination"
Write-Host "Launcher installed: $launcher"
