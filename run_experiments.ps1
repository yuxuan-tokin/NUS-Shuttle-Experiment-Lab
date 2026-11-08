param([ValidateSet('examples','baselines','evaluate')][string]$Task = 'baselines')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtime = 'C:/Users/User/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (-not (Test-Path -LiteralPath $runtime)) { $runtime = 'python' }
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
& $runtime -m nextbus.protocol $Task
exit $LASTEXITCODE
