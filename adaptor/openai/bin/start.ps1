$ErrorActionPreference = 'Stop'
$python = $env:CORKI_GATEWAY_PYTHON
if (-not $python) { $python = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Run install.ps1 first or set CORKI_GATEWAY_PYTHON.'
}
$corkiData = $env:CORKI_HOME
if (-not $corkiData) { $corkiData = Join-Path $HOME '.corki' }
$gatewayArguments = @('start', '--home', $corkiData)
if ($env:CORKI_GATEWAY_PORT) { $gatewayArguments += @('--port', $env:CORKI_GATEWAY_PORT) }
& $python -m corki_openai_gateway.control @gatewayArguments
if ($LASTEXITCODE -ne 0) { throw 'Gateway startup failed.' }
