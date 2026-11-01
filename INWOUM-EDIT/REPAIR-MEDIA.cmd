@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo Close running INWOUM EDIT windows before repairing the media reader.
if not exist ".venv\Scripts\python.exe" (
  echo Run START.cmd first to create the local environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -I "%~dp0media_runtime.py" --repair
if errorlevel 1 (
  echo Media repair failed. Keep this window open and send the error to support.
) else (
  echo Media check passed. Close the program, reopen START.cmd, then resume the same job.
)
pause
