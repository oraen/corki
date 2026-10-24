$ErrorActionPreference = 'Stop'
$python = $env:CORKI_GATEWAY_PYTHON
if (-not $python) { $python = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Gateway Python environment missing; set CORKI_GATEWAY_PYTHON.'
}
$corkiData = $env:CORKI_HOME
if (-not $corkiData) { $corkiData = Join-Path $HOME '.corki' }
& $python -m corki_openai_gateway.control stop --home $corkiData
if ($LASTEXITCODE -ne 0) { throw 'Gateway shutdown failed.' }
