$ErrorActionPreference = 'Stop'
$managerDir = Split-Path -Parent $PSScriptRoot
$repoDir = Split-Path -Parent $managerDir
$python = $env:CORKI_MANAGER_PYTHON
if (-not $python) {
    $python = Join-Path $repoDir '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Install Corki in the repository .venv or set CORKI_MANAGER_PYTHON to its Python executable.'
}

# Arguments remain separate, including paths with spaces and non-ASCII names.
# UTF-8 mode also applies when launched outside a UTF-8 terminal.
& $python -X utf8 -u (Join-Path $managerDir 'server.py') --workspace $repoDir @args
exit $LASTEXITCODE
