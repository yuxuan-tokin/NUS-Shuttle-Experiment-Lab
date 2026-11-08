param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtime = 'C:/Users/User/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (-not (Test-Path -LiteralPath $runtime)) { $runtime = 'python' }
if (Test-Path -LiteralPath 'config.json') {
    & $runtime -m nextbus --config config.json serve --port $Port
} else {
    & $runtime -m nextbus serve --port $Port
}
