$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'python-runtime.ps1')
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$taskVenv = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-InwoumPython312 $taskVenv) -or -not (Test-InwoumDependencies $taskVenv)) {
    Write-Host 'Preparing the local Python environment. First setup requires internet access.'
    & (Join-Path $PSScriptRoot 'setup.ps1')
}
if (-not (Test-InwoumPython312 $taskVenv) -or -not (Test-InwoumDependencies $taskVenv)) {
    throw 'The local environment is not ready. Please check the setup error above.'
}
Write-Host 'Starting INWOUM EDIT...'
& $taskVenv -X utf8 (Join-Path $PSScriptRoot 'server.py') --port 8776
if ($LASTEXITCODE -ne 0) { throw 'INWOUM EDIT stopped with an error. See the message above.' }
