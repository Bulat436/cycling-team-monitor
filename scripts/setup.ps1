[CmdletBinding()]
param([switch]$Dev, [switch]$Prebuilt, [string]$PythonCommand = 'python')

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv/Scripts/python.exe'
Push-Location $root
try {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $baseVersion = & $PythonCommand -c 'import sys; print(sys.version_info[:2] == (3, 13))'
        if ($LASTEXITCODE -ne 0 -or $baseVersion -ne 'True') { throw 'Select Python 3.13 with -PythonCommand.' }
        & $PythonCommand -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }
    $version = & $python -c 'import sys; print(sys.version_info[:2] == (3, 13))'
    if ($LASTEXITCODE -ne 0 -or $version -ne 'True') { throw 'Python 3.13 is required in .venv.' }
    $requirements = if ($Dev) { 'backend/requirements-dev.txt' } else { 'backend/requirements.txt' }
    & $python -m pip install -r $requirements -c backend/constraints.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python dependencies could not be installed.' }
    if ($Prebuilt) {
        foreach ($name in @('index.html','manifest.webmanifest','sw.js')) {
            if (-not (Test-Path -LiteralPath "frontend/dist/$name" -PathType Leaf)) {
                throw 'This folder does not contain the complete prebuilt application.'
            }
        }
    } else {
        $nodeVersion = & node -p 'process.versions.node.split(".")[0]'
        if ($LASTEXITCODE -ne 0 -or $nodeVersion -ne '24') { throw 'Node.js 24 is required.' }
        & npm.cmd --prefix frontend ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
        & npm.cmd --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
    Write-Output 'Setup complete. Run scripts/migrate.ps1 before starting.'
} finally {
    Pop-Location
}
