[CmdletBinding()]
param([switch]$Check)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Run scripts/setup.ps1 first.' }
Push-Location (Join-Path $root 'backend')
try {
    if ($Check) {
        & $python -m alembic current --check-heads
        if ($LASTEXITCODE -eq 0) { & $python -m alembic check }
    } else {
        & $python -m alembic upgrade head
    }
    if ($LASTEXITCODE -ne 0) { throw 'Database migration or schema check failed.' }
} finally {
    Pop-Location
}
