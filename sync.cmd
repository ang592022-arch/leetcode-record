@echo off
setlocal
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0scripts\leetcode_repo.py" sync %*
) else (
  python "%~dp0scripts\leetcode_repo.py" sync %*
)
exit /b %errorlevel%
