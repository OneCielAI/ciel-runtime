# Read-only: record HKCU registration and replay install.ps1's PATH cleanup to predict whether it writes PATH.
param([string]$Prefix, [string]$Target)
$key = Get-Item -Path HKCU:\Environment
"HKCU CIEL_RUNTIME_HOME=" + $key.GetValue("CIEL_RUNTIME_HOME")
"HKCU Path kind=" + $key.GetValueKind("Path")
"target snapshot exists=" + (Test-Path -LiteralPath $Target)
$binDir = Join-Path $Prefix "bin"
$expandedBinDir = [Environment]::ExpandEnvironmentVariables($binDir).TrimEnd('\')
$expandedTempDir = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$clean = [System.Collections.Generic.List[string]]::new()
[void]$seen.Add($expandedBinDir)
$clean.Add($binDir.TrimEnd('\'))
foreach ($entry in ([string]$userPath -split ';')) {
    $value = $entry.Trim()
    if (-not $value) { continue }
    $identity = [Environment]::ExpandEnvironmentVariables($value).TrimEnd('\')
    $dead = ($identity.StartsWith($expandedTempDir, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $identity) -eq "bin" -and
        (Split-Path -Leaf (Split-Path -Parent $identity)) -like "tmp*" -and
        -not (Test-Path -LiteralPath $identity))
    if ($dead) { "would drop dead temp bin: $value"; continue }
    if ($seen.Add($identity)) { $clean.Add($value) } else { "would drop duplicate: $value" }
}
$next = $clean -join ';'
"install.ps1 would write user Path=" + ($next -ne $userPath)
"user Path entries=" + (([string]$userPath -split ';') | Where-Object { $_.Trim() }).Count + " first=" + (([string]$userPath -split ';')[0])
