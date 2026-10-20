# Processes doing the most I/O over a few seconds (operations and bytes per second; all disks together).
param([int]$Seconds = 5)
$first = @{}
Get-CimInstance Win32_Process | ForEach-Object { $first[$_.ProcessId] = $_ }
Start-Sleep -Seconds $Seconds
Get-CimInstance Win32_Process | ForEach-Object {
    $before = $first[$_.ProcessId]
    if ($before) {
        [pscustomobject]@{
            Id = $_.ProcessId; Name = $_.Name
            ReadOps = [int](($_.ReadOperationCount - $before.ReadOperationCount) / $Seconds)
            WriteOps = [int](($_.WriteOperationCount - $before.WriteOperationCount) / $Seconds)
            OtherOps = [int](($_.OtherOperationCount - $before.OtherOperationCount) / $Seconds)
            ReadMBs = [math]::Round(($_.ReadTransferCount - $before.ReadTransferCount) / $Seconds / 1MB, 1)
            WriteMBs = [math]::Round(($_.WriteTransferCount - $before.WriteTransferCount) / $Seconds / 1MB, 1)
        }
    }
} | Sort-Object { $_.ReadOps + $_.WriteOps + $_.OtherOps } -Descending | Select-Object -First 10 | Format-Table -AutoSize
