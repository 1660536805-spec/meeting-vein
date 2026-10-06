@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================
rem  AI Meeting Organizer - backend launcher (uvicorn)
rem  Run standalone by double-click, or from start.bat.
rem  Env var AMO_LOGFILE: if set, output goes to that file.
rem  Keep CRLF + ASCII (windows-batch-launcher skill).
rem ============================================================

title AMO-Backend

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "VENV=%ROOT%\.venv\Scripts\python.exe"
set "BACKEND_DIR=%ROOT%\backend"

if not exist "%VENV%" (
    echo [backend] ERROR: python venv not found at:
    echo [backend]   %VENV%
    rem system python here is 3.7 -- too old for fastapi/langgraph, so resolve
    rem the managed interpreter the same way start.bat does
    set "PY_BIN="
    if exist "%USERPROFILE%\.workbuddy\binaries\python\versions" (
        for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
            rem only accept real python dirs (skip .locks and empty 'current')
            if exist "%%d\python.exe" set "PY_BIN=%%d\python.exe"
        )
    )
    if not "!PY_BIN!"=="" (
        echo [backend]   Create it with: "!PY_BIN!" -m venv .venv
    ) else (
        echo [backend]   Create it with: python -m venv .venv
        echo [backend]   NOTE: needs Python 3.10+ for fastapi/langgraph.
    )
    pause
    exit /b 1
)

echo [backend] venv    : %VENV%
echo [backend] workdir : %BACKEND_DIR%
echo [backend] command : uvicorn app.server:app --reload --port 8000
echo [backend] url     : http://localhost:8000

cd /d "%BACKEND_DIR%"
if defined AMO_LOGFILE (
    echo [backend] log     : %AMO_LOGFILE%
    "%VENV%" -m uvicorn app.server:app --reload --port 8000 > "%AMO_LOGFILE%" 2>&1
) else (
    "%VENV%" -m uvicorn app.server:app --reload --port 8000
)
set "RC=%errorlevel%"

echo.
echo [backend] uvicorn exited with code %RC%.
pause
exit /b %RC%
