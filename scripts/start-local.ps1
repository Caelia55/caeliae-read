[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$expectedWorktreeName = 'caeliae-read'
$actualWorktreeName = Split-Path $projectRoot -Leaf
$port = 8765
$baseUrl = "http://127.0.0.1:$port"
$dataRoot = Join-Path $projectRoot 'data'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$reader = Join-Path $projectRoot 'web\dist\index.html'

if ($actualWorktreeName -ne $expectedWorktreeName) {
    throw "Main launcher must run from '$expectedWorktreeName', got '$actualWorktreeName'."
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Python environment is missing. Run the README setup commands first.'
}
if (-not (Test-Path -LiteralPath $reader -PathType Leaf)) {
    throw 'Reader build is missing. Run pnpm build in web/ first.'
}
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null

function Get-ListenerProcesses {
    $connections = @()
    try {
        $connections = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop)
    } catch { }
    if ($connections.Count -eq 0) {
        $connections = @(netstat -ano -p tcp | Select-String "\s(?:127\.0\.0\.1|0\.0\.0\.0):$port\s+\S+\s+LISTENING\s+(\d+)\s*$" | ForEach-Object {
            [pscustomobject]@{ OwningProcess = [int]$_.Matches[0].Groups[1].Value }
        })
    }
    $seen = @{}
    foreach ($connection in $connections) {
        $processId = [int]$connection.OwningProcess
        if ($seen.ContainsKey($processId)) { continue }
        $seen[$processId] = $true
        $process = $null
        try {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction Stop
        } catch { }
        [pscustomobject]@{
            Pid = $processId
            CommandLine = if ($process) { [string]$process.CommandLine } else { '<process metadata unavailable>' }
            ExecutablePath = if ($process) { [string]$process.ExecutablePath } else { '' }
        }
    }
}

function Test-CorrectMainApi($listener) {
    if (-not $listener -or $listener.CommandLine -eq '<process metadata unavailable>') { return $false }
    $command = $listener.CommandLine
    return $command -like '*caeliae_read.api.app:app*' `
        -and $command -like "*--port $port*" `
        -and $command -like "*$projectRoot*"
}

function Get-MainStartupProcesses {
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.CommandLine -and $_.CommandLine -like '*caeliae_read.api.app:app*' -and
            $_.CommandLine -like "*--port $port*" -and $_.CommandLine -like "*$projectRoot*"
        })
        foreach ($process in $processes) {
            [pscustomobject]@{
                Pid = [int]$process.ProcessId
                CommandLine = [string]$process.CommandLine
                ExecutablePath = [string]$process.ExecutablePath
            }
        }
    } catch {
        @()
    }
}

$listeners = @(Get-ListenerProcesses)
$startupProcesses = @()
$launchedProcess = $null
if ($listeners.Count -gt 0) {
    Write-Host "Port $port is already listening. Preflight listener PID and complete command line:"
    foreach ($listener in $listeners) {
        Write-Host "PID $($listener.Pid): $($listener.CommandLine)"
    }
    $correct = @($listeners | Where-Object { Test-CorrectMainApi $_ })
    if ($correct.Count -eq $listeners.Count) {
        Write-Host "Reusing the existing main API listener on $baseUrl. No duplicate process will be started."
    } else {
        throw "Port $port is occupied by another or unverifiable process. No process was terminated."
    }
} elseif (($startupProcesses = @(Get-MainStartupProcesses)).Count -gt 0) {
    Write-Host "No listener is bound yet, but an existing main API startup process was found. Complete command line:"
    foreach ($processInfo in $startupProcesses) {
        Write-Host "PID $($processInfo.Pid): $($processInfo.CommandLine)"
    }
    Write-Host "Waiting for that main API instead of starting a duplicate."
} else {
    Write-Host "Port $port is free. Starting main API from $projectRoot."
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $python
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $quotedRoot = [char]34 + $projectRoot.Replace([string][char]34, '\"') + [char]34
    $startInfo.Arguments = "-m uvicorn caeliae_read.api.app:app --host 127.0.0.1 --port $port --app-dir $quotedRoot"
    $startInfo.EnvironmentVariables['CAELIAE_READ_DATA_ROOT'] = $dataRoot
    $startInfo.EnvironmentVariables['PYTHONPATH'] = Join-Path $projectRoot 'src'
    $launchedProcess = [System.Diagnostics.Process]::new()
    $launchedProcess.StartInfo = $startInfo
    if (-not $launchedProcess.Start()) { throw 'Failed to start the main API.' }
    Write-Host "Started main API process PID $($launchedProcess.Id) with data root $dataRoot."
}

 $ready = $false
for ($attempt = 1; $attempt -le 60; $attempt += 1) {
    try {
        $response = Invoke-WebRequest -Uri "$baseUrl/readyz" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 250
}
if (-not $ready) {
    throw "Main API did not become ready at $baseUrl/readyz."
}
$finalListeners = @(Get-ListenerProcesses)
if ($finalListeners.Count -eq 0 -or @($finalListeners | Where-Object { -not (Test-CorrectMainApi $_) }).Count -gt 0) {
    throw "Port $port did not resolve to a verified main API listener. No process was terminated."
}
if ($launchedProcess -and $launchedProcess.HasExited -and $finalListeners.Count -eq 0) {
    throw 'The main API process exited before a listener could be verified.'
}

Write-Host "Main API ready at $baseUrl/readyz. Opening Reader."
try {
    Start-Process "$baseUrl/reader/"
} catch {
    Write-Warning "Reader URL is ready but could not be opened automatically in this environment: $baseUrl/reader/"
}
