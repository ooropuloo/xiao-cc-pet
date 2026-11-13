@echo off
rem Keep host\player.py running: if it exits or crashes, start it again after 3 s.
rem Started hidden by start-xiao.bat; stop-xiao.bat stops this loop first, then the player.
cd /d "%~dp0..\host"
set PYTHONIOENCODING=utf-8
:loop
"%USERPROFILE%\.local\bin\uv.exe" run --python 3.11 --with pyserial --with pillow --with pystray --with zeroconf --with winrt-runtime --with winrt-Windows.Media.Control --with winrt-Windows.Foundation --with winrt-Windows.Foundation.Collections --with winrt-Windows.Storage.Streams python -u player.py --debug-touch --no-desktop >> player.err.log 2>&1
echo %date% %time% player exited (code %errorlevel%), restarting in 3 s >> player.err.log
"%SystemRoot%\System32\timeout.exe" /t 3 /nobreak >nul
goto loop
