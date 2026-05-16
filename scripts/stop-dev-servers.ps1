param(
    [ValidateSet('stop', 'start', 'restart')]
    [string]$Action = 'stop',
    [int]$FrontendPort = 5173,
    [int]$BackendPort = 8001,
    [string]$BindHost = '0.0.0.0',
    [int[]]$ExtraPorts = @()
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$frontendDir = Join-Path $repoRoot 'frontend'
$pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'
$backendLog = Join-Path $repoRoot 'backend\.backend-dev.log'
$backendErrLog = Join-Path $repoRoot 'backend\.backend-dev.err.log'

function Stop-DevServers {
    param(
        [int[]]$Ports
    )

    $killedPids = @()

    foreach ($port in ($Ports | Select-Object -Unique)) {
        $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue

        if (-not $listeners) {
            Write-Host "No process is listening on port $port"
            continue
        }

        $owningPids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique

        foreach ($procId in $owningPids) {
            try {
                $proc = Get-Process -Id $procId -ErrorAction Stop
                Stop-Process -Id $procId -Force -ErrorAction Stop
                Write-Host "Stopped PID $procId ($($proc.ProcessName)) on port $port"
                $killedPids += $procId
            }
            catch {
                Write-Host "Could not stop PID $procId on port ${port}: $($_.Exception.Message)"
            }
        }
    }

    if ($killedPids.Count -eq 0) {
        Write-Host 'No dev server processes were stopped by port.'
    }
    else {
        $uniqueKilled = $killedPids | Select-Object -Unique
        Write-Host "Stopped $($uniqueKilled.Count) process(es): $($uniqueKilled -join ', ')"
    }
}

function Start-DevServers {
    if (-not (Test-Path $pythonExe)) {
        throw "Python executable not found at $pythonExe"
    }
    if (-not (Test-Path $backendDir)) {
        throw "Backend directory not found at $backendDir"
    }
    if (-not (Test-Path $frontendDir)) {
        throw "Frontend directory not found at $frontendDir"
    }

    if (Test-Path $backendLog) { Remove-Item $backendLog -Force -ErrorAction SilentlyContinue }
    if (Test-Path $backendErrLog) { Remove-Item $backendErrLog -Force -ErrorAction SilentlyContinue }

    $backendArgs = "-m uvicorn app.main:app --host $BindHost --port $BackendPort"
    $backendProc = Start-Process -FilePath $pythonExe -ArgumentList $backendArgs -WorkingDirectory $backendDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $backendLog -RedirectStandardError $backendErrLog
    Write-Host "Started backend (PID $($backendProc.Id)) on port $BackendPort"

    $frontendCmd = "$env:VITE_API_BASE_URL='http://localhost:$BackendPort'; npm run dev -- --host $BindHost --port $FrontendPort"
    $frontendProc = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $frontendCmd) -WorkingDirectory $frontendDir -PassThru -WindowStyle Hidden
    Write-Host "Started frontend launcher (PID $($frontendProc.Id)) on port $FrontendPort"

    Start-Sleep -Seconds 3

    $backendListening = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue
    $frontendListening = Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue

    if ($backendListening) {
        Write-Host "Backend is listening on port $BackendPort"
    }
    else {
        Write-Host "Backend is not listening on port $BackendPort. Check $backendErrLog"
    }

    if ($frontendListening) {
        Write-Host "Frontend is listening on port $FrontendPort"
    }
    else {
        Write-Host "Frontend is not listening on port $FrontendPort"
    }
}

$portsToStop = @($FrontendPort, $BackendPort, 5174, 8000, 8002, 8003, 8010) + $ExtraPorts

switch ($Action) {
    'stop' {
        Stop-DevServers -Ports $portsToStop
    }
    'start' {
        Start-DevServers
    }
    'restart' {
        Stop-DevServers -Ports $portsToStop
        Start-Sleep -Seconds 1
        Start-DevServers
    }
}
