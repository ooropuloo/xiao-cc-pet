@echo off
rem Start everything the round display needs, in the background (no windows):
rem   1. ccpet server on 3378 (Claude Code states for page 4; no desktop pet)
rem   2. host\player.py (YouTube player page, Claude page, AskUserQuestion bridge on 127.0.0.1:5810)
rem Already running pieces are left alone. Logs: host\player.log, host\player.err.log, logs\ccpet-server.log
rem ccpet (https://github.com/ooropuloo/ccpet) is looked for in XIAO_CCPET, default D:\git\ccpet.
setlocal
set ROOT=%~dp0
set UV=%USERPROFILE%\.local\bin\uv.exe
if "%XIAO_CCPET%"=="" set XIAO_CCPET=D:\git\ccpet
if not exist "%ROOT%logs" mkdir "%ROOT%logs"

netstat -ano | findstr /r /c:":3378 .*LISTENING" >nul
if not errorlevel 1 (
  echo ccpet server already running on 3378
) else if not exist "%XIAO_CCPET%\server.js" (
  echo ccpet not found at %XIAO_CCPET% - the Claude page stays idle. Clone it there or set XIAO_CCPET, see README.
) else (
  echo starting ccpet server on 3378 ...
  wscript "%ROOT%scripts\run-hidden.vbs" "cmd /c cd /d ""%XIAO_CCPET%"" && node server.js >> ""%ROOT%logs\ccpet-server.log"" 2>&1"
)

netstat -ano | findstr /r /c:"127.0.0.1:5810 .*LISTENING" >nul
if errorlevel 1 (
  echo starting player ...
  wscript "%ROOT%scripts\run-hidden.vbs" "cmd /c ""%ROOT%scripts\run-player.bat"""
) else (
  echo player already running
)

echo.
echo done. The board connects within a few seconds (it must be on one of its saved Wi-Fi networks).
echo stop with stop-xiao.bat
"%SystemRoot%\System32\timeout.exe" /t 4 >nul
