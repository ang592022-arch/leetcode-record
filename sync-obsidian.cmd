@echo off
setlocal
chcp 65001 >nul

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=prepare"
if not "%~1"=="" shift

set "VAULT=C:\Users\1\Documents\Obsidian Vault"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0scripts\obsidian_sync.py" "%ACTION%" --repo "%~dp0." --vault "%VAULT%" %*
) else (
  python "%~dp0scripts\obsidian_sync.py" "%ACTION%" --repo "%~dp0." --vault "%VAULT%" %*
)
exit /b %errorlevel%
