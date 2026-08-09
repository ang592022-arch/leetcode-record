@echo off
setlocal
chcp 65001 >nul

set "VAULT=C:\Users\1\Documents\Obsidian Vault"
where py >nul 2>nul
if %errorlevel%==0 (
  if "%~1"=="" (
    py -3 "%~dp0scripts\obsidian_sync.py" prepare --repo "%~dp0." --vault "%VAULT%"
  ) else (
    py -3 "%~dp0scripts\obsidian_sync.py" %* --repo "%~dp0." --vault "%VAULT%"
  )
) else (
  if "%~1"=="" (
    python "%~dp0scripts\obsidian_sync.py" prepare --repo "%~dp0." --vault "%VAULT%"
  ) else (
    python "%~dp0scripts\obsidian_sync.py" %* --repo "%~dp0." --vault "%VAULT%"
  )
)
exit /b %errorlevel%
