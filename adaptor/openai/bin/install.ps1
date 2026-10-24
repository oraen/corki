$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv first, then run this script again.'
}
$previous = $env:UV_PROJECT_ENVIRONMENT
try {
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $project '.venv'
    & uv sync --project $project --frozen --no-dev
    if ($LASTEXITCODE -ne 0) { throw 'Gateway installation failed.' }
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $previous
}
Write-Host "Gateway installed in $project\.venv"

$corkiData = $env:CORKI_HOME
if (-not $corkiData) { $corkiData = Join-Path $HOME '.corki' }
& (Join-Path $project '.venv\Scripts\python.exe') -m corki_openai_gateway.control register --home $corkiData
if ($LASTEXITCODE -ne 0) { throw 'Gateway registration failed.' }
