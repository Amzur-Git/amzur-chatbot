param(
    [int[]]$Ports = @(5173, 5174, 8000, 8001)
)

$killed = @()

foreach ($port in ($Ports | Select-Object -Unique)) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue

    if (-not $listeners) {
        Write-Host "No process is listening on port $port"
        continue
    }

    $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique

    foreach ($pid in $pids) {
        try {
            $process = Get-Process -Id $pid -ErrorAction Stop
            Stop-Process -Id $pid -Force -ErrorAction Stop
            Write-Host "Stopped PID $pid ($($process.ProcessName)) on port $port"
            $killed += $pid
        }
        catch {
            Write-Host "Could not stop PID $pid on port $port: $($_.Exception.Message)"
        }
    }
}

if ($killed.Count -eq 0) {
    Write-Host "No dev server processes were stopped."
}
else {
    $uniqueKilled = $killed | Select-Object -Unique
    Write-Host "Stopped $($uniqueKilled.Count) process(es): $($uniqueKilled -join ', ')"
}
