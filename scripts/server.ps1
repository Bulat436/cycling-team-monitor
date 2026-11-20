[CmdletBinding()]
param(
    [Parameter(Mandatory, Position=0)]
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action,
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$RunDirectory = 'data/run'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv/Scripts/python.exe'
$backend = Join-Path $root 'backend'
$runtime = [IO.Path]::GetFullPath($RunDirectory, $root)
$marker = Join-Path $runtime 'running.json'

function Get-RunInfo {
    if (Test-Path -LiteralPath $marker -PathType Leaf) {
        try { $record = Get-Content -Raw -LiteralPath $marker | ConvertFrom-Json }
        catch {
            if (-not (Test-Path -LiteralPath $marker)) { return }
            throw
        }
        if ($record.run_id -cmatch '^[a-f0-9]{32}$' -and $record.port -ge 1 -and $record.port -le 65535) {
            $serverProcess = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
            if ($serverProcess -and $serverProcess.ProcessName -eq 'python') { return $record }
            return
        }
        throw 'Malformed runtime marker.'
    }
}
function Get-Health([int]$HealthPort) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$HealthPort/api/v1/health" -TimeoutSec 2 -NoProxy
        return $health.status -eq 'ok'
    } catch {
        return $false
    }
}

switch ($Action) {
    'start' {
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Run scripts/setup.ps1 first.' }
        $existing = Get-RunInfo
        if ($existing -and (Get-Health $existing.port)) { throw 'The server is already running.' }
        [void][IO.Directory]::CreateDirectory($runtime)
        $runId = [Guid]::NewGuid().ToString('N')
        $log = Join-Path $runtime "$runId.log"
        $arguments = @('-m', 'app.windows_server', ('"' + $runtime + '"'), $runId, $Port)
        $launch = @{
            FilePath = $python
            ArgumentList = $arguments
            WorkingDirectory = $backend
            WindowStyle = 'Hidden'
            RedirectStandardError = $log
            RedirectStandardOutput = "$log.out"
            PassThru = $true
        }
        $process = Start-Process @launch
        $deadline = [DateTime]::UtcNow.AddSeconds(25)
        do {
            $record = Get-RunInfo
            if ($record -and $record.run_id -eq $runId -and (Get-Health $record.port)) {
                Write-Output "Running at http://127.0.0.1:$Port. Log $log"
                return
            }
            Start-Sleep -Milliseconds 250
        } while (-not $process.HasExited -and [DateTime]::UtcNow -lt $deadline)
        if (-not $process.HasExited) {
            [IO.File]::WriteAllText((Join-Path $runtime "$runId.stop"), '')
        }
        throw "Startup check failed. Inspect $log and run scripts/migrate.ps1 if necessary."
    }
    'stop' {
        $record = Get-RunInfo
        if (-not $record) { Write-Output 'Stopped.'; return }
        [IO.File]::WriteAllText((Join-Path $runtime "$($record.run_id).stop"), '')
        $deadline = [DateTime]::UtcNow.AddSeconds(85)
        do {
            $current = Get-RunInfo
            if (-not $current -or $current.run_id -ne $record.run_id) {
                Write-Output 'Stopped.'
                return
            }
            Start-Sleep -Milliseconds 250
        } while ([DateTime]::UtcNow -lt $deadline)
        throw 'Graceful stop was not confirmed. Review the run log and process state.'
    }
    'status' {
        $record = Get-RunInfo
        if (-not $record) { Write-Output 'Stopped.'; exit 1 }
        if (-not (Get-Health $record.port)) { throw "Not responding. Check the log in $runtime." }
        Write-Output "Healthy at http://127.0.0.1:$($record.port)."
    }
}
