param([switch]$Desktop)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'python-runtime.ps1')
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$taskVenv = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$taskSettings = Join-Path $PSScriptRoot 'local-runtime.json'
if (-not (Test-InwoumPython312 $taskVenv) -and (Test-Path -LiteralPath $taskSettings)) {
    try {
        $taskShared = (Get-Content -LiteralPath $taskSettings -Raw -Encoding UTF8 | ConvertFrom-Json).shared_home
        if ($taskShared) {
            $taskSharedPython = Join-Path $taskShared '.venv\Scripts\python.exe'
            if ((Test-InwoumPython312 $taskSharedPython) -and (Test-InwoumDependencies $taskSharedPython)) { $taskVenv = $taskSharedPython }
        }
    } catch { }
}

if (-not (Test-InwoumPython312 $taskVenv) -or -not (Test-InwoumDependencies $taskVenv)) {
    Write-Host 'Preparing the local Python environment. First setup requires internet access.'
    & (Join-Path $PSScriptRoot 'setup.ps1')
}
if (-not (Test-InwoumPython312 $taskVenv) -or -not (Test-InwoumDependencies $taskVenv)) { throw 'The local environment is not ready. Please check the setup error above.' }
if ($Desktop) {
    & $taskVenv -I -c "import importlib.util,importlib.metadata;raise SystemExit(0 if importlib.util.find_spec('webview') and importlib.metadata.version('pywebview')=='6.2.1' else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing the desktop window components (first launch only)...'
        & $taskVenv -m pip install -r (Join-Path $PSScriptRoot 'requirements-desktop.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Desktop setup failed. START-WEB.cmd can still open the browser version.' }
    }
    $taskWindowPython = Join-Path (Split-Path -Parent $taskVenv) 'pythonw.exe'
    $taskDesktopArgs = @('-X','utf8',('"'+(Join-Path $PSScriptRoot 'desktop_app.py')+'"'),'--port','8776')
    $null=Start-Process -FilePath $taskWindowPython -ArgumentList $taskDesktopArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
} else {
    Write-Host 'Starting INWOUM EDIT in your browser...'
    & $taskVenv -X utf8 (Join-Path $PSScriptRoot 'server.py') --port 8776
    if ($LASTEXITCODE -ne 0) { throw 'INWOUM EDIT stopped with an error. See the message above.' }
}
