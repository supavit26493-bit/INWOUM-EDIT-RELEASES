$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'python-runtime.ps1')
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$taskVenv = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskVenv)) {
    $taskPy = Find-InwoumPython312
    Write-Host "Found Python 3.12 (64-bit): $taskPy"
    & $taskPy -X utf8 -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python 3.12 environment.' }
}
if (-not (Test-InwoumPython312 $taskVenv)) { throw 'The existing .venv is invalid or is not Python 3.12 (64-bit). Rename .venv and run START.cmd again; no files were deleted.' }
& $taskVenv -m pip install -r requirements-gpu.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network and Python version.' }
& $taskVenv -I (Join-Path $PSScriptRoot 'media_runtime.py') --check
if ($LASTEXITCODE -ne 0) {
    & $taskVenv -I (Join-Path $PSScriptRoot 'media_runtime.py') --repair
    if ($LASTEXITCODE -ne 0) { throw 'PyAV is not ready. See the diagnostic above or run REPAIR-MEDIA.cmd.' }
}
if (-not (Test-InwoumDependencies $taskVenv)) { throw 'Required modules or tkinter are missing. Check your Python installation and run SETUP.cmd again.' }
Write-Host 'Setup complete. Run START.cmd. Whisper model downloads on first GPU transcription.'
