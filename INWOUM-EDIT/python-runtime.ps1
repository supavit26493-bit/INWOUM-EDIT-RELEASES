# Shared by START and SETUP; no dependency on the Python Launcher.
function Test-InwoumPython312 {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try {
        $probe = & $Path -I -c "import sys,struct;print('INWOUM_PY312_OK' if sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 else 'INWOUM_WRONG_PYTHON')" 2>$null
        return ($LASTEXITCODE -eq 0 -and $probe -contains 'INWOUM_PY312_OK')
    } catch { return $false }
}

function Get-InwoumPythonCandidates {
    if ($env:INWOUM_PYTHON) { $env:INWOUM_PYTHON }
    # Ask a launcher when present, but continue without one if it fails.
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        try {
            $resolved = & $launcher.Source -3.12 -I -c 'import sys;print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $resolved | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } }
        } catch { }
    }
    foreach ($name in @('python.exe', 'python3.exe', 'python3.12.exe')) {
        foreach ($command in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            # Do not invoke an uninstalled Microsoft Store execution alias.
            if ($command.Source -notmatch '\\Microsoft\\WindowsApps\\') { $command.Source }
        }
    }
    foreach ($key in @('Registry::HKEY_CURRENT_USER\Software\Python\PythonCore', 'Registry::HKEY_LOCAL_MACHINE\Software\Python\PythonCore', 'Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Python\PythonCore')) {
        foreach ($version in @(Get-ChildItem -LiteralPath $key -ErrorAction SilentlyContinue)) {
            if ($version.PSChildName -notlike '3.12*') { continue }
            $installKey = Join-Path $version.PSPath 'InstallPath'
            $install = Get-Item -LiteralPath $installKey -ErrorAction SilentlyContinue
            if ($install) {
                $executable = $install.GetValue('ExecutablePath')
                if ($executable) { $executable }
                $folder = $install.GetValue('')
                if ($folder) { Join-Path $folder 'python.exe' }
            }
        }
    }
    foreach ($folder in @("$env:LOCALAPPDATA\Programs\Python\Python312", "$env:ProgramFiles\Python312", "$env:ProgramFiles\Python 3.12", "$env:SystemDrive\Python312")) {
        Join-Path $folder 'python.exe'
    }
}

function Find-InwoumPython312 {
    $seen = @{}
    foreach ($candidate in @(Get-InwoumPythonCandidates)) {
        if (-not $candidate) { continue }
        $candidate = ([string]$candidate).Trim().Trim('"')
        if ($seen.ContainsKey($candidate)) { continue }
        $seen[$candidate] = $true
        if (Test-InwoumPython312 $candidate) { return $candidate }
    }
    throw 'Python 3.12 (64-bit) was not found. Install it, reopen START.cmd, or set INWOUM_PYTHON to your Python 3.12 python.exe. Python Launcher is optional.'
}

function Test-InwoumDependencies {
    param([string]$Path)
    try {
        $probe = & $Path -I -c "import importlib.util;mods=('numpy','av','requests','faster_whisper','ctranslate2','pythainlp','tkinter');print('INWOUM_DEPS_OK' if all(importlib.util.find_spec(m) is not None for m in mods) else 'INWOUM_DEPS_MISSING')" 2>$null
        if (-not ($LASTEXITCODE -eq 0 -and $probe -contains 'INWOUM_DEPS_OK')) { return $false }
        & $Path -I (Join-Path $PSScriptRoot 'media_runtime.py') --check *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
