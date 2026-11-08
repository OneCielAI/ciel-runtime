$ErrorActionPreference = 'Stop'
$b = 'C:\Windows\Temp\ciel-obs-probe'
"LongPathsEnabled=" + (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem').LongPathsEnabled
$old = Join-Path $b 'snapshot'
$fix = Join-Path $b 'fixroot'
"old durable: " + (Select-String -Path "$old\ciel_runtime_support\durable_json_file.py" -Pattern 'temporary = ').Line.Trim()
if (-not (Test-Path "$fix\ciel_runtime_support")) {
  New-Item -ItemType Directory -Force $fix | Out-Null
  robocopy "$old\ciel_runtime_support" "$fix\ciel_runtime_support" /E /NFL /NDL /NJH /NJS /NP | Out-Null
}
Copy-Item "$b\durable_json_file.fix.py" "$fix\ciel_runtime_support\durable_json_file.py" -Force
"fix durable: " + (Select-String -Path "$fix\ciel_runtime_support\durable_json_file.py" -Pattern 'temporary = ').Line.Trim()
New-Item -ItemType Directory -Force "$b\maxpath" | Out-Null
"=== OLD (0a881e0 as installed on u42/u41 path shape) ==="
& py -3 -I "$b\maxpath_real_probe.py" $old "$b\maxpath" old 2>&1 | ForEach-Object { "$_" }
"=== FIX ==="
& py -3 -I "$b\maxpath_real_probe.py" $fix "$b\maxpath" fix 2>&1 | ForEach-Object { "$_" }
