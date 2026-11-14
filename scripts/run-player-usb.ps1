# Keep host\player.py running over USB (no Wi-Fi needed). Started hidden by start-xiao-usb.bat.
# Each round: find the board's COM port (ESP32-C3 USB-Serial/JTAG, VID 303A PID 1001), reset it
# into its firmware, run the player on that port. When the player exits (USB unplugged, crash),
# wait and go again. stop-xiao.bat stops this loop and the player.
#
# The reset is needed because the board sometimes powers up waiting in its ROM download mode
# (black screen, no serial output); a watchdog reset always boots the firmware. esptool connects
# without its DTR/RTS reset dance: that dance can itself leave the chip latched in download mode.
# If the firmware is already running the connect just fails, which is fine. player.py exits after
# 15 s without a byte from the board, so a board that is still stuck gets another reset here.
$root = Split-Path $PSScriptRoot
$uv = "$env:USERPROFILE\.local\bin\uv.exe"
$log = Join-Path $root 'host\player.err.log'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location (Join-Path $root 'host')

function Note([string]$text) { Add-Content -Path $log -Encoding utf8 -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') usb: $text" }

function Find-Board {
  $dev = Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match 'VID_303A&PID_1001' -and $_.Name -match 'COM\d+' } | Select-Object -First 1
  if ($dev -and $dev.Name -match '(COM\d+)') { return $Matches[1] }
  return $null
}

$esptool = if (Get-Command esptool -ErrorAction SilentlyContinue) { 'esptool' } else { "`"$uv`" tool run esptool" }
$pyArgs = '--with pyserial --with pillow --with pystray --with zeroconf --with winrt-runtime --with winrt-Windows.Media.Control --with winrt-Windows.Foundation --with winrt-Windows.Foundation.Collections --with winrt-Windows.Storage.Streams'

$waiting = $false
while ($true) {
  $port = Find-Board
  if (-not $port) {
    if (-not $waiting) { Note 'board not plugged in, waiting'; $waiting = $true }
    Start-Sleep -Seconds 2
    continue
  }
  $waiting = $false
  Note "board on $port, resetting it into the firmware"
  cmd /c "$esptool --port $port --before no-reset --after watchdog-reset --connect-attempts 2 chip-id >> `"$log`" 2>&1"
  Start-Sleep -Seconds 2  # let the firmware boot before the player opens the port
  Note "starting player on $port"
  cmd /c "`"$uv`" run --python 3.11 $pyArgs python -u player.py --port $port --debug-touch --no-desktop >> `"$log`" 2>&1"
  Note "player exited (code $LASTEXITCODE), restarting in 3 s"
  Start-Sleep -Seconds 3
}
