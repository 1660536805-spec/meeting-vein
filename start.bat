@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================
rem  AI Meeting Organizer - one-click launcher (debug-friendly)
rem  Backend: uvicorn via local .venv. Frontend: vite via npm.
rem  Features:
rem    - pre-check ports 8000/5173, offer to free them
rem    - wait until services really listen, print READY/FAIL
rem    - fixed dead node-fallback branch (delayed expansion)
rem  Logs: logs\backend.log / logs\frontend.log
rem  Press any key in THIS window to stop everything.
rem  Keep CRLF + ASCII (windows-batch-launcher skill).
rem ============================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "VENV=%ROOT%\.venv\Scripts\python.exe"
set "BACKEND_DIR=%ROOT%\backend"
set "FRONTEND_DIR=%ROOT%\frontend"
set "LOG_DIR=%ROOT%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

if not exist "%VENV%" (
    echo [start] ERROR: python venv not found at:
    echo [start]   %VENV%
    echo [start]   Create it with: python -m venv .venv
    pause
    exit /b 1
)

rem ---- locate node/npm (use managed WorkBuddy node if not on PATH) ----
where npm >nul 2>&1
if errorlevel 1 (
    set "NODE_BIN="
    if exist "%USERPROFILE%\.workbuddy\binaries\node\versions" (
        for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\node\versions\*") do (
            rem only accept real node dirs (skip .locks etc.)
            if exist "%%d\npm.cmd" if exist "%%d\node.exe" set "NODE_BIN=%%d"
        )
    )
    if not "!NODE_BIN!"=="" (
        set "PATH=!NODE_BIN!;%PATH%"
        echo [start] node fallback: !NODE_BIN!
    ) else (
        echo [start] WARNING: npm not found on PATH; frontend will NOT start.
        echo [start]          Install Node.js or add it to PATH, then retry.
    )
)

rem ---- pre-check ports; offer to free them ----
call :check_port 8000 "backend uvicorn"
if errorlevel 1 ( echo [start] Aborted by user. & pause & exit /b 1 )
call :check_port 5173 "frontend vite"
if errorlevel 1 ( echo [start] Aborted by user. & pause & exit /b 1 )

echo [start] Backend : "%VENV%" -m uvicorn app.server:app --reload --port 8000
echo [start] Frontend: npm run dev (vite, strict port 5173)
echo [start] Logs    : %LOG_DIR%
echo [start] Starting servers...

start "AMO-Backend" /D "%BACKEND_DIR%" cmd /c ""%VENV%" -m uvicorn app.server:app --reload --port 8000 > "%LOG_DIR%\backend.log" 2>&1"
start "AMO-Frontend" /D "%FRONTEND_DIR%" cmd /c "npm run dev -- --strictPort > "%LOG_DIR%\frontend.log" 2>&1"

rem ---- readiness poll (up to ~20s, ping-based sleep works everywhere) ----
echo [start] Waiting for services to listen...
set /a TRIES=0
:wait_loop
set /a TRIES+=1
call :port_listening 8000
if errorlevel 1 goto :sleep_step
call :port_listening 5173
if errorlevel 1 goto :sleep_step
goto :wait_report
:sleep_step
if !TRIES! geq 20 goto :wait_report
ping -n 2 127.0.0.1 >nul
goto :wait_loop
:wait_report
call :port_listening 8000 && (echo [start] Backend  READY on :8000) || (echo [start] Backend  NOT UP -- see %LOG_DIR%\backend.log)
call :port_listening 5173 && (echo [start] Frontend READY on :5173) || (echo [start] Frontend NOT UP -- see %LOG_DIR%\frontend.log)

echo.
echo [start] Backend  -^> http://localhost:8000
echo [start] Frontend -^> http://localhost:5173
echo [start] Press any key here to STOP all servers.
echo.

pause >nul

echo [start] Stopping servers...
taskkill /F /FI "WINDOWTITLE eq AMO-Backend" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq AMO-Frontend" /T >nul 2>&1
call :kill_port 8000
call :kill_port 5173
echo [start] Done.

endlocal
exit /b 0

rem ---------------- subroutines ----------------

:check_port
rem %1=port %2=label ; rc 1 = user declined to free the port
set "HOLD_PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%1 .*LISTENING"') do set "HOLD_PID=%%p"
if not defined HOLD_PID (
    echo [start] port %1 free.
    exit /b 0
)
echo [start] WARNING: port %1 in use by PID !HOLD_PID! (%~2).
choice /c YN /n /m "[start] Kill PID !HOLD_PID! and continue? [Y/N] "
if errorlevel 2 exit /b 1
taskkill /F /PID !HOLD_PID! >nul 2>&1
timeout /t 1 /nobreak >nul 2>&1
echo [start] port %1 freed.
exit /b 0

:port_listening
netstat -ano | findstr /r /c:":%1 .*LISTENING" >nul 2>&1
exit /b %errorlevel%

:kill_port
rem fallback: kill whatever still LISTENs on %1 (npm/vite may survive title-based kill)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%1 .*LISTENING"') do taskkill /F /PID %%p >nul 2>&1
exit /b 0
