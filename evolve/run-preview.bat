@echo off
rem preview.py in the same uv environment as the player (player.py needs winrt).
set PYTHONIOENCODING=utf-8
"%USERPROFILE%\.local\bin\uv.exe" run --python 3.11 --with pyserial --with pillow --with pystray --with zeroconf --with winrt-runtime --with winrt-Windows.Media.Control --with winrt-Windows.Foundation --with winrt-Windows.Foundation.Collections --with winrt-Windows.Storage.Streams python -u "%~dp0preview.py" %*
