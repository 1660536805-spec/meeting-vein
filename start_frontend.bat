@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================
rem  AI Meeting Organizer - frontend launcher (vite)
rem  Run standalone by double-click, or from start.bat.
rem  Env var AMO_LOGFILE: if set, output goes to that file.
rem  Keep CRLF + ASCII (windows-batch-launcher skill).
rem ============================================================

title AMO-Frontend

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "FRONTEND_DIR=%ROOT%\frontend"

rem ---- locate node/npm: PREFER managed WorkBuddy node 22 over system PATH ----
rem  system Node here is v14 -- too old for vite 5; managed is v22
set "NODE_BIN="
if exist "%USERPROFILE%\.workbuddy\binaries\node\versions" (
    rem alphabetical order; empty 'current' pointer is skipped by the exist check,
    rem so the highest real version dir (e.g. 22.22.2-3) ends up in NODE_BIN
    for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\node\versions\*") do (
        rem only accept real node dirs (skip .locks etc.)
        if exist "%%d\npm.cmd" if exist "%%d\node.exe" set "NODE_BIN=%%d"
    )
)
if not "!NODE_BIN!"=="" (
    set "PATH=!NODE_BIN!;%PATH%"
    echo [frontend] node managed: !NODE_BIN!
) else (
    where npm >nul 2>&1
    if errorlevel 1 (
        echo [frontend] ERROR: npm not found on PATH.
        echo [frontend]        Install Node.js or add it to PATH, then retry.
        pause
        exit /b 1
    )
    echo [frontend] node from system PATH
)

for /f "delims=" %%v in ('node -v 2^>nul') do echo [frontend] node version: %%v
echo [frontend] workdir : %FRONTEND_DIR%
echo [frontend] command : npm run dev -- --strictPort
echo [frontend] url     : http://localhost:5173

cd /d "%FRONTEND_DIR%"
if defined AMO_LOGFILE (
    echo [frontend] log     : %AMO_LOGFILE%
    call npm run dev -- --strictPort > "%AMO_LOGFILE%" 2>&1
) else (
    call npm run dev -- --strictPort
)
set "RC=%errorlevel%"

echo.
echo [frontend] vite exited with code %RC%.
pause
exit /b %RC%
