[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$NoBrowser,
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ($Stop) {
    & (Join-Path $PSScriptRoot 'server.ps1') stop
    return
}

$python = Join-Path $root '.venv/Scripts/python.exe'
$setupMarker = Join-Path $root '.venv/ctm-prebuilt-ready'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
    -not (Test-Path -LiteralPath $setupMarker -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $root '.env') -PathType Leaf)) {
    & (Join-Path $PSScriptRoot 'setup.ps1') -Prebuilt
    & (Join-Path $PSScriptRoot 'migrate.ps1')
    New-Item -ItemType File -Path $setupMarker -Force | Out-Null
}

Push-Location (Join-Path $root 'backend')
try {
    $coachCheck = @'
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import Settings
from app.database import make_engine
from app.models import User

engine = make_engine(Settings())
try:
    with Session(engine) as db:
        print("coach_exists" if db.scalar(select(User.id).where(User.role == "coach").limit(1)) else "no_coach")
finally:
    engine.dispose()
'@
    $coachStatus = & $python -c $coachCheck
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read the team database. See README.md.' }
    if ($coachStatus -eq 'no_coach') {
        & $python -m app.cli bootstrap-coach --email coach@example.com --name 'Test coach'
        if ($LASTEXITCODE -ne 0) { throw 'Coach setup failed. Open Start.cmd again to retry.' }
    }
} finally {
    Pop-Location
}

& (Join-Path $PSScriptRoot 'server.ps1') start -Port $Port
if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:$Port/login"
}
