# Print PID, image name and the ciel code path each live process runs; no full command lines.
$rx = '[A-Za-z]:[^"]*?ciel_runtime\.py|[A-Za-z]:[^"]*?ciel-runtime[^"]*?\.js'
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe' OR Name='pythonw.exe' OR Name='node.exe'" |
  ForEach-Object {
    $m = [regex]::Match([string]$_.CommandLine, $rx)
    if ($m.Success) { "{0,7} {1,-11} {2}" -f $_.ProcessId, $_.Name, $m.Value }
  }
