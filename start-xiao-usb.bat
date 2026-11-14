@echo off
rem Run the round display over the USB cable instead of Wi-Fi (e.g. no antenna, or no saved network here).
rem Starts scripts\run-player-usb.ps1 hidden: it finds the board's COM port, resets the board into its
rem firmware and keeps host\player.py running; unplugging and replugging USB reconnects by itself.
rem Also starts the ccpet server on 3378 (Claude Code states for the Claude page) like start-xiao.bat;
rem ccpet is looked for in XIAO_CCPET, default D:\git\ccpet.
rem Stop with stop-xiao.bat. Log: host\player.err.log (and host\player.log), logs\ccpet-server.log
setlocal
set ROOT=%~dp0
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
if not errorlevel 1 (
  echo player already running, stop it first with stop-xiao.bat
  "%SystemRoot%\System32\timeout.exe" /t 4 >nul
  exit /b
)

echo starting player over USB ...
wscript "%ROOT%scripts\run-hidden.vbs" "powershell -NoProfile -ExecutionPolicy Bypass -File ""%ROOT%scripts\run-player-usb.ps1"""
echo.
echo done. The screen shows the player page within about 10 seconds.
echo stop with stop-xiao.bat
"%SystemRoot%\System32\timeout.exe" /t 4 >nul
