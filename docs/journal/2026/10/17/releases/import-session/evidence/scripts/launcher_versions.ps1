# Run both .cmd launchers' `version`, then resolve bare `ciel-runtimectl` under a fresh registry PATH.
# Inherited CIEL_RUNTIME_* is removed; CIEL_RUNTIME_CONFIG_DIR points at an isolated scratch dir.
param([string]$ConfigDir, [string]$NeutralDir)
# cmd.exe searches the current directory before PATH; run from an empty directory.
Set-Location -LiteralPath $NeutralDir
"cwd=" + (Get-Location).Path
Get-ChildItem Env: | Where-Object { $_.Name -like "CIEL_RUNTIME_*" } | ForEach-Object { Remove-Item -LiteralPath ("Env:" + $_.Name) }
$env:CIEL_RUNTIME_CONFIG_DIR = $ConfigDir
$npm = Join-Path $env:APPDATA "npm\ciel-runtimectl.cmd"
$local = Join-Path $HOME ".local\bin\ciel-runtimectl.cmd"
foreach ($launcher in @($npm, $local)) {
    $out = & cmd.exe /d /c "`"$launcher`" version" 2>&1
    "launcher=$launcher exit=$LASTEXITCODE output=$out"
}
$machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
$user = [Environment]::GetEnvironmentVariable("Path", "User")
$env:Path = [Environment]::ExpandEnvironmentVariables("$machine;$user")
$resolved = & cmd.exe /d /c "where ciel-runtimectl" 2>&1
"fresh-PATH where ciel-runtimectl:"
$resolved | ForEach-Object { "  $_" }
$out = & cmd.exe /d /c "ciel-runtimectl version" 2>&1
"fresh-PATH bare 'ciel-runtimectl version' exit=$LASTEXITCODE output=$out"
"isolated config dir entries after run:"
Get-ChildItem -LiteralPath $ConfigDir -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object { "  " + $_.FullName.Substring($ConfigDir.Length) }
