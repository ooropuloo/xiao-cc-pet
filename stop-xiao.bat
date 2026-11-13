@echo off
rem Stop the round-display player and the ccpet server.
rem   stop-xiao.bat          stop both
rem   stop-xiao.bat keep     stop the player only, keep ccpet running (e.g. the desktop pet uses it)
if /i "%1"=="keep" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-xiao.ps1" -KeepCcpet
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-xiao.ps1"
)
"%SystemRoot%\System32\timeout.exe" /t 3 >nul
